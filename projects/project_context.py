"""
Project Context Middleware — bridges ProjectManager into the orchestrator.

Provides module-level globals that every agent (orchestrator + sub-agents)
can read to know which project is active, plus a middleware class that:

- Reads ``.current_project`` from disk on startup
- Injects a ``[ACTIVE PROJECT]`` block into the system prompt of ALL agents
- Saves previous project state (→ LTM) on switch
- Restores project context on load

Usage::

    from projects.project_context import (
        get_project_context,
        get_project_block,
        switch_project,
        get_current_project_id,
        get_current_project_name,
    )

    # In agent/system_prompt.py — inject project block:
    proj_block = get_project_block()
    if proj_block:
        stable_parts.append(proj_block)

    # In CLI (/project command handler):
    switch_project("my-app")
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# Module-level globals — the single source of truth for the
# active project in the running process.
# ═══════════════════════════════════════════════════════════════

_current_project_id: str | None = None
_current_project_name: str | None = None
_current_project_loaded_at: float = 0.0

# Re-read from disk at most once per 2 seconds (cross-process sync)
_CURRENT_PROJECT_CACHE_TTL = 2.0

# Hot-switch flag — set when project changes mid-session.
# Checked by the conversation loop to rebuild the system prompt CONTEXT line.
_project_just_switched: bool = False
_switch_new_prefix: str = ""


def notify_project_switched(new_prefix: str) -> None:
    """Signal that the project changed — conversation loop must rebuild CONTEXT."""
    global _project_just_switched, _switch_new_prefix
    _project_just_switched = True
    _switch_new_prefix = new_prefix


def check_and_apply_project_switch(agent) -> bool:
    """If a project switch happened, patch agent's system prompt CONTEXT line.

    Called by ``_restore_or_build_system_prompt`` at the start of every turn.
    Returns True if the prompt was modified.
    """
    global _project_just_switched, _switch_new_prefix
    if not _project_just_switched:
        return False

    _project_just_switched = False
    new_prefix = _switch_new_prefix
    _switch_new_prefix = ""

    # Patch CONTEXT line in cached system prompt
    cached = getattr(agent, '_cached_system_prompt', '') or ''
    import re
    new_context = f"CONTEXT: {new_prefix}"

    if 'CONTEXT:' in cached:
        cached = re.sub(r'CONTEXT:.*', new_context, cached)
    else:
        cached = cached + '\n' + new_context if cached else new_context

    agent._cached_system_prompt = cached

    # Persist to SessionDB so future turns see the new CONTEXT
    try:
        db = getattr(agent, '_session_db', None)
        sid = getattr(agent, 'session_id', None)
        if db and sid:
            db.update_system_prompt(sid, cached)
    except Exception:
        pass

    return True


def _lazy_load_current_project() -> None:
    """Load current project from disk, with short TTL for cross-process sync.

    The gateway process never calls switch_project() — it relies on
    the ``.current_project`` file written by the CLI process.

    Cache TTL of 2s prevents excessive disk reads while still picking
    up project switches within a single conversation turn.
    """
    global _current_project_id, _current_project_name, _current_project_loaded_at

    import time
    now = time.time()

    # Short TTL: re-read even if already loaded (cross-process sync)
    if _current_project_id is not None and (now - _current_project_loaded_at) < _CURRENT_PROJECT_CACHE_TTL:
        return

    _current_project_loaded_at = now

    try:
        from pathlib import Path
        from projects.project_manager import ProjectManager
        pm = ProjectManager()
        pid = pm._read_current()
        if pid and pid.strip():
            meta = pm._read_metadata(pid)
            if meta:
                _current_project_id = meta.get("project_id", pid)
                _current_project_name = meta.get("name", pid)
                return
        # No current project
        _current_project_id = None
        _current_project_name = None
    except Exception:
        pass


def get_current_project_id() -> str | None:
    """Return the active project_id, or None if no project is set.

    Falls back to reading ``.current_project`` from disk when the
    in-memory global hasn't been set (cross-process: CLI → gateway).
    """
    _lazy_load_current_project()
    return _current_project_id


def get_current_project_name() -> str | None:
    """Return the active project name, or None if no project is set.

    Falls back to reading ``.current_project`` from disk when the
    in-memory global hasn't been set (cross-process: CLI → gateway).
    """
    _lazy_load_current_project()
    return _current_project_name


# ═══════════════════════════════════════════════════════════════
# ProjectContextMiddleware — singleton that wraps ProjectManager
# ═══════════════════════════════════════════════════════════════

class ProjectContextMiddleware:
    """Singleton middleware that synchronises ProjectManager with the
    running agent process.

    Call ``ProjectContextMiddleware.get_instance()`` to obtain the
    singleton.  On first access it restores the active project from
    ``~/.hermes/projects/.current_project``.
    """

    _instance: ProjectContextMiddleware | None = None

    @classmethod
    def get_instance(cls) -> ProjectContextMiddleware:
        """Return (and lazily create) the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        # Guard against direct instantiation bypassing get_instance()
        if ProjectContextMiddleware._instance is not None:
            return
        ProjectContextMiddleware._instance = self

        from projects.project_manager import ProjectManager

        self._pm = ProjectManager()
        self._home = self._pm._projects_dir.parent  # Hermes home for state manager

        # Restore active project from disk
        global _current_project_id, _current_project_name
        current = self._pm.get_current_project()
        if current:
            _current_project_id = current["project_id"]
            _current_project_name = current["name"]
            logger.info(
                f"Project context restored: {_current_project_id} "
                f"({_current_project_name})"
            )
        else:
            logger.debug("No active project on startup")

    # ── Public API ────────────────────────────────────────────

    @property
    def active_project_id(self) -> str | None:
        return _current_project_id

    @property
    def active_project_name(self) -> str | None:
        return _current_project_name

    @property
    def manager(self):
        """Return the underlying ProjectManager."""
        return self._pm

    def get_project_block(self) -> str:
        """Build the ``[ACTIVE PROJECT]`` block for injection into
        the system prompt.

        Returns an empty string when no project is active — this keeps
        the prompt byte-stable for users who don't use projects.
        """
        if not _current_project_id:
            return ""

        proj = self._pm.get_project(_current_project_id)
        if not proj:
            return ""

        lines = [
            "[ACTIVE PROJECT]",
            f"You are working in project: {proj['name']}",
            f"  Project ID: {proj['project_id']}",
            f"  Subtree session: {proj['subtree_session_id']}",
            f"  ChromaDB collection: {proj['chroma_collection']}",
            f"  Project directory: {proj['project_dir']}",
            "",
            "Project isolation rules:",
            "- All session and memory operations must be scoped to this project.",
            "- Use the subtree_session_id for all conversation contexts.",
            f"- Tag all DuckDB records with project_id = \"{proj['project_id']}\".",
            "- The project directory is the primary workspace for file operations.",
            "[/ACTIVE PROJECT]",
        ]
        return "\n".join(lines)

    def get_project_block_compact(self) -> str:
        """Return a compact one-line project hint for sub-agents."""
        if not _current_project_id:
            return ""

        proj = self._pm.get_project(_current_project_id)
        if not proj:
            return ""

        return (
            f"[ACTIVE PROJECT: {proj['name']} | "
            f"ID: {proj['project_id']} | "
            f"Dir: {proj['project_dir']} | "
            f"Subtree: {proj['subtree_session_id']}]"
        )

    def switch_project(self, project_id: str) -> dict[str, Any]:
        """Switch to another project with full state lifecycle.

        1. Pause active workflows in current project
        2. Save state snapshot to disk
        3. Activate new project
        4. Restore saved state and return notifications

        Returns the newly activated project's metadata, with an
        additional ``_notifications`` key containing user-facing
        messages about paused workflows and unfinished tasks.
        """
        global _current_project_id, _current_project_name

        notifications: list[str] = []

        # ── Phase 1: Pause + save previous project ───────────
        if _current_project_id and _current_project_id != project_id:
            try:
                from projects.project_state import pause_project_and_summarize
                pause_msg = pause_project_and_summarize(
                    _current_project_id, hermes_home=self._home,
                )
                if pause_msg:
                    notifications.append(f"Previous project: {pause_msg}")
            except Exception:
                pass

            self._save_project_state(_current_project_id)

        # ── Phase 2: Activate new project ────────────────────
        proj = self._pm.switch_project(project_id)
        _current_project_id = proj["project_id"]
        _current_project_name = proj["name"]

        # ── Hot-switch: notify conversation loop ─────────────
        notify_project_switched(get_response_prefix())

        # ── Phase 3: Restore new project's state ─────────────
        try:
            from projects.project_state import restore_project_and_notify
            restore_msgs = restore_project_and_notify(
                project_id, hermes_home=self._home,
            )
            if restore_msgs:
                notifications.append(
                    f"Restored '{proj['name']}': {'; '.join(restore_msgs)}"
                )
        except Exception:
            pass

        logger.info(
            f"Project switched: {proj['project_id']} "
            f"({proj['name']})"
        )

        result = dict(proj)
        if notifications:
            result["_notifications"] = notifications
        return result

    def create_project(self, name: str) -> dict[str, Any]:
        """Create a new project and auto-switch to it.

        Returns the newly created project's metadata.
        """
        global _current_project_id, _current_project_name

        proj = self._pm.create_project(name)
        _current_project_id = proj["project_id"]
        _current_project_name = proj["name"]

        # ── Hot-switch: notify conversation loop ─────────────
        notify_project_switched(get_response_prefix())

        return dict(proj)

    def list_projects(self) -> list[dict[str, Any]]:
        """Return all projects, with the active one marked and saved state.

        Each project dict includes:
        - ``_is_active``: True if this is the current project
        - ``_paused_workflows``: count of paused workflows (from saved state)
        - ``_unfinished_tasks``: count of unfinished tasks (from saved state)
        """
        projects = self._pm.list_projects()
        try:
            from projects.project_state import ProjectStateManager
            psm = ProjectStateManager()
        except Exception:
            psm = None

        for p in projects:
            p["_is_active"] = (p["project_id"] == _current_project_id)
            if psm:
                state = psm.get_project_state_summary(p["project_id"])
                if state:
                    p["_paused_workflows"] = state.get("paused_workflow_count", 0)
                    p["_unfinished_tasks"] = state.get("unfinished_tasks", 0)
                else:
                    p["_paused_workflows"] = 0
                    p["_unfinished_tasks"] = 0
        return projects

    def get_current_project(self) -> dict[str, Any] | None:
        """Return the active project metadata, or None."""
        if not _current_project_id:
            return None
        proj = self._pm.get_project(_current_project_id)
        if proj:
            proj["_is_active"] = True
        return proj

    def rename_project(self, project_id: str, new_name: str) -> dict[str, Any]:
        """Rename a project (display name only, slug unchanged).

        If the renamed project is the active one, updates
        ``_current_project_name``.
        """
        global _current_project_name

        proj = self._pm.rename_project(project_id, new_name)
        if project_id == _current_project_id:
            _current_project_name = new_name
        return dict(proj)

    def delete_project_with_confirmation(
        self, project_id: str, *, force: bool = False,
    ) -> dict[str, Any]:
        """Delete a project.

        Parameters
        ----------
        project_id : str
            The project to delete.
        force : bool
            If False and the project is active, raises ValueError.
            If True, clears the active project and proceeds.

        Returns
        -------
        dict
            ``{"deleted": True, "project_id": ..., "was_active": bool}``

        Raises
        ------
        ValueError
            If project is active and ``force`` is False.
        """
        global _current_project_id, _current_project_name

        proj = self._pm.get_project(project_id)
        if not proj:
            raise ValueError(f"Project '{project_id}' not found.")

        was_active = (project_id == _current_project_id)
        if was_active and not force:
            raise ValueError(
                f"Cannot delete active project '{project_id}'. "
                f"Switch to another project first, or use --force."
            )

        self._pm.delete_project(project_id)
        if was_active:
            _current_project_id = None
            _current_project_name = None

        return {"deleted": True, "project_id": project_id, "was_active": was_active}

    # ── Internal ─────────────────────────────────────────────

    def _save_project_state(self, project_id: str) -> None:
        """Save current conversation state to long-term memory.

        This is a best-effort operation — failures are logged but
        never raised.  The project switch proceeds regardless.
        """
        try:
            from agent_registry import get_registry

            registry = get_registry()
            if (
                registry is not None
                and registry._longterm_memory is not None
                and registry._longterm_memory.enabled
            ):
                registry.summarize_to_longterm("orchestrator")
                logger.debug(
                    f"Saved orchestrator LTM snapshot for project {project_id}"
                )
        except Exception:
            # Best-effort — don't block the switch
            logger.debug(
                f"LTM snapshot skipped for project {project_id} "
                f"(registry or ChromaDB unavailable)"
            )


# ═══════════════════════════════════════════════════════════════
# Module-level convenience functions (used by prompt builder,
# CLI handlers, and tools).
# ═══════════════════════════════════════════════════════════════

def get_project_context() -> ProjectContextMiddleware:
    """Return the singleton middleware instance."""
    return ProjectContextMiddleware.get_instance()


def get_project_block() -> str:
    """Return the full ``[ACTIVE PROJECT]`` block for the system prompt.

    Returns an empty string when no project is active — safe to
    concatenate unconditionally.
    """
    return ProjectContextMiddleware.get_instance().get_project_block()


def get_project_block_compact() -> str:
    """Return a compact one-line project hint (for sub-agent prompts)."""
    return ProjectContextMiddleware.get_instance().get_project_block_compact()


def switch_project(project_id: str) -> dict[str, Any]:
    """Switch to another project (saves previous state first)."""
    return ProjectContextMiddleware.get_instance().switch_project(project_id)


def create_project(name: str) -> dict[str, Any]:
    """Create a new project and auto-switch to it."""
    return ProjectContextMiddleware.get_instance().create_project(name)


def list_projects() -> list[dict[str, Any]]:
    """Return all projects with ``_is_active`` marker."""
    return ProjectContextMiddleware.get_instance().list_projects()


def get_current_project() -> dict[str, Any] | None:
    """Return the active project metadata, or None."""
    return ProjectContextMiddleware.get_instance().get_current_project()


def get_response_prefix() -> str:
    """Return the context prefix, respecting per-project + global config.

    Compact format: ``📁 ProjectName`` or ``⚕ Orchestrator``

    Respects:
    - Per-project ``metadata.json`` → ``config.show_project_prefix``
    - Per-project ``metadata.json`` → ``config.prefix_emoji``
    - Global env: ``HERMES_NO_PROJECT_PREFIX=1`` disables all

    Returns ``"📁 ProjectName"``, ``"⚕ Orchestrator"``, or ``""``.
    """
    import os
    if os.environ.get("HERMES_NO_PROJECT_PREFIX", "").strip() in ("1", "true", "yes"):
        return ""

    if not _current_project_id or not _current_project_name:
        return "⚕ Orchestrator"

    try:
        from projects.project_manager import ProjectManager
        pm = ProjectManager()
        if not pm.get_show_project_prefix(_current_project_id):
            return ""
        use_emoji = pm.get_config(_current_project_id, "prefix_emoji", True)
        emoji = "📁 " if use_emoji else ""
    except Exception:
        emoji = "📁 "

    return f"{emoji}{_current_project_name}"


# ── Compression-safe project state (META message) ────────────

def build_project_state_meta() -> dict | None:
    """Build a ``[PROJECT STATE]`` meta message that survives compression.

    This message is injected after the system prompt but BEFORE conversation
    history.  It is NEVER part of ``conversation_history``, so it cannot be
    summarised or dropped by the context compressor.

    Returns ``None`` when no project is active (keeps the prompt byte-stable
    for non-project users).
    """
    if not _current_project_id or not _current_project_name:
        return None

    try:
        from projects.project_manager import ProjectManager
        pm = ProjectManager()
        proj = pm.get_project(_current_project_id)
    except Exception:
        proj = None

    subtree = (proj or {}).get("subtree_session_id", f"project-{_current_project_id}")
    created = (proj or {}).get("created_at", "?")[:19] if proj else "?"

    content = (
        f"[PROJECT STATE]\n"
        f"Active project: {_current_project_name}\n"
        f"Project ID:     {_current_project_id}\n"
        f"Subtree:        {subtree}\n"
        f"Created:        {created}\n"
        f"\n"
        f"All agents in this session are bound to this project.\n"
        f"Never switch projects in the middle of a session — use /project.\n"
        f"Do NOT remove or summarise this block during compression."
    )

    return {
        "role": "user",
        "content": content,
        "_meta": True,
        "_compression_protected": True,
    }


def get_meta_preamble() -> list[dict]:
    """Return meta messages to inject after the system prompt.

    These messages are NEVER compressed — they live outside conversation
    history and are rebuilt fresh on every turn.
    """
    messages: list[dict] = []

    # Project state (when active)
    project_meta = build_project_state_meta()
    if project_meta:
        messages.append(project_meta)

    return messages
