"""
Synthesizer — Stage 5 of Research Pipeline.

Produces a structured, evidence-backed Markdown report from
validated claims, with source tables, confidence scores,
and actionable conclusions.  Saves to project directory.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Synthesizer
# ═══════════════════════════════════════════════════════════════


class Synthesizer:
    """Assembles validated claims into a structured research report.

    Produces:
    - Executive summary with confidence + source count
    - Sections per sub-question with evidence chains
    - Source table (URL, title, confidence contribution)
    - Conclusions with actionable recommendations
    - Saved to project's research/<slug>.md
    """

    def __init__(self):
        pass

    # ── Public API ──────────────────────────────────────────

    async def synthesize(
        self,
        topic: str,
        sub_questions: list[Any],   # SubQuestion objects
        confidence: float,
        language: str = "ru",
    ) -> tuple[str, str]:
        """Generate full Markdown report and return (markdown, filepath).

        Returns:
            (report_text, saved_filepath) — empty path if save failed.
        """
        report = self._build_report(topic, sub_questions, confidence, language)
        filepath = self._save_to_project(topic, report)
        logger.info(
            "Synthesizer: report for '%s' → %s (%d chars)",
            topic[:60], filepath or "not saved", len(report),
        )
        return report, filepath

    # ── Report builder ──────────────────────────────────────

    def _build_report(
        self,
        topic: str,
        sub_questions: list[Any],
        confidence: float,
        language: str,
    ) -> str:
        """Build a structured Markdown report."""
        lang = "ru" if language == "ru" else "en"

        lines: list[str] = []
        lines.append(f"# Research: {topic}")
        lines.append(f"")
        lines.append(f"**Generated:** {time.strftime('%Y-%m-%d %H:%M UTC')}")
        lines.append(f"**Confidence:** {confidence:.0%} | **Sub-questions:** {len(sub_questions)}")
        lines.append(f"")

        # ── Executive Summary ────────────────────────────────
        lines.append(f"## Executive Summary")
        lines.append(f"")

        total_confirmed, total_disputed, total_rejected = self._count_claims(sub_questions)
        total_sources = self._count_sources(sub_questions)
        lines.append(
            f"This research examined **{len(sub_questions)} sub-questions** "
            f"across **{total_sources} sources**. "
            f"**{total_confirmed} claims confirmed**, "
            f"**{total_disputed} disputed**, "
            f"**{total_rejected} rejected**. "
            f"Overall confidence: **{confidence:.0%}**."
        )
        lines.append(f"")

        # ── Key Findings ─────────────────────────────────────
        lines.append(f"## Key Findings")
        lines.append(f"")
        for i, sq in enumerate(sub_questions, 1):
            confirmed = [f for f in getattr(sq, "facts", []) or []
                        if "[CONFIRMED]" in f or "[confirmed]" in f.lower()]
            if confirmed:
                best = confirmed[0]
                clean = self._clean_fact(best)
                lines.append(f"**{i}. {getattr(sq, 'question', str(sq))}**")
                lines.append(f"   → {clean}")
                lines.append(f"")

        # ── Detailed Sections ────────────────────────────────
        lines.append(f"## Detailed Analysis")
        lines.append(f"")

        for i, sq in enumerate(sub_questions, 1):
            question = getattr(sq, "question", str(sq))
            lines.append(f"### {i}. {question}")
            lines.append(f"")

            facts = getattr(sq, "facts", []) or []
            sources = getattr(sq, "sources", []) or []

            if facts:
                lines.append(f"**Claims ({len(facts)}):**")
                lines.append(f"")
                for fact in facts:
                    status_icon = self._status_icon(fact)
                    clean = self._clean_fact(fact)
                    confidence = self._extract_confidence(fact)
                    lines.append(f"- {status_icon} {clean} *(confidence: {confidence:.0%})*")
                lines.append(f"")
            else:
                lines.append(f"*No claims extracted for this sub-question.*")
                lines.append(f"")

            # Evidence chain
            if sources:
                lines.append(f"**Evidence ({len(sources)} sources):**")
                lines.append(f"")
                for s in sources[:3]:
                    url = s.get("url", "") or s.get("title", "")
                    title = s.get("title", "")[:100]
                    strategy = s.get("strategy", "")
                    lines.append(f"- [{title}]({url}) *({strategy})*")
                lines.append(f"")

        # ── Source Table ─────────────────────────────────────
        lines.append(f"## Sources")
        lines.append(f"")
        lines.append(f"| # | Source | Strategy |")
        lines.append(f"|---|--------|----------|")

        seen = set()
        for sq in sub_questions:
            for s in (getattr(sq, "sources", []) or []):
                url = s.get("url", "")
                if url and url not in seen:
                    seen.add(url)
                    title = (s.get("title", "") or url)[:80]
                    strategy = s.get("strategy", "broad")
                    lines.append(f"| {len(seen)} | [{title}]({url}) | {strategy} |")

        if not seen:
            lines.append(f"| — | No sources collected | — |")
        lines.append(f"")

        # ── Confidence Breakdown ─────────────────────────────
        lines.append(f"## Confidence Breakdown")
        lines.append(f"")
        lines.append(f"| Claim Status | Count |")
        lines.append(f"|--------------|-------|")
        lines.append(f"| ✅ Confirmed | {total_confirmed} |")
        lines.append(f"| ⚠️ Disputed   | {total_disputed} |")
        lines.append(f"| ❌ Rejected   | {total_rejected} |")
        lines.append(f"")

        # ── Conclusions & Recommendations ────────────────────
        lines.append(f"## Conclusions & Recommendations")
        lines.append(f"")

        if confidence >= 0.7:
            lines.append(
                "**High confidence.** The findings are well-supported by "
                "multiple independent sources. Recommended for decision-making."
            )
        elif confidence >= 0.4:
            lines.append(
                "**Moderate confidence.** Some claims are disputed or lack "
                "corroboration. Verify critical findings before acting."
            )
        else:
            lines.append(
                "**Low confidence.** Many claims could not be verified. "
                "Additional research recommended before any decisions."
            )
        lines.append(f"")

        # Actionable recommendations
        lines.append(f"### Recommended Actions")
        lines.append(f"")
        lines.append(f"1. Review **disputed claims** — they need human verification")
        lines.append(f"2. Cross-reference **key findings** with primary sources")
        lines.append(f"3. Re-run research with updated queries if confidence < 0.5")

        lines.append(f"")
        lines.append(f"---")
        lines.append(f"*Report generated by Hermes Research Pipeline*")

        return "\n".join(lines)

    # ── Save to project ─────────────────────────────────────

    def _save_to_project(self, topic: str, report: str) -> str:
        """Save report to project's research/ directory.

        Returns the filepath or empty string on failure.
        """
        try:
            from projects.project_context import get_current_project_id
            pid = get_current_project_id()
            if not pid:
                return ""

            from projects.project_manager import ProjectManager
            pm = ProjectManager()
            research_dir = pm._project_dir(pid) / "research"
            research_dir.mkdir(parents=True, exist_ok=True)

            # Generate safe filename
            safe_name = re.sub(r"[^a-z0-9_а-яё-]+", "_", topic.lower().strip())[:50]
            fname = f"{safe_name}.md"
            fpath = research_dir / fname

            fpath.write_text(report, encoding="utf-8")
            logger.info("Synthesizer: saved report to %s", fpath)
            return str(fpath)
        except Exception as e:
            logger.debug("Failed to save research report: %s", e)
            return ""

    # ── Helpers ─────────────────────────────────────────────

    @staticmethod
    def _clean_fact(fact: str) -> str:
        """Strip status prefix from fact string."""
        fact = re.sub(r"^\[(?:CONFIRMED|DISPUTED|REJECTED|confirmed|disputed|rejected)\]\s*",
                      "", fact, flags=re.IGNORECASE)
        return fact[:200]

    @staticmethod
    def _status_icon(fact: str) -> str:
        """Return emoji for claim status."""
        f = fact.lower()
        if "[confirmed]" in f or "[confirmed" in f:
            return "✅"
        elif "[disputed]" in f:
            return "⚠️"
        elif "[rejected]" in f:
            return "❌"
        return "•"

    @staticmethod
    def _extract_confidence(fact: str) -> float:
        """Extract confidence % from fact string."""
        m = re.search(r"confidence:\s*(\d+)%", fact)
        return int(m.group(1)) / 100 if m else 0.5

    @staticmethod
    def _count_claims(sub_questions: list[Any]) -> tuple[int, int, int]:
        """Count confirmed/disputed/rejected claims."""
        conf = disp = rej = 0
        for sq in sub_questions:
            for f in (getattr(sq, "facts", []) or []):
                fl = f.lower()
                if "[confirmed]" in fl or "[confirmed" in fl:
                    conf += 1
                elif "[disputed]" in fl:
                    disp += 1
                elif "[rejected]" in fl:
                    rej += 1
        return conf, disp, rej

    @staticmethod
    def _count_sources(sub_questions: list[Any]) -> int:
        """Count unique sources."""
        seen = set()
        for sq in sub_questions:
            for s in (getattr(sq, "sources", []) or []):
                url = s.get("url", "")
                if url:
                    seen.add(url)
        return len(seen)
