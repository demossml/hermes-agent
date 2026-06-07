"""
Hermes Tool: create_subagent

Lets the main agent dynamically create new sub-agents during conversation.
The main agent asks the user for: agent_id, model, tokens, role.
Config is persisted to YAML and the agent is immediately usable.

Registered as a tool — the main agent calls it like any other tool.
"""

import json
import logging

from tools.registry import registry

logger = logging.getLogger(__name__)

# ── Available models for sub-agents ──────────────────────────
AVAILABLE_MODELS = [
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
]


def _create_subagent_handler(args: dict, **kw) -> str:
    """Handler for create_subagent tool.

    Expects:
        agent_id: str — short name for the sub-agent (e.g., "translator")
        model: str — model to use (e.g., "claude-sonnet-4-20250514")
        system_prompt: str — what the agent does, its role, instructions
        max_context_tokens: int (optional, default 8000) — context window size
        description: str (optional) — one-line description for the agent list

    If parameters are missing (empty string), returns what needs to be asked.
    The main agent should then ask the user and retry with complete data.

    Returns JSON with status or missing fields.
    """
    agent_id = (args.get("agent_id") or "").strip()
    model = (args.get("model") or "").strip()
    system_prompt = (args.get("system_prompt") or "").strip()
    max_tokens_raw = args.get("max_context_tokens", 8000)
    description = (args.get("description") or "").strip()

    # If called with empty params, guide the main agent on what to ask
    missing = []
    if not agent_id:
        missing.append("agent_id (short name, e.g. 'translator', 'analyst')")
    if not model:
        missing.append(f"model (one of: {', '.join(AVAILABLE_MODELS)})")
    if not system_prompt:
        missing.append(
            "system_prompt (what the agent does — its role, capabilities, "
            "behavior rules, language preference)"
        )

    if missing:
        return json.dumps({
            "status": "need_info",
            "missing_fields": missing,
            "available_models": AVAILABLE_MODELS,
            "message": (
                "To create a sub-agent, I need you to specify:\n"
                + "\n".join(f"  • {m}" for m in missing)
                + "\n\nCall create_subagent again with all fields filled."
            ),
        })

    # Validate model
    if model not in AVAILABLE_MODELS:
        return json.dumps({
            "status": "invalid_model",
            "provided": model,
            "available_models": AVAILABLE_MODELS,
            "message": f"Model '{model}' is not in the supported list. Choose from: {', '.join(AVAILABLE_MODELS)}",
        })

    # Parse token count
    try:
        max_context_tokens = int(max_tokens_raw)
    except (ValueError, TypeError):
        max_context_tokens = 8000

    if max_context_tokens < 1000:
        return json.dumps({
            "status": "error",
            "message": "max_context_tokens must be at least 1000",
        })
    if max_context_tokens > 200000:
        return json.dumps({
            "status": "error",
            "message": "max_context_tokens must be at most 200000 (Claude limit)",
        })

    # Create the agent
    from subagent_manager import get_subagent_manager

    manager = get_subagent_manager()

    result = manager.create_agent(
        agent_id=agent_id,
        model=model,
        system_prompt=system_prompt,
        max_context_tokens=max_context_tokens,
        description=description,
    )

    return json.dumps(result)


# Register the tool
registry.register(
    name="create_subagent",
    toolset="delegation",
    schema={
        "name": "create_subagent",
        "description": (
            "Create a new sub-agent dynamically. Use this when the user asks "
            "you to make a new assistant with specific capabilities. You need "
            "to ask the user for: a name for the agent, which model to use, "
            "what it should do (system prompt), and how many tokens for its "
            "context window.\n\n"
            "If you call this with empty fields, it will tell you exactly "
            "what to ask the user. Then call again with all fields filled.\n\n"
            "After creation, the agent is immediately available via "
            "delegate_to_agent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": (
                        "Short name for the sub-agent. Lowercase, hyphens ok. "
                        "Examples: 'translator', 'analyst', 'debugger', 'writer'."
                    ),
                },
                "model": {
                    "type": "string",
                    "description": (
                        "Model for the sub-agent. One of: "
                        "claude-sonnet-4-20250514, claude-opus-4-20250514, "
                        "claude-3-5-sonnet-20241022, claude-3-5-haiku-20241022."
                    ),
                },
                "system_prompt": {
                    "type": "string",
                    "description": (
                        "The sub-agent's system prompt — what it does, its role, "
                        "capabilities, behavior rules. Be specific about its "
                        "purpose, tone, and constraints."
                    ),
                },
                "max_context_tokens": {
                    "type": "integer",
                    "description": (
                        "Maximum tokens for the sub-agent's context window. "
                        "Larger = more memory but slower/costlier. "
                        "Default 8000, range 1000–200000."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Optional one-line description shown in the agent list."
                    ),
                },
            },
            "required": ["agent_id", "model", "system_prompt"],
        },
    },
    handler=_create_subagent_handler,
)
