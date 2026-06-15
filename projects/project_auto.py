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
