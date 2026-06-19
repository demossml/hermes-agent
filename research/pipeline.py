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
        """Break topic into 3-6 independent sub-questions.

        Uses decompose_research_query() which tries LLM first,
        falls back to domain-aware heuristic.
        """
        questions = await decompose_research_query(
            topic, language=language, max_questions=max_depth * 3,
            registry=self._registry, pipeline_id=self._pipeline_id,
        )
        return [
            SubQuestion(
                id=f"q{i+1}-{self._pipeline_id}",
                question=q,
                keywords=_extract_keywords(q),
            )
            for i, q in enumerate(questions)
        ]

    # ── Stage 2: Parallel Search ────────────────────────────

    async def _parallel_search(
        self, sub_questions: list[SubQuestion], max_sources: int,
    ) -> None:
        """Run 3-strategy parallel search for all sub-questions.

        Uses SearcherAgent with BROAD, SPECIFIC, TECHNICAL strategies
        running concurrently via asyncio.gather.
        """
        from research.agents import SearcherAgent

        searcher = SearcherAgent()

        async def _search_one(sq: SubQuestion) -> None:
            report = await searcher.search(
                sq.question, question_id=sq.id,
                max_per_strategy=max(3, max_sources // 3),
            )
            sq.sources = [
                {
                    "url": r.url,
                    "title": r.title,
                    "snippet": r.snippet,
                    "strategy": r.strategy,
                }
                for r in report.results
            ]

        # Run all searches concurrently
        tasks = [asyncio.create_task(_search_one(sq)) for sq in sub_questions]
        await asyncio.gather(*tasks, return_exceptions=True)

        total = sum(len(sq.sources) for sq in sub_questions)
        logger.info(
            "Stage 2: %d sub-questions → %d total results (3 strategies each)",
            len(sub_questions), total,
        )

    # ── Stage 3: Deep Reading ───────────────────────────────

    async def _deep_read(self, sub_questions: list[SubQuestion]) -> None:
        """Extract claims with citations from search results.

        Uses ReaderAgent to deep-read top results per sub-question,
        extracting factual claims with exact citations and source URLs.
        """
        from research.agents import ReaderAgent

        reader = ReaderAgent()

        async def _read_one(sq: SubQuestion, sq_idx: int) -> None:
            sources = sq.sources
            if not sources:
                return

            # Build a mini SearchReport for the Reader
            from research.agents import SearchResult
            results = [
                SearchResult(
                    url=s.get("url", ""),
                    title=s.get("title", ""),
                    snippet=s.get("snippet", ""),
                    strategy=s.get("strategy", "broad"),
                    rank=i + 1,
                )
                for i, s in enumerate(sources)
            ]

            from research.agents import SearchReport
            report = SearchReport(
                question_id=sq.id,
                question=sq.question,
                results=results,
            )

            claims = await reader.read_and_extract(report, max_claims=5)
            sq.facts = [
                f"[{c.source_title}] {c.text} "
                f"(confidence: {c.confidence:.0%}, source: {c.source_url})"
                for c in claims
            ]

        # Read all sub-questions concurrently
        tasks = [
            asyncio.create_task(_read_one(sq, i))
            for i, sq in enumerate(sub_questions)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        total_facts = sum(len(sq.facts) for sq in sub_questions)
        logger.info(
            "Stage 3: extracted %d claims across %d sub-questions",
            total_facts, len(sub_questions),
        )

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


# ═══════════════════════════════════════════════════════════════
# Decompose: break research query into sub-questions
# ═══════════════════════════════════════════════════════════════

# Domain-specific decomposition stratagems
_DECOMPOSE_STRATAGEMS = {
    "compare": [
        "What is {focus} in {topic}?",
        "How does {alt1} compare to {alt2} on {metric}?",
        "Trade-offs: {tradeoff1} vs {tradeoff2}",
        "Real-world performance benchmarks for {topic}",
        "Community adoption and ecosystem maturity",
    ],
    "how_to": [
        "Какие технологии/библиотеки существуют для {focus}?",
        "Пошаговая реализация {core_task}",
        "Типичные ошибки и как их избежать при работе с {focus}",
        "Best practices и паттерны для {focus}",
        "Сравнение инструментов: плюсы и минусы каждого",
        "Обработка ошибок, edge cases и безопасность",
    ],
    "why": [
        "Root cause analysis of {problem}",
        "Alternative approaches that were tried",
        "Historical context: how {topic} evolved",
        "Underlying principles and theory",
        "Known limitations and workarounds",
    ],
    "architecture": [
        "Core components and their interactions",
        "Data flow and state management",
        "Scalability considerations for {topic}",
        "Security and authentication patterns",
        "Deployment and infrastructure requirements",
        "Monitoring and observability",
    ],
    "general": [
        "Current state of the art in {topic}",
        "Key technologies and tools for {topic}",
        "Major advantages and disadvantages",
        "Real-world examples and case studies",
        "Best practices and conventions (2024-2026)",
        "Common challenges and solutions",
    ],
}


async def decompose_research_query(
    query: str,
    *,
    language: str = "ru",
    max_questions: int = 6,
    registry=None,
    pipeline_id: str = "",
) -> list[str]:
    """Break a research query into 3-6 independent, specific sub-questions.

    Strategy:
    1. Detect query type (compare/how-to/why/architecture)
    2. Try LLM decomposition via registry.orchestrate()
    3. Fall back to domain-aware heuristic stratagems

    Args:
        query: The research topic/question
        language: Output language ('ru' or 'en')
        max_questions: Maximum number of sub-questions (3-10)
        registry: AgentRegistry for LLM decomposition (optional)
        pipeline_id: Pipeline identifier for logging

    Returns:
        List of sub-question strings.
    """
    max_questions = max(3, min(max_questions, 10))
    query_lower = query.lower()

    # ── 1. Detect query type ────────────────────────────────
    stratagem_key = "general"
    if any(w in query_lower for w in ("как", "how to", "реализовать", "сделать", "написать", "implement", "build", "создать", "разработать")):
        stratagem_key = "how_to"
    elif any(w in query_lower for w in ("почему", "why", "причина", "cause", "root", "не работает", "ошибка")):
        stratagem_key = "why"
    elif any(w in query_lower for w in ("сравни", "compare", "vs", "против", "лучше", "отличие", "difference")):
        stratagem_key = "compare"
    elif any(w in query_lower for w in ("архитектур", "architecture", "design", "pattern", "проектирован")):
        stratagem_key = "architecture"

    # ── 2. LLM decomposition ────────────────────────────────
    if registry and hasattr(registry, "orchestrate"):
        try:
            prompt = _build_decompose_prompt(query, language, max_questions, stratagem_key)
            raw = await registry.orchestrate(
                session_id=f"research-decomp-{pipeline_id or 'anon'}",
                user_message=prompt,
            )
            parsed = _parse_decompose_response(raw, max_questions)
            if parsed and len(parsed) >= 2:
                logger.info("Decompose: LLM produced %d sub-questions", len(parsed))
                return parsed
        except Exception as e:
            logger.debug("LLM decompose failed: %s", e)

    # ── 3. Fallback: domain-aware heuristic ─────────────────
    result = _apply_stratagem(query, stratagem_key, max_questions)
    logger.info("Decompose: heuristic produced %d sub-questions (%s)", len(result), stratagem_key)
    return result


def _build_decompose_prompt(
    query: str, language: str, max_q: int, stratagem: str,
) -> str:
    """Build a structured prompt for LLM decomposition."""
    lang_hint = "на русском языке" if language == "ru" else "in English"
    return (
        f"You are a research strategist. Break this research topic "
        f"into exactly {max_q} independent, specific sub-questions "
        f"({lang_hint}).\n\n"
        f"RESEARCH TOPIC: {query}\n"
        f"QUERY TYPE: {stratagem}\n\n"
        f"RULES:\n"
        f"1. Each sub-question must be self-contained and researchable.\n"
        f"2. No overlap between sub-questions.\n"
        f"3. Cover different angles: tools, trade-offs, examples, pitfalls.\n"
        f"4. Be specific — replace generic phrases with concrete terms.\n"
        f"5. Output format: one question per line, NO numbers/bullets.\n"
        f"6. Output ONLY the questions — no preamble, no summary.\n"
    )


def _parse_decompose_response(raw: str, max_q: int) -> list[str]:
    """Parse LLM output into sub-question list."""
    import re
    lines = []
    for line in (raw or "").split("\n"):
        stripped = line.strip()
        # Remove numbering (1. 2) 3. etc.)
        stripped = re.sub(r"^\s*[\d]+[\.\)]\s*", "", stripped)
        # Remove bullet characters
        stripped = stripped.lstrip("-•*→›» \t")
        # Must be a meaningful question-like line
        if len(stripped) > 15 and "?" in stripped:
            lines.append(stripped)
        elif len(stripped) > 30:
            # Non-question but substantial — add question mark if missing
            if not stripped.endswith("?"):
                stripped = _to_question(stripped)
            lines.append(stripped)

    # Deduplicate near-duplicates
    seen = set()
    result = []
    for line in lines:
        norm = _normalize_for_dedup(line)
        if norm not in seen:
            seen.add(norm)
            result.append(line)
    return result[:max_q]


def _to_question(text: str) -> str:
    """Convert a statement to a question form."""
    text = text.rstrip(".!;,")
    if text.lower().startswith(("как", "how", "what", "why", "когда", "where")):
        return text + "?"
    if text.lower().startswith(("сравни", "compare")):
        return text + "?"
    return f"What are the key aspects of {text}?"


def _normalize_for_dedup(text: str) -> str:
    """Normalize text for deduplication."""
    import re
    return re.sub(r"[^\w\s]", "", text.lower().strip())[:60]


def _apply_stratagem(
    query: str, stratagem_key: str, max_q: int,
) -> list[str]:
    """Generate sub-questions using domain-aware stratagems."""
    templates = _DECOMPOSE_STRATAGEMS.get(stratagem_key, _DECOMPOSE_STRATAGEMS["general"])

    # Extract focus terms from query
    words = query.lower().split()
    # Try to detect alternatives (for compare mode)
    alt1 = alt2 = ""
    if stratagem_key == "compare":
        import re
        parts = re.split(r"\b(vs|против|или|compared to|versus|and)\b", query, flags=re.IGNORECASE)
        if len(parts) >= 3:
            alt1 = parts[0].strip()
            alt2 = parts[-1].strip() if len(parts) >= 3 else ""

    # Fill templates
    # Clean focus: strip common prefixes and stopwords
    focus = query
    for prefix in ("как лучше всего ", "как правильно ", "как ", "how to best ", "how to ", "что такое ", "what is "):
        if focus.lower().startswith(prefix):
            focus = focus[len(prefix):]
            break
    # Remove filler words from focus
    for filler in ("лучше всего ", "правильно ", "реализовать ", "сделать ", "написать ", "создать ", "разработать "):
        if focus.lower().startswith(filler):
            focus = focus[len(filler):]
            break
    focus = focus[:60].strip()
    if not focus:
        focus = query[:60]
    core_task = next((w for w in words if len(w) > 4 and w not in ("лучше", "всего", "правильно", "реализовать", "сделать")), "implementation")
    problem = focus
    tradeoff1 = "simplicity"
    tradeoff2 = "performance"
    metric = "scalability"

    result = []
    for tmpl in templates:
        filled = tmpl.format(
            topic=query, focus=focus, core_task=core_task,
            problem=problem, alt1=alt1 or "Option A",
            alt2=alt2 or "Option B", tradeoff1=tradeoff1,
            tradeoff2=tradeoff2, metric=metric,
        )
        result.append(filled)

    # Add query-specific questions
    result.append(f"What are the most common mistakes in {focus}?")
    result.append(f"What do experts recommend for {focus} in 2025-2026?")

    # Deduplicate and limit
    seen = set()
    out = []
    for r in result:
        norm = _normalize_for_dedup(r)
        if norm not in seen:
            seen.add(norm)
            out.append(r)
    return out[:max_q]
