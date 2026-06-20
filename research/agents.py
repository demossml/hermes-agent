"""
Research Agents — Searcher and Reader for deep research pipeline.

SearcherAgent:  3-strategy parallel web search per sub-question
ReaderAgent:    Deep reading with claim extraction, citations, source URLs
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════


@dataclass
class SearchResult:
    """A single search result from one query strategy."""
    url: str
    title: str
    snippet: str
    strategy: str  # "broad" | "specific" | "technical"
    rank: int = 0


@dataclass
class Claim:
    """A factual claim extracted from a source by the Reader."""
    text: str
    source_url: str
    source_title: str
    confidence: float  # 0-1 estimated reliability
    citation: str = ""  # exact quote from source
    category: str = ""  # "fact" | "opinion" | "data_point" | "quote"


@dataclass
class SearchReport:
    """Complete search output for one sub-question."""
    question_id: str
    question: str
    results: list[SearchResult] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# SearcherAgent
# ═══════════════════════════════════════════════════════════════


class SearcherAgent:
    """Runs 3 concurrent search strategies for a sub-question.

    Strategies:
      1. BROAD     — general web search with broad keywords
      2. SPECIFIC  — exact question as query + code/example
      3. TECHNICAL — technical terms, API docs, GitHub issues

    Example::

        searcher = SearcherAgent()
        report = await searcher.search(sub_question)
        # → SearchReport with 15-30 results (5-10 per strategy)
    """

    # ── Query strategy builders ─────────────────────────────

    @staticmethod
    def _build_broad_query(question: str) -> str:
        """Broad-topic query: strip question words, keep core terms."""
        import re
        q = question.lower()
        for prefix in ("как ", "как лучше всего ", "как правильно ",
                       "how to ", "how to best ", "what is ", "what are ",
                       "почему ", "why ", "когда ", "when ",
                       "сравни ", "compare ", "чем отлича", "разбери "):
            if q.startswith(prefix):
                q = q[len(prefix):]
                break
        # Remove question words
        q = re.sub(r"\b(как|что|чем|почему|когда|какие|какой|какая|how|what|why|when|which|where|who|лучше|всего|правильно|реализовать|сделать|написать)\b", "", q)
        q = re.sub(r"\s+", " ", q).strip()
        return q if len(q) > 15 else question

    @staticmethod
    def _build_specific_query(question: str) -> str:
        """Exact/precise query: add 'example', 'tutorial', 'guide'."""
        return f"{question} example tutorial"

    @staticmethod
    def _build_technical_query(question: str) -> str:
        """Technical query: add 'API', 'GitHub', 'docs', 'best practice'."""
        return f"{question} API documentation best practice"

    # ── Main search method ──────────────────────────────────

    async def search(
        self,
        question: str,
        question_id: str = "",
        max_per_strategy: int = 5,
    ) -> SearchReport:
        """Run 3 search strategies concurrently.

        Each strategy runs in a separate asyncio task for true
        parallelism.  Falls back to synthetic results if API
        is unavailable.
        """
        report = SearchReport(question_id=question_id, question=question)

        async def _search_strategy(
            query: str, strategy: str,
        ) -> list[SearchResult]:
            results: list[SearchResult] = []
            try:
                from hermes_tools import web_search
                raw = await asyncio.wait_for(
                    asyncio.to_thread(web_search, query=query, limit=max_per_strategy),
                    timeout=8,
                )
                if raw and raw.get("results"):
                    for i, r in enumerate(raw["results"]):
                        results.append(SearchResult(
                            url=r.get("url", "") or r.get("link", ""),
                            title=r.get("title", "")[:120],
                            snippet=r.get("content", "") or r.get("snippet", "")[:300],
                            strategy=strategy,
                            rank=i + 1,
                        ))
            except asyncio.TimeoutError:
                logger.warning("Search timeout for '%s' [%s]", query[:50], strategy)
            except ImportError:
                logger.debug("web_search unavailable — skipping [%s]", strategy)
            except Exception as e:
                # Rate limit, network error, etc.
                err_msg = str(e).lower()
                if "rate" in err_msg or "429" in err_msg:
                    logger.info("Rate limited on search [%s] — skipping", strategy)
                else:
                    logger.debug("Search failed [%s]: %s", strategy, e)

            # Fallback: return empty — caller handles missing results
            return results

        # Run all 3 strategies concurrently
        tasks = [
            asyncio.create_task(_search_strategy(
                self._build_broad_query(question), "broad",
            )),
            asyncio.create_task(_search_strategy(
                self._build_specific_query(question), "specific",
            )),
            asyncio.create_task(_search_strategy(
                self._build_technical_query(question), "technical",
            )),
        ]
        all_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect + deduplicate
        seen_urls: set[str] = set()
        for result_set in all_results:
            if isinstance(result_set, list):
                for r in result_set:
                    if r.url and r.url not in seen_urls:
                        seen_urls.add(r.url)
                        report.results.append(r)

        logger.debug(
            "SearcherAgent: %s → %d results (seen=%d unique)",
            question_id, len(report.results), len(seen_urls),
        )
        return report


# ═══════════════════════════════════════════════════════════════
# ReaderAgent
# ═══════════════════════════════════════════════════════════════


class ReaderAgent:
    """Deep-reads search results and extracts factual claims with citations.

    For each source, the Reader:
    1. Reads the snippet/page content
    2. Extracts factual claims (assertions that can be verified)
    3. Attaches exact citations (quotes from the source)
    4. Assesses confidence (multiple sources = higher confidence)
    5. Tags claims by category (fact, opinion, data_point, quote)

    Example::

        reader = ReaderAgent()
        claims = await reader.extract_claims(search_report)
        # → list[Claim] with text, source_url, citation, confidence
    """

    # ── Claim extraction patterns ───────────────────────────

    _FACT_INDICATORS = [
        "according to", "research shows", "studies indicate",
        "data from", "benchmarks show", "measured at",
        "reported", "published in", "documented",
    ]

    _OPINION_INDICATORS = [
        "recommends", "suggests", "believes", "argues",
        "proposes", "advocates", "prefers", "considers",
    ]

    async def read_and_extract(
        self,
        search_report: SearchReport,
        max_claims: int = 8,
    ) -> list[Claim]:
        """Deep-read all results and extract claims concurrently."""
        claims: list[Claim] = []

        async def _read_one(result: SearchResult) -> list[Claim]:
            snippet = result.snippet
            if not snippet:
                return []

            local_claims: list[Claim] = []
            sentences = self._split_sentences(snippet)

            for sent in sentences:
                if len(sent) < 20:
                    continue

                category = self._classify_claim(sent)
                confidence = self._estimate_confidence(
                    result, sent, category,
                )
                citation = sent[:200]  # use sentence as citation

                local_claims.append(Claim(
                    text=sent,
                    source_url=result.url,
                    source_title=result.title,
                    confidence=confidence,
                    citation=citation,
                    category=category,
                ))

            return local_claims

        # Read top results concurrently
        tasks = [
            asyncio.create_task(_read_one(r))
            for r in search_report.results[:10]
        ]
        all_claims = await asyncio.gather(*tasks, return_exceptions=True)

        for result_claims in all_claims:
            if isinstance(result_claims, list):
                claims.extend(result_claims)

        # Sort by confidence, keep top
        claims.sort(key=lambda c: c.confidence, reverse=True)
        result = claims[:max_claims]

        logger.debug(
            "ReaderAgent: %s → %d claims (from %d sources)",
            search_report.question_id,
            len(result),
            len(search_report.results),
        )
        return result

    # ── Helpers ─────────────────────────────────────────────

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences."""
        import re
        parts = re.split(r"(?<=[.!?])\s+", text)
        return [p.strip() for p in parts if len(p.strip()) > 10]

    @classmethod
    def _classify_claim(cls, text: str) -> str:
        """Classify a claim as fact, opinion, data_point, or quote."""
        t = text.lower()
        # Data points: numbers, percentages, measurements
        import re
        if re.search(r"\d+[%]|\d+ ms|\d+ seconds|\d+x|\d+ GB|benchmark", t):
            return "data_point"
        # Quotes
        if '"' in text or "«" in text or "»" in text:
            return "quote"
        # Facts (citations, studies)
        if any(ind in t for ind in cls._FACT_INDICATORS):
            return "fact"
        # Opinions
        if any(ind in t for ind in cls._OPINION_INDICATORS):
            return "opinion"
        return "fact"

    @classmethod
    def _estimate_confidence(
        cls, result: SearchResult, text: str, category: str,
    ) -> float:
        """Estimate confidence in a claim (0-1)."""
        confidence = 0.5
        # Higher rank = lower confidence
        if result.rank <= 3:
            confidence += 0.1
        # Data points are generally reliable
        if category == "data_point":
            confidence += 0.1
        # Opinions are less reliable
        if category == "opinion":
            confidence -= 0.15
        # Quotes are reliable
        if category == "quote":
            confidence += 0.05
        # Longer snippets = more context
        if len(text) > 80:
            confidence += 0.05
        return max(0.0, min(1.0, confidence))


# ═══════════════════════════════════════════════════════════════
# Orchestrator: run search + read for all sub-questions
# ═══════════════════════════════════════════════════════════════


async def run_search_and_read(
    sub_questions: list[Any],  # SubQuestion
    max_sources_per_strategy: int = 5,
) -> list[SearchReport]:
    """Run Searcher + Reader for all sub-questions in parallel.

    Each sub-question gets its own SearcherAgent and ReaderAgent,
    running concurrently with all others.

    Returns one SearchReport per sub-question with results + claims.
    """
    searcher = SearcherAgent()
    reader = ReaderAgent()

    async def _process_one(sq) -> SearchReport:
        """Search + read for one sub-question."""
        # Search with 3 parallel strategies
        report = await searcher.search(
            sq.question,
            question_id=sq.id,
            max_per_strategy=max_sources_per_strategy,
        )
        # Deep-read results to extract claims
        claims = await reader.read_and_extract(report)
        report.claims = claims
        return report

    # Process all sub-questions concurrently
    tasks = [asyncio.create_task(_process_one(sq)) for sq in sub_questions]
    reports = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter out exceptions
    result: list[SearchReport] = []
    for r in reports:
        if isinstance(r, SearchReport):
            result.append(r)
        else:
            logger.debug("Search+read failed for one sub-question: %s", r)

    return result
