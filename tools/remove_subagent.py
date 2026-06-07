"""
Hermes Tool: remove_subagent

Lets the main agent remove dynamically created sub-agents.
"""

import json
import logging

from tools.registry import registry

logger = logging.getLogger(__name__)


def _remove_subagent_handler(args: dict, **kw) -> str:
    agent_id = (args.get("agent_id") or "").strip()
    if not agent_id:
        return json.dumps({"error": "agent_id is required"})

    from subagent_manager import get_subagent_manager
    manager = get_subagent_manager()
    result = manager.remove_agent(agent_id)
    return json.dumps(result)


registry.register(
    name="remove_subagent",
    toolset="delegation",
    schema={
        "name": "remove_subagent",
        "description": (
            "Remove a dynamically created sub-agent. "
            "Only works on agents created via create_subagent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The sub-agent ID to remove.",
                },
            },
            "required": ["agent_id"],
        },
    },
    handler=_remove_subagent_handler,
)
