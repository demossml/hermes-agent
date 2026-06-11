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

from workflow_store import save_workflow, update_workflow_status, save_workflow_result

logger = logging.getLogger(__name__)

# ── Global tracker ────────────────────────────────────────────

_workflows: dict[str, "WorkflowTracker"] = {}


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
    from code_workflow import CodeGenerationWorkflow

    wf = CodeGenerationWorkflow(
        registry=registry, task=task, language=language,
    )
    tracker = WorkflowTracker(
        task_id=wf.task_id,
        task_description=task,
        workflow=wf,
        coder_id=wf.coder_id,
        tester_id=wf.tester_id,
        max_iterations=wf.max_iterations,
    )
    _workflows[wf.task_id] = tracker

    # Start in background
    asyncio.create_task(_run_workflow(tracker))
    return tracker


async def _run_workflow(tracker: WorkflowTracker) -> None:
    """Run the workflow, updating tracker status at each phase."""
    wf = tracker.workflow
    reg = wf.registry

    try:
        wf._ensure_agents()
        tracker.status = "writing"
        save_workflow(tracker.task_id, tracker.task_description,
                      language=wf.language, status="writing", iteration=1,
                      coder_id=wf.coder_id, tester_id=wf.tester_id)

        # Phase 1: Write
        tracker.iteration = 1
        tracker.status = "writing"
        if tracker.stopped:
            return
        wf.current_code = await wf._call_coder(wf.task)
        wf.history.append({"phase": "write", "code": wf.current_code[:500]})

        # Phase 2-4: Review + fix
        for wf.iteration in range(1, tracker.max_iterations + 1):
            if tracker.stopped:
                tracker.status = "stopped"
                update_workflow_status(tracker.task_id, "stopped", wf.iteration)
                return

            tracker.iteration = wf.iteration
            tracker.status = "reviewing"
            update_workflow_status(tracker.task_id, "reviewing", wf.iteration)
            wf.current_review = await wf._call_tester(wf.current_code)
            wf.history.append({
                "phase": f"review-{wf.iteration}",
                "review": wf.current_review[:500],
            })

            if wf._is_passing(wf.current_review):
                tracker.status = "passed"
                tracker._result = wf._build_result("passed")
                save_workflow_result(
                    tracker.task_id, "passed", wf.iteration,
                    wf.current_code, wf.current_review,
                    json.dumps(tracker._result, default=str),
                )
                logger.info(f"Workflow {tracker.task_id}: PASSED")
                return

            if wf.iteration < tracker.max_iterations:
                tracker.status = "fixing"
                update_workflow_status(tracker.task_id, "fixing", wf.iteration)
                wf.current_code = await wf._call_coder_fix(
                    wf.current_code, wf.current_review,
                )
                wf.history.append({
                    "phase": f"fix-{wf.iteration}",
                    "code": wf.current_code[:500],
                })
            else:
                tracker.status = "failed"
                tracker._result = wf._build_result("failed")
                save_workflow_result(
                    tracker.task_id, "failed", wf.iteration,
                    wf.current_code, wf.current_review,
                    json.dumps(tracker._result, default=str),
                )

    except asyncio.CancelledError:
        tracker.status = "stopped"
    except Exception as e:
        logger.error(f"Workflow {tracker.task_id} error: {e}")
        tracker.status = "failed"
        tracker._result = {"status": "failed", "error": str(e)}


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
