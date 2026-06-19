"""
Cross-Validator — adversarial claim verification for Stage 4.

Two independent Validator agents examine claims from opposing angles:
  Validator A (Corroborator):  "Can this claim be supported by other sources?"
  Validator B (Contradictor):  "Does any source contradict this claim?"

When they disagree → Adjudicator resolves via additional targeted search.
Each claim receives: status (confirmed/disputed/rejected) + adjusted confidence.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════


@dataclass
class ValidatedClaim:
    """A claim after adversarial cross-validation."""
    text: str
    source_url: str
    source_title: str
    original_confidence: float   # reader confidence
    adjusted_confidence: float    # after validation
    status: str                   # "confirmed" | "disputed" | "rejected"
    validator_a: str              # "supported" | "unsupported"
    validator_b: str              # "clean" | "contradicted"
    adjudicator_note: str = ""    # additional ruling if disputed
    corroborating_sources: int = 0


@dataclass
class ValidationReport:
    """Complete cross-validation output."""
    sub_question_id: str
    total_claims: int
    confirmed: int
    disputed: int
    rejected: int
    average_confidence: float
    claims: list[ValidatedClaim]


# ═══════════════════════════════════════════════════════════════
# CrossValidator
# ═══════════════════════════════════════════════════════════════


class CrossValidator:
    """Adversarial cross-validation with two independent validators.

    Validator A (CORROBORATOR):
      Checks whether a claim is supported by at least one other source.
      Strategy: compare claim text against snippets from ALL other sources.
      If ≥1 other source has similar content → "supported", else "unsupported".

    Validator B (CONTRADICTOR):
      Checks whether any source CONTRADICTS the claim.
      Strategy: search for negation patterns, opposite claims, or "not X".
      If contradiction found → "contradicted", else "clean".

    Adjudicator:
      When A and B disagree → search additional sources for the claim.
      Resolves: supported + contradicted → verify with deep search.
                unsupported + clean → borderline → additional source check.
    """

    def __init__(self):
        pass

    # ── Public API ──────────────────────────────────────────

    async def validate(
        self,
        claims: list[Any],   # list of Claim
        all_sources: list[dict[str, Any]],
        sub_question_id: str = "",
    ) -> ValidationReport:
        """Run adversarial validation on all claims for one sub-question.

        Each claim is evaluated by Validator A and B independently,
        with Adjudicator resolving disagreements.
        """
        if not claims:
            return ValidationReport(
                sub_question_id=sub_question_id,
                total_claims=0, confirmed=0, disputed=0, rejected=0,
                average_confidence=0.0, claims=[],
            )

        # Prepare source pool
        source_pool = self._build_source_pool(all_sources)

        # Validate all claims concurrently
        async def _validate_one(claim) -> ValidatedClaim:
            vc = await self._validate_single(claim, source_pool)
            return vc

        tasks = [asyncio.create_task(_validate_one(c)) for c in claims]
        validated = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[ValidatedClaim] = []
        for v in validated:
            if isinstance(v, ValidatedClaim):
                results.append(v)

        # Count
        confirmed = sum(1 for c in results if c.status == "confirmed")
        disputed = sum(1 for c in results if c.status == "disputed")
        rejected = sum(1 for c in results if c.status == "rejected")
        avg_conf = (
            sum(c.adjusted_confidence for c in results) / len(results)
            if results else 0.0
        )

        logger.info(
            "CrossValidator: %s → %d confirmed, %d disputed, %d rejected "
            "(avg conf=%.2f)",
            sub_question_id, confirmed, disputed, rejected, avg_conf,
        )

        return ValidationReport(
            sub_question_id=sub_question_id,
            total_claims=len(results),
            confirmed=confirmed,
            disputed=disputed,
            rejected=rejected,
            average_confidence=avg_conf,
            claims=results,
        )

    # ── Single claim validation ─────────────────────────────

    async def _validate_single(
        self, claim, source_pool: list[str],
    ) -> ValidatedClaim:
        """Run both validators on a single claim, adjudicate if needed."""
        # Extract claim attributes
        text = getattr(claim, "text", str(claim))
        confidence = getattr(claim, "confidence", 0.5)
        source_url = getattr(claim, "source_url", "")
        source_title = getattr(claim, "source_title", "")

        # ── Validator A: Corroboration ──────────────────────
        result_a = self._validate_corroboration(text, source_pool)
        supported = result_a["supported"]
        corroborating_count = result_a["count"]

        # ── Validator B: Contradiction ──────────────────────
        result_b = self._validate_contradiction(text, source_pool)
        contradicted = result_b["contradicted"]

        # ── Determine status ────────────────────────────────
        adjudicator_note = ""
        adjusted_confidence = confidence

        if supported and not contradicted:
            # Both agree: claim is confirmed
            status = "confirmed"
            adjusted_confidence = min(1.0, confidence + 0.1 + 0.03 * corroborating_count)
        elif not supported and contradicted:
            # Both agree: claim is rejected
            status = "rejected"
            adjusted_confidence = max(0.05, confidence - 0.3)
        elif supported and contradicted:
            # Disagreement: supported by some, contradicted by others
            status = "disputed"
            adjudicator_note = (
                f"Supported by {corroborating_count} sources but "
                f"contradiction found in source pool"
            )
            adjusted_confidence = max(0.15, confidence - 0.15)
        else:
            # unsupported + clean → borderline
            status = "disputed"
            adjusted_confidence = max(0.1, confidence - 0.2)
            adjudicator_note = (
                "No corroboration found, but no direct contradiction either"
            )

        return ValidatedClaim(
            text=text,
            source_url=source_url,
            source_title=source_title,
            original_confidence=confidence,
            adjusted_confidence=round(adjusted_confidence, 2),
            status=status,
            validator_a="supported" if supported else "unsupported",
            validator_b="contradicted" if contradicted else "clean",
            adjudicator_note=adjudicator_note,
            corroborating_sources=corroborating_count,
        )

    # ── Validator A: Corroboration ──────────────────────────

    @staticmethod
    def _validate_corroboration(
        claim_text: str, source_pool: list[str],
    ) -> dict[str, Any]:
        """Check if other sources support this claim.

        Strategy: extract key noun phrases, check how many other
        sources contain similar phrases.  Requires ≥2 keyword
        matches for corroboration.
        """
        import re

        keywords = CrossValidator._extract_key_terms(claim_text)
        if not keywords:
            return {"supported": False, "count": 0}

        count = 0
        for source_text in source_pool:
            matches = sum(1 for kw in keywords if kw.lower() in source_text.lower())
            if matches >= 2:  # at least 2 keyword matches
                count += 1
                if count >= 2:  # break early — 2+ is solid
                    break

        return {"supported": count >= 1, "count": count}

    # ── Validator B: Contradiction ──────────────────────────

    @staticmethod
    def _validate_contradiction(
        claim_text: str, source_pool: list[str],
    ) -> dict[str, Any]:
        """Check if any source contradicts this claim.

        Strategy: look for negation patterns, "not X", "X is false",
        opposite claims that directly oppose the claim's assertion.
        """
        # Negation patterns
        negation_markers = [
            "not ", "no ", "never ", "false", "incorrect",
            "wrong", "misleading", "myth", "debunked",
            "however", "but actually", "contrary",
            "не ", "нет ", "никогда ", "неверно",
            "ошибочно", "миф", "опровергнуто",
        ]

        keywords = CrossValidator._extract_key_terms(claim_text)
        if not keywords:
            return {"contradicted": False}

        for source_text in source_pool:
            # Check if source contains negation + claim keywords
            has_negation = any(m in source_text.lower() for m in negation_markers)
            if has_negation:
                kw_matches = sum(
                    1 for kw in keywords if kw.lower() in source_text.lower()
                )
                if kw_matches >= 2:
                    return {"contradicted": True}

        return {"contradicted": False}

    # ── Helpers ─────────────────────────────────────────────

    @staticmethod
    def _extract_key_terms(text: str) -> list[str]:
        """Extract meaningful key terms for comparison."""
        import re
        # Remove punctuation, split into words
        words = re.findall(r"[a-zа-яё0-9]{4,}", text.lower())
        # Remove stopwords
        stop = {
            "это", "для", "как", "что", "чем", "когда", "тогда", "очень",
            "which", "what", "that", "this", "with", "from", "have",
            "been", "were", "they", "their", "there",
            "может", "более", "менее", "также", "кроме",
        }
        return list({w for w in words if w not in stop})[:10]

    @staticmethod
    def _build_source_pool(sources: list[dict[str, Any]]) -> list[str]:
        """Build a concatenated text pool from all sources."""
        pool = []
        for s in sources:
            parts = []
            if s.get("title"):
                parts.append(str(s["title"]))
            if s.get("snippet"):
                parts.append(str(s["snippet"]))
            if s.get("content"):
                parts.append(str(s["content"])[:500])
            if parts:
                pool.append(" ".join(parts))
        return pool


# ═══════════════════════════════════════════════════════════════
# Adjudicator
# ═══════════════════════════════════════════════════════════════


class Adjudicator:
    """Resolves disputes between Validator A and B.

    When validators disagree:
    1. Generate a targeted search query from the disputed claim
    2. Search for additional sources
    3. Re-run both validators against the expanded source pool
    4. Return final ruling
    """

    async def adjudicate(
        self,
        disputed_claims: list[ValidatedClaim],
        original_pool: list[str],
    ) -> list[ValidatedClaim]:
        """Resolve disputed claims by searching additional sources."""
        if not disputed_claims:
            return disputed_claims

        resolved: list[ValidatedClaim] = []
        for vc in disputed_claims:
            # Build targeted search query from claim text
            query = self._build_adjudication_query(vc.text)

            # Search additional sources (if available)
            additional_sources: list[str] = []
            try:
                from hermes_tools import web_search
                raw = await asyncio.to_thread(
                    web_search, query=query, limit=3,
                )
                if raw and raw.get("results"):
                    additional_sources = [
                        (r.get("content", "") or r.get("snippet", ""))[:300]
                        for r in raw["results"]
                    ]
            except Exception:
                pass

            # Expand pool and re-validate
            expanded_pool = list(original_pool) + additional_sources

            # Re-run both validators on expanded pool
            a_result = CrossValidator._validate_corroboration(vc.text, expanded_pool)
            b_result = CrossValidator._validate_contradiction(vc.text, expanded_pool)

            supported = a_result["supported"]
            contradicted = b_result["contradicted"]
            extra_count = a_result["count"] + (0 if not additional_sources else 1)

            # Final ruling
            if supported and not contradicted:
                vc.status = "confirmed"
                vc.adjusted_confidence = min(1.0, vc.original_confidence + 0.15)
                vc.adjudicator_note = (
                    f"Adjudicator: confirmed after additional search "
                    f"({len(additional_sources)} extra sources, "
                    f"{vc.corroborating_sources + extra_count} corroborating)"
                )
            elif not supported and contradicted:
                vc.status = "rejected"
                vc.adjusted_confidence = max(0.02, vc.original_confidence - 0.4)
                vc.adjudicator_note = (
                    "Adjudicator: rejected — contradiction confirmed "
                    "in additional sources"
                )
            else:
                # Still disputed after additional search
                vc.adjudicator_note = (
                    f"Adjudicator: remains disputed after "
                    f"{len(additional_sources)} extra sources"
                )

            vc.corroborating_sources += extra_count
            resolved.append(vc)

        logger.info(
            "Adjudicator: resolved %d disputes", len(resolved),
        )
        return resolved

    @staticmethod
    def _build_adjudication_query(claim_text: str) -> str:
        """Build a targeted search query from a claim."""
        # Use first 100 chars + key terms
        terms = CrossValidator._extract_key_terms(claim_text)
        query = " ".join(terms[:5])
        return query if len(query) > 10 else claim_text[:100]


# ═══════════════════════════════════════════════════════════════
# Orchestrator
# ═══════════════════════════════════════════════════════════════


async def run_cross_validation(
    claims: list[Any],
    sources: list[dict[str, Any]],
    sub_question_id: str = "",
) -> ValidationReport:
    """Run full adversarial cross-validation pipeline.

    1. CrossValidator: both Validator A and B check all claims
    2. Adjudicator: resolve disputed claims with additional search
    3. Return ValidationReport with status per claim
    """
    validator = CrossValidator()
    report = await validator.validate(claims, sources, sub_question_id)

    # Run adjudicator on disputed claims
    if report.disputed > 0:
        source_pool = CrossValidator._build_source_pool(sources)
        adjudicator = Adjudicator()
        disputed = [c for c in report.claims if c.status == "disputed"]
        resolved = await adjudicator.adjudicate(disputed, source_pool)

        # Update report
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

    # Recompute average confidence
    if report.claims:
        report.average_confidence = (
            sum(c.adjusted_confidence for c in report.claims) / len(report.claims)
        )

    return report
