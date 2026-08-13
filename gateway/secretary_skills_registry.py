"""Secretary skill catalog — fixed registry of toggleable skills.

Each skill has a stable ``id``, a display ``title``/``emoji``, whether it is
enabled by default, and whether it needs a setup wizard (``requires_setup``).

The per-profile skill state (enabled + status) lives in the active profile's
HERMES_HOME — see :mod:`gateway.secretary_skills_store`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Valid per-skill status values.
STATUS_OFF = "off"
STATUS_NEEDS_SETUP = "needs_setup"
STATUS_READY = "ready"
STATUS_ERROR = "error"
VALID_STATUSES = frozenset({STATUS_OFF, STATUS_NEEDS_SETUP, STATUS_READY, STATUS_ERROR})


@dataclass(frozen=True)
class SecretarySkill:
    id: str
    title: str
    emoji: str
    default_enabled: bool = False
    requires_setup: bool = False


SKILLS: tuple[SecretarySkill, ...] = (
    SecretarySkill("mail", "Почта", "📧", default_enabled=True, requires_setup=True),
    SecretarySkill("calendar", "Календарь", "📅", default_enabled=False, requires_setup=True),
    SecretarySkill("groups", "Группы", "📁", default_enabled=True, requires_setup=False),
    SecretarySkill("tasks", "Задачи", "📝", default_enabled=True, requires_setup=False),
    SecretarySkill("vision", "Зрение", "👁", default_enabled=False, requires_setup=True),
    SecretarySkill("tgcli", "Telegram CLI", "✈️", default_enabled=False, requires_setup=True),
)

SKILL_BY_ID: dict[str, SecretarySkill] = {s.id: s for s in SKILLS}


def get_skill(skill_id: str) -> Optional[SecretarySkill]:
    """Return the skill for ``skill_id``, or None if unknown."""
    return SKILL_BY_ID.get((skill_id or "").strip())


def _initial_status(skill: SecretarySkill) -> str:
    if not skill.default_enabled:
        return STATUS_OFF
    if skill.requires_setup:
        return STATUS_NEEDS_SETUP
    return STATUS_READY


def default_state() -> dict[str, dict[str, object]]:
    """Return the default skill state: ``{skill_id: {"enabled": bool, "status": str}}``."""
    state: dict[str, dict[str, object]] = {}
    for skill in SKILLS:
        state[skill.id] = {
            "enabled": skill.default_enabled,
            "status": _initial_status(skill),
        }
    return state


def apply_toggle(skill_id: str, on: bool) -> Optional[dict[str, object]]:
    """Return the new ``{enabled, status}`` for toggling a skill, or None if unknown.

    - ``on=True``  → enabled=True, status = needs_setup if requires_setup else ready
    - ``on=False`` → enabled=False, status = off
    """
    skill = get_skill(skill_id)
    if skill is None:
        return None
    if on:
        status = STATUS_NEEDS_SETUP if skill.requires_setup else STATUS_READY
        return {"enabled": True, "status": status}
    return {"enabled": False, "status": STATUS_OFF}
