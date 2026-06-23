#!/usr/bin/env python3
"""
Playwright Browser Tool — headless Chromium automation for Hermes Agent.

Uses Playwright's **sync API** to avoid event-loop complications.
Provides:

- Page opening with automatic console error/warning collection
- Screenshots (full-page + viewport)
- JavaScript evaluation
- HTML / text extraction
- Multiple tab support
- Real-time console log + uncaught exception capture

Designed for Linux headless environments.  Falls back gracefully
when Playwright is not installed.

Usage::

    from tools.playwright_browser import (
        pw_browser_open, pw_browser_screenshot, pw_browser_evaluate,
        pw_browser_html, pw_browser_console, pw_browser_close,
    )

    result = pw_browser_open("https://example.com")

Setup::

    pip install playwright
    playwright install chromium
"""

from __future__ import annotations

import json, logging, os, threading, time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from playwright.sync_api import sync_playwright  # sync API!
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    sync_playwright = None  # type: ignore


# ═══════════════════════════════════════════════════════════════
# Singleton browser manager (sync, thread-safe)
# ═══════════════════════════════════════════════════════════════

class _PlaywrightManager:
    """Singleton holding one Playwright browser with multiple pages.

    All methods are synchronous.  Thread-safe via internal lock.
    """

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

    @property
    def is_ready(self) -> bool:
        return self._browser is not None

    @property
    def page(self) -> Any:
        if not self._pages or self._active_index >= len(self._pages):
            return None
        return self._pages[self._active_index]

    @property
    def page_count(self) -> int:
        return len(self._pages)

    def _attach_console_listener(self, page: Any) -> None:
        """Attach console + pageerror listeners."""

        def _on_console(msg):
            entry = {
                "type": msg.type,
                "text": msg.text,
                "timestamp": time.time(),
            }
            try:
                loc = msg.location
                entry["location"] = {
                    "url": loc.get("url", ""),
                    "line": loc.get("lineNumber", 0),
                    "column": loc.get("columnNumber", 0),
                }
            except Exception:
                pass
            self._console_logs.append(entry)

        def _on_pageerror(err):
            self._console_logs.append({
                "type": "error",
                "text": str(err),
                "timestamp": time.time(),
                "location": {"url": page.url, "line": 0, "column": 0},
                "uncaught": True,
            })

        page.on("console", _on_console)
        page.on("pageerror", _on_pageerror)

    def _ensure_browser(self) -> None:
        """Lazy-init: start Playwright + Chromium if not running."""
        if self._browser is not None:
            return

        if not HAS_PLAYWRIGHT:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        pw = sync_playwright().start()
        self._pw = pw

        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        self._browser = browser

        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        self._context = context

    def open_page(self, url: str, timeout_ms: int = 30000) -> dict:
        """Open a URL in a new page. Returns page info + console errors."""
        with self._lock:
            self._ensure_browser()
            page = self._context.new_page()
            self._attach_console_listener(page)
            self._pages.append(page)
            self._active_index = len(self._pages) - 1

        console_before = len(self._console_logs)

        try:
            response = page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            # Brief wait for JS errors to surface
            page.wait_for_timeout(500)
        except Exception as e:
            return {
                "url": url,
                "title": "",
                "status_code": None,
                "content_length": 0,
                "console_errors": [],
                "navigation_errors": [f"Navigation error: {e}"],
                "page_index": self._active_index,
                "total_pages": self.page_count,
            }

        new_errors = [
            log for log in self._console_logs[console_before:]
            if log["type"] in ("error", "warning")
        ]

        try:
            title = page.title()
            content_len = page.evaluate("() => document.body?.innerText?.length || 0")
        except Exception:
            title = ""
            content_len = 0

        return {
            "url": url,
            "title": title,
            "status_code": response.status if response else None,
            "content_length": content_len,
            "console_errors": new_errors,
            "navigation_errors": [],
            "page_index": self._active_index,
            "total_pages": self.page_count,
        }

    def screenshot(self, path: str | None = None, full_page: bool = True) -> str:
        """Capture a screenshot. Returns the file path."""
        page = self.page
        if page is None:
            raise RuntimeError("No page open. Call pw_browser_open first.")

        if path is None:
            path = f"/tmp/hermes_screenshot_{int(time.time())}.png"

        page.screenshot(path=path, full_page=full_page)
        return path

    def evaluate(self, js_code: str) -> Any:
        """Execute JavaScript and return result."""
        page = self.page
        if page is None:
            raise RuntimeError("No page open.")
        return page.evaluate(js_code)

    def get_html(self) -> str:
        """Return full HTML of current page."""
        page = self.page
        if page is None:
            return ""
        return page.content()

    def get_text(self, selector: str = "body") -> str:
        """Return visible text of current page."""
        page = self.page
        if page is None:
            return ""
        return page.inner_text(selector)

    def get_title(self) -> str:
        """Return page title."""
        page = self.page
        if page is None:
            return ""
        return page.title()

    def get_console_logs(self, log_type: str = "") -> list[dict]:
        """Return collected console logs, optionally filtered."""
        if not log_type:
            return list(self._console_logs)
        return [log for log in self._console_logs if log["type"] == log_type]

    def new_tab(self, url: str = "about:blank", timeout_ms: int = 30000) -> dict:
        """Open a URL in a new tab and switch to it."""
        return self.open_page(url, timeout_ms=timeout_ms)

    def switch_tab(self, index: int) -> dict:
        """Switch to a tab by index (0-based)."""
        if index < 0 or index >= len(self._pages):
            return {"error": f"Invalid tab index {index}. Available: 0-{len(self._pages) - 1}"}

        self._active_index = index
        page = self.page
        return {
            "active_tab": index,
            "total_tabs": self.page_count,
            "url": page.url if page else "",
            "title": page.title() if page else "",
        }

    def close_tab(self, index: int | None = None) -> dict:
        """Close a tab."""
        idx = index if index is not None else self._active_index
        if idx < 0 or idx >= len(self._pages):
            return {"error": f"Invalid tab index {idx}"}

        page = self._pages.pop(idx)
        page.close()

        if not self._pages:
            self._active_index = 0
            return {"closed": True, "remaining": 0}

        if self._active_index >= len(self._pages):
            self._active_index = len(self._pages) - 1

        return {"closed": True, "remaining": len(self._pages), "active_tab": self._active_index}

    def close(self) -> None:
        """Close all pages and browser."""
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

        self._console_logs.clear()
        _PlaywrightManager._instance = None


# ═══════════════════════════════════════════════════════════════
# Public API (synchronous)
# ═══════════════════════════════════════════════════════════════

def pw_browser_open(url: str, timeout: int = 30000, task_id: str = "") -> str:
    """Open a URL and return page info + console errors as JSON."""
    mgr = _PlaywrightManager.get()
    try:
        result = mgr.open_page(url, timeout_ms=timeout)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e), "url": url})


def pw_browser_screenshot(path: str = "", full_page: bool = True, task_id: str = "") -> str:
    """Take a full-page screenshot. Returns JSON with file path."""
    mgr = _PlaywrightManager.get()
    try:
        filepath = mgr.screenshot(
            path=path if path else None,
            full_page=full_page,
        )
        return json.dumps({"screenshot_path": filepath, "success": True})
    except Exception as e:
        return json.dumps({"error": str(e)})


def pw_browser_evaluate(js_code: str, task_id: str = "") -> str:
    """Execute JS in the current page. Returns JSON with result."""
    mgr = _PlaywrightManager.get()
    try:
        result = mgr.evaluate(js_code)
        return json.dumps({"result": result}, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


def pw_browser_html(task_id: str = "") -> str:
    """Return full HTML source. Truncated at 200KB."""
    mgr = _PlaywrightManager.get()
    try:
        html = mgr.get_html()
        if len(html) > 200000:
            html = html[:200000] + f"\n<!-- Truncated at 200KB. Total: {len(html)} bytes -->"
        return html
    except Exception as e:
        return json.dumps({"error": str(e)})


def pw_browser_text(selector: str = "body", task_id: str = "") -> str:
    """Return visible text. Truncated at 100KB."""
    mgr = _PlaywrightManager.get()
    try:
        text = mgr.get_text(selector)
        if len(text) > 100000:
            text = text[:100000] + f"\n<!-- Truncated at 100KB -->"
        return text
    except Exception as e:
        return json.dumps({"error": str(e)})


def pw_browser_title(task_id: str = "") -> str:
    """Return page title as JSON."""
    mgr = _PlaywrightManager.get()
    try:
        return json.dumps({"title": mgr.get_title()})
    except Exception as e:
        return json.dumps({"error": str(e)})


def pw_browser_console(log_type: str = "", task_id: str = "") -> str:
    """Return collected console logs (last 200)."""
    mgr = _PlaywrightManager.get()
    try:
        logs = mgr.get_console_logs(log_type=log_type if log_type else "")
        return json.dumps(logs[-200:], ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


def pw_browser_new_tab(url: str = "about:blank", timeout: int = 30000, task_id: str = "") -> str:
    """Open URL in new tab, switch to it."""
    mgr = _PlaywrightManager.get()
    try:
        result = mgr.new_tab(url, timeout_ms=timeout)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


def pw_browser_switch_tab(index: int, task_id: str = "") -> str:
    """Switch to tab by index."""
    mgr = _PlaywrightManager.get()
    try:
        return json.dumps(mgr.switch_tab(index), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


def pw_browser_close_tab(index: int = -1, task_id: str = "") -> str:
    """Close a tab. -1 = current."""
    mgr = _PlaywrightManager.get()
    try:
        idx = None if index < 0 else index
        return json.dumps(mgr.close_tab(idx), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


def pw_browser_close(task_id: str = "") -> str:
    """Close browser and all pages."""
    mgr = _PlaywrightManager.get()
    try:
        mgr.close()
        return json.dumps({"closed": True})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ═══════════════════════════════════════════════════════════════
# Requirements check
# ═══════════════════════════════════════════════════════════════

def check_playwright_requirements() -> bool:
    """Check if Playwright + Chromium are installed."""
    return HAS_PLAYWRIGHT


# ═══════════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════════

from tools.registry import registry

_PW_SCHEMAS = {
    "pw_browser_open": {
        "name": "pw_browser_open",
        "description": "Open a URL in headless Chromium (Playwright sync). Returns page title, status code, console errors, and content length. Auto-collects JS console errors and uncaught exceptions.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to open (e.g., 'https://example.com')"},
                "timeout": {"type": "integer", "description": "Navigation timeout in milliseconds (default: 30000)", "default": 30000},
            },
            "required": ["url"],
        },
    },
    "pw_browser_screenshot": {
        "name": "pw_browser_screenshot",
        "description": "Take a full-page screenshot. Returns the file path.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Custom file path (default: /tmp/hermes_screenshot_<ts>.png)"},
                "full_page": {"type": "boolean", "description": "Full page or viewport only (default: true)", "default": True},
            },
        },
    },
    "pw_browser_evaluate": {
        "name": "pw_browser_evaluate",
        "description": "Execute JavaScript in the current page. Use for DOM inspection, reading page state, or extracting data.",
        "parameters": {
            "type": "object",
            "properties": {
                "js_code": {"type": "string", "description": "JavaScript code. Returns serialized result. Example: 'document.title'"},
            },
            "required": ["js_code"],
        },
    },
    "pw_browser_html": {
        "name": "pw_browser_html",
        "description": "Return full HTML source. Truncated at 200KB.",
        "parameters": {"type": "object", "properties": {}},
    },
    "pw_browser_text": {
        "name": "pw_browser_text",
        "description": "Return visible text content. Truncated at 100KB.",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector (default: 'body')", "default": "body"},
            },
        },
    },
    "pw_browser_title": {
        "name": "pw_browser_title",
        "description": "Return the current page title.",
        "parameters": {"type": "object", "properties": {}},
    },
    "pw_browser_console": {
        "name": "pw_browser_console",
        "description": "Return browser console logs. Use to detect JS errors, warnings, uncaught exceptions. Returns last 200 logs.",
        "parameters": {
            "type": "object",
            "properties": {
                "log_type": {"type": "string", "description": "Filter: 'error', 'warning', 'log', 'info'. Empty = all.", "default": ""},
            },
        },
    },
    "pw_browser_new_tab": {
        "name": "pw_browser_new_tab",
        "description": "Open URL in a new tab and switch to it. Supports multiple concurrent tabs.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL for the new tab", "default": "about:blank"},
                "timeout": {"type": "integer", "description": "Timeout ms (default: 30000)", "default": 30000},
            },
        },
    },
    "pw_browser_switch_tab": {
        "name": "pw_browser_switch_tab",
        "description": "Switch to a tab by index (0-based).",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "Tab index (0-based)"},
            },
            "required": ["index"],
        },
    },
    "pw_browser_close_tab": {
        "name": "pw_browser_close_tab",
        "description": "Close a tab. -1 = current tab.",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "Tab index (-1 for current)", "default": -1},
            },
        },
    },
    "pw_browser_close": {
        "name": "pw_browser_close",
        "description": "Close the Playwright browser and all pages.",
        "parameters": {"type": "object", "properties": {}},
    },
}

# Register all tools
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
    # Build a handler that passes matching kwargs
    _props = list(_schema["parameters"]["properties"].keys()) if "properties" in _schema["parameters"] else []

    def _make_handler(fn=_fn, props=_props):
        def _handler(args, task_id="", **kw):
            kwargs = {}
            for p in props:
                if p in args:
                    kwargs[p] = args[p]
            return fn(task_id=task_id, **kwargs)
        return _handler

    registry.register(
        name=_name,
        toolset="browser",
        schema=_schema,
        handler=_make_handler(),
        check_fn=check_playwright_requirements,
        emoji="🎭",
    )
