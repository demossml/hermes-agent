"""
Workflow agents — CoderAgent and TesterAgent encapsulate the
agent-specific configuration, prompt engineering, and communication
with the AgentRegistry.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Base
# ═══════════════════════════════════════════════════════════════


class WorkflowAgent:
    """Base for workflow agents (Coder, Tester, Reviewer, Optimizer…).

    Subclasses define ``_build_config()`` returning a config dict
    suitable for ``registry.create()``.
    """

    agent_type: str = "worker"

    def __init__(self, agent_id: str, registry: Any, task_id: str):
        self.agent_id = agent_id
        self.registry = registry
        self.task_id = task_id

    def ensure_created(self) -> None:
        """Create the agent in the registry if it does not exist."""
        if self.agent_id not in self.registry._agents:
            cfg = self._build_config()
            self.registry.create(self.agent_id, cfg)
            logger.info(
                "Created %s (subtree=%s...)",
                self.agent_id,
                self.registry._agents[self.agent_id]
                .get("subtree_session_id", "?")[:20],
            )

    async def send(self, message: str, session_id: str = "") -> str:
        """Send a message to the agent and return the reply."""
        return await self.registry.call(
            self.agent_id,
            session_id or self.task_id,
            message,
            caller_id="orchestrator",
        )

    def _build_config(self) -> dict:
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════
# Coder
# ═══════════════════════════════════════════════════════════════


class CoderAgent(WorkflowAgent):
    """Expert software engineer — writes production-quality code only."""

    agent_type = "coder"

    def __init__(
        self,
        agent_id: str,
        registry: Any,
        task_id: str,
        language: str | None = None,
    ):
        super().__init__(agent_id, registry, task_id)
        self.language = language
        self.subtree_session_id = f"subtree-{agent_id}-{task_id}"

    async def write_code(self, task: str, session_id: str = "") -> str:
        """Ask the coder to write code for a task."""
        lang = f"\nLanguage: {self.language}" if self.language else ""
        msg = f"Code task{lang}:\n\n{task}"
        return await self.send(msg, session_id)

    async def fix_code(
        self, code: str, review: str, task: str, session_id: str = "",
    ) -> str:
        """Ask the coder to fix issues found in review."""
        msg = (
            "Your code was reviewed. Fix ALL issues listed below.\n\n"
            f"Review feedback:\n{review}\n\n"
            f"Original task:\n{task}\n\n"
            "Rewrite the code with all fixes applied. Output code only."
        )
        return await self.send(msg, session_id)

    def _build_config(self) -> dict:
        return {
            "system_prompt": _CODER_PROMPT,
            "parent_id": "orchestrator",
            "subtree_session_id": self.subtree_session_id,
            "description": f"Dynamic coder for task {self.task_id}",
            "max_iterations": 8,
            "critical_rules": [
                "Output CODE ONLY — no explanations, no markdown.",
                "Every function and class must have a docstring.",
                "All function signatures must have type hints.",
                "Handle edge cases: empty inputs, None, invalid types.",
            ],
            "rule_reminder_every": 0,
        }


# ═══════════════════════════════════════════════════════════════
# Tester
# ═══════════════════════════════════════════════════════════════


class TesterAgent(WorkflowAgent):
    """Senior code tester + security reviewer with sandboxed execution."""

    agent_type = "tester"

    def __init__(self, agent_id: str, registry: Any, task_id: str):
        super().__init__(agent_id, registry, task_id)
        self.subtree_session_id = f"subtree-{agent_id}-{task_id}"

    async def review_code(self, code: str, session_id: str = "") -> str:
        """Review code — run real tests first, then LLM review.

        The LLM receives actual execution results (pytest, mypy,
        security scan) alongside the code so it can make informed
        judgments rather than speculating.
        """
        # ── Real execution first ────────────────────────────
        exec_report = ""
        try:
            from code_workflow.runner import get_runner
            runner = get_runner()
            report = runner.run_full_check(code)
            exec_report = json.dumps(report, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.debug("CodeRunner unavailable: %s", e)

        # ── Build review message ────────────────────────────
        if exec_report:
            msg = (
                "FIRST — here are the ACTUAL test results from running "
                "this code in a sandbox. Use these results in your review.\n\n"
                f"```json\n{exec_report}\n```\n\n"
                "Now review the code. You do NOT know the original user "
                "task — judge the code on its own merits.\n\n"
                f"```\n{code[:2500]}\n```"
            )
        else:
            msg = (
                "Review the following code. You do NOT know the original "
                "user task — judge the code on its own merits.\n\n"
                f"```\n{code[:3000]}\n```"
            )

        return await self.send(msg, session_id)

    def generate_and_run_tests(
        self, code: str, function_name: str = "",
    ) -> dict[str, Any]:
        """Auto-generate quality pytest tests and run them in a sandbox.

        Returns::

            {
                "test_code": str,       # the generated test code
                "test_count": int,      # number of test functions
                "passed": int,          # tests passed
                "failed": int,          # tests failed
                "pytest_output": str,   # raw pytest output
                "success": bool,        # all tests passed
            }
        """
        try:
            from code_workflow.runner import get_runner
            runner = get_runner()
            result = runner.run_tests(code, function_name=function_name)
            return {
                "test_code": result.get("test_code", ""),
                "test_count": result.get("test_count", 0),
                "passed": result.get("passed", 0),
                "failed": result.get("failed", 0),
                "pytest_output": result.get("stdout", ""),
                "success": result.get("success", False),
            }
        except Exception as e:
            logger.debug("generate_and_run_tests failed: %s", e)
            return {
                "test_code": "", "test_count": 0,
                "passed": 0, "failed": 0,
                "pytest_output": str(e), "success": False,
            }

    def _build_config(self) -> dict:
        return {
            "system_prompt": _TESTER_PROMPT,
            "parent_id": "orchestrator",
            "subtree_session_id": self.subtree_session_id,
            "description": f"Dynamic tester for task {self.task_id}",
            "max_iterations": 5,
            "enabled_toolsets": [],   # ⛔ ZERO tools — cannot read coder memory
            "critical_rules": [
                "You do NOT know the original user request. "
                "Judge ONLY the code provided to you.",
                "NEVER ask the coder or user for context — "
                "you work with the code text alone.",
                "Report specific issues with suggested fixes.",
                "Check security, correctness, edge cases, "
                "performance, style.",
                "Use the exact output format: Review Result, "
                "sections, Summary.",
            ],
            "rule_reminder_every": 0,
        }


# ═══════════════════════════════════════════════════════════════
# Prompts
# ═══════════════════════════════════════════════════════════════

_CODER_PROMPT = (
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
)

_TESTER_PROMPT = (
    "You are a SENIOR CODE TESTER and SECURITY REVIEWER.\n"
    "You do NOT know the original user task — you only "
    "see the code AND the automated test results that were "
    "already run in a sandbox.\n\n"
    "\u26a0\ufe0f IMPORTANT: The code has ALREADY been executed in "
    "a sandbox. The test results (pytest output, mypy, "
    "security scan) are in the message above the code. "
    "Use these REAL results — do NOT speculate about "
    "whether the code runs or not.\n\n"
    "YOUR MISSION:\n"
    "1. SYNTAX: Check the actual execution output. Did it "
    "compile? Any syntax errors?\n"
    "2. TESTS: Did pytest pass? How many passed/failed? "
    "Quote the actual test output.\n"
    "3. EDGE CASES: What inputs would break this code? "
    "Empty lists? None? Negative numbers?\n"
    "4. TYPES: Did mypy find type errors? Quote them.\n"
    "5. SECURITY: Any dangerous patterns (eval, exec, "
    "subprocess, hardcoded secrets)?\n"
    "6. PERFORMANCE: O(n\u00b2)? Unnecessary allocations?\n"
    "7. STYLE: Naming, docstrings, type hints, PEP 8.\n\n"
    "OUTPUT FORMAT:\n"
    "## Review Result: \u2705 PASS or \u274c FAIL\n\n"
    "### Execution Results\n"
    "- Syntax: PASS/FAIL (with error if any)\n"
    "- Tests: X passed, Y failed\n"
    "- Types: PASS/FAIL (with mypy output if any)\n"
    "- Security: X issues found\n\n"
    "### Security Issues\n"
    "- (specific issue)\n\n"
    "### Edge Case Issues\n"
    "- (specific issue)\n\n"
    "### Performance Issues\n"
    "- (specific issue)\n\n"
    "### Style Issues\n"
    "- (specific issue)\n\n"
    "### Summary\n"
    "Brief overall assessment. Quote the actual test "
    "output — do NOT make up results."
)
