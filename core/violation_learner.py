"""
Self-learning RuleChecker — violation history + pattern generation.

Stores violation history and analyses it to suggest new keyword/regex
patterns that would have caught missed violations.

Usage:
    from core.violation_learner import get_violation_history, learn_from_violations

    # Record a violation
    get_violation_history().record(
        agent_id="coder",
        rule_text="НЕ пиши код",
        response_snippet="def sort(arr): ...",
        was_caught=True,
        category="code_restriction",
    )

    # Learn from history
    suggestions = learn_from_violations(agent_id="coder")
    # → [SuggestedRule(text="...", pattern="...", reason="..."), ...]
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_HISTORY: "ViolationHistory | None" = None
_RULE_CACHE: "RuleCheckCache | None" = None
MAX_HISTORY = 100


def get_violation_history() -> "ViolationHistory":
    global _HISTORY
    if _HISTORY is None:
        _HISTORY = ViolationHistory()
    return _HISTORY


def get_rule_cache() -> "RuleCheckCache":
    global _RULE_CACHE
    if _RULE_CACHE is None:
        _RULE_CACHE = RuleCheckCache()
    return _RULE_CACHE


# ═══════════════════════════════════════════════════════════════
# RuleCheckCache — TTL-based cache for rule violation results
# ═══════════════════════════════════════════════════════════════

@dataclass
class CacheEntry:
    violations: list[str]
    timestamp: float  # time.time()


class RuleCheckCache:
    """TTL-based cache for rule check results.

    Caches both keyword-based (AdvancedRuleChecker) and semantic
    (LLM pass) violation results.  Entries expire after TTL seconds.

    Usage::

        cache = get_rule_cache()
        key = cache.make_key(rules, response)
        violations = cache.get(key)      # None if miss/expired
        cache.set(key, violations)
    """

    def __init__(self, ttl_seconds: int = 1800, max_entries: int = 500):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._store: dict[str, CacheEntry] = {}
        # Track which rules hash was used for the key (for invalidation)
        self._rules_hash: str = ""

    @staticmethod
    def make_key(rules: list, response: str) -> str:
        """Deterministic cache key: sha256(normalized_rules + response)."""
        import hashlib
        # Normalize rules list
        rules_str = "|".join(
            r if isinstance(r, str) else r.get("rule", json.dumps(r, sort_keys=True))
            for r in rules
        )
        data = rules_str + "||" + response[:800]
        return hashlib.sha256(data.encode()).hexdigest()

    def get(self, key: str) -> list[str] | None:
        """Return cached violations or None if missing/expired."""
        import time
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.time() - entry.timestamp > self._ttl:
            del self._store[key]
            return None
        return list(entry.violations)

    def set(self, key: str, violations: list[str]) -> None:
        """Store violations in cache."""
        import time
        self._store[key] = CacheEntry(
            violations=list(violations),
            timestamp=time.time(),
        )
        # LRU eviction
        if len(self._store) > self._max:
            # Remove oldest 10%
            remove_count = max(1, self._max // 10)
            sorted_keys = sorted(
                self._store.keys(),
                key=lambda k: self._store[k].timestamp,
            )
            for old_key in sorted_keys[:remove_count]:
                del self._store[old_key]

    def clear(self) -> int:
        """Clear all cached entries. Returns count cleared."""
        count = len(self._store)
        self._store.clear()
        logger.info(f"Rule cache cleared: {count} entries")
        return count

    def invalidate_by_rules(self, rules: list) -> int:
        """Invalidate cache entries matching specific rules.

        Called when critical_rules or semantic_rules change.
        Returns count of invalidated entries.
        """
        import hashlib
        import json as _json
        rules_str = "|".join(
            r if isinstance(r, str) else r.get("rule", _json.dumps(r, sort_keys=True))
            for r in rules
        )
        rules_hash = hashlib.sha256(rules_str.encode()).hexdigest()[:16]
        before = len(self._store)
        # Remove entries whose key contains the rules hash prefix
        # (simplified — full invalidation on any rules change)
        self._store.clear()
        count = before
        logger.info(
            f"Rule cache invalidated: {count} entries "
            f"(rules changed, hash={rules_hash})"
        )
        return count

    @property
    def stats(self) -> dict:
        """Cache statistics."""
        import time
        now = time.time()
        active = sum(1 for e in self._store.values() if now - e.timestamp <= self._ttl)
        expired = len(self._store) - active
        return {
            "total": len(self._store),
            "active": active,
            "expired": expired,
            "ttl_seconds": self._ttl,
            "max_entries": self._max,
        }

    @property
    def ttl(self) -> int:
        return self._ttl

    @ttl.setter
    def ttl(self, seconds: int) -> None:
        self._ttl = max(60, seconds)


@dataclass
class ViolationRecord:
    rule_text: str
    response_snippet: str      # first 300 chars
    agent_id: str
    was_caught: bool            # True = caught by checker, False = missed
    category: str               # "code_restriction", "forbidden_word", etc.
    model: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class SuggestedRule:
    """A rule suggestion generated by PatternLearner."""
    text: str                   # Human-readable rule text
    pattern_type: str           # "keyword", "regex", "forbidden_word"
    pattern: str                # The actual keyword or regex pattern
    reason: str                 # Why this rule was suggested
    confidence: float = 0.0     # 0.0–1.0
    hit_count: int = 0          # How many violations match this pattern


class ViolationHistory:
    """Persistent violation history (JSONL, max 100 entries)."""

    def __init__(self, path: Path | None = None):
        if path is None:
            home = Path.home() / ".hermes"
            home.mkdir(parents=True, exist_ok=True)
            path = home / "data" / "violations.jsonl"
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._records: list[ViolationRecord] = list(self._load())

    def _load(self) -> list[ViolationRecord]:
        if not self._path.exists():
            return []
        records = []
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    records.append(ViolationRecord(
                        rule_text=data.get("rule_text", ""),
                        response_snippet=data.get("response_snippet", ""),
                        agent_id=data.get("agent_id", ""),
                        was_caught=data.get("was_caught", True),
                        category=data.get("category", ""),
                        model=data.get("model", ""),
                        timestamp=data.get("timestamp", ""),
                    ))
                except (json.JSONDecodeError, KeyError):
                    pass
        except Exception:
            pass
        return records[-MAX_HISTORY:]

    def _save(self) -> None:
        lines = []
        for r in self._records[-MAX_HISTORY:]:
            lines.append(json.dumps({
                "rule_text": r.rule_text,
                "response_snippet": r.response_snippet[:300],
                "agent_id": r.agent_id,
                "was_caught": r.was_caught,
                "category": r.category,
                "model": r.model,
                "timestamp": r.timestamp,
            }, ensure_ascii=False))
        self._path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def record(self, **kwargs) -> ViolationRecord:
        rec = ViolationRecord(**kwargs)
        self._records.append(rec)
        if len(self._records) > MAX_HISTORY:
            self._records = self._records[-MAX_HISTORY:]
        self._save()
        return rec

    def recent(self, agent_id: str | None = None, limit: int = 20) -> list[ViolationRecord]:
        records = self._records
        if agent_id:
            records = [r for r in records if r.agent_id == agent_id]
        return records[-limit:]

    def missed(self, agent_id: str | None = None) -> list[ViolationRecord]:
        """Return violations that were NOT caught by the checker."""
        records = self._records
        if agent_id:
            records = [r for r in records if r.agent_id == agent_id]
        return [r for r in records if not r.was_caught]

    @property
    def count(self) -> int:
        return len(self._records)

    @property
    def path(self) -> Path:
        return self._path


class PatternLearner:
    """Analyses violation history and generates keyword/regex suggestions."""

    # Words to exclude from pattern generation (too common)
    STOP_WORDS = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
        "for", "of", "with", "from", "by", "is", "are", "was", "were",
        "it", "this", "that", "these", "those", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would",
        "can", "could", "should", "may", "might", "not", "no",
        "и", "в", "на", "с", "по", "к", "из", "от", "для", "не",
        "что", "это", "как", "так", "то", "все", "она", "они",
        "быть", "есть", "был", "была", "были",
    }

    def __init__(self, history: ViolationHistory):
        self._history = history

    def learn(
        self,
        agent_id: str | None = None,
        min_occurrences: int = 2,
    ) -> list[SuggestedRule]:
        """Analyse violation history and return suggested new rules.

        Returns rules sorted by confidence (highest first).
        """
        suggestions: list[SuggestedRule] = []

        # ── Strategy 1: Repeated violations of same rule ──────
        suggestions.extend(self._learn_repeated_rule_violations(
            agent_id, min_occurrences,
        ))

        # ── Strategy 2: Missed violations — extract keywords ─
        suggestions.extend(self._learn_missed_violations(
            agent_id, min_occurrences,
        ))

        # ── Strategy 3: Cross-agent pattern discovery ─────────
        suggestions.extend(self._learn_cross_agent_patterns(
            agent_id, min_occurrences,
        ))

        # Sort by confidence descending
        suggestions.sort(key=lambda s: s.confidence, reverse=True)
        return suggestions

    def _learn_repeated_rule_violations(
        self, agent_id: str | None, min_occurrences: int,
    ) -> list[SuggestedRule]:
        """If the same rule is violated repeatedly, suggest stricter checks."""
        records = self._history.recent(agent_id, limit=MAX_HISTORY)
        rule_counts: Counter = Counter()

        for r in records:
            if r.was_caught:
                # Short key — first 60 chars of rule
                rule_key = r.rule_text[:60]
                rule_counts[rule_key] += 1

        suggestions = []
        for rule_key, count in rule_counts.most_common(10):
            if count < min_occurrences:
                continue
            # Find the latest violation for this rule
            latest = next(
                (r for r in reversed(records)
                 if r.rule_text[:60] == rule_key and r.was_caught),
                None,
            )
            if not latest:
                continue

            # Extract keywords from the response that triggered it
            keywords = self._extract_keywords(latest.response_snippet)
            if keywords:
                kw_list = "|".join(sorted(keywords)[:5])
                suggestions.append(SuggestedRule(
                    text=(
                        f"НЕ {kw_list.replace('|', ', ')} "
                        f"(авто-правило на основе {count} нарушений)"
                    ),
                    pattern_type="regex",
                    pattern=rf"(?i)\b({'|'.join(re.escape(k) for k in sorted(keywords)[:5])})\b",
                    reason=(
                        f"Rule '{rule_key[:40]}...' violated {count} times. "
                        f"Suggested keywords from response: {kw_list}"
                    ),
                    confidence=min(0.5 + count * 0.1, 0.95),
                    hit_count=count,
                ))

        return suggestions

    def _learn_missed_violations(
        self, agent_id: str | None, min_occurrences: int,
    ) -> list[SuggestedRule]:
        """Analyse violations that were NOT caught — extract patterns."""
        missed = self._history.missed(agent_id)
        if len(missed) < min_occurrences:
            return []

        # Group by rule text
        groups: dict[str, list[ViolationRecord]] = defaultdict(list)
        for r in missed:
            groups[r.rule_text[:60]].append(r)

        suggestions = []
        for rule_key, group in groups.items():
            if len(group) < min_occurrences:
                continue

            # Collect all words from missed responses
            all_keywords: Counter = Counter()
            for r in group:
                for kw in self._extract_keywords(r.response_snippet):
                    all_keywords[kw] += 1

            # Take top keywords that appear in most responses
            top_kw = [
                kw for kw, cnt in all_keywords.most_common(8)
                if cnt >= min_occurrences
            ]
            if top_kw:
                kw_list = "|".join(top_kw[:5])
                suggestions.append(SuggestedRule(
                    text=(
                        f"[MISSED] НЕ {top_kw[0] if len(top_kw) == 1 else ', '.join(top_kw[:3])} "
                        f"(авто из {len(group)} пропущенных нарушений)"
                    ),
                    pattern_type="regex",
                    pattern=rf"(?i)\b({'|'.join(re.escape(k) for k in top_kw[:5])})\b",
                    reason=(
                        f"Rule violated {len(group)} times but NOT caught. "
                        f"Suggested keywords: {kw_list}"
                    ),
                    confidence=min(0.3 + len(group) * 0.15, 0.85),
                    hit_count=len(group),
                ))

        return suggestions

    def _learn_cross_agent_patterns(
        self, agent_id: str | None, min_occurrences: int,
    ) -> list[SuggestedRule]:
        """Find violation patterns common across multiple agents."""
        records = self._history.recent(agent_id=None, limit=MAX_HISTORY)

        # Group by category
        cat_groups: dict[str, list[ViolationRecord]] = defaultdict(list)
        for r in records:
            if r.was_caught:
                cat_groups[r.category].append(r)

        suggestions = []
        for category, group in cat_groups.items():
            if len(group) < min_occurrences:
                continue

            # Count unique agents
            agents = {r.agent_id for r in group}
            if len(agents) < 2:
                continue  # Only one agent — not cross-agent

            # Extract common keywords
            all_kw: Counter = Counter()
            for r in group:
                for kw in self._extract_keywords(r.response_snippet):
                    all_kw[kw] += 1

            top_kw = [kw for kw, cnt in all_kw.most_common(5) if cnt >= 2]
            if top_kw:
                suggestions.append(SuggestedRule(
                    text=(
                        f"[CROSS-AGENT] regex: /({top_kw[0]}|{top_kw[1] if len(top_kw) > 1 else '...'})/i "
                        f"(общее для {len(agents)} агентов)"
                    ),
                    pattern_type="regex",
                    pattern=rf"(?i)\b({'|'.join(re.escape(k) for k in top_kw[:5])})\b",
                    reason=(
                        f"Cross-agent pattern ({len(agents)} agents): "
                        f"category={category}, keywords={', '.join(top_kw[:3])}"
                    ),
                    confidence=min(0.4 + len(agents) * 0.1, 0.8),
                    hit_count=len(group),
                ))

        return suggestions

    @classmethod
    def _extract_keywords(cls, text: str) -> list[str]:
        """Extract meaningful keywords from response text."""
        if not text:
            return []
        # Split on word boundaries, filter stop words and short tokens
        words = re.findall(r"[a-zA-Zа-яА-ЯёЁ]{3,}", text.lower())
        return [
            w for w in words
            if w not in cls.STOP_WORDS
        ]


def learn_from_violations(
    agent_id: str | None = None,
    min_occurrences: int = 2,
) -> list[SuggestedRule]:
    """Convenience function: learn from history, return suggestions."""
    history = get_violation_history()
    learner = PatternLearner(history)
    return learner.learn(agent_id=agent_id, min_occurrences=min_occurrences)


def format_learn_report(
    suggestions: list[SuggestedRule],
) -> str:
    """Format suggestions as a user-friendly report."""
    if not suggestions:
        return "✅ Нет новых предложений. Все нарушения ловятся."

    lines = [
        "🧠 Анализ истории нарушений",
        "═" * 50,
        f"Найдено предложений: {len(suggestions)}",
        "",
    ]

    for i, s in enumerate(suggestions, 1):
        lines.append(f"  {i}. {s.text}")
        lines.append(f"     Тип: {s.pattern_type}")
        lines.append(f"     Паттерн: `{s.pattern}`")
        lines.append(f"     Причина: {s.reason}")
        lines.append(f"     Уверенность: {s.confidence:.0%} | Нарушений: {s.hit_count}")
        lines.append("")

    lines.append("═" * 50)
    lines.append("Добавить предложенные правила в critical_rules?")
    lines.append("  hermes config set agent.<id>.critical_rules [...]")
    return "\n".join(lines)
