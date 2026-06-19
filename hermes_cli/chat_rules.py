"""
Chat Rules — DuckDB-backed chat rules, zero LLM token overhead.

Rules are stored in ``~/.hermes/data/chat_rules.duckdb`` and
injected into the system prompt BEFORE every LLM call.  One SQL
query per message, microseconds latency.

Usage (by main agent):
    # Add a rule
    hermes chat-rules add --group telegram:123456 "Always reply in Russian"

    # List rules
    hermes chat-rules list --group telegram:123456

    # Delete a rule
    hermes chat-rules delete --group telegram:123456 --id 3

    # Toggle a rule on/off
    hermes chat-rules toggle --group telegram:123456 --id 3

Integration (automatic):
    from hermes_cli.chat_rules import get_rules_for_group
    rules = get_rules_for_group("telegram:123456")
    # → ["Always reply in Russian", "Never mention politics"]

The returned rules are injected into the system prompt as:
    [ПРАВИЛА ЭТОГО ЧАТА. Нарушать их нельзя ни при каких обстоятельствах:]
    1. Always reply in Russian
    2. Never mention politics
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".hermes" / "data" / "chat_rules.duckdb"

# ── Lazy DuckDB connection ────────────────────────────────────

_conn = None


def _get_conn():
    """Lazy-init DuckDB connection. Creates DB + table on first call."""
    global _conn
    if _conn is not None:
        return _conn

    import duckdb

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _conn = duckdb.connect(str(DB_PATH))

    _conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_rules (
            id INTEGER PRIMARY KEY,
            group_id TEXT NOT NULL,
            rule TEXT NOT NULL,
            priority INTEGER DEFAULT 0,
            active BOOLEAN DEFAULT true,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by TEXT DEFAULT 'system',
            expires_at TIMESTAMP
        )
    """)
    _conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_chat_rules_id START 1
    """)
    # Migration: add expires_at column if missing
    try:
        cols = _conn.execute("PRAGMA table_info('chat_rules')").fetchall()
        col_names = {c[1] for c in cols}
        if "expires_at" not in col_names:
            _conn.execute("ALTER TABLE chat_rules ADD COLUMN expires_at TIMESTAMP")
            logger.info("Added expires_at column to chat_rules")
    except Exception:
        pass
    # Ensure id auto-increments
    try:
        _conn.execute("""
            ALTER TABLE chat_rules ALTER id SET DEFAULT nextval('seq_chat_rules_id')
        """)
    except Exception:
        pass  # Already set

    logger.debug(f"Chat rules DB ready: {DB_PATH}")
    return _conn


# ── Public API (for gateway pre-processing) ──────────────────


def get_rules_for_group(group_id: str) -> list[str]:
    """Return active rules for a group, ordered by priority.

    Automatically filters out expired rules (expires_at <= now()).
    """
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT rule FROM chat_rules "
            "WHERE group_id = ? AND active = true "
            "AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP) "
            "ORDER BY priority DESC, id ASC",
            [group_id],
        ).fetchall()
        return [row[0] for row in rows]
    except Exception as e:
        logger.warning(f"Failed to query chat rules for {group_id}: {e}")
        return []


def format_rules_prompt(rules: list[str]) -> str:
    """Format rules as a system prompt block.

    Returns empty string if rules list is empty (no tokens wasted).
    """
    if not rules:
        return ""

    lines = [
        "[ПРАВИЛА ЭТОГО ЧАТА. Нарушать их нельзя ни при каких обстоятельствах:]"
    ]
    for i, rule in enumerate(rules, 1):
        lines.append(f"{i}. {rule}")

    return "\n".join(lines)


# ── Management API (for CLI and agent tools) ─────────────────


def add_rule(group_id: str, rule: str, priority: int = 0, created_by: str = "system", ttl: str | None = None) -> int:
    """Add a rule to a group. Returns the new rule ID.

    Args:
        ttl: Duration string like '30m', '1h', '2h30m', '1d'.
             Rule auto-expires after this duration.
    """
    import re as _re

    expires_at = None
    if ttl:
        seconds = _parse_ttl(ttl)
        if seconds:
            expires_at = f"CURRENT_TIMESTAMP + INTERVAL {seconds} SECONDS"

    conn = _get_conn()
    if expires_at:
        conn.execute(
            f"INSERT INTO chat_rules (group_id, rule, priority, created_by, expires_at) "
            f"VALUES (?, ?, ?, ?, {expires_at})",
            [group_id, rule, priority, created_by],
        )
    else:
        conn.execute(
            "INSERT INTO chat_rules (group_id, rule, priority, created_by) "
            "VALUES (?, ?, ?, ?)",
            [group_id, rule, priority, created_by],
        )
    row = conn.execute("SELECT currval('seq_chat_rules_id')").fetchone()
    rule_id = int(row[0]) if row else 0
    logger.info(f"Added chat rule {rule_id} for {group_id}: {rule[:60]}... ttl={ttl}")
    return rule_id


def list_rules(group_id: str) -> list[dict]:
    """List all rules for a group (including inactive)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, rule, priority, active, created_by, created_at "
        "FROM chat_rules WHERE group_id = ? ORDER BY priority DESC, id ASC",
        [group_id],
    ).fetchall()
    return [
        {
            "id": row[0],
            "rule": row[1],
            "priority": row[2],
            "active": bool(row[3]),
            "created_by": row[4],
            "created_at": str(row[5]) if row[5] else "",
        }
        for row in rows
    ]


def delete_rule(group_id: str, rule_id: int) -> bool:
    """Delete a rule by ID. Returns True if deleted."""
    conn = _get_conn()
    conn.execute(
        "DELETE FROM chat_rules WHERE group_id = ? AND id = ?",
        [group_id, rule_id],
    )
    deleted = conn.execute("SELECT CHANGES()").fetchone()[0] > 0
    if deleted:
        logger.info(f"Deleted chat rule {rule_id} from {group_id}")
    return deleted


def toggle_rule(group_id: str, rule_id: int) -> bool | None:
    """Toggle a rule's active state. Returns new state or None if not found."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT active FROM chat_rules WHERE group_id = ? AND id = ?",
        [group_id, rule_id],
    ).fetchone()
    if not row:
        return None

    new_state = not bool(row[0])
    conn.execute(
        "UPDATE chat_rules SET active = ? WHERE group_id = ? AND id = ?",
        [new_state, group_id, rule_id],
    )
    logger.info(f"Chat rule {rule_id} for {group_id}: active={new_state}")
    return new_state


def set_priority(group_id: str, rule_id: int, priority: int) -> bool:
    """Change a rule's priority. Returns True if updated."""
    conn = _get_conn()
    conn.execute(
        "UPDATE chat_rules SET priority = ? WHERE group_id = ? AND id = ?",
        [priority, group_id, rule_id],
    )
    updated = conn.execute("SELECT CHANGES()").fetchone()[0] > 0
    return updated


def rule_count(group_id: str | None = None) -> int:
    """Count rules, optionally filtered by group."""
    conn = _get_conn()
    if group_id:
        row = conn.execute(
            "SELECT COUNT(*) FROM chat_rules WHERE group_id = ?", [group_id]
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM chat_rules").fetchone()
    return int(row[0]) if row else 0


def _parse_ttl(duration: str) -> int | None:
    """Parse TTL string like '30m', '1h', '2h30m', '1d' to seconds."""
    import re
    total = 0
    pattern = re.compile(r"(\d+)\s*(d|h|m|s)")
    for m in pattern.finditer(duration.lower()):
        val = int(m.group(1))
        unit = m.group(2)
        if unit == "d":
            total += val * 86400
        elif unit == "h":
            total += val * 3600
        elif unit == "m":
            total += val * 60
        elif unit == "s":
            total += val
    return total if total > 0 else None


def disable_rule_by_text(group_id: str, text: str) -> int:
    """Disable rules matching text (LIKE %...%) in the group. Returns count."""
    conn = _get_conn()
    conn.execute(
        "UPDATE chat_rules SET active = false "
        "WHERE group_id = ? AND rule LIKE ? AND active = true",
        [group_id, f"%{text}%"],
    )
    return conn.execute("SELECT CHANGES()").fetchone()[0]


def enable_rule_by_text(group_id: str, text: str) -> int:
    """Enable previously disabled rules matching text. Returns count."""
    conn = _get_conn()
    conn.execute(
        "UPDATE chat_rules SET active = true "
        "WHERE group_id = ? AND rule LIKE ? AND active = false",
        [group_id, f"%{text}%"],
    )
    return conn.execute("SELECT CHANGES()").fetchone()[0]


def list_active_rules_text(group_id: str) -> list[str]:
    """Return active rule texts with optional TTL info."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT rule, expires_at FROM chat_rules "
        "WHERE group_id = ? AND active = true "
        "AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP) "
        "ORDER BY priority DESC, id ASC",
        [group_id],
    ).fetchall()
    result = []
    for rule, exp in rows:
        if exp:
            result.append(f"{rule} [истекает: {str(exp)[:16]}]")
        else:
            result.append(rule)
    return result


# ── Gateway integration ──────────────────────────────────────


def inject_rules_into_prompt(agent, source=None) -> int:
    """Inject chat rules into the agent's system prompt.

    Called by the gateway BEFORE every LLM call.  Reads rules from
    DuckDB for the chat's group_id, formats them as a prompt block,
    and prepends them to ``agent.ephemeral_system_prompt``.

    Zero-cost when:
    - DuckDB is not installed
    - No rules exist for this group
    - Source has no group_id

    Args:
        agent: AIAgent instance (must have ephemeral_system_prompt)
        source: Gateway source with platform, chat_id, chat_type

    Returns:
        Number of rules injected (0 = no change).
    """
    if source is None:
        return 0

    platform = getattr(source, "platform", None)
    chat_id = getattr(source, "chat_id", None)
    if not platform or not chat_id:
        return 0

    group_id = f"{platform}:{chat_id}"

    try:
        rules = get_rules_for_group(group_id)
    except Exception:
        return 0  # DuckDB not installed or DB error — silent

    if not rules:
        return 0

    rules_block = format_rules_prompt(rules)
    existing = getattr(agent, "ephemeral_system_prompt", "") or ""

    # Avoid double-injection if rules already in prompt
    if "ПРАВИЛА ЭТОГО ЧАТА" in existing:
        # Strip old rules block, re-inject fresh
        import re
        existing = re.sub(
            r"\[ПРАВИЛА ЭТОГО ЧАТА.*?\](?:.*?)(?=\n\n\[|$)",
            "", existing, flags=re.DOTALL,
        ).strip()

    agent.ephemeral_system_prompt = f"{rules_block}\n\n{existing}" if existing else rules_block

    logger.debug(f"Injected {len(rules)} chat rules for {group_id}")
    return len(rules)
