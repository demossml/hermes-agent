"""
Workflow Tracker — manages active CodeGenerationWorkflow instances.

Provides status display, stop/continue commands, and auto-detection
integration for the CLI and gateway.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from workflow_store import (
    save_workflow, update_workflow_status, save_workflow_result,
    save_iteration,
)

logger = logging.getLogger(__name__)

# ── Global tracker ────────────────────────────────────────────

_workflows: dict[str, "WorkflowTracker"] = {}
_active_workflow_id: str | None = None  # currently focused workflow


def set_active_workflow(task_id: str | None) -> None:
    """Set which workflow is currently focused for display."""
    global _active_workflow_id
    _active_workflow_id = task_id


def get_active_workflow_id() -> str | None:
    return _active_workflow_id


@dataclass
class WorkflowTracker:
    """Tracks a running CodeGenerationWorkflow for status display."""

    task_id: str
    task_description: str
    workflow: Any  # CodeGenerationWorkflow
    status: str = "starting"  # starting, writing, reviewing, fixing, passed, failed, stopped
    iteration: int = 0
    max_iterations: int = 4
    coder_id: str = ""
    tester_id: str = ""
    _stop_requested: bool = False
    _result: dict[str, Any] | None = None

    @property
    def display_status(self) -> str:
        """Human-readable status line."""
        icons = {
            "starting":   "🚀",
            "writing":    "✍️",
            "reviewing":  "🔍",
            "fixing":     "🔧",
            "passed":     "✅",
            "failed":     "❌",
            "stopped":    "⏹️",
        }
        icon = icons.get(self.status, "⏳")
        return (
            f"{icon} Code Workflow • {self.status.upper()} "
            f"• Iteration {self.iteration}/{self.max_iterations} "
            f"• {self.coder_id} → {self.tester_id}"
        )

    def request_stop(self) -> None:
        """Request workflow stop. The loop checks this flag."""
        self._stop_requested = True
        logger.info(f"Workflow {self.task_id}: stop requested")

    @property
    def stopped(self) -> bool:
        return self._stop_requested or self.status == "stopped"


def start_workflow(
    registry: Any,
    task: str,
    language: str | None = None,
) -> WorkflowTracker:
    """Start a code workflow and return a tracker for status display."""
    from code_workflow.manager import CodeWorkflowManager

    manager = CodeWorkflowManager(
        registry=registry, task=task, language=language,
    )
    tracker = WorkflowTracker(
        task_id=manager.task_id,
        task_description=task,
        workflow=manager,
        coder_id=manager.coder.agent_id,
        tester_id=manager.tester.agent_id,
        max_iterations=manager.max_iterations,
    )
    _workflows[manager.task_id] = tracker
    set_active_workflow(manager.task_id)  # auto-focus new workflow

    # Start in background
    asyncio.create_task(_run_workflow(tracker))
    return tracker


async def _run_workflow(tracker: WorkflowTracker) -> None:
    """Run the workflow via CodeWorkflowManager, polling for status."""
    manager = tracker.workflow

    try:
        # Start manager.run() in background
        task = asyncio.create_task(manager.run())

        # Poll for status updates
        last_status = ""
        while not task.done():
            await asyncio.sleep(2)
            status = manager.state.value
            if status != last_status:
                tracker.status = status
                tracker.iteration = manager.iteration or 1
                save_workflow(
                    tracker.task_id, tracker.task_description,
                    language=manager.language,
                    status=status, iteration=manager.iteration or 1,
                    coder_id=manager.coder.agent_id,
                    tester_id=manager.tester.agent_id,
                )
                last_status = status
            if tracker.stopped:
                task.cancel()
                tracker.status = "stopped"
                update_workflow_status(tracker.task_id, "stopped", manager.iteration)
                return

        # Task done — get result
        result = task.result()
        tracker._result = result
        tracker.status = result["status"]
        save_workflow_result(
            tracker.task_id, result["status"], result["iterations"],
            result["code"], result["review"],
            json.dumps(result, default=str),
        )

    except asyncio.CancelledError:
        tracker.status = "stopped"
    except Exception as e:
        logger.error("Workflow %s error: %s", tracker.task_id, e)
        tracker.status = "failed"


def get_workflow(task_id: str) -> WorkflowTracker | None:
    return _workflows.get(task_id)


def get_active_workflow() -> WorkflowTracker | None:
    """Return the most recently started active workflow."""
    for tid in reversed(list(_workflows.keys())):
        t = _workflows[tid]
        if t.status in ("starting", "writing", "reviewing", "fixing"):
            return t
    return None


def format_workflow_result(tracker: WorkflowTracker) -> str:
    """Format the final workflow result for display."""
    if not tracker._result:
        return f"[dim]Workflow {tracker.task_id}: {tracker.status}[/]"

    r = tracker._result
    status = r["status"]
    code = r.get("code", "")
    review = r.get("review", "")
    iters = r.get("iterations", 0)
    cid = r.get("coder_id", "")
    tid = r.get("tester_id", "")

    if status == "passed":
        header = f"[bold green]✅ Code Workflow PASSED[/] after {iters} iteration(s)"
    else:
        header = f"[bold red]❌ Code Workflow — max iterations reached[/] ({iters} attempts)"

    lines = [
        "",
        header,
        f"[dim]Agents: {cid} → {tid}[/]",
        "",
        "[bold]## Code[/]",
        code,
        "",
        "[bold]## Review[/]",
        review,
        "",
    ]
    return "\n".join(lines)


def get_all_workflows() -> list[WorkflowTracker]:
    """Return all tracked workflows (active and completed)."""
    return list(_workflows.values())


def get_active_workflows() -> list[WorkflowTracker]:
    """Return workflows that are still running."""
    return [
        t for t in _workflows.values()
        if t.status in ("starting", "writing", "reviewing", "fixing")
    ]
