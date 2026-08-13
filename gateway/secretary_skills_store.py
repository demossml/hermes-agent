"""Per-profile secretary skill state (JSON).

The state file lives in the ACTIVE profile's HERMES_HOME (not the control
home), so switching secretary profiles naturally switches skill state. The
caller resolves the profile home via
:func:`gateway.secretary_router.get_active_profile_path`; the functions here
take an explicit ``profile_home`` path so they stay testable.

State file: ``<profile_home>/secretary_skills.json``

Format::

    {
      "version": 1,
      "skills": {
        "mail":      {"enabled": true,  "status": "needs_setup"},
        "groups":    {"enabled": true,  "status": "ready"},
        "vision":    {"enabled": false, "status": "off"},
      }
    }
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

from gateway.secretary_skills_registry import VALID_STATUSES, default_state

logger = logging.getLogger(__name__)

_STATE_FILENAME = "secretary_skills.json"
_STATE_VERSION = 1

_lock = threading.Lock()


def state_path(profile_home: Path) -> Path:
    """Return the path to the skills state file for a profile home."""
    return Path(profile_home) / _STATE_FILENAME


def load_state(profile_home: Path) -> dict[str, dict[str, Any]]:
    """Load ``{skill_id: {enabled, status}}`` for a profile, merged with defaults.

    Unknown skill ids are dropped; missing skills get their default state so a
    newly-added skill appears with sensible values. Corrupt/unreadable state
    falls back to defaults.
    """
    defaults = default_state()

    data: dict[str, Any] = {}
    path = state_path(profile_home)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                skills = raw.get("skills")
                if isinstance(skills, dict):
                    data = skills
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read %s: %s", path, exc)

    merged: dict[str, dict[str, Any]] = {}
    for skill_id, default in defaults.items():
        entry = data.get(skill_id)
        if not isinstance(entry, dict):
            merged[skill_id] = dict(default)
            continue
        enabled = bool(entry.get("enabled", default["enabled"]))
        status = entry.get("status") if entry.get("status") in VALID_STATUSES else default["status"]
        merged[skill_id] = {"enabled": enabled, "status": status}
    return merged


def save_state(profile_home: Path, state: dict[str, dict[str, Any]]) -> None:
    """Atomically write the full skill state for a profile home."""
    path = state_path(profile_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    payload = {"version": _STATE_VERSION, "skills": state}
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def get_skill_state(profile_home: Path, skill_id: str) -> dict[str, Any]:
    """Return ``{enabled, status}`` for one skill (with defaults)."""
    return load_state(profile_home).get(skill_id, {"enabled": False, "status": "off"})


def set_skill(profile_home: Path, skill_id: str, enabled: bool, status: str) -> dict[str, Any]:
    """Persist ``{enabled, status}`` for one skill. Returns the stored entry."""
    with _lock:
        state = load_state(profile_home)
        state[skill_id] = {"enabled": bool(enabled), "status": status}
        save_state(profile_home, state)
    return state[skill_id]


# ── Per-profile .env helpers ──────────────────────────────────

def env_path(profile_home: Path) -> Path:
    """Return the path to a profile's ``.env`` file."""
    return Path(profile_home) / ".env"


def _quote_env_value(value: str) -> str:
    """Quote a dotenv value when it contains characters that need escaping."""
    if value == "":
        return ""
    if any(ch in value for ch in " \t#\"'=") or "\n" in value or "\r" in value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def read_env_value(profile_home: Path, key: str) -> Optional[str]:
    """Read a single value from a profile's ``.env`` (None if absent)."""
    path = env_path(profile_home)
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                val = v.strip()
                # Strip matching surrounding double quotes.
                if len(val) >= 2 and val.startswith('"') and val.endswith('"'):
                    val = val[1:-1].replace('\\"', '"').replace("\\\\", "\\")
                return val
    except OSError as exc:
        logger.warning("Failed to read %s: %s", path, exc)
    return None


def write_env_value(profile_home: Path, key: str, value: str) -> None:
    """Write (or update) a single ``KEY=value`` in a profile's ``.env``.

    Restricts the file to 0600 — it holds API keys and tokens.
    """
    path = env_path(profile_home)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing_lines: list[str] = []
    if path.exists():
        try:
            existing_lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            existing_lines = []

    rendered = f"{key}={_quote_env_value(value)}"
    updated = False
    new_lines: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            k = line.split("=", 1)[0].strip()
            if k == key:
                new_lines.append(rendered)
                updated = True
                continue
        new_lines.append(line)
    if not updated:
        new_lines.append(rendered)

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    try:
        import stat
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass  # Windows or read-only FS
