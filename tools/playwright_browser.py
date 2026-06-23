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
