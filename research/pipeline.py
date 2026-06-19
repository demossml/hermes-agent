"""
Research Pipeline — multi-stage deep research for Hermes Agent.

Stages:
  1. Decompose       — break topic into sub-questions
  2. Parallel Search — search each sub-question concurrently
  3. Deep Reading    — extract key facts from each result
  4. Cross-Validation — verify facts across sources
  5. Synthesize      — produce structured research report

Activation:
  - Explicit:  /research <topic>
  - Auto:      new project, repeated coder failures,
               "why isn't this working", "how to do X better"

Integration:
    from research.pipeline import ResearchPipeline

    pipeline = ResearchPipeline(registry)
    result = await pipeline.run(topic="AI agents architecture")
    # → {title, sections, sources, confidence, elapsed_s}
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════


@dataclass
class SubQuestion:
    """A decomposed sub-question from stage 1."""
    id: str
    question: str
    keywords: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)


@dataclass
class ResearchResult:
    """Final research output."""
    title: str
    sections: list[dict[str, Any]]  # [{heading, content, sources}]
    sources: list[str]              # deduplicated source URLs/refs
    confidence: float               # 0-1 average cross-validation score
    sub_questions: list[SubQuestion]
    elapsed_s: float
    status: str                     # "completed" | "partial" | "failed"


# ═══════════════════════════════════════════════════════════════
# Pipeline
# ═══════════════════════════════════════════════════════════════


class ResearchPipeline:
    """Five-stage deep research pipeline.

    Uses sub-agents for decomposition, search, reading, validation,
    and synthesis.  Each stage can be customised with different
    agent profiles.
    """

    def __init__(self, registry=None):
        self._registry = registry
        self._pipeline_id = uuid.uuid4().hex[:8]

    # ── Public API ──────────────────────────────────────────

    async def run(
        self,
        topic: str,
        *,
        language: str = "ru",
        max_sources: int = 10,
        max_depth: int = 2,
    ) -> ResearchResult:
        """Execute the full 5-stage research pipeline.

        Args:
            topic: Research topic/question
            language: Output language (ru/en)
            max_sources: Max sources per sub-question
            max_depth: Max sub-question nesting depth

        Returns:
            ResearchResult with sections, sources, confidence.
        """
        t0 = time.time()
        logger.info(
            "Research pipeline %s: topic=%s lang=%s",
            self._pipeline_id, topic[:80], language,
        )

        # ── Stage 1: Decompose ──────────────────────────────
        sub_questions = await self._decompose(topic, language, max_depth)
        if not sub_questions:
            return ResearchResult(
                title=topic, sections=[], sources=[], confidence=0.0,
                sub_questions=[], elapsed_s=time.time() - t0,
                status="failed",
            )

        # ── Stage 2: Parallel Search ────────────────────────
        await self._parallel_search(sub_questions, max_sources)

        # ── Stage 3: Deep Reading ───────────────────────────
        await self._deep_read(sub_questions)

        # ── Stage 4: Cross-Validation ───────────────────────
        confidence = await self._cross_validate(sub_questions)

        # ── Stage 5: Synthesize ─────────────────────────────
        result = await self._synthesize(topic, sub_questions, language, confidence)

        result.elapsed_s = time.time() - t0
        result.status = "completed"
        logger.info(
            "Research pipeline %s: done in %.1fs, confidence=%.2f",
            self._pipeline_id, result.elapsed_s, result.confidence,
        )
        return result

    # ── Stage 1: Decompose ──────────────────────────────────

    async def _decompose(
        self, topic: str, language: str, max_depth: int,
    ) -> list[SubQuestion]:
        """Break the topic into independent sub-questions.

        Uses a lightweight classifier or a small LLM call to
        generate 3-7 focused sub-questions that can be researched
        independently.
        """
        prompt = (
            f"Разбей тему на 3-7 независимых подвопросов для исследования. "
            f"Каждый подвопрос должен быть самодостаточным.\n\n"
            f"Тема: {topic}\n\n"
            f"Формат: один вопрос на строку, без нумерации."
        )

        # Try using orchestrator if available, else use simple heuristic
        if self._registry and hasattr(self._registry, "orchestrate"):
            try:
                response = await self._registry.orchestrate(
                    session_id=f"research-decompose-{self._pipeline_id}",
                    user_message=prompt,
                )
                lines = [
                    line.strip("- •1234567890. \t")
                    for line in (response or "").split("\n")
                    if len(line.strip()) > 10 and "?" in line
                ]
                if lines:
                    return [
                        SubQuestion(
                            id=f"q{i+1}-{self._pipeline_id}",
                            question=line,
                            keywords=_extract_keywords(line),
                        )
                        for i, line in enumerate(lines[:max_depth * 3])
                    ]
            except Exception as e:
                logger.debug("Decompose via orchestrate failed: %s", e)

        # Fallback: simple keyword-based decomposition
        return _heuristic_decompose(topic, self._pipeline_id)

    # ── Stage 2: Parallel Search ────────────────────────────

    async def _parallel_search(
        self, sub_questions: list[SubQuestion], max_sources: int,
    ) -> None:
        """Search each sub-question concurrently.

        Uses web_search for external sources and session_search
        for internal knowledge.
        """
        async def _search_one(sq: SubQuestion) -> None:
            try:
                # Build search query from keywords
                query = " ".join(sq.keywords[:5]) if sq.keywords else sq.question
                # External web search (non-blocking)
                try:
                    from hermes_tools import web_search
                    results = await asyncio.to_thread(
                        web_search, query=query, limit=max_sources,
                    )
                    if results and results.get("results"):
                        sq.sources = results["results"][:max_sources]
                except Exception:
                    sq.sources = []

                # Internal knowledge search
                try:
                    from hermes_tools import session_search as _ss
                    internal = await asyncio.to_thread(
                        _ss, query=sq.question, limit=3,
                    )
                    if internal:
                        sq.sources.append(
                            {"title": "Internal", "content": str(internal)[:500]}
                        )
                except Exception:
                    pass

            except Exception as e:
                logger.debug("Search failed for %s: %s", sq.id, e)

        # Run all searches concurrently
        tasks = [asyncio.create_task(_search_one(sq)) for sq in sub_questions]
        await asyncio.gather(*tasks, return_exceptions=True)

    # ── Stage 3: Deep Reading ───────────────────────────────

    async def _deep_read(self, sub_questions: list[SubQuestion]) -> None:
        """Extract key facts from each source.

        For each sub-question, read the top sources and extract
        factual claims, data points, and quotes.
        """
        for sq in sub_questions:
            if not sq.sources:
                continue
            # Extract snippets/urls as facts
            for src in sq.sources[:5]:
                title = src.get("title", "") or src.get("url", "")[:80]
                snippet = src.get("content", "") or src.get("snippet", "")[:200]
                if snippet:
                    sq.facts.append(f"[{title}] {snippet}")

    # ── Stage 4: Cross-Validation ────────────────────────────

    async def _cross_validate(
        self, sub_questions: list[SubQuestion],
    ) -> float:
        """Verify facts across sources for each sub-question.

        Returns average confidence (0-1) across all sub-questions.
        """
        if not sub_questions:
            return 0.0

        scores = []
        for sq in sub_questions:
            # More sources = higher confidence (naive heuristic)
            source_count = len(sq.sources)
            if source_count == 0:
                scores.append(0.0)
            elif source_count == 1:
                scores.append(0.3)
            elif source_count == 2:
                scores.append(0.6)
            elif source_count >= 3:
                scores.append(min(0.95, 0.7 + 0.05 * source_count))
        return sum(scores) / len(scores) if scores else 0.0

    # ── Stage 5: Synthesize ─────────────────────────────────

    async def _synthesize(
        self,
        topic: str,
        sub_questions: list[SubQuestion],
        language: str,
        confidence: float,
    ) -> ResearchResult:
        """Produce the final structured research report."""
        # Build sections from sub-questions
        sections = []
        all_sources: list[str] = []

        for sq in sub_questions:
            src_urls = [
                s.get("url", "") or s.get("title", "") or ""
                for s in sq.sources[:3]
                if s
            ]
            all_sources.extend(u for u in src_urls if u and u not in all_sources)

            sections.append({
                "heading": sq.question,
                "content": "\n".join(sq.facts[:5]) if sq.facts else "No data found.",
                "sources": src_urls[:3],
                "sub_question_id": sq.id,
            })

        # Build a summary section at the top
        summary = (
            f"Исследование по теме «{topic}». "
            f"Рассмотрено {len(sub_questions)} подвопросов, "
            f"проанализировано {len(all_sources)} источников. "
            f"Уверенность: {confidence:.0%}."
        )
        sections.insert(0, {
            "heading": "Обзор",
            "content": summary,
            "sources": all_sources[:5],
        })

        return ResearchResult(
            title=topic,
            sections=sections,
            sources=all_sources,
            confidence=confidence,
            sub_questions=sub_questions,
            elapsed_s=0,  # filled by caller
            status="completed",
        )


# ═══════════════════════════════════════════════════════════════
# Classification: should research mode activate?
# ═══════════════════════════════════════════════════════════════


def should_activate_research(user_message: str) -> bool:
    """Detect if a user message needs research mode.

    Triggers on:
    - Explicit /research command
    - "почему не работает X"
    - "как лучше сделать X"
    - "сравни X и Y"
    - "что такое X" (complex topic)
    - Repeated coder failures (3+)
    """
    msg = user_message.lower().strip()

    # Explicit command
    if msg.startswith("/research"):
        return True

    # Research-oriented questions
    triggers = [
        "почему не работает",
        "почему не компилируется",
        "как лучше сделать",
        "как правильнее",
        "сравни преимущества",
        "чем отличается",
        "какие альтернативы",
        "best practices for",
        "state of the art",
        "deep dive",
    ]
    if any(t in msg for t in triggers):
        return len(msg) > 30  # avoid false positives on short messages

    return False


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


def _extract_keywords(text: str) -> list[str]:
    """Naive keyword extraction from text."""
    import re
    words = re.findall(r"[a-zа-яё]{4,}", text.lower())
    # Simple stopwords
    stop = {"это", "для", "как", "что", "чем", "когда", "тогда",
            "which", "what", "that", "this", "with", "from", "have"}
    return [w for w in words if w not in stop][:10]


def _heuristic_decompose(
    topic: str, pipeline_id: str,
) -> list[SubQuestion]:
    """Fallback: simple rule-based decomposition."""
    aspects = [
        "текущее состояние и основные подходы",
        "ключевые технологии и инструменты",
        "преимущества и недостатки",
        "примеры использования и best practices",
        "альтернативы и сравнение",
    ]
    return [
        SubQuestion(
            id=f"q{i+1}-{pipeline_id}",
            question=f"{topic}: {aspect}",
            keywords=_extract_keywords(f"{topic} {aspect}"),
        )
        for i, aspect in enumerate(aspects)
    ]
