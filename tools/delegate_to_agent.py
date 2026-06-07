"""
Hermes Tool: delegate_to_agent

Lets the main agent delegate tasks to sub-agents.
Each sub-agent has isolated memory and its own context window.

Registered as a tool — the main agent sees it in its tool list
and can call it like any other tool.
"""

import json
import logging
import asyncio

from tools.registry import registry

logger = logging.getLogger(__name__)

# ── Auto-register ────────────────────────────────────────────


def _delegate_to_agent_handler(args: dict, **kw) -> str:
    """Handler for delegate_to_agent tool call.

    Expects:
        agent_id: str — which sub-agent to call
        message: str — the task/message
        session_id: str (optional) — for context tracking

    Returns JSON with the sub-agent's response.
    """
    agent_id = args.get("agent_id", "")
    message = args.get("message", "")

    if not agent_id or not message:
        return json.dumps({
            "error": "agent_id and message are required",
            "available_agents": _list_available(),
        })

    from subagent_manager import get_subagent_manager

    manager = get_subagent_manager()

    if agent_id not in manager._agents:
        return json.dumps({
            "error": f"Unknown agent: {agent_id}",
            "available_agents": _list_available(),
        })

    try:
        # Run async delegation synchronously (tool handlers are sync)
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # In async context — create task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run, manager.delegate(agent_id, message)
                )
                response = future.result(timeout=120)
        else:
            response = asyncio.run(manager.delegate(agent_id, message))

        return json.dumps({
            "agent_id": agent_id,
            "response": response,
        })
    except Exception as e:
        logger.error(f"delegate_to_agent failed: {e}")
        return json.dumps({
            "error": str(e),
            "agent_id": agent_id,
        })


def _list_available() -> list[str]:
    """List available sub-agent IDs for error messages."""
    try:
        from subagent_manager import get_subagent_manager
        manager = get_subagent_manager()
        return list(manager._agents.keys())
    except Exception:
        return []


# Register the tool
registry.register(
    name="delegate_to_agent",
    toolset="delegation",
    schema={
        "name": "delegate_to_agent",
        "description": (
            "Delegate a task to a specialized sub-agent. Each sub-agent has "
            "its own memory, context window, and system prompt. Use this when "
            "you need to offload work to a specialist — e.g., delegate coding "
            "to 'coder', research to 'researcher', code review to 'reviewer'.\n\n"
            "Available agents: check the system prompt or call with an invalid "
            "agent_id to see available options.\n\n"
            "The sub-agent remembers its own conversation history — multiple "
            "calls to the same agent_id share context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": (
                        "The sub-agent ID to delegate to. Available: coder (writes code), "
                        "researcher (searches/analyzes), reviewer (code review/QA)."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": "The task or message to send to the sub-agent.",
                },
            },
            "required": ["agent_id", "message"],
        },
    },
    handler=_delegate_to_agent_handler,
)
