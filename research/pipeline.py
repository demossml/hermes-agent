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
    sections: list[dict[str, Any]]
    sources: list[str]
    confidence: float
    sub_questions: list[SubQuestion]
    elapsed_s: float
    status: str
    report_path: str = ""
    health: dict[str, Any] | None = None   # API health report


# ═══════════════════════════════════════════════════════════════
# Research Cache (TTL 24h)
# ═══════════════════════════════════════════════════════════════


class _ResearchCache:
    """Simple in-memory cache for research results (TTL 24h)."""

    def __init__(self, ttl_seconds: int = 86400):
        self._cache: dict[str, tuple[float, ResearchResult]] = {}
        self._ttl = ttl_seconds

    def get(self, key: str) -> ResearchResult | None:
        import time
        if key in self._cache:
            ts, result = self._cache[key]
            if time.time() - ts < self._ttl:
                return result
            del self._cache[key]
        return None

    def set(self, key: str, result: ResearchResult) -> None:
        import time
        self._cache[key] = (time.time(), result)


_research_cache = _ResearchCache(ttl_seconds=86400)


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
        force_api: bool = False,
    ) -> ResearchResult:
        """Execute the full 5-stage research pipeline.

        Features:
        - Cache check (TTL 24h) — instant return on repeated topics
        - Smart fallback — heuristic deep mode if all API calls fail
        - Health tracking — warns user about API degradation
        """
        t0 = time.time()
        health: dict[str, Any] = {
            "search_ok": 0, "search_fail": 0,
            "mode": "api", "notifications": [],
        }

        # ── Cache check ──────────────────────────────────────
        cached = _research_cache.get(topic)
        if cached and not force_api:
            cached.elapsed_s = time.time() - t0
            cached.status = "cached"
            logger.info("Research cache HIT for '%s'", topic[:60])
            return cached

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
                status="failed", report_path="",
            )

        # ── Stage 2: Parallel Search (with health tracking) ─
        await self._parallel_search(sub_questions, max_sources)
        health = self._assess_search_health(sub_questions)

        # Smart fallback: all strategies failed → heuristic deep mode
        if health["search_ok"] == 0 and health["search_fail"] > 0:
            health["mode"] = "heuristic"
            health["notifications"].append(
                "Web search временно недоступен. Использую heuristic mode. "
                "Качество ответа может быть ниже."
            )
            logger.warning("All search strategies failed — switching to heuristic deep mode")
            # Heuristic deep mode: expand decompose with local knowledge
            sub_questions = await self._heuristic_deep_mode(topic, sub_questions)
        elif health["search_fail"] > 0:
            health["notifications"].append(
                f"Часть поисковых запросов не выполнена "
                f"({health['search_fail']} из {health['search_ok'] + health['search_fail']}). "
                f"Использую доступные результаты."
            )

        # ── Stage 3: Deep Reading ───────────────────────────
        await self._deep_read(sub_questions)

        # ── Stage 4: Cross-Validation ───────────────────────
        confidence = await self._cross_validate(sub_questions)

        # ── Stage 5: Synthesize ─────────────────────────────
        result = await self._synthesize(topic, sub_questions, language, confidence)

        # Inject health notifications into the result
        if health["notifications"]:
            result.sections.insert(1, {
                "heading": "⚠️ Доступность API",
                "content": "\n".join(health["notifications"]),
                "sources": [],
            })

        result.elapsed_s = time.time() - t0
        result.status = "completed" if health["mode"] == "api" else "partial"
        result.health = health

        # ── Cache the result ─────────────────────────────────
        _research_cache.set(topic, result)

        # ── Save to project research history ─────────────────
        from research.synthesizer import Synthesizer
        report_text, _ = await Synthesizer().synthesize(
            topic, sub_questions, confidence, language,
        )
        try:
            from projects.project_context import get_current_project_id
            pid = get_current_project_id()
            if pid:
                from projects.project_manager import ProjectManager
                pm = ProjectManager()
                pm.save_research_history(pid, topic, report_text)
        except Exception:
            pass

        logger.info(
            "Research pipeline %s: done in %.1fs, confidence=%.2f, mode=%s",
            self._pipeline_id, result.elapsed_s, result.confidence, health["mode"],
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

    # ── Health + Fallback ──────────────────────────────────

    @staticmethod
    def _assess_search_health(sub_questions: list[SubQuestion]) -> dict[str, Any]:
        """Count successful vs failed search strategies."""
        ok = fail = 0
        for sq in sub_questions:
            strategies_seen = set()
            for s in (sq.sources or []):
                strategies_seen.add(s.get("strategy", "unknown"))
            ok += len(strategies_seen)
            fail += max(0, 3 - len(strategies_seen))  # 3 strategies expected
        return {"search_ok": ok, "search_fail": fail, "mode": "api", "notifications": []}

    async def _heuristic_deep_mode(
        self, topic: str, sub_questions: list[SubQuestion],
    ) -> list[SubQuestion]:
        """Expand decomposition when API is unavailable.

        Generates additional sub-questions covering angles that
        the basic heuristic might miss.
        """
        deep_angles = [
            f"Конкретные примеры и case studies: {topic}",
            f"Типичные ошибки и как их избежать: {topic}",
            f"Альтернативные подходы и компромиссы: {topic}",
        ]
        for angle in deep_angles:
            sub_questions.append(SubQuestion(
                id=f"deep-{len(sub_questions)+1}-{self._pipeline_id}",
                question=angle,
                keywords=angle.lower().split(),
            ))
        return sub_questions

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
        """Adversarial cross-validation with two validators + adjudicator.

        Validator A (Corroborator): checks if other sources support claims.
        Validator B (Contradictor): checks if any source contradicts claims.
        Adjudicator: resolves disputes with additional targeted search.

        Returns average adjusted confidence across all claims.
        """
        from research.cross_validator import (
            CrossValidator, Adjudicator,
        )

        all_scores: list[float] = []

        for sq in sub_questions:
            if not sq.sources:
                all_scores.append(0.0)
                continue

            # Build claims from facts (legacy: facts are strings)
            claims = self._facts_to_claims(sq.facts, sq.sources)

            # Run adversarial validation
            validator = CrossValidator()
            report = await validator.validate(claims, sq.sources, sq.id)

            # Adjudicate disputes
            if report.disputed > 0:
                source_pool = CrossValidator._build_source_pool(sq.sources)
                adjudicator = Adjudicator()
                disputed = [c for c in report.claims if c.status == "disputed"]
                resolved = await adjudicator.adjudicate(disputed, source_pool)

                for rc in resolved:
                    for i, oc in enumerate(report.claims):
                        if oc.text == rc.text:
                            report.claims[i] = rc
                            if rc.status == "confirmed":
                                report.confirmed += 1
                                report.disputed -= 1
                            elif rc.status == "rejected":
                                report.rejected += 1
                                report.disputed -= 1
                            break

                if report.claims:
                    report.average_confidence = (
                        sum(c.adjusted_confidence for c in report.claims)
                        / len(report.claims)
                    )

            # Update facts with validation status
            sq.facts = [
                f"[{c.status.upper()}] {c.text} "
                f"(confidence: {c.adjusted_confidence:.0%}, "
                f"corroborated: {c.corroborating_sources})"
                for c in report.claims
            ]

            all_scores.append(report.average_confidence)

        avg = sum(all_scores) / len(all_scores) if all_scores else 0.0
        logger.info(
            "Stage 4: cross-validation complete, avg confidence=%.2f", avg,
        )
        return avg

    @staticmethod
    def _facts_to_claims(
        facts: list[str], sources: list[dict[str, Any]],
    ) -> list[Any]:
        """Convert fact strings back to Claim-like objects for validation."""
        from research.agents import Claim
        claims = []
        for i, fact in enumerate(facts):
            # Extract source info from fact format: [Title] text (confidence: X%, source: URL)
            source_url = ""
            source_title = ""
            conf = 0.5
            text = fact

            import re
            m = re.match(r"\[(.*?)\]\s*(.*)", fact)
            if m:
                source_title = m.group(1)
                text = m.group(2)

            m = re.search(r"confidence:\s*(\d+)%", fact)
            if m:
                conf = int(m.group(1)) / 100

            m = re.search(r"source:\s*(\S+)", fact)
            if m:
                source_url = m.group(1)

            claims.append(Claim(
                text=text[:300],
                source_url=source_url,
                source_title=source_title,
                confidence=conf,
                citation=text[:200],
            ))
        return claims

    # ── Stage 5: Synthesize ─────────────────────────────────

    async def _synthesize(
        self,
        topic: str,
        sub_questions: list[SubQuestion],
        language: str,
        confidence: float,
    ) -> ResearchResult:
        """Produce structured Markdown report with evidence chains.

        Uses Synthesizer to:
        - Build executive summary, detailed sections, source table
        - Save report to project's research/<topic>.md
        """
        from research.synthesizer import Synthesizer

        synth = Synthesizer()
        report_text, filepath = await synth.synthesize(
            topic, sub_questions, confidence, language,
        )

        # Build sections from sub-questions for the result object
        sections = []
        all_sources: list[str] = []

        for sq in sub_questions:
            src_urls = [
                s.get("url", "") or s.get("title", "") or ""
                for s in (sq.sources or [])[:3]
                if s
            ]
            all_sources.extend(u for u in src_urls if u and u not in all_sources)

            sections.append({
                "heading": sq.question,
                "content": "\n".join(sq.facts[:5]) if sq.facts else "No data found.",
                "sources": src_urls[:3],
                "sub_question_id": sq.id,
            })

        # Build summary
        total_confirmed = sum(
            1 for sq in sub_questions
            for f in (sq.facts or [])
            if "[CONFIRMED]" in f or "[confirmed]" in f.lower()
        )
        summary = (
            f"Исследование по теме «{topic}». "
            f"Рассмотрено {len(sub_questions)} подвопросов, "
            f"проанализировано {len(all_sources)} источников. "
            f"Подтверждено утверждений: {total_confirmed}. "
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
            elapsed_s=0,
            status="completed",
            report_path=filepath,
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

# 8 domain-specific stratagem sets with 2025-2026 anchoring
_DECOMPOSE_STRATAGEMS_V2 = {
    "best_practices": {
        "templates": [
            "Best practices and conventions for {focus} in 2025-2026",
            "Common pitfalls and anti-patterns when working with {focus}",
            "Real-world production experience and case studies: {focus}",
            "Recommended tools and libraries for {focus} in production",
            "Performance and scalability considerations for {focus}",
        ],
        "triggers": ("best practice", "лучшие практик", "best way", "лучший способ",
                     "рекомендаци", "recommend", "production ready", "продакшен"),
    },
    "troubleshooting": {
        "templates": [
            "Common causes of {focus} and how to diagnose them",
            "Step-by-step solutions for {focus}",
            "Version-specific issues and compatibility for {focus}",
            "Workarounds and quick fixes for {focus}",
            "How to prevent {focus} from recurring in production",
        ],
        "triggers": ("не работает", "ошибка", "error", "fix", "исправить",
                     "починить", "debug", "troubleshoot", "проблема", "issue",
                     "bug", "fail", "crash", "broken", "сломан"),
    },
    "hypothesis": {
        "templates": [
            "Arguments supporting that {focus}",
            "Arguments against that {focus}",
            "Counter-examples and edge cases where {focus} is false",
            "Empirical evidence and data for and against {focus}",
            "Expert opinions and community consensus on {focus}",
        ],
        "triggers": ("is it true", "правда ли", "действительно ли",
                     "значительный прирост", "faster than", "быстрее чем"),
    },
    "comparison": {
        "templates": [
            "Key differences between {alt1} and {alt2} for {metric}",
            "Performance benchmarks: {alt1} vs {alt2}",
            "Developer experience and ecosystem: {alt1} vs {alt2}",
            "Production readiness and scaling: {alt1} vs {alt2}",
            "Community adoption trends 2025-2026: {alt1} vs {alt2}",
        ],
        "triggers": ("сравни", "compare", "vs", "против", "лучше", "отличие",
                     "difference", "преимуществ", "недостатк", "плюсы", "минусы"),
    },
    "how_to": {
        "templates": [
            "What technologies and libraries exist for {focus}?",
            "Step-by-step implementation guide for {focus}",
            "Common mistakes and how to avoid them: {focus}",
            "Production-ready patterns and architecture for {focus}",
            "Security, error handling and edge cases for {focus}",
        ],
        "triggers": ("как ", "how to ", "реализовать", "сделать", "написать",
                     "implement", "build", "создать", "разработать", "настроить",
                     "установить", "подключить", "задеплоить"),
    },
    "why": {
        "templates": [
            "Root causes of {focus} — technical deep dive",
            "Historical evolution: how {focus} developed over time",
            "Alternative approaches that were tried and why they failed",
            "Underlying principles and theory behind {focus}",
            "Known limitations and when NOT to use {focus}",
        ],
        "triggers": ("почему", "why", "причина", "cause", "root",
                     "из-за чего", "в чём смысл", "зачем"),
    },
    "architecture": {
        "templates": [
            "Core architectural components of {focus} and their interactions",
            "Data flow, state management, and communication patterns: {focus}",
            "Scalability and performance architecture for {focus}",
            "Security, authentication, and authorization in {focus}",
            "Deployment, infrastructure and monitoring for {focus}",
        ],
        "triggers": ("архитектур", "architecture", "design pattern", "структур",
                     "проектирован", "компонент", "микросервис", "system design"),
    },
    "general": {
        "templates": [
            "Current state of the art for {focus} in 2025-2026",
            "Key technologies and tools in the {focus} ecosystem",
            "Major advantages and trade-offs of {focus}",
            "Real-world examples, case studies, and adoption of {focus}",
            "Common challenges and solutions for {focus}",
        ],
        "triggers": (),
    },
}


_DEEP_ANGLES = {
    "best_practices": [
        "Edge cases and boundary conditions for {focus}",
        "Migration and upgrade strategies for {focus}",
        "Testing and quality assurance for {focus}",
    ],
    "troubleshooting": [
        "Log analysis and monitoring for {focus}",
        "Incident response and rollback for {focus}",
        "Long-term prevention strategies for {focus}",
    ],
    "comparison": [
        "Total cost of ownership: {alt1} vs {alt2}",
        "Hiring and talent availability: {alt1} vs {alt2}",
        "Future roadmap and long-term viability: {alt1} vs {alt2}",
    ],
    "how_to": [
        "Testing and CI/CD integration for {focus}",
        "Monitoring and observability for {focus}",
        "Documentation and knowledge sharing for {focus}",
    ],
    "why": [
        "Academic research and papers on {focus}",
        "Industry case studies: {focus} in practice",
        "Future evolution and predictions for {focus}",
    ],
    "architecture": [
        "Cost optimization and resource planning for {focus}",
        "Disaster recovery and high availability for {focus}",
        "Multi-region and edge deployment for {focus}",
    ],
    "hypothesis": [
        "Controlled experiments testing {focus}",
        "Statistical significance of claims about {focus}",
        "Replication studies and meta-analyses of {focus}",
    ],
    "general": [
        "Competitive landscape and alternatives to {focus}",
        "Regulatory and compliance considerations for {focus}",
        "Community and ecosystem health for {focus}",
    ],
}


async def decompose_research_query(
    query: str,
    *,
    language: str = "ru",
    max_questions: int = 6,
    depth: str = "normal",
    registry=None,
    pipeline_id: str = "",
) -> list[str]:
    """Break a research query into 4-6 context-aware sub-questions.

    Features:
    - 8 domain types with specialized templates
    - Relevance scoring (keyword match count)
    - Deduplication with normalized keys
    - --depth high adds +3 deeper questions
    - LLM decompose → domain-aware heuristic fallback
    """
    max_questions = max(4, min(max_questions, 8))
    query_lower = query.lower()

    # ── 1. Detect domain type (8 types, best match first) ────
    domain = _detect_domain(query_lower)

    # ── 2. LLM decomposition ────────────────────────────────
    if registry and hasattr(registry, "orchestrate"):
        try:
            prompt = _build_decompose_prompt_v2(query, language, max_questions, domain)
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

    # ── 3. Domain-aware heuristic with relevance scoring ─────
    result = _apply_stratagem_v2(query, domain, max_questions)

    # ── 4. Deep mode: add specialized angles ─────────────────
    if depth == "high" and len(result) < max_questions + 3:
        deep = _DEEP_ANGLES.get(domain, _DEEP_ANGLES["general"])
        focus, alt1, alt2 = _extract_focus_v2(query, domain)
        for tmpl in deep:
            filled = tmpl.format(focus=focus, alt1=alt1, alt2=alt2)
            if filled not in result:
                result.append(filled)
            if len(result) >= max_questions + 3:
                break

    logger.info("Decompose: heuristic produced %d sub-questions (%s, depth=%s)",
                len(result), domain, depth)
    return result[:max_questions + 3]


_DOMAIN_SIGNAL_WEIGHTS = {
    # Strong signals — override generic words
    "почему": 50, "why": 50, "зачем": 40, "root cause": 40,
    "не работает": 40, "error": 35, "fix": 35, "bug": 35,
    "is it true": 45, "правда ли": 45, "гипотез": 45,
    "сравни": 30, "compare": 30, "vs": 35, "лучше": 25,
    "best practice": 35, "лучшие практик": 35, "best way": 30,
    "архитектур": 30, "architecture": 30, "system design": 35, "проектирован": 30,
    "как ": 20, "how to ": 20, "implement": 25,
}


def _detect_domain(query_lower: str) -> str:
    """Detect research domain by keyword scoring (best match wins).

    Uses weighted keyword matching — strong signals ('почему', 'is it true')
    get higher weight than generic words ('архитектур').
    """
    best_domain = "general"
    best_score = 0
    for domain, config in _DECOMPOSE_STRATAGEMS_V2.items():
        score = 0
        for t in config["triggers"]:
            if t in query_lower:
                w = _DOMAIN_SIGNAL_WEIGHTS.get(t, len(t))
                score += w
        if score > best_score:
            best_score = score
            best_domain = domain
    return best_domain


def _extract_focus_v2(query: str, domain: str) -> tuple[str, str, str]:
    """Extract focus, alt1, alt2 from query based on domain."""
    import re
    focus = query
    # Strip common prefixes
    for pfx in ("как лучше всего ", "как правильно ", "как ", "how to best ",
                "how to ", "что такое ", "what is ", "почему ", "why "):
        if focus.lower().startswith(pfx):
            focus = focus[len(pfx):]
            break
    # Strip filler words
    for filler in ("лучше всего ", "правильно ", "реализовать ", "сделать ",
                   "написать ", "создать ", "разработать ", "исправить "):
        if focus.lower().startswith(filler):
            focus = focus[len(filler):]
            break

    alt1 = alt2 = ""
    if domain == "comparison":
        parts = re.split(r"\b(vs|против|или|compared to|versus|and)\b", query, flags=re.IGNORECASE)
        if len(parts) >= 3:
            alt1 = parts[0].strip()
            alt2 = parts[-1].strip()

    return focus[:60].strip(), alt1, alt2


def _apply_stratagem_v2(
    query: str, domain: str, max_q: int,
) -> list[str]:
    """Generate scored, deduplicated sub-questions."""
    config = _DECOMPOSE_STRATAGEMS_V2.get(domain, _DECOMPOSE_STRATAGEMS_V2["general"])
    focus, alt1, alt2 = _extract_focus_v2(query, domain)
    metric = "performance"

    # Fill templates
    scored: list[tuple[int, str]] = []
    for tmpl in config["templates"]:
        filled = tmpl.format(focus=focus, alt1=alt1 or "Option A",
                             alt2=alt2 or "Option B", metric=metric)
        # Score: how many query keywords appear in the filled question
        score = sum(1 for w in focus.lower().split() if len(w) > 3 and w in filled.lower())
        scored.append((score, filled))

    # Sort by relevance (higher score = more relevant)
    scored.sort(reverse=True)

    # Deduplicate and limit
    seen = set()
    result = []
    for score, q in scored:
        norm = _normalize_for_dedup(q)
        if norm not in seen:
            seen.add(norm)
            result.append(q)
        if len(result) >= max_q:
            break
    return result


def _build_decompose_prompt_v2(
    query: str, language: str, max_q: int, domain: str,
) -> str:
    """Build domain-specific prompt for LLM decomposition."""
    lang_hint = "на русском языке" if language == "ru" else "in English"
    domain_hints = {
        "best_practices": "Focus on conventions, production experience, pitfalls.",
        "troubleshooting": "Focus on causes, solutions, version-specific issues.",
        "hypothesis": "Focus on evidence, counter-arguments, data.",
        "comparison": "Focus on benchmarks, trade-offs, ecosystem differences.",
        "how_to": "Focus on implementation steps, tools, patterns.",
        "why": "Focus on root causes, principles, historical context.",
        "architecture": "Focus on components, data flow, infrastructure.",
    }
    hint = domain_hints.get(domain, "Cover different angles comprehensively.")
    return (
        f"Research domain: {domain}. {hint}\n"
        f"Break into exactly {max_q} self-contained sub-questions ({lang_hint}).\n\n"
        f"TOPIC: {query}\n\n"
        f"Output: one question per line, NO numbering. Questions ONLY."
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


def _to_question(text: str) -> str:
    """Convert a statement to a question form."""
    text = text.rstrip(".!;,")
    if text.lower().startswith(("как", "how", "what", "why", "когда", "where")):
        return text + "?"
    if text.lower().startswith(("сравни", "compare")):
        return text + "?"
    return f"What are the key aspects of {text}?"


def __apply_stratagem_legacy__(
    query: str, stratagem_key: str, max_q: int,
) -> list[str]:
    """REMOVED — replaced by _apply_stratagem_v2"""
    return []


def _to_question(text: str) -> str:

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
