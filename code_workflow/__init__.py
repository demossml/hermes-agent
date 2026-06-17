"""
CodeGenerationWorkflow — production-grade multi-agent code pipeline.

Chain: PromptEngineer (opt) → Coder → Tester → Optimizer (opt)

Strict isolation: each agent has a separate subtree_session_id.
Tester never sees the user's original request — only the coder's output.

Usage::

    from code_workflow import CodeGenerationWorkflow

    wf = CodeGenerationWorkflow(task="Write a REST API in Python")
    wf.run()  # blocking
    # or
    async for event in wf.run_stream():
        print(event)
"""

from __future__ import annotations

import json, logging, os, re, time, uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Data models
# ═══════════════════════════════════════════════════════════════

@dataclass
class StageResult:
    stage: str           # "prompt_engineer" | "coder" | "tester" | "optimizer"
    success: bool
    content: str         # main output
    iteration: int
    duration_ms: float
    metadata: dict = field(default_factory=dict)


@dataclass
class WorkflowState:
    """Serializable workflow state."""
    task_id: str
    task: str
    status: str = "idle"     # idle | running | waiting_approval | completed | stopped | failed
    stages: list[StageResult] = field(default_factory=list)
    best_code: str = ""
    best_score: float = 0.0
    current_iteration: int = 0
    max_iterations: int = 5
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at


# ═══════════════════════════════════════════════════════════════
# Prompt engineer keyword detector (zero-token)
# ═══════════════════════════════════════════════════════════════

_NEEDS_PROMPT_ENGINEER = re.compile(
    r"(?i)(optimiz|улучши|рефакторинг|refactor|improve|enhance"
    r"|architecture|архитектур|design|дизайн|best.practice"
    r"|complex|сложн|разбей|decompose|spec|спецификац"
    r"|from scratch|с нуля|новый проект|new project"
    r"|сначала создай промпт|улучши запрос|сделай хороший промпт"
    r"|напиши промпт|составь промпт|продумай промпт"
    r"|big|large|huge|большой|крупный|масштабный)"
)


def detect_need_prompt_engineer(task: str) -> bool:
    """Zero-token detection — returns True when task is complex enough
    to benefit from prompt engineering."""
    return bool(_NEEDS_PROMPT_ENGINEER.search(task))


# ═══════════════════════════════════════════════════════════════
# Agent invoker — isolated call per stage
# ═══════════════════════════════════════════════════════════════

def _invoke_agent(agent_id: str, prompt: str, *, max_iterations: int = 5) -> StageResult:
    """Invoke a sub-agent with strict subtree isolation.

    Each agent gets its own subtree_session_id.
    Tester receives only the coder's output, never the user prompt.
    """
    from agent_registry import AgentRegistry

    registry = AgentRegistry()
    config = registry.get(agent_id)
    if not config:
        return StageResult(stage=agent_id, success=False,
                           content=f"Agent '{agent_id}' not configured.",
                           iteration=0, duration_ms=0)

    t0 = time.monotonic()
    try:
        # Build isolated call context
        subtree = config.get("subtree_session_id", f"subtree-{agent_id}")
        response = _run_agent_turn(agent_id, prompt, subtree_id=subtree,
                                   max_iterations=max_iterations)
        elapsed = (time.monotonic() - t0) * 1000
        return StageResult(stage=agent_id, success=True, content=response,
                           iteration=0, duration_ms=elapsed)
    except Exception as e:
        elapsed = (time.monotonic() - t0) * 1000
        logger.error(f"Agent '{agent_id}' failed: {e}")
        return StageResult(stage=agent_id, success=False, content=str(e),
                           iteration=0, duration_ms=elapsed)


def _run_agent_turn(agent_id: str, prompt: str, *,
                    subtree_id: str = "",
                    max_iterations: int = 5) -> str:
    """Execute a single-turn agent call with subtree isolation."""
    # This hooks into Hermes's agent execution engine.
    # In production, this calls agent.run() with the subtree context.
    try:
        from agent.core import Agent
        agent = Agent(agent_id=agent_id, subtree_session_id=subtree_id)
        result = agent.chat(prompt, max_tool_iterations=max_iterations)
        return result.get("final_response", "") or ""
    except ImportError:
        # Fallback: direct LLM call via provider
        return _direct_llm_call(agent_id, prompt, max_iterations)


def _direct_llm_call(agent_id: str, prompt: str, max_iterations: int = 5) -> str:
    """Direct LLM fallback when agent core is unavailable."""
    try:
        from agent_registry import AgentRegistry
        registry = AgentRegistry()
        config = registry.get(agent_id) or {}

        provider_name = config.get("provider", "anthropic")
        model = config.get("model", "claude-sonnet-4-20250514")

        from providers import get_provider
        provider = get_provider(provider_name)
        response = provider.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
        )
        return response.get("content", "") or ""
    except Exception as e:
        return f"[{agent_id} fallback error: {e}]"


# ═══════════════════════════════════════════════════════════════
# Stage builders — each stage composes a prompt for its agent
# ═══════════════════════════════════════════════════════════════

def _build_prompt_engineer_prompt(task: str) -> str:
    return (
        "You are a PROMPT ENGINEER. Transform this raw request into "
        "a precise, detailed coding prompt.\n\n"
        "Include:\n"
        "- Exact task description\n"
        "- Input/output specs with types\n"
        "- Edge cases to handle\n"
        "- Required docstrings, type hints, error handling\n"
        "- Target language/framework if specified\n\n"
        f"RAW REQUEST:\n{task}\n\n"
        "DETAILED PROMPT:"
    )


def _build_coder_prompt(task: str) -> str:
    return (
        "You are an expert SOFTWARE ENGINEER. Write production-quality code.\n\n"
        "RULES:\n"
        "- Output CODE ONLY — no explanations, no markdown fences.\n"
        "- Every function/class must have a docstring.\n"
        "- All function signatures must have type hints.\n"
        "- Handle edge cases: empty inputs, None, invalid types.\n"
        "- Write clean, idiomatic code.\n\n"
        f"TASK:\n{task}\n\n"
        "CODE:"
    )


def _build_tester_prompt(code: str, iteration: int) -> str:
    """Tester receives ONLY the code — never the user's original request."""
    return (
        "You are a SENIOR CODE TESTER. Review this code for:\n"
        "- Bugs and logic errors\n"
        "- Security vulnerabilities\n"
        "- Edge cases (empty inputs, None, invalid types)\n"
        "- Missing docstrings or type hints\n"
        "- Code style violations\n\n"
        "Reply with:\n"
        "- Score: X/10\n"
        "- Issues found (if any)\n"
        "- Specific line references\n"
        "- Recommendation: APPROVE / REVISE\n\n"
        f"CODE (iteration {iteration}):\n```\n{code}\n```\n\n"
        "REVIEW:"
    )


def _build_optimizer_prompt(code: str, review: str) -> str:
    return (
        "You are a CODE OPTIMIZER. Improve this code based on the review.\n\n"
        f"ORIGINAL CODE:\n```\n{code}\n```\n\n"
        f"REVIEW:\n{review}\n\n"
        "Apply ALL fixes. Improve style, performance, readability, and security.\n"
        "Output the COMPLETE corrected code with tests included.\n"
        "IMPROVED CODE:"
    )


def _detect_tests_in_code(code: str) -> str:
    """Extract test functions/classes from code if present."""
    test_match = re.search(
        r"(?:def test_|class Test|import pytest|from pytest).*",
        code, re.DOTALL,
    )
    return test_match.group(0).strip() if test_match else ""


def _parse_tester_score(review: str) -> tuple[float, bool, list[str]]:
    """Extract score, approval flag, and issues from tester output."""
    score = 5.0
    approved = False
    issues = []

    # Score: X/10
    score_match = re.search(r"(?:Score|Оценка)[:\s]*(\d+)\s*/\s*10", review, re.I)
    if score_match:
        score = min(float(score_match.group(1)), 10.0)

    # APPROVE / REVISE
    if re.search(r"\bAPPROVE\b", review, re.I):
        approved = True
    if re.search(r"\bREVISE\b", review, re.I):
        approved = False

    # Extract issues (numbered or bullet)
    for line in review.split("\n"):
        line = line.strip()
        # Match numbered (1. 2) 3.), bulleted (- * •), or task-list items
        if re.match(r"^[\d\-•*]+[.)\s]+\s+", line) and len(line) > 10:
            issues.append(line)

    return score, approved, issues


# ═══════════════════════════════════════════════════════════════
# Main workflow
# ═══════════════════════════════════════════════════════════════

class CodeGenerationWorkflow:
    """Production-grade multi-agent code pipeline.

    Teams:
    - ``basic``:    Coder → Tester (2 agents, fast)
    - ``full``:     PromptEng → Coder → Tester (3 agents)
    - ``advanced``: PromptEng → Coder → Tester → Optimizer (4 agents)

    Parameters
    ----------
    task : str
        The coding task description.
    team : str
        One of ``"basic"``, ``"full"``, ``"advanced"``. Default ``"full"``.
    use_prompt_engineer : bool | None
        Override PE detection. None = auto-detect.
    max_iterations : int
        Maximum coder → tester → optimizer loops (default 5).
    auto_approve_threshold : float
        Auto-approve if tester score >= threshold (default 8.0).
    save_dir : Path | None
        Output directory. Default: project's code/ folder.
    """

    _TEAMS = {
        "basic":    {"pe": False, "opt": False},
        "full":     {"pe": True,  "opt": False},
        "advanced": {"pe": True,  "opt": True},
    }

    def __init__(
        self,
        task: str,
        *,
        team: str = "full",
        use_prompt_engineer: bool | None = None,
        max_iterations: int = 5,
        auto_approve_threshold: float = 8.0,
        save_dir: Path | None = None,
    ):
        self.task = task.strip()
        team_cfg = self._TEAMS.get(team, self._TEAMS["full"])

        # PE: explicit override beats team config
        if use_prompt_engineer is not None:
            self.use_prompt_engineer = use_prompt_engineer
        elif team_cfg["pe"]:
            self.use_prompt_engineer = detect_need_prompt_engineer(task)
        else:
            self.use_prompt_engineer = False

        self.use_optimizer = team_cfg["opt"]
        self.team = team
        self.max_iterations = max_iterations
        self.auto_approve_threshold = auto_approve_threshold
        self.save_dir = save_dir or self._default_save_dir()
        self.save_dir.mkdir(parents=True, exist_ok=True)

        self.state = WorkflowState(
            task_id=uuid.uuid4().hex[:12],
            task=self.task,
            max_iterations=max_iterations,
        )

        self._stop_requested = False
        self._on_stage: list[Callable] = []
        self._current_test_code: str = ""  # extracted test code

    # ── Paths ──────────────────────────────────────────────

    @staticmethod
    def _default_save_dir() -> Path:
        try:
            from projects.project_context import get_current_project_id
            pid = get_current_project_id()
            if pid:
                from projects.project_artifacts import get_project_artifact_dirs
                code_dir, _ = get_project_artifact_dirs(pid)
                return code_dir
        except Exception:
            pass
        return Path.home() / ".hermes" / "code_output"

    # ── Events ─────────────────────────────────────────────

    def on_stage(self, callback: Callable) -> None:
        """Register a callback for stage events: callback(stage: StageResult)."""
        self._on_stage.append(callback)

    def _emit(self, result: StageResult) -> None:
        self.state.stages.append(result)
        self.state.updated_at = datetime.now(timezone.utc).isoformat()
        for cb in self._on_stage:
            try:
                cb(result)
            except Exception:
                pass

    # ── Save best code ─────────────────────────────────────

    def _save_best(self, code: str, score: float) -> None:
        if score > self.state.best_score:
            self.state.best_score = score
            self.state.best_code = code
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = self.save_dir / f"best_v{self.state.current_iteration}_{ts}.py"
            fname.write_text(code, encoding="utf-8")
            (self.save_dir / "latest.py").write_text(code, encoding="utf-8")

            # Extract and save test code separately
            tests = _detect_tests_in_code(code)
            if tests:
                tname = self.save_dir / f"test_v{self.state.current_iteration}_{ts}.py"
                tname.write_text(tests, encoding="utf-8")
                (self.save_dir / "test_latest.py").write_text(tests, encoding="utf-8")
                self._current_test_code = tests

            logger.info(f"Saved best (score={score:.1f}) → {fname}")

    # ── Run (blocking) ─────────────────────────────────────

    def run(self) -> WorkflowState:
        """Execute the full workflow synchronously. Returns final state."""
        self.state.status = "running"

        # ── Stage 0: Prompt Engineer (optional) ──────────────
        engineered_prompt = self.task
        if self.use_prompt_engineer:
            pe_prompt = _build_prompt_engineer_prompt(self.task)
            result = _invoke_agent("prompt-engineer", pe_prompt)
            self._emit(result)
            if result.success and result.content:
                engineered_prompt = result.content

        # ── Stage 1-n: Coder → Tester → (Optimizer) loop ────
        current_code = ""
        current_prompt = engineered_prompt

        for i in range(1, self.max_iterations + 1):
            if self._stop_requested:
                self.state.status = "stopped"
                break

            self.state.current_iteration = i

            # ── Coder ─────────────────────────────────────────
            coder_prompt = _build_coder_prompt(current_prompt)
            coder_result = _invoke_agent("coder", coder_prompt,
                                         max_iterations=8)
            self._emit(coder_result)
            if not coder_result.success:
                continue

            current_code = _extract_code(coder_result.content)
            if not current_code:
                continue

            # ── Tester (blind to user task!) ──────────────────
            tester_prompt = _build_tester_prompt(current_code, i)
            tester_result = _invoke_agent("tester", tester_prompt,
                                          max_iterations=5)
            self._emit(tester_result)
            if not tester_result.success:
                continue

            score, approved, issues = _parse_tester_score(tester_result.content)

            # ── Save best ─────────────────────────────────────
            self._save_best(current_code, score)

            # ── Check approval ────────────────────────────────
            if approved or score >= self.auto_approve_threshold:
                self.state.status = "completed"
                break

            # ── Optimizer (if in advanced team + issues found) ─
            if self.use_optimizer:
                opt_prompt = _build_optimizer_prompt(current_code,
                                                     tester_result.content)
                opt_result = _invoke_agent("prompt-engineer", opt_prompt)
                opt_result.stage = "optimizer"  # label correctly
                self._emit(opt_result)
                if opt_result.success and opt_result.content:
                    opt_code = _extract_code(opt_result.content)
                    if opt_code:
                        current_code = opt_code
                        # Re-test the optimized code (fast loop)
                        current_prompt = (
                            f"Previous iteration produced code that the tester "
                            f"rated {score:.1f}/10. Optimizer has improved it. "
                            f"Re-evaluate the optimized code."
                        )
                    else:
                        current_prompt = opt_result.content
            elif issues:
                current_prompt = f"Fix issues:\n" + "\n".join(
                    f"- {iss}" for iss in issues[:5]
                )

        else:
            self.state.status = "completed"  # max iterations reached

        # Final save
        if self.state.best_code:
            final = self.save_dir / "final.py"
            final.write_text(self.state.best_code, encoding="utf-8")

        self.state.updated_at = datetime.now(timezone.utc).isoformat()
        return self.state

    # ── Control ────────────────────────────────────────────

    def stop(self) -> None:
        """Request workflow stop after current stage."""
        self._stop_requested = True
        self.state.status = "stopping"

    def approve(self) -> None:
        """Approve current code and complete workflow."""
        self.state.status = "completed"
        self._stop_requested = True  # stop after current iteration

    def edit_prompt(self, new_instruction: str) -> None:
        """Inject a new instruction for the next coder iteration."""
        self.state.task = f"{self.task}\n\n[EDITOR'S NOTE]: {new_instruction}"

    def regenerate(self) -> None:
        """Force regeneration of current stage."""
        # Resets current_prompt to original for next iteration
        pass  # next loop iteration will regenerate naturally

    # ── Status display ─────────────────────────────────────

    def status_summary(self) -> str:
        """Return a rich status summary for CLI display."""
        s = self.state
        icon = {"running": "⚡", "completed": "✅", "stopped": "⏹",
                "failed": "❌", "idle": "⏳"}.get(s.status, "•")

        phases = []
        if self.use_prompt_engineer:
            phases.append("PromptEng")
        phases.append("Coder")
        phases.append("Tester")
        if self.use_optimizer:
            phases.append("Optimizer")

        lines = [
            f"  {icon} Code Workflow [{self.team.upper()}] [{s.status.upper()}]",
            f"  {'─' * 48}",
            f"  Task:    {s.task[:55]}{'...' if len(s.task) > 55 else ''}",
            f"  Team:    {' → '.join(phases)}",
            f"  Iter:    {s.current_iteration}/{s.max_iterations}  "
            f"Score: {s.best_score:.1f}/10" if s.best_score > 0 else "",
        ]

        if s.stages:
            lines.append(f"  Stages:  {len(s.stages)} completed")
            for st in s.stages[-3:]:
                icon_s = "✓" if st.success else "✗"
                lines.append(f"           {icon_s} {st.stage:<14} {st.duration_ms:.0f}ms")

        if s.best_code:
            lines.append(f"  Output:  {self.save_dir}/latest.py")
            if self._current_test_code:
                lines.append(f"           {self.save_dir}/test_latest.py")

        lines.append(f"  {'─' * 48}")
        lines.append(f"  /approve | /edit <instr> | /regenerate | /stop")
        return "\n".join(lines)

    @property
    def _has_optimizer(self) -> bool:
        return any(s.stage == "prompt-engineer" and s.iteration > 0
                   for s in self.state.stages)


# ── Helpers ────────────────────────────────────────────────

def _extract_code(text: str) -> str:
    """Extract code from agent output (handles markdown fences)."""
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # No fence — return as-is, stripping common wrappers
    for prefix in ["CODE:", "```", "'''"]:
        if text.strip().startswith(prefix):
            text = text.strip()[len(prefix):]
    for suffix in ["```", "'''"]:
        if text.strip().endswith(suffix):
            text = text.strip()[:-len(suffix)]
    return text.strip()


# ═══════════════════════════════════════════════════════════════
# Module-level orchestrate function
# ═══════════════════════════════════════════════════════════════

_active_workflows: dict[str, CodeGenerationWorkflow] = {}


def orchestrate(task: str, *, team: str = "full",
                use_prompt_engineer: bool | None = None,
                max_iterations: int = 5) -> CodeGenerationWorkflow:
    """Start a new code workflow. Returns the workflow handle."""
    wf = CodeGenerationWorkflow(
        task=task, team=team,
        use_prompt_engineer=use_prompt_engineer,
        max_iterations=max_iterations,
    )
    _active_workflows[wf.state.task_id] = wf
    return wf


def get_active_workflow(task_id: str | None = None) -> CodeGenerationWorkflow | None:
    """Get an active workflow by ID, or the most recent."""
    if task_id:
        return _active_workflows.get(task_id)
    if _active_workflows:
        return list(_active_workflows.values())[-1]
    return None


def list_active_workflows() -> list[CodeGenerationWorkflow]:
    return list(_active_workflows.values())
