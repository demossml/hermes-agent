"""
Secretary Router — per-user active secretary profile state.

State file: ~/.hermes/secretary_router.json
Key:        telegram_user_id (str)
Value:      {"active_profile": "secretary-acme", "updated_at": "<iso8601>"}

Sources profiles from disk scan and optional SECRETARY_PROFILES allowlist.
Filters: secretary-* prefix OR allowlist from SECRETARY_PROFILES env/config.

This module does NOT switch HERMES_HOME or touch agent runtime.
It ONLY reads/writes the routing pointer for a given Telegram user.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Same regex as hermes_cli/profiles.py (_PROFILE_ID_RE)
_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Prefix for secretary profiles scanned from ~/.hermes/profiles/
_SECRETARY_PREFIX = "secretary-"

# State file (always in default/control HERMES_HOME)
_STATE_FILE = "secretary_router.json"
_STATE_VERSION = 1

# Profiles to EXCLUDE from list_secretaries() when include_tests=False
_TEST_PROFILE_PATTERNS = (
    "test-",
    "test_",
)

_lock = threading.Lock()


def _hermes_home() -> Path:
    """Control (default) HERMES_HOME — where router state lives."""
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except ImportError:
        return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def _state_path() -> Path:
    return _hermes_home() / _STATE_FILE


def _profiles_root() -> Path:
    return _hermes_home() / "profiles"


def _get_allowlist() -> set[str]:
    """
    Read SECRETARY_PROFILES allowlist from env or config.

    Env:  SECRETARY_PROFILES=dev,secretary-acme,secretary-beta
    Config: modes.secretary_profiles in config.yaml (fallback).

    Returns a set of allowed profile names (lowercased, stripped).
    Empty set = no allowlist — use prefix-based filtering.
    """
    env_val = os.environ.get("SECRETARY_PROFILES", "").strip()
    if env_val:
        return {n.strip().lower() for n in env_val.split(",") if n.strip()}

    try:
        from hermes_cli.config import cfg_get, load_config
        cfg = load_config()
        cfg_val = cfg_get(cfg, "modes", "secretary_profiles", default="")
        if isinstance(cfg_val, str) and cfg_val.strip():
            return {n.strip().lower() for n in cfg_val.split(",") if n.strip()}
        if isinstance(cfg_val, list):
            return {str(n).strip().lower() for n in cfg_val if str(n).strip()}
    except Exception:
        pass

    return set()


def _get_default_profile() -> str:
    """
    Return the default profile name for users without an active secretary.

    Priority: SECRETARY_DEFAULT_PROFILE env → config modes.default_secretary → "default"
    """
    env_val = os.environ.get("SECRETARY_DEFAULT_PROFILE", "").strip()
    if env_val:
        return env_val

    try:
        from hermes_cli.config import cfg_get, load_config
        cfg = load_config()
        cfg_val = cfg_get(cfg, "modes", "default_secretary", default="default")
        if isinstance(cfg_val, str) and cfg_val.strip():
            return cfg_val.strip()
    except Exception:
        pass

    return "default"


def _load() -> dict:
    path = _state_path()
    if not path.exists():
        return {"version": _STATE_VERSION, "users": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": _STATE_VERSION, "users": {}}
        # Migrate old flat format → new versioned format
        if "version" not in data:
            users = {}
            for k, v in data.items():
                if isinstance(v, dict) and "name" in v:
                    users[k] = {
                        "active_profile": v["name"],
                        "updated_at": v.get("updated_at", ""),
                    }
                elif isinstance(v, dict) and "active_profile" in v:
                    users[k] = v
            data = {"version": _STATE_VERSION, "users": users}
        data.setdefault("version", _STATE_VERSION)
        data.setdefault("users", {})
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return {"version": _STATE_VERSION, "users": {}}


def _save(data: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def list_secretaries(include_tests: bool = False) -> list[str]:
    """
    Return sorted list of available secretary profile names.

    Sources:
      1. Allowlist from SECRETARY_PROFILES (env or config) — if set, ONLY these
         (plus default if missing).
      2. Otherwise: scan ~/.hermes/profiles/ for secretary-* prefix.
      3. Always includes "default" as fallback (at position 0).

    Excludes test profiles (test-, test_) unless include_tests=True.
    Filters by _PROFILE_ID_RE (same as hermes profile create).
    """
    allowlist = _get_allowlist()

    if allowlist:
        result: list[str] = []
        for name in sorted(allowlist):
            if name == "default":
                if "default" not in result:
                    result.append("default")
                continue
            if not include_tests and _is_test_profile(name):
                continue
            if validate_profile(name):
                result.append(name)
        if "default" not in result:
            result.insert(0, "default")
        elif result and result[0] != "default":
            result = ["default"] + [n for n in result if n != "default"]
        return result

    result = []
    seen = {"default"}

    profiles_dir = _profiles_root()
    if profiles_dir.is_dir():
        for entry in sorted(profiles_dir.iterdir()):
            if not entry.is_dir():
                continue
            name = entry.name

            if not _PROFILE_ID_RE.match(name):
                continue
            if not name.startswith(_SECRETARY_PREFIX):
                continue
            if not include_tests and _is_test_profile(name):
                continue

            if name not in seen:
                seen.add(name)
                result.append(name)

    result.insert(0, "default")
    return result


def get_active(telegram_user_id: str) -> str:
    """
    Return the active secretary profile for a Telegram user.

    Returns SECRETARY_DEFAULT_PROFILE (or "default") if no entry exists.
    """
    user_id = str(telegram_user_id).strip()
    if not user_id:
        return _get_default_profile()

    with _lock:
        data = _load()
        users = data.get("users", {})
        entry = users.get(user_id)

    if isinstance(entry, dict):
        name = str(entry.get("active_profile", "")).strip()
        if name and validate_profile(name):
            return name

    return _get_default_profile()


def set_active(telegram_user_id: str, profile_name: str) -> None:
    """
    Set the active secretary profile for a Telegram user.

    Only changes the routing pointer — does NOT copy memory, sessions,
    or any other state between profiles.

    Raises:
        ValueError: if profile_name is invalid or the profile doesn't exist.
    """
    user_id = str(telegram_user_id).strip()
    if not user_id:
        raise ValueError("telegram_user_id must not be empty")

    name = str(profile_name).strip()
    if not validate_profile(name):
        raise ValueError(f"Invalid or non-existent profile: {name!r}")

    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        data = _load()
        data.setdefault("users", {})[user_id] = {
            "active_profile": name,
            "updated_at": now,
        }
        _save(data)

    logger.info("Secretary router: user %s → profile %s", user_id, name)


def unset_active_for_profile(profile_name: str) -> int:
    """Clear the active pointer for every user pointing at ``profile_name``.

    Called when a profile is deleted so stale routing pointers don't linger in
    the state file. Deleting the entry (rather than rewriting it to "default")
    leaves the user in the natural "no explicit preference → default" state.

    Returns the number of users affected.
    """
    name = str(profile_name).strip()
    count = 0
    with _lock:
        data = _load()
        users = data.get("users", {})
        for user_id, entry in list(users.items()):
            if isinstance(entry, dict) and str(entry.get("active_profile", "")).strip() == name:
                del users[user_id]
                count += 1
        if count:
            _save(data)
    if count:
        logger.info(
            "Secretary router: cleared active pointers for profile %s (%d user(s))",
            name,
            count,
        )
    return count


def validate_profile(name: str) -> bool:
    """
    Validate a profile name for secretary routing.

    Checks (in order):
      1. Non-empty after strip
      2. Matches _PROFILE_ID_RE (same as hermes profile create)
      3. No path traversal (/, \\, ..)
      4. "default" is always valid
      5. Profile directory exists on disk (or list_profiles fallback)

    Returns True if valid, False otherwise. Does NOT raise.
    """
    name = str(name).strip()

    if not name:
        return False

    if "/" in name or "\\" in name or ".." in name:
        return False

    if name == "default":
        return True

    if not _PROFILE_ID_RE.match(name):
        return False

    profile_path = _profiles_root() / name
    if profile_path.is_dir():
        return True

    try:
        from hermes_cli.profiles import list_profiles
        for p in list_profiles():
            pname = getattr(p, "name", None) or (p.get("name") if isinstance(p, dict) else None)
            if pname == name:
                return True
    except Exception:
        pass

    return False


def get_profile_path(profile_name: str) -> Path:
    """
    Return the filesystem path (HERMES_HOME) for a profile.

    "default" → control HERMES_HOME.
    Anything else → ~/.hermes/profiles/<name>/.
    """
    name = str(profile_name).strip()
    if name == "default":
        return _hermes_home()
    return _profiles_root() / name


def get_active_profile_path(telegram_user_id: str) -> Path:
    """Filesystem path for the user's active secretary profile."""
    active = get_active(telegram_user_id)
    return get_profile_path(active)


def _is_test_profile(name: str) -> bool:
    """Check if a profile name looks like a test/mock profile."""
    lower = name.lower()
    return any(lower.startswith(p) for p in _TEST_PROFILE_PATTERNS)
