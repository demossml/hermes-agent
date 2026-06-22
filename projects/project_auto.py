"""
Auto-Project Detection — intelligent project creation from user intent.

When the user says something like "давай начнём новый проект" or
"создай проект для X", the orchestrator should:

1. Recognise the intent
2. Propose creating a project (using `clarify` tool)
3. Act on confirmation — create the project and switch to it

This module provides the guidance text injected into the system prompt
and lightweight keyword matching for pre-filtering (no LLM overhead).
"""

from __future__ import annotations


# ═══════════════════════════════════════════════════════════════
# System prompt guidance — tells the orchestrator how to handle
# project-creation intent
# ═══════════════════════════════════════════════════════════════

PROJECT_AUTO_GUIDANCE = (
    "# Project-aware behaviour\n"
    "Hermes Projects let you isolate work into named workspaces with separate "
    "memory, sessions, and sub-agents. Use the tools below to manage them.\n"
    "\n"
    "## Automatic project creation\n"
    "When the user expresses intent to start a new project — phrases like:\n"
    '- "давай начнём новый проект"\n'
    '- "создай проект для ..."\n'
    '- "работаем над новым проектом X"\n'
    '- "start a new project called ..."\n'
    '- "let\'s work on a project for ..."\n'
    '- "new project: ..."\n'
    "- or any message where the primary request is to create a project\n"
    "\n"
    "You should:\n"
    "1. **Extract the project name** from the user's message. If no name is "
    "provided, ask for one.\n"
    "2. **Propose the project** using the `clarify` tool with a brief "
    "confirmation: 'Create project \"{name}\"?'\n"
    "3. **On confirmation** ('yes', 'да', 'ок', 'создавай', 'go ahead', etc.):\n"
    "   - Call the project creation flow (see below)\n"
    "   - Switch to the new project\n"
    "4. **On rejection or ambiguity**: ask for clarification.\n"
    "\n"
    "## How to create a project (the agent does NOT need to write code)\n"
    "The user-facing `/project new <name>` command is handled by the CLI. "
    "To create a project programmatically, tell the user to run:\n"
    "  `/project new <name>`\n"
    "Or, if you have terminal access, use the `projects.project_context` module:\n"
    "```python\n"
    "from projects.project_context import create_project, get_project_block\n"
    "proj = create_project(\"Name\")\n"
    "print(f\"Created: {proj['project_id']}\")\n"
    "print(get_project_block())\n"
    "```\n"
    "\n"
    "## After creating a project\n"
    "Once the project is created and you've switched to it:\n"
    "1. Run `/reset` to load the project context into your system prompt\n"
    "2. Offer to create a welcome sub-agent for the project:\n"
    "   `/subagents create <name>-assistant \"Ты — ассистент проекта X. ...\"`\n"
    "3. Create a welcome note or AGENTS.md if the project has a codebase\n"
    "4. Confirm the project is active by checking the status bar or `/project`"
)


# ═══════════════════════════════════════════════════════════════
# Lightweight keyword detection (no LLM calls)
# ═══════════════════════════════════════════════════════════════

# Phrase sets for fast pre-filtering.  If ANY phrase matches, the
# system prompt guidance kicks in.  If NONE match, zero overhead.
_NEW_PROJECT_RU = [
    "новый проект",
    "создай проект",
    "начнём проект",
    "начинаем проект",
    "работаем над проектом",
    "работаем над новым проектом",
    "новый workspace",
    "сделай проект",
    "проект для",
    "проекта для",
]

_NEW_PROJECT_EN = [
    "new project",
    "create a project",
    "create project",
    "start project",
    "begin project",
    "work on a project",
    "new workspace",
    "set up a project",
]


def detect_project_creation_intent(message: str) -> str | None:
    """Check if *message* expresses intent to create a new project.

    Returns the extracted project name if detected, or ``None`` if
    this doesn't look like a project-creation request.

    This is a lightweight keyword check — zero LLM tokens.  Used
    only to decide whether to surface the PROJECT_AUTO_GUIDANCE
    in the dynamic part of the system prompt.
    """
    msg_lower = message.lower().strip()

    # ── Detect intent (RU + EN) ─────────────────────────────
    detected = False
    for phrase in _NEW_PROJECT_RU:
        if phrase in msg_lower:
            detected = True
            break
    if not detected:
        for phrase in _NEW_PROJECT_EN:
            if phrase in msg_lower:
                detected = True
                break

    if not detected:
        return None

    # ── Extract project name ────────────────────────────────
    # Try common patterns: "проект X", "проект для X", "project X",
    # "called X", "named X", "проект: X", "project: X"
    name = _extract_name(msg_lower)
    return name


def _extract_name(msg: str) -> str | None:
    """Extract project name from a message that matched intent patterns."""
    import re

    patterns = [
        # Russian patterns
        r"(?:проект(?:а|ом)?|проектом)\s+(?:для\s+)?[\"«]([^\"»]+)[\"»]",
        r"(?:проект(?:а|ом)?|проектом)\s+(?:для\s+)?([а-яёa-z0-9][а-яёa-z0-9\s\-]{1,50})$",
        r"проект[:\s—–-]+\s*([а-яёa-z0-9][а-яёa-z0-9\s\-]{1,50})$",
        # English patterns
        r'project\s+(?:called|named|for)\s+["\']([^"\']+)["\']',
        r"project\s+(?:called|named|for)\s+([a-z0-9][a-z0-9\s\-]{1,50})$",
        r"project[:\s—–-]+\s*([a-z0-9][a-z0-9\s\-]{1,50})$",
        r"project\s+for\s+([a-z0-9][a-z0-9\s\-]{1,50})$",
    ]

    for pat in patterns:
        m = re.search(pat, msg)
        if m:
            return m.group(1).strip()

    # Fallback: if a new-project phrase is found but no name extracted,
    # return empty string (triggers "ask for name" behaviour)
    return ""


# ═══════════════════════════════════════════════════════════════
# CWD-based project detection — "you cd into a project folder,
#  Hermes auto-switches"
# ═══════════════════════════════════════════════════════════════

def detect_project_from_cwd(
    cwd: str | None = None,
    hermes_home: str | None = None,
) -> dict | None:
    """Check if *cwd* is inside a Hermes project directory.

    If the current directory is ``~/.hermes/projects/<slug>/`` or any
    subdirectory of it, return the project metadata dict.  Otherwise
    return ``None``.

    Parameters
    ----------
    cwd : str | None
        Directory to check.  Defaults to ``os.getcwd()``.
    hermes_home : str | None
        Hermes home directory.  Defaults to ``~/.hermes``.

    Returns
    -------
    dict | None
        Project metadata if cwd is inside a project, or None.
    """
    import os
    from pathlib import Path
    from projects.project_manager import ProjectManager

    if cwd is None:
        cwd = os.getcwd()

    cwd = Path(cwd).resolve()

    try:
        pm = ProjectManager(hermes_home=hermes_home)
    except Exception:
        return None

    projects_dir = pm.projects_dir.resolve()

    # ── Strategy 1: .hermes-project marker file ──────────
    # Walk up from cwd looking for a .hermes-project file
    # containing a project slug.  This lets users link ANY
    # directory to a Hermes project.
    current = cwd
    while current != current.parent:
        marker = current / ".hermes-project"
        try:
            if marker.exists():
                slug = marker.read_text(encoding="utf-8").strip()
                if slug:
                    meta = pm.get_project(slug)
                    if meta:
                        meta["_matched_by"] = "marker"
                        meta["_cwd"] = str(cwd)
                        meta["_marker_path"] = str(marker)
                        return meta
        except Exception:
            pass
        current = current.parent

    # ── Strategy 2: Hermes projects directory ────────────
    # Check if cwd is inside ~/.hermes/projects/<slug>/
    current = cwd
    while current != current.parent:  # stop at root
        try:
            # Check: is this directory a direct child of projects_dir?
            if current.parent == projects_dir:
                # It is! Extract slug from directory name
                slug = current.name
                meta = pm.get_project(slug)
                if meta:
                    meta["_matched_by"] = "cwd"
                    meta["_cwd"] = str(cwd)
                    return meta
        except Exception:
            pass
        # Also check: is cwd inside a code/ subdirectory of a project?
        # ~/.hermes/projects/<slug>/code/...
        try:
            current_rel = current.relative_to(projects_dir)
            parts = current_rel.parts
            if parts:
                slug = parts[0]
                if slug and not slug.startswith("."):
                    meta = pm.get_project(slug)
                    if meta:
                        meta["_matched_by"] = "cwd"
                        meta["_cwd"] = str(cwd)
                        return meta
        except ValueError:
            pass  # not inside projects_dir at all
        current = current.parent

    return None


def auto_switch_if_in_project(
    cwd: str | None = None,
    hermes_home: str | None = None,
) -> dict | None:
    """Detect project from cwd and switch to it automatically.

    This is the "it just works" entry point — call at startup and
    after ``/new`` to silently bind the session to the right project.

    Parameters
    ----------
    cwd : str | None
        Directory to check.  Defaults to ``os.getcwd()``.
    hermes_home : str | None
        Hermes home directory.

    Returns
    -------
    dict | None
        The project that was activated, or None if no project matched.
    """
    proj = detect_project_from_cwd(cwd=cwd, hermes_home=hermes_home)
    if proj is None:
        return None

    pid = proj["project_id"]

    try:
        from projects.project_context import (
            get_current_project_id,
            switch_project,
        )

        current = get_current_project_id()
        if current == pid:
            # Already on this project — nothing to do
            proj["_already_active"] = True
            return proj

        switch_project(pid)
        return proj
    except Exception:
        return None
