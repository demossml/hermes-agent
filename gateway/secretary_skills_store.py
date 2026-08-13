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
from typing import Any

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
