"""L7 — bind ready optional secretary skills to extra toolsets + guidance.

When a secretary profile has an optional skill enabled AND ``ready`` (validated
in L4), the gateway grants the corresponding toolset to that user's agent and
surfaces it in the secretary system prompt:

    vision  (ready)  →  "vision" toolset  (vision_analyze)
    tgcli   (ready)  →  "terminal" toolset (run `tg` on the host)

Terminal is otherwise blocked in secretary mode; granting it here is the
explicit, per-skill opt-in that makes the Telegram CLI usable. It is granted
only when the user has enabled AND validated the tgcli skill.
"""

from __future__ import annotations

from gateway.secretary_skills_registry import STATUS_READY

# ready skill id -> toolset name granted to the secretary agent
SKILL_TO_TOOLSET: dict[str, str] = {
    "vision": "vision",
    "tgcli": "terminal",
}

# skill id -> human label for the guidance line
SKILL_LABEL: dict[str, str] = {
    "vision": "Зрение (vision_analyze)",
    "tgcli": "Telegram CLI (terminal/tg)",
}


def get_ready_skill_ids(profile_home) -> frozenset[str]:
    """Return the ids of enabled-and-ready skills for a profile home."""
    try:
        from gateway.secretary_skills_store import load_state

        state = load_state(profile_home)
    except Exception:
        return frozenset()
    return frozenset(
        sid for sid, entry in state.items()
        if entry.get("enabled") and entry.get("status") == STATUS_READY
    )


def extra_toolsets_for(user_id) -> frozenset[str]:
    """Extra toolsets granted by ready optional skills for a secretary user."""
    if not user_id:
        return frozenset()
    try:
        from gateway.secretary_router import get_active_profile_path

        ready = get_ready_skill_ids(get_active_profile_path(str(user_id)))
    except Exception:
        return frozenset()
    return frozenset(SKILL_TO_TOOLSET[s] for s in ready if s in SKILL_TO_TOOLSET)


def skill_guidance_line(user_id) -> str:
    """One-line RU summary of ready optional skills (for the secretary prompt)."""
    if not user_id:
        return ""
    try:
        from gateway.secretary_router import get_active_profile_path

        ready = get_ready_skill_ids(get_active_profile_path(str(user_id)))
    except Exception:
        return ""
    labels = [SKILL_LABEL[s] for s in ready if s in SKILL_LABEL]
    if not labels:
        return ""
    return "Активные умения секретаря: " + ", ".join(sorted(labels)) + "."
