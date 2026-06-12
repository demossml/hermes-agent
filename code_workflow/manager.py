"""
Code Workflow Manager — orchestrates the coder→tester→fix pipeline.

Usage::

    from code_workflow import CodeWorkflowManager

    manager = CodeWorkflowManager(registry, task="write a sort function")
    result = await manager.run()
    # → {"status": "passed", "code": "...", "review": "...", "iterations": 2}
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from code_workflow.agents import CoderAgent, TesterAgent, PromptEngineer
from code_workflow.state import WorkflowState

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 5


@dataclass
class CodeWorkflowManager:
    """Orchestrates a code generation task through coder and tester agents.

    Parameters
    ----------
    registry : AgentRegistry
        Agent registry for creating and calling sub-agents.
    task : str
        The user's code task description.
    language : str or None
        Optional language hint (python, javascript, etc.).
    session_id : str
        Session identifier for result tracking.
    max_iterations : int
        Maximum review→fix cycles (default 5).
    """

    registry: Any
    task: str
    language: str | None = None
    session_id: str = ""
    max_iterations: int = MAX_ITERATIONS

    # ── Internal state ─────────────────────────────────────
    state: WorkflowState = WorkflowState.IDLE
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    prompt_engineer: PromptEngineer | None = None
    coder: CoderAgent | None = None
    tester: TesterAgent | None = None
    coder_session_id: str = ""
    tester_session_id: str = ""
    current_code: str = ""
    current_review: str = ""
    iteration: int = 0
    consecutive_passes: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        pe_id = f"prompt-eng-{self.task_id}"
        coder_id = f"coder-{self.task_id}"
        tester_id = f"tester-{self.task_id}"
        self.prompt_engineer = PromptEngineer(pe_id, self.registry, self.task_id)
        self.coder = CoderAgent(coder_id, self.registry, self.task_id, self.language)
        self.tester = TesterAgent(tester_id, self.registry, self.task_id)

    # ── Public API ──────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        """Execute the full code generation pipeline.

        Returns::

            {
                "status": "passed" | "failed",
                "code": str,
                "review": str,
                "iterations": int,
                "coder_id": str,
                "tester_id": str,
                "history": [...],
                "stop_reason": str,
                "display": str,  # human-readable summary
            }
        """
        self._ensure_agents()

        # ── Phase 0: Prompt engineering ─────────────────────
        self.state = WorkflowState.PROMPTING
        self._notify("prompting")
        engineered_prompt = await self.prompt_engineer.engineer_prompt(
            self.task, self.language,
        )
        logger.debug(
            "PromptEngineer output (%d chars): %s…",
            len(engineered_prompt), engineered_prompt[:120],
        )
        self._record("prompting", code=engineered_prompt[:500])

        # ── Phase 1: Initial code generation ────────────────
        self.state = WorkflowState.WRITING
        self._notify("writing")
        self.current_code = await self.coder.write_code(engineered_prompt, self.session_id)
        self._record("write", code=self.current_code)

        # ── Phase 2+: Review → Fix loop ─────────────────────
        stop_reason = ""
        self.consecutive_passes = 0

        for self.iteration in range(1, self.max_iterations + 1):
            # Review
            self.state = WorkflowState.REVIEWING
            self._notify("reviewing")
            self.current_review = await self.tester.review_code(
                self.current_code, self.session_id,
            )
            self._record(f"review-{self.iteration}", review=self.current_review)

            # Assess
            passing = self._is_passing(self.current_review)
            confidence = self._assess_confidence(self.current_review)

            # Track consecutive passes
            self.consecutive_passes = (
                self.consecutive_passes + 1 if passing else 0
            )

            # ── Stop criteria ────────────────────────────────

            # 2 consecutive confident passes → done
            if self.consecutive_passes >= 2 and confidence >= 0.8:
                stop_reason = (
                    f"2 consecutive confident passes "
                    f"(confidence={confidence:.0%})"
                )
                break

            # Very high confidence single pass at iter ≥ 2
            if passing and confidence >= 0.95 and self.iteration >= 2:
                stop_reason = (
                    f"very high confidence pass "
                    f"(confidence={confidence:.0%})"
                )
                break

            # No improvement after 3 iterations
            if not passing and self.iteration >= 3 and self._no_progress():
                self.state = WorkflowState.FAILED
                stop_reason = "no improvement after 3 iterations"
                return self._result("failed", stop_reason)

            # Max iterations reached
            if self.iteration >= self.max_iterations:
                self.state = WorkflowState.FAILED
                stop_reason = f"max iterations ({self.max_iterations}) reached"
                return self._result("failed", stop_reason)

            # ── Fix ──────────────────────────────────────────
            self.state = WorkflowState.FIXING
            self._notify("fixing")
            self.current_code = await self.coder.fix_code(
                self.current_code, self.current_review,
                self.task, self.session_id,
            )
            self._record(f"fix-{self.iteration}", code=self.current_code)

        self.state = WorkflowState.PASSED
        return self._result("passed", stop_reason)

    # ── Helpers ─────────────────────────────────────────────

    def _ensure_agents(self) -> None:
        self.prompt_engineer.ensure_created()
        self.coder.ensure_created()
        self.tester.ensure_created()

        # Store validated session IDs
        coder_cfg = self.registry._agents.get(self.coder.agent_id, {})
        tester_cfg = self.registry._agents.get(self.tester.agent_id, {})
        self.coder_session_id = coder_cfg.get("subtree_session_id", "")
        self.tester_session_id = tester_cfg.get("subtree_session_id", "")

        # ── Verify isolation ─────────────────────────────
        self._verify_isolation()

    def _verify_isolation(self) -> None:
        """Verify coder and tester have different subtree_session_ids.

        Logs a warning if isolation is compromised (shared subtrees).
        Does not raise — the workflow continues but the fact is logged.
        """
        if not self.coder_session_id or not self.tester_session_id:
            logger.debug(
                "Isolation: session IDs not yet assigned "
                "(coder=%s, tester=%s)",
                bool(self.coder_session_id), bool(self.tester_session_id),
            )
            return

        if self.coder_session_id == self.tester_session_id:
            logger.warning(
                "⚠ ISOLATION BROKEN: coder and tester share subtree "
                "'%s'. They can read each other's memory.",
                self.coder_session_id,
            )
        else:
            logger.debug(
                "Isolation OK: coder=%s… tester=%s…",
                self.coder_session_id[:24], self.tester_session_id[:24],
            )

        # Verify tester has zero tools
        tester_cfg = self.registry._agents.get(self.tester.agent_id, {})
        tester_tools = tester_cfg.get("enabled_toolsets", None)
        if tester_tools is not None and tester_tools != []:
            logger.warning(
                "⚠ Tester has tools configured: %s. "
                "It may be able to read coder memory.",
                tester_tools,
            )

    def _record(self, phase: str, code: str = "", review: str = "") -> None:
        self.history.append({
            "phase": phase,
            "code": code[:500],
            "review": review[:500],
        })

    @staticmethod
    def _notify(phase: str) -> None:
        """Log a human-friendly progress message."""
        msgs = {
            "prompting": "🎯 Prompt Engineer optimising the request…",
            "writing":   "✍️  Coder writing code…",
            "reviewing": "🔍 Tester reviewing code (real execution + analysis)…",
            "fixing":    "🔧 Coder fixing issues found in review…",
        }
        if phase in msgs:
            logger.info(msgs[phase])

    def _result(self, status: str, stop_reason: str) -> dict[str, Any]:
        """Build the result dictionary."""
        return {
            "status": status,
            "code": self.current_code,
            "review": self.current_review,
            "iterations": self.iteration,
            "coder_id": self.coder.agent_id,
            "tester_id": self.tester.agent_id,
            "task_id": self.task_id,
            "history": self.history,
            "stop_reason": stop_reason,
            "display": self._format_display(status, stop_reason),
        }

    def _format_display(self, status: str, reason: str) -> str:
        """Build a human-readable result summary."""
        icon = "✅" if status == "passed" else "❌"
        iters = f"{self.iteration} iteration(s)"
        lines = [
            f"{icon} Code Workflow {'PASSED' if status == 'passed' else 'FAILED'} "
            f"after {iters}",
            f"   Stop reason: {reason}",
            f"   Agents: {self.prompt_engineer.agent_id} → "
            f"{self.coder.agent_id} → {self.tester.agent_id}",
            "",
            "## Code",
            self.current_code[:3000],
            "",
            "## Review",
            self.current_review[:2000],
        ]
        return "\n".join(lines)

    # ── Review assessment ───────────────────────────────────

    @staticmethod
    def _is_passing(review: str) -> bool:
        """Determine if the review indicates the code passes."""
        ru = review.upper()
        if "✅ PASS" in ru and "❌" not in ru:
            return True
        if "## REVIEW RESULT: ✅ PASS" in ru:
            return True
        if "❌ FAIL" in ru:
            return False
        pass_phrases = [
            "PASS", "ALL TESTS PASSED", "CODE LOOKS GOOD",
            "NO ISSUES FOUND", "LGTM", "SHIP IT",
        ]
        fail_phrases = [
            "FAIL", "ISSUES FOUND", "NEEDS WORK",
            "BUG", "ERROR", "VULNERABILITY",
        ]
        pc = sum(1 for p in pass_phrases if p in ru)
        fc = sum(1 for p in fail_phrases if p in ru)
        return pc > fc

    @staticmethod
    def _assess_confidence(review: str) -> float:
        """Estimate tester confidence from review text (0-1)."""
        ru = review.upper()
        confidence = 0.5

        exec_signals = [
            "PASSED", "FAILED", "EXIT CODE", "PYTEST",
            "STDOUT", "STDERR", "RAN ", "TEST_",
        ]
        confidence += min(0.3, sum(1 for s in exec_signals if s in ru) * 0.05)

        if "line" in review.lower() and any(c.isdigit() for c in review):
            confidence += 0.1

        hedging = ["might", "maybe", "could be", "possibly", "probably"]
        confidence -= min(0.2, sum(1 for h in hedging if h in review.lower()) * 0.05)

        if "PASS" not in ru and "FAIL" not in ru:
            confidence -= 0.15

        return max(0.0, min(1.0, confidence))

    def _no_progress(self) -> bool:
        """True if the last two reviews are substantially similar."""
        if len(self.history) < 3:
            return False
        a = self.history[-1].get("review", "")
        b = self.history[-3].get("review", "")
        if not a or not b:
            return False
        wa = set(a.lower().split())
        wb = set(b.lower().split())
        if not wa or not wb:
            return False
        return len(wa & wb) / max(len(wa), len(wb)) > 0.7
