"""
Semantic Checker Logger — structured JSONL logging for semantic violations.

Logs every semantic check to ~/.hermes/logs/semantic_violations.log
with full context: rules, response, confidence, violations, explanation.

Usage:
    from core.semantic_logger import log_semantic_check, read_semantic_log

    log_semantic_check(
        agent_id="coder",
        rules=["Rule 1", "Rule 2"],
        response="def sort(arr): ...",
        violations=["Rule 1"],
        confidence=0.92,
        explanation="Code detected in response",
    )

    entries = read_semantic_log(limit=10)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _log_path() -> Path:
    home = Path.home() / ".hermes" / "logs"
    home.mkdir(parents=True, exist_ok=True)
    return home / "semantic_violations.log"


def log_semantic_check(
    *,
    agent_id: str,
    rules: list[str],
    response: str,
    violations: list[str],
    confidence: float = 0.0,
    explanation: str = "",
    model: str = "",
    duration_ms: float = 0.0,
    cache_hit: bool = False,
    rate_limited: bool = False,
) -> None:
    """Log a semantic check result to JSONL file."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": agent_id,
        "rule_count": len(rules),
        "rules": rules,
        "violations": violations,
        "violation_count": len(violations),
        "confidence": round(confidence, 4),
        "explanation": explanation[:500] if explanation else "",
        "model": model,
        "duration_ms": round(duration_ms, 1),
        "cache_hit": cache_hit,
        "rate_limited": rate_limited,
        # Full response only on violation (for debugging)
        "response": response[:2000] if violations else "",
    }

    try:
        path = _log_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Non-critical


def read_semantic_log(limit: int = 10, agent_id: str | None = None) -> list[dict[str, Any]]:
    """Read recent semantic check entries from log file.

    Returns list of dicts, most recent first.
    """
    path = _log_path()
    if not path.exists():
        return []

    entries: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if agent_id and entry.get("agent_id") != agent_id:
                    continue
                entries.append(entry)
            except json.JSONDecodeError:
                pass
    except Exception:
        pass

    return list(reversed(entries[-limit:]))


def format_semantic_log_entries(entries: list[dict[str, Any]]) -> str:
    """Format log entries for CLI display."""
    if not entries:
        return "  No semantic check entries found."

    lines = []
    for e in entries:
        ts = e.get("timestamp", "")[:19].replace("T", " ")
        agent = e.get("agent_id", "?")
        confidence = e.get("confidence", 0)
        violations = e.get("violations", [])
        explanation = e.get("explanation", "")
        cache = "⚡" if e.get("cache_hit") else ""
        limited = "⏳" if e.get("rate_limited") else ""
        dur = f" {e.get('duration_ms', 0):.0f}ms" if e.get("duration_ms") else ""

        if violations:
            icon = "❌"
            vlist = ", ".join(v[:40] for v in violations[:3])
            lines.append(
                f"  {icon} {ts} [{agent}] conf={confidence:.2f}{dur}\n"
                f"     violations: {vlist}"
            )
        elif e.get("rate_limited"):
            lines.append(f"  {limited} {ts} [{agent}] RATE LIMITED")
        else:
            lines.append(f"  ✅ {ts} [{agent}] OK{dur} {cache}")

        if explanation:
            lines.append(f"     {explanation[:100]}")

    return "\n".join(lines)
