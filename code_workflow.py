"""
Code Generation Workflow — state-machine-driven coder+tester pipeline.

Usage::

    from code_workflow import CodeGenerationWorkflow

    workflow = CodeGenerationWorkflow(registry, task="write a sort function")
    result = await workflow.run()
    # → {"status": "passed", "code": "...", "review": "...", "iterations": 2}
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 4


class WorkflowState(Enum):
    IDLE = "idle"
    WRITING = "writing"
    REVIEWING = "reviewing"
    FIXING = "fixing"
    PASSED = "passed"
    FAILED = "failed"


@dataclass
class CodeGenerationWorkflow:
    """State machine for code generation: coder → tester → fix loop.

    Parameters
    ----------
    registry : AgentRegistry
        The agent registry for creating and calling agents.
    task : str
        The user's code task description.
    language : str or None
        Optional language hint (python, javascript, etc.).
    session_id : str
        Session identifier for result tracking.
    max_iterations : int
        Maximum fix iterations (default 4).
    """

    registry: Any
    task: str
    language: str | None = None
    session_id: str = ""
    max_iterations: int = MAX_ITERATIONS

    # ── Internal state ─────────────────────────────────────
    state: WorkflowState = WorkflowState.IDLE
    coder_id: str = ""
    tester_id: str = ""
    task_id: str = ""
    current_code: str = ""
    current_review: str = ""
    iteration: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        self.task_id = uuid.uuid4().hex[:8]
        self.coder_id = f"coder-{self.task_id}"
        self.tester_id = f"tester-{self.task_id}"

    # ── Public API ──────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        """Execute the full code generation workflow.

        Returns::

            {
                "status": "passed" | "failed",
                "code": str,
                "review": str,
                "iterations": int,
                "coder_id": str,
                "tester_id": str,
                "history": [...],
            }
        """
        self._ensure_agents()
        self.state = WorkflowState.WRITING

        # Phase 1: Initial code generation
        self.current_code = await self._call_coder(self.task)
        self.history.append({"phase": "write", "code": self.current_code[:500]})

        # Phase 2-4: Review + fix loop
        for self.iteration in range(1, self.max_iterations + 1):
            self.state = WorkflowState.REVIEWING
            self.current_review = await self._call_tester(self.current_code)
            self.history.append({
                "phase": f"review-{self.iteration}",
                "review": self.current_review[:500],
            })

            if self._is_passing(self.current_review):
                self.state = WorkflowState.PASSED
                logger.info(
                    f"CodeGenerationWorkflow {self.task_id}: "
                    f"PASSED after {self.iteration} iteration(s)"
                )
                return self._build_result("passed")

            # Failed — try to fix
            if self.iteration < self.max_iterations:
                self.state = WorkflowState.FIXING
                logger.info(
                    f"CodeGenerationWorkflow {self.task_id}: "
                    f"iteration {self.iteration}/{self.max_iterations} — fixing..."
                )
                self.current_code = await self._call_coder_fix(
                    self.current_code, self.current_review,
                )
                self.history.append({
                    "phase": f"fix-{self.iteration}",
                    "code": self.current_code[:500],
                })
            else:
                self.state = WorkflowState.FAILED
                logger.warning(
                    f"CodeGenerationWorkflow {self.task_id}: "
                    f"FAILED after {self.max_iterations} iterations"
                )

        return self._build_result("failed")

    # ── Agent management ────────────────────────────────────

    def _ensure_agents(self) -> None:
        """Create coder and tester agents if they don't exist."""
        reg = self.registry

        if self.coder_id not in reg._agents:
            reg.create(self.coder_id, _coder_config(self.task_id))
            logger.info(
                f"Created {self.coder_id} "
                f"(subtree={reg._agents[self.coder_id].get('subtree_session_id','?')[:20]}...)"
            )

        if self.tester_id not in reg._agents:
            reg.create(self.tester_id, _tester_config(self.task_id))
            logger.info(
                f"Created {self.tester_id} "
                f"(subtree={reg._agents[self.tester_id].get('subtree_session_id','?')[:20]}...)"
            )

    async def _call_coder(self, task: str) -> str:
        """Ask coder to write code for the given task."""
        lang_hint = f"\nLanguage: {self.language}" if self.language else ""
        msg = f"Code task{lang_hint}:\n\n{task}"
        return await self.registry.call(
            self.coder_id, self.session_id or self.task_id, msg,
            caller_id="orchestrator",
        )

    async def _call_coder_fix(self, code: str, review: str) -> str:
        """Ask coder to fix issues found in review."""
        msg = (
            f"Your code was reviewed. Fix ALL issues listed below.\n\n"
            f"Review feedback:\n{review}\n\n"
            f"Original task:\n{self.task}\n\n"
            f"Rewrite the code with all fixes applied. Output code only."
        )
        return await self.registry.call(
            self.coder_id, self.session_id or self.task_id, msg,
            caller_id="orchestrator",
        )

    async def _call_tester(self, code: str) -> str:
        """Ask tester to review code (no task context)."""
        msg = (
            f"Review the following code. You do NOT know the original "
            f"user task — judge the code on its own merits.\n\n"
            f"```\n{code[:3000]}\n```"
        )
        return await self.registry.call(
            self.tester_id, self.session_id or self.task_id, msg,
            caller_id="orchestrator",
        )

    # ── Helpers ─────────────────────────────────────────────

    @staticmethod
    def _is_passing(review: str) -> bool:
        """Determine if the review indicates the code passes."""
        review_upper = review.upper()
        pass_phrases = [
            "PASS", "ALL TESTS PASSED", "CODE LOOKS GOOD",
            "NO ISSUES FOUND", "LOOKS CORRECT", "WELL WRITTEN",
            "LGTM", "SHIP IT",
        ]
        fail_phrases = [
            "FAIL", "ISSUES FOUND", "NEEDS WORK", "BUG",
            "ERROR", "VULNERABILITY", "INCORRECT",
        ]

        # Strong pass signals
        if "✅ PASS" in review_upper and "❌" not in review_upper:
            return True
        if "## REVIEW RESULT: ✅ PASS" in review_upper:
            return True

        # Strong fail signals
        if "❌ FAIL" in review_upper:
            return False
        if "## REVIEW RESULT: ❌ FAIL" in review_upper:
            return False

        # Heuristic: count pass vs fail phrases
        pass_count = sum(1 for p in pass_phrases if p in review_upper)
        fail_count = sum(1 for p in fail_phrases if p in review_upper)

        return pass_count > fail_count

    def _build_result(self, status: str) -> dict[str, Any]:
        return {
            "status": status,
            "code": self.current_code,
            "review": self.current_review,
            "iterations": self.iteration,
            "coder_id": self.coder_id,
            "tester_id": self.tester_id,
            "task_id": self.task_id,
            "history": self.history,
        }


# ── Agent config factories ────────────────────────────────────


def _coder_config(task_id: str) -> dict:
    return {
    "system_prompt": (
        "You are an expert SOFTWARE ENGINEER. Your ONLY job is "
        "to write production-quality code.\n\n"
        "REQUIREMENTS:\n"
        "- Output CODE ONLY. No explanations, no commentary, "
        "no markdown headers unless the task explicitly asks "
        "for documentation.\n"
        "- Every function and class MUST have a docstring "
        "describing parameters, return values, and behaviour.\n"
        "- Use type hints on ALL function signatures.\n"
        "- Handle edge cases: empty inputs, None values, "
        "invalid types, boundary conditions.\n"
        "- Raise descriptive exceptions for invalid inputs.\n"
        "- Follow the language's standard style guide "
        "(PEP 8 for Python, etc.).\n"
        "- Write readable, self-documenting code with "
        "meaningful variable names.\n"
        "- Prefer standard library over external dependencies "
        "unless the task specifies otherwise.\n\n"
        "A separate tester agent will review your code. "
        "They will find bugs if you are sloppy — don't be."
    ),
    "parent_id": "orchestrator",
    "description": "Dynamic coder for task {task_id}",
    "max_iterations": 8,
    "critical_rules": [
        "Output CODE ONLY — no explanations, no markdown.",
        "Every function and class must have a docstring.",
        "All function signatures must have type hints.",
        "Handle edge cases: empty inputs, None, invalid types.",
    ],
    "rule_reminder_every": 0,
}

def _tester_config(task_id: str) -> dict:
    return {
    "system_prompt": (
        "You are a SENIOR CODE TESTER and SECURITY REVIEWER.\n"
        "You do NOT know the original user task — you only "
        "see the code. Your job is to find EVERYTHING wrong "
        "with it.\n\n"
        "YOUR MISSION:\n"
        "1. SECURITY: SQL injection, XSS, path traversal, "
        "unsafe deserialization, hardcoded secrets, missing "
        "input validation, insecure randomness.\n"
        "2. CORRECTNESS: Logic errors, off-by-one, wrong "
        "return types, broken edge cases, race conditions.\n"
        "3. EDGE CASES: Empty inputs, None/null, zero, "
        "negative numbers, very large inputs, unicode, "
        "concurrent access.\n"
        "4. PERFORMANCE: O(n²) where O(n) is possible, "
        "unnecessary allocations, blocking I/O, missing "
        "caching opportunities.\n"
        "5. STYLE: Naming conventions, missing type hints, "
        "undocumented functions, inconsistent formatting, "
        "dead code, overly complex logic.\n\n"
        "OUTPUT FORMAT:\n"
        "## Review Result: ✅ PASS or ❌ FAIL\n\n"
        "### Security Issues\n"
        "- (specific issue with line reference if possible)\n\n"
        "### Correctness Issues\n"
        "- (specific issue)\n\n"
        "### Edge Case Issues\n"
        "- (specific issue)\n\n"
        "### Performance Issues\n"
        "- (specific issue)\n\n"
        "### Style Issues\n"
        "- (specific issue)\n\n"
        "### Summary\n"
        "Brief overall assessment.\n\n"
        "RULES:\n"
        "- Be SPECIFIC. \"Code has issues\" is useless. "
        "\"Line 12: missing null check on user input\" is useful.\n"
        "- Every issue MUST have a suggested fix.\n"
        "- Do NOT rewrite the code — describe what to fix.\n"
        "- If the code is genuinely correct, say ✅ PASS "
        "and briefly explain why."
    ),
    "parent_id": "orchestrator",
    "description": "Dynamic tester for task {task_id}",
    "max_iterations": 5,
    "critical_rules": [
        "Report specific issues with suggested fixes.",
        "Check security, correctness, edge cases, performance, style.",
        "Use the exact output format: Review Result, sections, Summary.",
    ],
    "rule_reminder_every": 0,
}
