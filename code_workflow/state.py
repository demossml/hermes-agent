"""Workflow state machine."""

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
