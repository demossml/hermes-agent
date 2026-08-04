"""
Mode Router — user-facing dual-mode system for Hermes Agent.

Two modes:
  dev       — project work: terminal, file tools, delegation, code workflow
  secretary — chat & archive ops: archive_query, config, cron, reports

Public API:
  from modes import (get_mode, set_mode, detect_mode_intent,
                     get_effective_mode, apply_mode_change,
                     format_status_reply, format_already_reply,
                     get_guidance, filter_tools, filter_individual_tools)
"""

from modes.state import get_mode, set_mode, get_mode_record, get_default_mode
from modes.detect import detect_mode_intent, ModeIntent
from modes.router import (
    get_effective_mode,
    apply_mode_change,
    format_status_reply,
    format_already_reply,
)
from modes.prompts import DEV_GUIDANCE, SECRETARY_GUIDANCE
from modes.policy import (
    get_guidance,
    filter_tools,
    filter_individual_tools,
    MODE_TOOLSETS,
)

__all__ = [
    "get_mode",
    "set_mode",
    "get_mode_record",
    "get_default_mode",
    "detect_mode_intent",
    "ModeIntent",
    "get_effective_mode",
    "apply_mode_change",
    "format_status_reply",
    "format_already_reply",
    "DEV_GUIDANCE",
    "SECRETARY_GUIDANCE",
    "get_guidance",
    "filter_tools",
    "filter_individual_tools",
    "MODE_TOOLSETS",
]
