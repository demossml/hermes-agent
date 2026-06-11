"""
Profile orchestration tools — parent agent manages clone profiles.

Tools:
    profile_memory  — read/add/delete a clone's memory
    profile_tools   — view/enable/disable a clone's tools
    profile_config  — read/change a clone's LLM settings
    profile_sessions — read a clone's session history

Security:
    Each clone can set ``profile.allow_orchestration: false`` in its
    config.yaml to block parent access.  The parent agent ONLY accesses
    clones when the user explicitly requests it.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from hermes_constants import get_default_hermes_root
from tools.registry import registry

logger = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────


def _resolve_profile(profile_name: str) -> Path | None:
    """Return the profile's home directory, or None if not found."""
    root = get_default_hermes_root()
    candidates = [
        root / "profiles" / profile_name,        # named profile
        root if profile_name in ("default", "") else None,  # default profile
    ]
    for c in candidates:
        if c and c.is_dir():
            return c
    return None


def _check_orchestration_allowed(profile_home: Path) -> bool:
    """Check if the profile allows orchestration."""
    config_path = profile_home / "config.yaml"
    if not config_path.exists():
        return True  # no config = allowed

    try:
        import yaml
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
        profile_section = cfg.get("profile", {})
        return profile_section.get("allow_orchestration", True)
    except Exception:
        return True  # unreadable config = allowed (fail open)


def _read_profile_config(profile_home: Path) -> dict:
    """Read a profile's config.yaml."""
    config_path = profile_home / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"Failed to read config for {profile_home}: {e}")
        return {}


# ── Tool 1: profile_memory ────────────────────────────────────


def profile_memory(
    profile: str = "",
    action: str = "read",
    key: str = "",
    value: str = "",
    task_id: str = "",
) -> str:
    """Read, add, or delete a clone profile's memory.

    Args:
        profile: Profile name (e.g. "bot-1")
        action:  "read" (default), "add", or "delete"
        key:     Memory key for add/delete
        value:   Memory value for add

    Returns:
        JSON with memory entries or operation result.
    """
    if not profile:
        return json.dumps({"error": "profile name is required"})

    profile_home = _resolve_profile(profile)
    if not profile_home:
        return json.dumps({"error": f"Profile '{profile}' not found"})

    if not _check_orchestration_allowed(profile_home):
        return json.dumps({
            "error": f"Profile '{profile}' has orchestration disabled",
            "profile": profile,
        })

    memories_dir = profile_home / "memories"
    memories_dir.mkdir(parents=True, exist_ok=True)

    if action == "read":
        entries = {}
        for mem_file in sorted(memories_dir.glob("*.json")):
            try:
                with open(mem_file) as f:
                    data = json.load(f)
                entries[mem_file.stem] = data
            except Exception:
                entries[mem_file.stem] = "(unreadable)"

        return json.dumps({
            "profile": profile,
            "action": "read",
            "count": len(entries),
            "memories": entries,
        }, ensure_ascii=False, indent=2)

    elif action == "add":
        if not key:
            return json.dumps({"error": "key is required for 'add' action"})
        safe_key = "".join(c for c in key if c.isalnum() or c in "_-.")
        mem_path = memories_dir / f"{safe_key}.json"
        try:
            with open(mem_path, "w") as f:
                json.dump({"key": key, "value": value, "added_by": "orchestrator"}, f)
            return json.dumps({
                "profile": profile,
                "action": "add",
                "key": key,
                "status": "stored",
            })
        except Exception as e:
            return json.dumps({"error": f"Failed to write memory: {e}"})

    elif action == "delete":
        if not key:
            return json.dumps({"error": "key is required for 'delete' action"})
        safe_key = "".join(c for c in key if c.isalnum() or c in "_-.")
        mem_path = memories_dir / f"{safe_key}.json"
        if mem_path.exists():
            mem_path.unlink()
            return json.dumps({
                "profile": profile,
                "action": "delete",
                "key": key,
                "status": "deleted",
            })
        return json.dumps({
            "profile": profile,
            "action": "delete",
            "key": key,
            "status": "not_found",
        })

    else:
        return json.dumps({"error": f"Unknown action: {action}. Use read/add/delete."})


# ── Tool 2: profile_tools ─────────────────────────────────────


def profile_tools(
    profile: str = "",
    action: str = "view",
    toolset: str = "",
    enable: bool = True,
    task_id: str = "",
) -> str:
    """View, enable, or disable a clone profile's tools.

    Args:
        profile: Profile name
        action:  "view" (default), "enable", or "disable"
        toolset: Toolset name for enable/disable
        enable:  True to enable, False to disable

    Returns:
        JSON with current tool configuration.
    """
    if not profile:
        return json.dumps({"error": "profile name is required"})

    profile_home = _resolve_profile(profile)
    if not profile_home:
        return json.dumps({"error": f"Profile '{profile}' not found"})

    if not _check_orchestration_allowed(profile_home):
        return json.dumps({
            "error": f"Profile '{profile}' has orchestration disabled",
        })

    cfg = _read_profile_config(profile_home)

    if action == "view":
        platform_toolsets = cfg.get("platform_toolsets", {})
        return json.dumps({
            "profile": profile,
            "action": "view",
            "platform_toolsets": platform_toolsets,
        }, ensure_ascii=False, indent=2)

    elif action in ("enable", "disable"):
        if not toolset:
            return json.dumps({"error": "toolset name is required"})

        platform_toolsets = cfg.setdefault("platform_toolsets", {})
        cli_tools = platform_toolsets.setdefault("cli", {})

        if action == "enable":
            if "disabled_toolsets" in cli_tools and toolset in cli_tools["disabled_toolsets"]:
                cli_tools["disabled_toolsets"].remove(toolset)
            if "enabled_toolsets" not in cli_tools:
                cli_tools["enabled_toolsets"] = []
            if toolset not in cli_tools["enabled_toolsets"]:
                cli_tools["enabled_toolsets"].append(toolset)
        else:  # disable
            if "enabled_toolsets" in cli_tools and toolset in cli_tools["enabled_toolsets"]:
                cli_tools["enabled_toolsets"].remove(toolset)
            if "disabled_toolsets" not in cli_tools:
                cli_tools["disabled_toolsets"] = []
            if toolset not in cli_tools["disabled_toolsets"]:
                cli_tools["disabled_toolsets"].append(toolset)

        # Save
        try:
            import yaml
            config_path = profile_home / "config.yaml"
            with open(config_path, "w") as f:
                yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
            return json.dumps({
                "profile": profile,
                "action": action,
                "toolset": toolset,
                "status": "updated",
            })
        except Exception as e:
            return json.dumps({"error": f"Failed to save config: {e}"})

    else:
        return json.dumps({"error": f"Unknown action: {action}. Use view/enable/disable."})


# ── Tool 3: profile_config ────────────────────────────────────


def profile_config(
    profile: str = "",
    action: str = "read",
    key: str = "",
    value: str = "",
    task_id: str = "",
) -> str:
    """Read or change a clone profile's LLM configuration.

    Args:
        profile: Profile name
        action:  "read" (default) or "set"
        key:     Config key for set (e.g. "model.default", "model.provider")
        value:   New value for set

    Returns:
        JSON with current or updated config.
    """
    if not profile:
        return json.dumps({"error": "profile name is required"})

    profile_home = _resolve_profile(profile)
    if not profile_home:
        return json.dumps({"error": f"Profile '{profile}' not found"})

    if not _check_orchestration_allowed(profile_home):
        return json.dumps({
            "error": f"Profile '{profile}' has orchestration disabled",
        })

    cfg = _read_profile_config(profile_home)

    if action == "read":
        # Return LLM-related config only
        model_cfg = cfg.get("model", {})
        agent_cfg = cfg.get("agent", {})
        return json.dumps({
            "profile": profile,
            "action": "read",
            "model": model_cfg,
            "agent": agent_cfg,
        }, ensure_ascii=False, indent=2)

    elif action == "set":
        if not key:
            return json.dumps({"error": "key is required for 'set' action"})

        # Parse dotted key: "model.default" → cfg["model"]["default"]
        parts = key.split(".")
        target = cfg
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value

        try:
            import yaml
            config_path = profile_home / "config.yaml"
            with open(config_path, "w") as f:
                yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
            return json.dumps({
                "profile": profile,
                "action": "set",
                "key": key,
                "value": value,
                "status": "updated",
            })
        except Exception as e:
            return json.dumps({"error": f"Failed to save config: {e}"})

    else:
        return json.dumps({"error": f"Unknown action: {action}. Use read/set."})


# ── Tool 4: profile_sessions ──────────────────────────────────


def profile_sessions(
    profile: str = "",
    action: str = "list",
    limit: int = 10,
    task_id: str = "",
) -> str:
    """Read a clone profile's session history from state.db.

    Args:
        profile: Profile name
        action:  "list" (default) — list recent sessions
        limit:   Max sessions to return (default 10)

    Returns:
        JSON with session list.
    """
    if not profile:
        return json.dumps({"error": "profile name is required"})

    profile_home = _resolve_profile(profile)
    if not profile_home:
        return json.dumps({"error": f"Profile '{profile}' not found"})

    if not _check_orchestration_allowed(profile_home):
        return json.dumps({
            "error": f"Profile '{profile}' has orchestration disabled",
        })

    state_db = profile_home / "state.db"
    if not state_db.exists():
        return json.dumps({
            "profile": profile,
            "action": "list",
            "sessions": [],
            "note": "No session database found",
        })

    try:
        import sqlite3
        conn = sqlite3.connect(str(state_db))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT session_id, title, source, created_at, updated_at "
            "FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        sessions = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return json.dumps({
            "profile": profile,
            "action": "list",
            "count": len(sessions),
            "sessions": sessions,
        }, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": f"Failed to read sessions: {e}"})


# ── Register tools ────────────────────────────────────────────

registry.register(
    name="profile_memory",
    toolset="profile",
    schema={
        "name": "profile_memory",
        "description": (
            "Read, add, or delete a clone profile's persistent memory. "
            "Use only when the user explicitly asks to manage a clone's memory."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "string",
                    "description": "Profile name (e.g. 'bot-1')",
                },
                "action": {
                    "type": "string",
                    "enum": ["read", "add", "delete"],
                    "description": "Action: read (list all), add (store), delete (remove)",
                    "default": "read",
                },
                "key": {
                    "type": "string",
                    "description": "Memory key for add/delete actions",
                },
                "value": {
                    "type": "string",
                    "description": "Memory value for add action",
                },
            },
            "required": ["profile"],
        },
    },
    handler=lambda args, **kw: profile_memory(
        profile=args.get("profile", ""),
        action=args.get("action", "read"),
        key=args.get("key", ""),
        value=args.get("value", ""),
        task_id=kw.get("task_id", ""),
    ),
    requires_env=[],
)

registry.register(
    name="profile_tools",
    toolset="profile",
    schema={
        "name": "profile_tools",
        "description": (
            "View, enable, or disable a clone profile's toolsets. "
            "Use only when the user explicitly asks to manage clone tools."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "string",
                    "description": "Profile name (e.g. 'bot-1')",
                },
                "action": {
                    "type": "string",
                    "enum": ["view", "enable", "disable"],
                    "description": "Action: view, enable, or disable",
                    "default": "view",
                },
                "toolset": {
                    "type": "string",
                    "description": "Toolset name for enable/disable (e.g. 'delegation')",
                },
            },
            "required": ["profile"],
        },
    },
    handler=lambda args, **kw: profile_tools(
        profile=args.get("profile", ""),
        action=args.get("action", "view"),
        toolset=args.get("toolset", ""),
        enable=args.get("action", "view") == "enable",
        task_id=kw.get("task_id", ""),
    ),
    requires_env=[],
)

registry.register(
    name="profile_config",
    toolset="profile",
    schema={
        "name": "profile_config",
        "description": (
            "Read or change a clone profile's LLM configuration "
            "(model, provider, temperature, etc.). "
            "Use only when the user explicitly asks to manage clone config."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "string",
                    "description": "Profile name (e.g. 'bot-1')",
                },
                "action": {
                    "type": "string",
                    "enum": ["read", "set"],
                    "description": "Action: read (view config) or set (change a value)",
                    "default": "read",
                },
                "key": {
                    "type": "string",
                    "description": "Config key for set action (e.g. 'model.default')",
                },
                "value": {
                    "type": "string",
                    "description": "New value for set action",
                },
            },
            "required": ["profile"],
        },
    },
    handler=lambda args, **kw: profile_config(
        profile=args.get("profile", ""),
        action=args.get("action", "read"),
        key=args.get("key", ""),
        value=args.get("value", ""),
        task_id=kw.get("task_id", ""),
    ),
    requires_env=[],
)

registry.register(
    name="profile_sessions",
    toolset="profile",
    schema={
        "name": "profile_sessions",
        "description": (
            "Read a clone profile's session history. "
            "Use only when the user explicitly asks about clone sessions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "string",
                    "description": "Profile name (e.g. 'bot-1')",
                },
                "action": {
                    "type": "string",
                    "enum": ["list"],
                    "description": "Action: list recent sessions",
                    "default": "list",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max sessions to return (default 10)",
                    "default": 10,
                },
            },
            "required": ["profile"],
        },
    },
    handler=lambda args, **kw: profile_sessions(
        profile=args.get("profile", ""),
        action=args.get("action", "list"),
        limit=args.get("limit", 10),
        task_id=kw.get("task_id", ""),
    ),
    requires_env=[],
)
