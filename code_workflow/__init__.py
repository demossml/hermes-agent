"""
WorkflowState — finite state machine for code generation workflow.

States: IDLE → WRITING → REVIEWING → FIXING → PASSED | FAILED | STOPPED
"""

from __future__ import annotations

from enum import Enum


class WorkflowState(Enum):
    IDLE = "idle"
    WRITING = "writing"
    REVIEWING = "reviewing"
    FIXING = "fixing"
    PASSED = "passed"
    FAILED = "failed"
    STOPPED = "stopped"
