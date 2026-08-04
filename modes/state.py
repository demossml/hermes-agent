
"""
Per-chat mode state — atomic JSON store.

File: ~/.hermes/state/modes.json
Key:  "{platform}::{chat_id}" or "{platform}::{chat_id}::{user_id}"
"""

from __future__ import annotations

import json
import logging
import os as _os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_VALID_MODES = frozenset({"dev", "secretary"})
_DEFAULT_MODE = "dev"
_STATE_DIR = "state"
_STATE_FILE = "modes.json"
_lock = threading.Lock()


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home()
    except ImportError:
        return Path(_os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def _state_path() -> Path:
    p = _hermes_home() / _STATE_DIR / _STATE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _make_key(platform: str, chat_id: str, user_id: Optional[str] = None) -> str:
    p = (platform or "").strip().lower()
    c = str(chat_id or "").strip()
    if user_id:
        u = str(user_id).strip()
        return f"{p}::{c}::{u}"
    return f"{p}::{c}"


def _load() -> dict:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return {}


def _save(data: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def get_mode(platform: str, chat_id: str, user_id: Optional[str] = None) -> str:
    """Return current mode: 'dev' or 'secretary' (falls back to _DEFAULT_MODE)."""
    key = _make_key(platform, chat_id, user_id)
    if not key or key in ("::", "::::"):
        return _DEFAULT_MODE
    with _lock:
        data = _load()
        entry = data.get(key)
    if isinstance(entry, dict):
        mode = str(entry.get("mode", "")).strip().lower()
        if mode in _VALID_MODES:
            return mode
    # Try without user_id as fallback
    if user_id:
        key2 = _make_key(platform, chat_id)
        with _lock:
            data = _load()
            entry = data.get(key2)
        if isinstance(entry, dict):
            mode = str(entry.get("mode", "")).strip().lower()
            if mode in _VALID_MODES:
                return mode
    return _DEFAULT_MODE


def set_mode(
    platform: str,
    chat_id: str,
    mode: str,
    user_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> dict:
    """Persist mode switch. Returns the stored entry."""
    mode = str(mode or "").strip().lower()
    if mode not in _VALID_MODES:
        raise ValueError(
            f"Invalid mode: {mode!r}. Must be one of {sorted(_VALID_MODES)}."
        )
    key = _make_key(platform, chat_id, user_id)
    now = datetime.now(timezone.utc).isoformat()
    entry: dict = {"mode": mode, "updated_at": now}
    if project_id:
        entry["project_id"] = str(project_id)
    with _lock:
        data = _load()
        data[key] = entry
        _save(data)
    logger.info("Mode set: key=%s mode=%s project=%s", key, mode, project_id or "-")
    return entry


def get_mode_record(
    platform: str, chat_id: str, user_id: Optional[str] = None
) -> Optional[dict]:
    """Return full mode entry or None."""
    key = _make_key(platform, chat_id, user_id)
    with _lock:
        data = _load()
        entry = data.get(key)
    return entry if isinstance(entry, dict) else None


def get_default_mode() -> str:
    """Return the configured default mode (dev)."""
    try:
        from hermes_cli.config import cfg_get, load_config
        cfg = load_config()
        default = cfg_get(cfg, "modes", "default", default=_DEFAULT_MODE)
        if isinstance(default, str) and default.strip().lower() in _VALID_MODES:
            return default.strip().lower()
    except Exception:
        pass
    return _DEFAULT_MODE


# ── Per-turn mode slot ────────────────────────────────────────
# Set by GatewayRunner before agent dispatch, consumed by
# system_prompt.py and check_fn gates. Thread-local.

_current_turn_mode = None


def set_current_turn_mode(mode):
    global _current_turn_mode
    mode_name = str(mode or "").strip().lower()
    _VALID = frozenset({"dev", "secretary"})
    _current_turn_mode = mode_name if mode_name in _VALID else None


def get_current_turn_mode():
    global _current_turn_mode
    mode = _current_turn_mode
    _current_turn_mode = None
    return mode
