"""
Self-Improvement Loop — continuous agent improvement within a project.

After a significant task completes, the system:
1. Analyzes the agent trajectory (what worked, what didn't)
2. Extracts patterns and lessons
3. Proposes concrete improvements (rules, prompts, skills)
4. Shows proposals with confirmation
5. Versions all changes for rollback

Usage::

    from projects.self_improve import SelfImprover

    si = SelfImprover("my-project")
    proposals = si.analyze_trajectory(workflow.state)
    for p in proposals:
        print(p)
    si.apply(proposals[0])  # after user confirmation

    # CLI: /improve
"""

from __future__ import annotations

import json, logging, re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Data models
# ═══════════════════════════════════════════════════════════════

@dataclass
class Proposal:
    """A concrete improvement suggestion."""
    id: str
    category: str          # "critical_rule" | "system_prompt" | "skill" | "tool_usage"
    title: str             # one-line summary
    description: str       # detailed explanation
    before: str = ""       # current value (if modifying)
    after: str = ""        # proposed value
    confidence: float = 0.7  # 0-1, how sure is the analyzer
    evidence: list[str] = field(default_factory=list)  # supporting trajectory quotes
    applied: bool = False
    applied_at: str = ""

    def summary(self) -> str:
        stars = "★" * min(int(self.confidence * 5), 5) + "☆" * max(5 - int(self.confidence * 5), 0)
        return f"[{stars}] [{self.category}] {self.title}"


@dataclass
class ImprovementReport:
    """Full report from an improvement analysis session."""
    project_id: str
    analyzed_at: str = ""
    proposals: list[Proposal] = field(default_factory=list)
    lessons_learned: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.analyzed_at:
            self.analyzed_at = datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════
# Pattern matchers — zero-token trajectory analysis
# ═══════════════════════════════════════════════════════════════

_ERROR_PATTERNS = [
    (r"(?i)(?:error|traceback|exception|failed)\s*[:\-]\s*(.+)", "error"),
    (r"(?i)(?:bug|issue|problem)\s*(?:#\d+)?[:\-]\s*(.+)", "bug"),
    (r"(?i)(?:missing|forgot|should have|ought to)\s+(.+)", "oversight"),
]

_SUCCESS_PATTERNS = [
    (r"(?i)(?:approved|score:\s*(?:[89]|10)/10|looks good|lgtm)", "high_score"),
    (r"(?i)(?:elegant|clean|well.structured|idiomatic)", "quality"),
    (r"(?i)(?:fast|efficient|performant|optimized)", "performance"),
]

_PROMPT_QUALITY_PATTERNS = [
    (r"(?i)coder.*(?:understood|correct|accurate)", "coder_understood"),
    (r"(?i)(?:wrong|misunderstood|off.track|hallucinat)", "coder_misunderstood"),
    (r"(?i)(?:too verbose|too long|redundant|unnecessary)", "too_verbose"),
    (r"(?i)(?:concise|precise|exact|spot.on)", "precise"),
]


@dataclass
class TrajectoryInsight:
    text: str
    category: str    # "error" | "success" | "pattern"
    source_stage: str
    confidence: float


def _extract_insights(stages: list[Any]) -> list[TrajectoryInsight]:
    """Zero-token extraction of insights from workflow stages."""
    insights: list[TrajectoryInsight] = []

    for stage in stages:
        content = getattr(stage, "content", "")
        stage_name = getattr(stage, "stage", "unknown")
        if not content:
            continue

        # Errors
        for pattern, label in _ERROR_PATTERNS:
            for m in re.finditer(pattern, content):
                insights.append(TrajectoryInsight(
                    text=m.group(1).strip()[:120],
                    category=f"error_{label}",
                    source_stage=stage_name,
                    confidence=0.8,
                ))

        # Successes
        for pattern, label in _SUCCESS_PATTERNS:
            if re.search(pattern, content):
                insights.append(TrajectoryInsight(
                    text=f"Pattern '{label}' detected in {stage_name}",
                    category=f"success_{label}",
                    source_stage=stage_name,
                    confidence=0.6,
                ))

        # Prompt quality (tester stage only — evaluates coder's understanding)
        if stage_name == "tester":
            for pattern, label in _PROMPT_QUALITY_PATTERNS:
                if re.search(pattern, content):
                    insights.append(TrajectoryInsight(
                        text=f"Prompt quality: {label}",
                        category=f"prompt_{label}",
                        source_stage=stage_name,
                        confidence=0.65,
                    ))

    return insights


# ═══════════════════════════════════════════════════════════════
# SelfImprover
# ═══════════════════════════════════════════════════════════════

class SelfImprover:
    """Analyze agent trajectories and propose improvements.

    Parameters
    ----------
    project_id : str
        The project to improve.
    hermes_home : Path | None
        Override Hermes home directory.
    """

    def __init__(self, project_id: str, hermes_home: str | Path | None = None):
        self.project_id = project_id
        self._home = Path(hermes_home) if hermes_home else self._resolve_home()
        self._proj_dir = self._home / "projects" / project_id
        self._improve_dir = self._proj_dir / "state" / "improvements"
        self._improve_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _resolve_home() -> Path:
        try:
            from hermes_constants import get_hermes_home
            return get_hermes_home()
        except ImportError:
            import os
            return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))

    # ── Main analysis ─────────────────────────────────────

    def analyze_trajectory(self, workflow_state: Any) -> ImprovementReport:
        """Analyze a completed workflow and produce improvement proposals.

        Parameters
        ----------
        workflow_state : WorkflowState
            From ``CodeGenerationWorkflow.state`` after run() completes.
        """
        stages = getattr(workflow_state, "stages", [])
        task = getattr(workflow_state, "task", "")
        score = getattr(workflow_state, "best_score", 0.0)

        report = ImprovementReport(project_id=self.project_id)
        insights = _extract_insights(stages)

        # ── Generate proposals from insights ───────────────
        proposals = self._generate_proposals(insights, task, score)
        report.proposals = proposals
        report.lessons_learned = [i.text for i in insights[:10]]
        report.stats = {
            "stages_analyzed": len(stages),
            "insights_found": len(insights),
            "proposals_generated": len(proposals),
            "task_score": score,
            "task": task[:80],
        }

        # Save report
        self._save_report(report)
        return report

    def _generate_proposals(
        self, insights: list[TrajectoryInsight], task: str, score: float,
    ) -> list[Proposal]:
        """Convert raw insights into actionable proposals."""
        proposals: list[Proposal] = []

        errors = [i for i in insights if i.category.startswith("error_")]
        successes = [i for i in insights if i.category.startswith("success_")]
        prompts = [i for i in insights if i.category.startswith("prompt_")]

        # Proposal 1: Add critical rule for repeated errors
        if len(errors) >= 2:
            error_texts = [e.text for e in errors[:3]]
            proposals.append(Proposal(
                id=_pid(), category="critical_rule",
                title=f"Add rules for {len(errors)} detected error patterns",
                description="Repeated errors suggest missing guardrails.",
                after=f"CRITICAL: Avoid these patterns — {'; '.join(error_texts)}",
                confidence=0.75,
                evidence=error_texts[:2],
            ))

        # Proposal 2: Reward successful patterns
        if successes and score >= 8:
            proposals.append(Proposal(
                id=_pid(), category="critical_rule",
                title="Reinforce successful patterns",
                description="The agent produced high-quality output using these approaches.",
                after=f"PREFER: {successes[0].text}",
                confidence=0.7,
                evidence=[s.text for s in successes[:2]],
            ))

        # Proposal 3: Prompt improvements if coder misunderstood
        misunderstood = [p for p in prompts if "misunderstood" in p.category]
        if misunderstood:
            proposals.append(Proposal(
                id=_pid(), category="system_prompt",
                title="Improve coder prompt for better understanding",
                description="Coder sometimes misunderstood the task. "
                            "Adding clarification rules may help.",
                after=(
                    "When the task is ambiguous, ask ONE clarifying question "
                    "before generating code. Do not assume."
                ),
                confidence=0.65,
                evidence=[m.text for m in misunderstood[:2]],
            ))

        # Proposal 4: Skill suggestion for repeated task types
        if score >= 7 and "API" in task.upper():
            proposals.append(Proposal(
                id=_pid(), category="skill",
                title="Create a reusable skill for API development",
                description="High-scoring API tasks suggest this is a common pattern. "
                            "Save as a skill for faster future iterations.",
                after=f"Skill: api-development — covers {task[:60]}",
                confidence=0.6,
            ))

        # Proposal 5: Tool usage suggestion based on task content
        if re.search(r"(?i)(?:test|pytest|unittest|spec)", task):
            proposals.append(Proposal(
                id=_pid(), category="tool_usage",
                title="Use test-driven workflow for test-heavy tasks",
                description="Task mentions testing — TDD approach may yield better results.",
                after="Run /orchestrate --team advanced for test-heavy tasks",
                confidence=0.55,
            ))

        return proposals

    # ── Apply ─────────────────────────────────────────────

    def apply(self, proposal: Proposal) -> bool:
        """Apply an approved proposal. Versions the previous state."""
        if proposal.applied:
            return False

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        if proposal.category == "critical_rule":
            self._version_and_apply("critical_rules", proposal.after, ts)
        elif proposal.category == "system_prompt":
            self._version_and_apply("system_prompt_additions", proposal.after, ts)
        elif proposal.category == "skill":
            self._save_skill_suggestion(proposal, ts)
        elif proposal.category == "tool_usage":
            self._save_tool_pattern(proposal, ts)

        proposal.applied = True
        proposal.applied_at = datetime.now(timezone.utc).isoformat()
        return True

    def _version_and_apply(self, key: str, value: str, ts: str) -> None:
        """Save current version, then write new value."""
        current_file = self._improve_dir / f"{key}.json"
        versions_dir = self._improve_dir / "versions"
        versions_dir.mkdir(exist_ok=True)

        # Version the current state
        if current_file.exists():
            import shutil
            shutil.copy2(current_file, versions_dir / f"{key}_{ts}.json")

        # Write new
        data = {"value": value, "updated_at": ts, "applied": True}
        current_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def _save_skill_suggestion(self, proposal: Proposal, ts: str) -> None:
        skills_file = self._improve_dir / "suggested_skills.jsonl"
        entry = {
            "title": proposal.title,
            "description": proposal.description,
            "content": proposal.after,
            "confidence": proposal.confidence,
            "suggested_at": ts,
        }
        with open(skills_file, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _save_tool_pattern(self, proposal: Proposal, ts: str) -> None:
        tools_file = self._improve_dir / "tool_patterns.jsonl"
        entry = {
            "pattern": proposal.after,
            "description": proposal.description,
            "confidence": proposal.confidence,
            "suggested_at": ts,
        }
        with open(tools_file, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── History ───────────────────────────────────────────

    def list_improvements(self, limit: int = 10) -> list[dict]:
        """List applied improvements."""
        results = []
        for f in sorted(self._improve_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text())
                data["source_file"] = f.name
                results.append(data)
            except Exception:
                pass
        return results[:limit]

    def list_pending_proposals(self) -> list[Proposal]:
        """List proposals from the most recent report that haven't been applied."""
        reports = sorted(
            self._improve_dir.glob("report_*.json"), reverse=True,
        )
        if not reports:
            return []
        try:
            data = json.loads(reports[0].read_text())
            return [
                Proposal(**p) for p in data.get("proposals", [])
                if not p.get("applied")
            ]
        except Exception:
            return []

    # ── Persistence ───────────────────────────────────────

    def _save_report(self, report: ImprovementReport) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._improve_dir / f"report_{ts}.json"
        data = {
            "project_id": report.project_id,
            "analyzed_at": report.analyzed_at,
            "lessons_learned": report.lessons_learned,
            "stats": report.stats,
            "proposals": [
                {
                    "id": p.id, "category": p.category,
                    "title": p.title, "description": p.description,
                    "after": p.after, "confidence": p.confidence,
                    "evidence": p.evidence, "applied": p.applied,
                    "applied_at": p.applied_at,
                }
                for p in report.proposals
            ],
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        logger.info(f"Improvement report saved → {path}")


# ── Helpers ────────────────────────────────────────────────

def _pid() -> str:
    import uuid
    return f"prop_{uuid.uuid4().hex[:8]}"


# ═══════════════════════════════════════════════════════════════
# Module-level API
# ═══════════════════════════════════════════════════════════════

def analyze_and_propose(workflow_state: Any) -> ImprovementReport | None:
    """Run self-improvement analysis on a completed workflow."""
    try:
        from projects.project_context import get_current_project_id
        pid = get_current_project_id()
        if not pid:
            return None
        si = SelfImprover(pid)
        return si.analyze_trajectory(workflow_state)
    except Exception as e:
        logger.warning(f"Self-improvement analysis failed: {e}")
        return None


def force_improve_analysis() -> ImprovementReport | None:
    """Run improvement analysis on the current project (no workflow required).
    Uses the most recent workflow data if available.
    """
    try:
        from projects.project_context import get_current_project_id
        pid = get_current_project_id()
        if not pid:
            return None
        si = SelfImprover(pid)

        # Try to load most recent workflow state
        latest = si._improve_dir.parent / "workflows" / "snapshot.json"
        if latest.exists():
            data = json.loads(latest.read_text())
            from code_workflow import WorkflowState
            wf = WorkflowState(
                task_id=data.get("task_id", "unknown"),
                task=data.get("task", "no task"),
                status="completed",
                best_score=data.get("best_score", 0),
                stages=[],  # simplified
            )
            return si.analyze_trajectory(wf)
        return None
    except Exception as e:
        logger.warning(f"Force improve failed: {e}")
        return None
