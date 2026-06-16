"""
Live workflow tracker — beautiful CLI status display with progress.

Integrates with CodeGenerationWorkflow callbacks to show:
- Current stage + progress bar
- Last 3 stage results
- Best score so far
- Iteration count
"""

from __future__ import annotations

import sys, time
from typing import Any

from code_workflow import CodeGenerationWorkflow, StageResult

# ── Rich import (optional) ────────────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import Progress, BarColumn, TextColumn
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


def render_live_status(wf: CodeGenerationWorkflow) -> str:
    """Return a rich-formatted live status string for terminal display."""
    s = wf.state
    stages = s.stages

    icon = {"running": "⚡", "completed": "✅", "stopped": "⏹",
            "failed": "❌", "idle": "⏳"}.get(s.status, "•")

    lines = [
        f"  {icon} [bold]Code Workflow[/] [{s.status.upper()}]",
        f"  {'─' * 48}",
        f"  Task:     {_truncate(s.task, 50)}",
    ]

    # ── Progress bar (text-based) ──────────────────────────
    pct = min(s.current_iteration / s.max_iterations, 1.0)
    bar_width = 30
    filled = int(pct * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    lines.append(f"  Progress: [{bar}] {s.current_iteration}/{s.max_iterations}")

    # ── Phase indicator ────────────────────────────────────
    phase = _current_phase(wf)
    lines.append(f"  Phase:    [bold]{phase}[/]")

    # ── Recent stage results ───────────────────────────────
    if stages:
        lines.append(f"  Stages:   {len(stages)} completed")
        for st in stages[-3:]:
            icon_s = "[green]✓[/]" if st.success else "[red]✗[/]"
            dur = f"{st.duration_ms:.0f}ms" if st.duration_ms else "—"
            lines.append(f"            {icon_s} {st.stage:<20} {dur}")

    # ── Score ──────────────────────────────────────────────
    if s.best_score > 0:
        score_bar = _score_bar(s.best_score)
        lines.append(f"  Best:     {s.best_score:.1f}/10 {score_bar}")

    # ── Output path ────────────────────────────────────────
    if s.best_code:
        try:
            out = wf.save_dir / "latest.py"
            lines.append(f"  Output:   {out}")
        except Exception:
            pass

    lines.append(f"  {'─' * 48}")
    lines.append(f"  /approve | /edit | /regenerate | /stop")
    return "\n".join(lines)


def render_compact_status(wf: CodeGenerationWorkflow) -> str:
    """One-line compact status for status bar / quick glance."""
    s = wf.state
    pct = min(s.current_iteration / s.max_iterations, 1.0)
    bar_width = 10
    filled = int(pct * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    score = f" {s.best_score:.1f}/10" if s.best_score > 0 else ""
    return f"[WF] [{bar}] {s.current_iteration}/{s.max_iterations}{score}"


def _current_phase(wf: CodeGenerationWorkflow) -> str:
    """Determine current phase from the last stage."""
    stages = wf.state.stages
    if not stages:
        return "Prompt Engineer" if wf.use_prompt_engineer else "Coder"
    last = stages[-1].stage
    if last == "prompt-engineer":
        return "Coder"
    if last == "coder":
        return "Tester"
    if last == "tester":
        return "Optimizer" if wf._has_optimizer else "Coder (next iter)"
    return "Coder"


def _score_bar(score: float) -> str:
    """Visual score bar."""
    if score >= 9:
        return "[green]★★★★★[/]"
    if score >= 7:
        return "[green]★★★★[/][dim]★[/]"
    if score >= 5:
        return "[yellow]★★★[/][dim]★★[/]"
    if score >= 3:
        return "[yellow]★★[/][dim]★★★[/]"
    return "[red]★[/][dim]★★★★[/]"


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


# ═══════════════════════════════════════════════════════════════
# Live display (Rich-based, optional)
# ═══════════════════════════════════════════════════════════════

class LiveWorkflowDisplay:
    """Optional Rich-based live updating display for workflows."""

    def __init__(self, wf: CodeGenerationWorkflow):
        self.wf = wf
        self._live: Any = None
        if HAS_RICH:
            self._console = Console()

    def start(self) -> None:
        if not HAS_RICH:
            return
        self._live = Live(
            Panel(render_live_status(self.wf), title="Code Workflow"),
            console=self._console,
            refresh_per_second=4,
        )
        self._live.start()
        self.wf.on_stage(self._on_stage_callback)

    def _on_stage_callback(self, _result: StageResult) -> None:
        if self._live:
            self._live.update(
                Panel(render_live_status(self.wf), title="Code Workflow")
            )

    def stop(self) -> None:
        if self._live:
            self._live.stop()
