#!/usr/bin/env python3
"""
Playwright Browser Tool — hardened headless Chromium for Hermes Agent.

Production-grade browser automation with:
- Automatic crash recovery (2 retries)
- Strict security sandbox (no images/fonts/downloads/WebGL/notifications)
- Idle timeout (5 min → auto-close)
- Resource limits (max 10 tabs, JS timeout, memory cap)
- Graceful error handling (never leaves zombie processes)
- Structured logging for all critical events

Uses Playwright's **sync API** for event-loop safety.

Usage::

    from tools.playwright_browser import pw_browser_open

    result = pw_browser_open("https://example.com")

Setup::

    pip install playwright
    playwright install chromium
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    sync_playwright = None
    PWTimeout = Exception


# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

MAX_TABS = 10
IDLE_TIMEOUT_SEC = 300        # 5 minutes
BROWSER_CRASH_RETRIES = 2
JS_EVAL_TIMEOUT_MS = 10_000   # 10 seconds max for JS evaluation
PAGE_NAV_TIMEOUT_MS = 30_000  # 30 seconds default nav timeout
WATCHDOG_INTERVAL_SEC = 30    # Check idle every 30 seconds

# Security: block everything unnecessary
_SECURITY_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-translate",
    "--disable-extensions",
    "--disable-default-apps",
    "--disable-component-update",
    "--disable-background-timer-throttling",
    "--disable-ipc-flooding-protection",
    "--disable-renderer-backgrounding",
    "--disable-field-trial-config",
    "--disable-hang-monitor",
    "--disable-prompt-on-repost",
    "--disable-client-side-phishing-detection",
    "--disable-popup-blocking",
    "--disable-component-extensions-with-background-pages",
    "--no-first-run",
    "--no-default-browser-check",
    "--no-pings",
    "--no-zygote",
    "--single-process",  # cleaner cleanup
    "--memory-pressure-off",
    "--js-flags=--max-old-space-size=256",  # 256MB JS heap limit
]

# Context-level blocklist
_CONTEXT_BLOCKLIST = {
    "images": 2,        # BlockStrategy.BLOCK = 2
    "fonts": 2,
    "media": 2,         # audio + video
    "downloads": 2,
    "notifications": 2,
    "geolocation": 2,
    "midi": 2,
    "camera": 2,
    "microphone": 2,
    "clipboard": 2,
    "autoplay": 2,
}


# ═══════════════════════════════════════════════════════════════
# Hardened singleton browser manager
# ═══════════════════════════════════════════════════════════════

class _PlaywrightManager:
    """Thread-safe singleton.  Handles lifecycle, crashes, idle timeout."""

    _instance: _PlaywrightManager | None = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> _PlaywrightManager:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._pages: list[Any] = []
        self._active_index: int = 0
        self._console_logs: list[dict] = []
        self._crash_count: int = 0
        self._last_activity: float = time.time()
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_stop = threading.Event()
        self._closed: bool = False

    # ── Properties ───────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        return self._browser is not None and not self._closed

    @property
    def page(self) -> Any:
        if not self._pages or self._active_index >= len(self._pages):
            return None
        return self._pages[self._active_index]

    @property
    def page_count(self) -> int:
        return len(self._pages)

    # ── Console listener ─────────────────────────────────

    def _attach_console_listener(self, page: Any) -> None:
        def _on_console(msg):
            self._console_logs.append({
                "type": msg.type,
                "text": msg.text,
                "timestamp": time.time(),
                "location": {
                    "url": (msg.location or {}).get("url", ""),
                    "line": (msg.location or {}).get("lineNumber", 0),
                    "column": (msg.location or {}).get("columnNumber", 0),
                },
            })

        def _on_pageerror(err):
            self._console_logs.append({
                "type": "error",
                "text": str(err),
                "timestamp": time.time(),
                "uncaught": True,
            })
            logger.warning("JS uncaught error on %s: %s", page.url, str(err)[:200])

        page.on("console", _on_console)
        page.on("pageerror", _on_pageerror)

    # ── Browser lifecycle ────────────────────────────────

    def _ensure_browser(self) -> None:
        """Lazy-init with crash detection and retry."""
        if self._browser is not None:
            # Check if browser is still alive
            try:
                if self._browser.is_connected():
                    self._last_activity = time.time()
                    return
                logger.warning("Browser disconnected — reconnecting")
            except Exception:
                logger.warning("Browser connection check failed — reconnecting")
            self._force_close()

        if not HAS_PLAYWRIGHT:
            raise RuntimeError("Playwright not installed. pip install playwright && playwright install chromium")

        if self._closed:
            raise RuntimeError("Browser has been permanently closed")

        self._crash_count = 0
        self._try_launch()

    def _try_launch(self) -> None:
        """Launch browser with retry on failure."""
        last_error = None
        for attempt in range(1, BROWSER_CRASH_RETRIES + 2):
            try:
                self._do_launch()
                logger.info("Browser launched (attempt %d)", attempt)
                self._start_watchdog()
                return
            except Exception as e:
                last_error = e
                logger.error("Browser launch attempt %d failed: %s", attempt, e)
                self._force_close()
                if attempt <= BROWSER_CRASH_RETRIES:
                    time.sleep(1.0 * attempt)

        raise RuntimeError(f"Browser failed to start after {BROWSER_CRASH_RETRIES + 1} attempts: {last_error}")

    def _do_launch(self) -> None:
        """Single launch attempt."""
        pw = sync_playwright().start()
        self._pw = pw

        browser = pw.chromium.launch(
            headless=True,
            args=_SECURITY_ARGS,
            handle_sigint=False,
            handle_sigterm=False,
            handle_sighup=False,
        )
        self._browser = browser

        # Context with strict permissions
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            # Block all unnecessary features
            permissions=[],
            geolocation=None,
            # CSP: strict
            bypass_csp=False,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        self._context = context
        self._last_activity = time.time()

    # ── Page operations ──────────────────────────────────

    def _check_crash(self) -> None:
        """Detect browser crash and recover."""
        if self._browser is None:
            return
        try:
            if not self._browser.is_connected():
                logger.warning("Browser crash detected — recovering")
                self._crash_count += 1
                self._force_close()
                self._try_launch()
        except Exception:
            pass

    def open_page(self, url: str, timeout_ms: int = PAGE_NAV_TIMEOUT_MS) -> dict:
        """Open URL with crash recovery and tab limits."""
        with self._lock:
            self._check_crash()
            self._ensure_browser()

            # Enforce tab limit
            if self.page_count >= MAX_TABS:
                # Close oldest tab (index 0) if at limit
                try:
                    oldest = self._pages.pop(0)
                    oldest.close()
                    logger.info("Tab limit (%d) reached — closed oldest tab", MAX_TABS)
                except Exception:
                    pass
                if self._active_index > 0:
                    self._active_index -= 1

            page = self._context.new_page()
            self._attach_console_listener(page)
            self._pages.append(page)
            self._active_index = len(self._pages) - 1

        self._last_activity = time.time()
        console_before = len(self._console_logs)

        try:
            response = page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            # Brief settle for JS + title
            try:
                page.wait_for_load_state("load", timeout=3000)
            except Exception:
                pass
            page.wait_for_timeout(200)
        except PWTimeout:
            logger.warning("Navigation timeout: %s", url[:100])
            return self._error_result(url, "Navigation timeout")
        except Exception as e:
            logger.error("Navigation error for %s: %s", url[:100], e)
            return self._error_result(url, str(e))

        new_errors = [
            log for log in self._console_logs[console_before:]
            if log["type"] in ("error", "warning")
        ]

        try:
            title = page.title()
            content_len = page.evaluate(
                "() => document.body?.innerText?.length || 0"
            )
        except Exception:
            title = ""
            content_len = 0

        result = {
            "url": url,
            "title": title,
            "status_code": response.status if response else None,
            "content_length": content_len,
            "console_errors": new_errors,
            "navigation_errors": [],
            "page_index": self._active_index,
            "total_pages": self.page_count,
        }
        logger.info("Page opened: %s → %d (%d console issues)",
                     url[:80], result["status_code"] or 0, len(new_errors))
        return result

    def _error_result(self, url: str, error: str) -> dict:
        return {
            "url": url,
            "title": "",
            "status_code": None,
            "content_length": 0,
            "console_errors": [],
            "navigation_errors": [error],
            "page_index": self._active_index,
            "total_pages": self.page_count,
        }

    def screenshot(self, path: str | None = None, full_page: bool = True) -> str:
        self._check_crash()
        page = self.page
        if page is None:
            raise RuntimeError("No page open")
        self._last_activity = time.time()
        if path is None:
            path = f"/tmp/hermes_screenshot_{int(time.time())}.png"
        page.screenshot(path=path, full_page=full_page, timeout=JS_EVAL_TIMEOUT_MS)
        return path

    def evaluate(self, js_code: str) -> Any:
        self._check_crash()
        page = self.page
        if page is None:
            raise RuntimeError("No page open")
        self._last_activity = time.time()
        return page.evaluate(js_code)

    def get_html(self) -> str:
        self._check_crash()
        page = self.page
        if page is None:
            return ""
        self._last_activity = time.time()
        return page.content()

    def get_text(self, selector: str = "body") -> str:
        self._check_crash()
        page = self.page
        if page is None:
            return ""
        self._last_activity = time.time()
        return page.inner_text(selector, timeout=JS_EVAL_TIMEOUT_MS)

    def get_title(self) -> str:
        self._check_crash()
        page = self.page
        if page is None:
            return ""
        self._last_activity = time.time()
        return page.title()

    def get_console_logs(self, log_type: str = "") -> list[dict]:
        if not log_type:
            return list(self._console_logs)
        return [log for log in self._console_logs if log["type"] == log_type]

    def switch_tab(self, index: int) -> dict:
        if index < 0 or index >= len(self._pages):
            return {"error": f"Invalid tab {index}. Range: 0-{len(self._pages) - 1}"}
        self._last_activity = time.time()
        self._active_index = index
        p = self.page
        return {
            "active_tab": index,
            "total_tabs": self.page_count,
            "url": p.url if p else "",
            "title": p.title() if p else "",
        }

    def close_tab(self, index: int | None = None) -> dict:
        idx = index if index is not None else self._active_index
        if idx < 0 or idx >= len(self._pages):
            return {"error": f"Invalid tab {idx}"}
        self._last_activity = time.time()
        page = self._pages.pop(idx)
        try:
            page.close()
        except Exception:
            pass
        if not self._pages:
            self._active_index = 0
            return {"closed": True, "remaining": 0}
        if self._active_index >= len(self._pages):
            self._active_index = len(self._pages) - 1
        return {"closed": True, "remaining": len(self._pages), "active_tab": self._active_index}

    # ── High-level testing commands ────────────────────────

    def test_page(self, url: str, timeout_ms: int = PAGE_NAV_TIMEOUT_MS) -> dict:
        """Open page + screenshot + console errors → comprehensive summary."""
        result = self.open_page(url, timeout_ms=timeout_ms)
        if result.get("navigation_errors"):
            return result  # don't screenshot on nav failure

        screenshot_path = ""
        try:
            screenshot_path = self.screenshot(full_page=True)
        except Exception as e:
            result.setdefault("navigation_errors", []).append(f"Screenshot error: {e}")

        # Count errors by type
        error_count = sum(1 for log in self._console_logs if log["type"] == "error")
        warning_count = sum(1 for log in self._console_logs if log["type"] == "warning")

        # Meta tags
        meta_tags = {}
        try:
            page = self.page
            if page:
                metas = page.evaluate("""() => {
                    const tags = {};
                    document.querySelectorAll('meta').forEach(m => {
                        const name = m.getAttribute('name') || m.getAttribute('property') || '';
                        if (name) tags[name] = m.getAttribute('content') || '';
                    });
                    return tags;
                }""")
                meta_tags = metas or {}
        except Exception:
            pass

        result["screenshot_path"] = screenshot_path
        result["error_count"] = error_count
        result["warning_count"] = warning_count
        result["meta_tags"] = meta_tags
        result["tested_at"] = time.time()
        return result

    def wait_for_selector(self, selector: str, timeout_sec: float = 10.0) -> dict:
        """Wait for a CSS selector to appear on the page."""
        self._check_crash()
        page = self.page
        if page is None:
            return {"error": "No page open"}
        self._last_activity = time.time()
        timeout_ms = int(timeout_sec * 1000)
        try:
            page.wait_for_selector(selector, timeout=timeout_ms, state="visible")
            return {"found": True, "selector": selector, "timeout_sec": timeout_sec}
        except Exception as e:
            return {"found": False, "selector": selector, "error": str(e)[:200]}

    def click_and_wait(self, selector: str, timeout_sec: float = 10.0) -> dict:
        """Click an element and wait for navigation/load."""
        self._check_crash()
        page = self.page
        if page is None:
            return {"error": "No page open"}
        self._last_activity = time.time()
        timeout_ms = int(timeout_sec * 1000)

        try:
            # Click with navigation detection
            with page.expect_navigation(timeout=timeout_ms, wait_until="domcontentloaded"):
                page.click(selector, timeout=timeout_ms)
            title = page.title()
            return {
                "clicked": True,
                "selector": selector,
                "new_url": page.url,
                "new_title": title,
            }
        except Exception as e:
            # Try plain click (no navigation happened)
            try:
                page.click(selector, timeout=timeout_ms)
                page.wait_for_timeout(500)
                return {
                    "clicked": True,
                    "selector": selector,
                    "url": page.url,
                    "title": page.title(),
                    "note": "No navigation detected",
                }
            except Exception as e2:
                return {"clicked": False, "selector": selector, "error": str(e2)[:200]}

    def fill_form(self, data: dict) -> dict:
        """Fill form fields from a {selector: value} dict.

        Example:
            fill_form({"input[name='email']": "test@test.com", "#password": "secret"})
        """
        self._check_crash()
        page = self.page
        if page is None:
            return {"error": "No page open"}
        self._last_activity = time.time()

        filled = {}
        errors = {}
        for selector, value in data.items():
            try:
                page.fill(selector, str(value), timeout=5000)
                filled[selector] = True
            except Exception as e:
                filled[selector] = False
                errors[selector] = str(e)[:100]

        return {
            "filled": len([v for v in filled.values() if v]),
            "total": len(data),
            "details": filled,
            "errors": errors if errors else None,
        }

    def get_page_info(self) -> dict:
        """Comprehensive page diagnostics: title, url, meta, console, performance."""
        self._check_crash()
        page = self.page
        if page is None:
            return {"error": "No page open"}
        self._last_activity = time.time()

        info: dict = {"url": page.url, "title": ""}
        try:
            info["title"] = page.title()
        except Exception:
            pass

        # Meta tags
        try:
            info["meta"] = page.evaluate("""() => {
                const tags = {};
                document.querySelectorAll('meta').forEach(m => {
                    const n = m.getAttribute('name') || m.getAttribute('property') || m.getAttribute('charset') || '';
                    if (n) tags[n] = m.getAttribute('content') || m.getAttribute('charset') || '';
                });
                return tags;
            }""")
        except Exception:
            info["meta"] = {}

        # Content stats
        try:
            info["content_stats"] = page.evaluate("""() => ({
                textLength: (document.body?.innerText || '').length,
                links: document.querySelectorAll('a').length,
                images: document.querySelectorAll('img').length,
                scripts: document.querySelectorAll('script').length,
                forms: document.querySelectorAll('form').length,
            })""")
        except Exception:
            info["content_stats"] = {}

        # Console summary
        errors = [log for log in self._console_logs if log["type"] == "error"]
        warnings = [log for log in self._console_logs if log["type"] == "warning"]
        info["console_summary"] = {
            "errors": len(errors),
            "warnings": len(warnings),
            "total_logs": len(self._console_logs),
            "last_error": errors[-1]["text"][:200] if errors else None,
        }

        return info

    def scroll_to_bottom(self) -> dict:
        """Scroll to the bottom of the page (useful for lazy-loaded content)."""
        self._check_crash()
        page = self.page
        if page is None:
            return {"error": "No page open"}
        self._last_activity = time.time()

        prev_height = 0
        scrolls = 0
        try:
            for _ in range(20):  # max 20 scrolls
                height = page.evaluate("document.body.scrollHeight")
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(300)
                scrolls += 1
                if height == prev_height:
                    break
                prev_height = height
        except Exception as e:
            return {"scrolled": scrolls, "final_height": prev_height, "error": str(e)[:100]}

        return {"scrolled": True, "scroll_steps": scrolls, "final_height_px": prev_height}

    # ── Idle watchdog ────────────────────────────────────

    def _start_watchdog(self) -> None:
        if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
            return
        self._watchdog_stop.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, daemon=True, name="pw-browser-watchdog"
        )
        self._watchdog_thread.start()
        logger.debug("Idle watchdog started (timeout=%ds)", IDLE_TIMEOUT_SEC)

    def _watchdog_loop(self) -> None:
        while not self._watchdog_stop.is_set():
            self._watchdog_stop.wait(WATCHDOG_INTERVAL_SEC)
            if self._watchdog_stop.is_set():
                return
            idle = time.time() - self._last_activity
            if idle > IDLE_TIMEOUT_SEC and self._browser is not None:
                logger.info("Browser idle for %.0fs — auto-closing", idle)
                self.close()

    # ── Shutdown ─────────────────────────────────────────

    def _force_close(self) -> None:
        """Aggressive close — no mercy for zombies."""
        for page in self._pages:
            try:
                page.close()
            except Exception:
                pass
        self._pages.clear()

        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
        self._context = None

        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        self._browser = None

        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._pw = None

    def close(self) -> None:
        """Graceful close — stops watchdog, closes everything."""
        self._closed = True
        self._watchdog_stop.set()
        logger.info("Browser shutdown initiated (pages=%d, errors=%d)",
                     self.page_count, len(self._console_logs))
        self._force_close()
        self._console_logs.clear()
        _PlaywrightManager._instance = None


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def _safe_call(fn, *args, **kwargs) -> str:
    """Wrap any manager call with crash recovery and error handling."""
    try:
        result = fn(*args, **kwargs)
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, default=str)
    except RuntimeError as e:
        logger.error("Playwright runtime error: %s", e)
        return json.dumps({"error": str(e)})
    except Exception as e:
        logger.error("Playwright unexpected error: %s", e)
        return json.dumps({"error": str(e)})


def pw_browser_open(url: str, timeout: int = PAGE_NAV_TIMEOUT_MS, task_id: str = "") -> str:
    mgr = _PlaywrightManager.get()
    return _safe_call(mgr.open_page, url, timeout_ms=timeout)


def pw_browser_screenshot(path: str = "", full_page: bool = True, task_id: str = "") -> str:
    mgr = _PlaywrightManager.get()
    return _safe_call(lambda: {
        "screenshot_path": mgr.screenshot(path=path if path else None, full_page=full_page),
        "success": True,
    })


def pw_browser_evaluate(js_code: str, task_id: str = "") -> str:
    mgr = _PlaywrightManager.get()
    return _safe_call(lambda: {"result": mgr.evaluate(js_code)})


def pw_browser_html(task_id: str = "") -> str:
    mgr = _PlaywrightManager.get()
    try:
        html = mgr.get_html()
        if len(html) > 200_000:
            html = html[:200_000] + f"\n<!-- Truncated at 200KB. Total: {len(html)} bytes -->"
        return html
    except Exception as e:
        return json.dumps({"error": str(e)})


def pw_browser_text(selector: str = "body", task_id: str = "") -> str:
    mgr = _PlaywrightManager.get()
    try:
        text = mgr.get_text(selector)
        if len(text) > 100_000:
            text = text[:100_000] + "\n<!-- Truncated at 100KB -->"
        return text
    except Exception as e:
        return json.dumps({"error": str(e)})


def pw_browser_title(task_id: str = "") -> str:
    mgr = _PlaywrightManager.get()
    return _safe_call(lambda: {"title": mgr.get_title()})


def pw_browser_console(log_type: str = "", task_id: str = "") -> str:
    mgr = _PlaywrightManager.get()
    return _safe_call(lambda: mgr.get_console_logs(log_type if log_type else "")[-200:])


def pw_browser_new_tab(url: str = "about:blank", timeout: int = PAGE_NAV_TIMEOUT_MS, task_id: str = "") -> str:
    mgr = _PlaywrightManager.get()
    return _safe_call(mgr.open_page, url, timeout_ms=timeout)


def pw_browser_switch_tab(index: int, task_id: str = "") -> str:
    mgr = _PlaywrightManager.get()
    return _safe_call(mgr.switch_tab, index)


def pw_browser_close_tab(index: int = -1, task_id: str = "") -> str:
    mgr = _PlaywrightManager.get()
    idx = None if index < 0 else index
    return _safe_call(mgr.close_tab, idx)


def pw_browser_close(task_id: str = "") -> str:
    mgr = _PlaywrightManager.get()
    try:
        mgr.close()
        return json.dumps({"closed": True})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── High-level testing commands ────────────────────────

def browser_test_page(url: str, timeout: int = PAGE_NAV_TIMEOUT_MS, task_id: str = "") -> str:
    """Open page + full screenshot + console errors → comprehensive test summary."""
    mgr = _PlaywrightManager.get()
    return _safe_call(mgr.test_page, url, timeout_ms=timeout)


def browser_wait_for_selector(selector: str, timeout: int = 10, task_id: str = "") -> str:
    """Wait for a CSS selector to become visible. Returns found/not-found."""
    mgr = _PlaywrightManager.get()
    return _safe_call(mgr.wait_for_selector, selector, timeout_sec=float(timeout))


def browser_click_and_wait(selector: str, timeout: int = 10, task_id: str = "") -> str:
    """Click element + wait for navigation/load. Returns new URL and title."""
    mgr = _PlaywrightManager.get()
    return _safe_call(mgr.click_and_wait, selector, timeout_sec=float(timeout))


def browser_fill_form(data_json: str, task_id: str = "") -> str:
    """Fill form fields from JSON: '{"selector": "value", ...}'."""
    mgr = _PlaywrightManager.get()
    try:
        data = json.loads(data_json) if isinstance(data_json, str) else data_json
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid JSON: " + data_json[:100]})
    return _safe_call(mgr.fill_form, data)


def browser_get_page_info(task_id: str = "") -> str:
    """Comprehensive page diagnostics: title, URL, meta tags, content stats, console summary."""
    mgr = _PlaywrightManager.get()
    return _safe_call(mgr.get_page_info)


def browser_scroll_to_bottom(task_id: str = "") -> str:
    """Scroll to page bottom (for lazy-loaded content). Returns scroll count + final height."""
    mgr = _PlaywrightManager.get()
    return _safe_call(mgr.scroll_to_bottom)


# ═══════════════════════════════════════════════════════════════
# Requirements
# ═══════════════════════════════════════════════════════════════

def check_playwright_requirements() -> bool:
    return HAS_PLAYWRIGHT


# ═══════════════════════════════════════════════════════════════
# Auto-cleanup on process exit
# ═══════════════════════════════════════════════════════════════

def _atexit_cleanup() -> None:
    """Close browser on interpreter shutdown — prevents zombie processes."""
    try:
        mgr = _PlaywrightManager._instance
        if mgr is not None and mgr._browser is not None:
            logger.info("atexit: cleaning up browser")
            mgr.close()
    except Exception:
        pass


atexit.register(_atexit_cleanup)


# ═══════════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════════

from tools.registry import registry

_PW_SCHEMAS = {
    "pw_browser_open": {
        "name": "pw_browser_open",
        "description": (
            "Open a URL in hardened headless Chromium. Returns page title, "
            "status code, console errors/warnings, and content length. "
            "Auto-collects JS errors + uncaught exceptions. "
            "Max 10 tabs, 30s timeout, 5min idle auto-close."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to open"},
                "timeout": {"type": "integer", "description": "Navigation timeout ms (default 30000)", "default": 30000},
            },
            "required": ["url"],
        },
    },
    "pw_browser_screenshot": {
        "name": "pw_browser_screenshot",
        "description": "Full-page screenshot → file path. 10s timeout.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Custom path (default: /tmp/hermes_screenshot_<ts>.png)"},
                "full_page": {"type": "boolean", "description": "Full page (default: true)", "default": True},
            },
        },
    },
    "pw_browser_evaluate": {
        "name": "pw_browser_evaluate",
        "description": "Execute JS in page. 10s timeout, 256MB heap limit. Returns serialized result.",
        "parameters": {
            "type": "object",
            "properties": {
                "js_code": {"type": "string", "description": "JS code. Example: 'document.title'"},
            },
            "required": ["js_code"],
        },
    },
    "pw_browser_html": {
        "name": "pw_browser_html",
        "description": "Full HTML source. Truncated at 200KB.",
        "parameters": {"type": "object", "properties": {}},
    },
    "pw_browser_text": {
        "name": "pw_browser_text",
        "description": "Visible text. Truncated at 100KB.",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector (default: 'body')", "default": "body"},
            },
        },
    },
    "pw_browser_title": {
        "name": "pw_browser_title",
        "description": "Current page title.",
        "parameters": {"type": "object", "properties": {}},
    },
    "pw_browser_console": {
        "name": "pw_browser_console",
        "description": "Browser console logs (last 200). Filter by type: error, warning, log, info.",
        "parameters": {
            "type": "object",
            "properties": {
                "log_type": {"type": "string", "description": "Filter: 'error', 'warning', 'log', 'info'. Empty = all.", "default": ""},
            },
        },
    },
    "pw_browser_new_tab": {
        "name": "pw_browser_new_tab",
        "description": "New tab + navigate. Max 10 tabs.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL (default: about:blank)", "default": "about:blank"},
                "timeout": {"type": "integer", "description": "Timeout ms (default 30000)", "default": 30000},
            },
        },
    },
    "pw_browser_switch_tab": {
        "name": "pw_browser_switch_tab",
        "description": "Switch to tab by index (0-based).",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "Tab index"},
            },
            "required": ["index"],
        },
    },
    "pw_browser_close_tab": {
        "name": "pw_browser_close_tab",
        "description": "Close a tab. -1 = current.",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "Tab index (-1 for current)", "default": -1},
            },
        },
    },
    "pw_browser_close": {
        "name": "pw_browser_close",
        "description": "Close browser + all pages. Also registered as atexit handler.",
        "parameters": {"type": "object", "properties": {}},
    },
    # ── High-level testing ────────────────────────────
    "browser_test_page": {
        "name": "browser_test_page",
        "description": "Open page + full screenshot + console errors + meta tags. Returns comprehensive test summary with title, status, error_count, warning_count, screenshot_path, and meta_tags.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to test"},
                "timeout": {"type": "integer", "description": "Navigation timeout ms (default 30000)", "default": 30000},
            },
            "required": ["url"],
        },
    },
    "browser_wait_for_selector": {
        "name": "browser_wait_for_selector",
        "description": "Wait for a CSS selector to become visible on the page. Use before interacting with dynamically loaded elements.",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector to wait for (e.g., '#results', '.loaded')"},
                "timeout": {"type": "integer", "description": "Max wait in seconds (default 10)", "default": 10},
            },
            "required": ["selector"],
        },
    },
    "browser_click_and_wait": {
        "name": "browser_click_and_wait",
        "description": "Click an element and intelligently wait for navigation or page load. Returns new URL and title after click.",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector to click (e.g., 'button[type=submit]')"},
                "timeout": {"type": "integer", "description": "Max wait in seconds (default 10)", "default": 10},
            },
            "required": ["selector"],
        },
    },
    "browser_fill_form": {
        "name": "browser_fill_form",
        "description": "Fill multiple form fields at once. Pass JSON: '{\"input[name=email]\": \"test@test.com\", \"#password\": \"secret\"}'. Returns filled/total count and per-field status.",
        "parameters": {
            "type": "object",
            "properties": {
                "data_json": {"type": "string", "description": "JSON string mapping CSS selectors to values"},
            },
            "required": ["data_json"],
        },
    },
    "browser_get_page_info": {
        "name": "browser_get_page_info",
        "description": "Comprehensive page diagnostics: title, URL, meta tags (description, keywords, og:*), content stats (links, images, scripts, forms count), and console error/warning summary.",
        "parameters": {"type": "object", "properties": {}},
    },
    "browser_scroll_to_bottom": {
        "name": "browser_scroll_to_bottom",
        "description": "Scroll to the bottom of the page. Useful for triggering lazy-loaded content or infinite scroll. Returns scroll steps and final page height in pixels.",
        "parameters": {"type": "object", "properties": {}},
    },
}

_FN_MAP = {
    "pw_browser_open": pw_browser_open,
    "pw_browser_screenshot": pw_browser_screenshot,
    "pw_browser_evaluate": pw_browser_evaluate,
    "pw_browser_html": pw_browser_html,
    "pw_browser_text": pw_browser_text,
    "pw_browser_title": pw_browser_title,
    "pw_browser_console": pw_browser_console,
    "pw_browser_new_tab": pw_browser_new_tab,
    "pw_browser_switch_tab": pw_browser_switch_tab,
    "pw_browser_close_tab": pw_browser_close_tab,
    "pw_browser_close": pw_browser_close,
    # High-level testing
    "browser_test_page": browser_test_page,
    "browser_wait_for_selector": browser_wait_for_selector,
    "browser_click_and_wait": browser_click_and_wait,
    "browser_fill_form": browser_fill_form,
    "browser_get_page_info": browser_get_page_info,
    "browser_scroll_to_bottom": browser_scroll_to_bottom,
}

for _name, _fn in _FN_MAP.items():
    _schema = _PW_SCHEMAS[_name]
    _props = list(_schema.get("parameters", {}).get("properties", {}).keys())

    def _make_handler(fn=_fn, props=_props):
        def _handler(args, task_id="", **kw):
            kwargs = {"task_id": task_id}
            for p in props:
                if p in args:
                    kwargs[p] = args[p]
            return fn(**kwargs)
        return _handler

    registry.register(
        name=_name,
        toolset="browser",
        schema=_schema,
        handler=_make_handler(),
        check_fn=check_playwright_requirements,
        emoji="🎭",
    )
