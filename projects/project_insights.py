"""
Shared Insights Layer — collective project memory.

Each project has a ChromaDB collection ``project_insights_{id}``
storing key lessons, solutions, and decisions as vector embeddings.

Insights are:
- Auto-extracted after workflow completion
- Injected into agent prompts at task start
- Manually added via ``/insight add "text"``
- Queried via ``/insights`` (list / search)

Usage::

    from projects.project_insights import ProjectInsights

    pi = ProjectInsights("my-project")
    pi.add("Use asyncpg for Postgres — 3x faster than psycopg2", importance=8)
    results = pi.search("database performance")
"""

from __future__ import annotations

import json, logging, re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False
    ChromaSettings = None  # type: ignore
    chromadb = None  # type: ignore


# ═══════════════════════════════════════════════════════════════
# Data model
# ═══════════════════════════════════════════════════════════════

@dataclass
class Insight:
    """A single shared insight in project memory."""
    id: str
    text: str
    importance: int = 5       # 1-10
    tags: list[str] = field(default_factory=list)
    source: str = ""           # "manual" | "workflow" | "auto"
    created_at: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════
# ProjectInsights
# ═══════════════════════════════════════════════════════════════

class ProjectInsights:
    """Shared vector memory for a single project.

    Parameters
    ----------
    project_id : str
        The project slug.
    hermes_home : Path | None
        Override Hermes home directory.
    """

    def __init__(self, project_id: str, hermes_home: str | Path | None = None):
        self.project_id = project_id
        self._home = Path(hermes_home) if hermes_home else self._resolve_home()
        self._collection_name = f"project_insights_{project_id}"
        self._chroma_dir = (
            self._home / "projects" / project_id / "memory" / "insights"
        )
        self._chroma_dir.mkdir(parents=True, exist_ok=True)
        self._client = None
        self._collection = None
        if HAS_CHROMA:
            self._init_chroma()

    @staticmethod
    def _resolve_home() -> Path:
        try:
            from hermes_constants import get_hermes_home
            return get_hermes_home()
        except ImportError:
            import os
            return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))

    def _init_chroma(self) -> None:
        try:
            self._client = chromadb.PersistentClient(
                path=str(self._chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as e:
            logger.warning(f"ChromaDB init failed for insights '{self.project_id}': {e}")
            self._client = None
            self._collection = None

    # ── Add ────────────────────────────────────────────────

    def add(
        self,
        text: str,
        *,
        importance: int = 5,
        tags: list[str] | None = None,
        source: str = "manual",
        metadata: dict | None = None,
    ) -> str:
        """Add an insight. Returns its ID."""
        if not self._collection:
            return ""

        insight_id = _make_id()
        importance = max(1, min(10, importance))

        doc = {
            "text": text.strip(),
            "importance": importance,
            "tags": ",".join(tags or []),
            "source": source,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata_json": json.dumps(metadata or {}, ensure_ascii=False),
        }

        try:
            self._collection.add(
                ids=[insight_id],
                documents=[text.strip()],
                metadatas=[doc],
            )
            logger.info(f"Insight added: {text[:60]}... (importance={importance})")
            return insight_id
        except Exception as e:
            logger.error(f"Failed to add insight: {e}")
            return ""

    # ── Search ─────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        min_importance: int = 0,
    ) -> list[Insight]:
        """Search insights by semantic similarity. Returns most relevant."""
        if not self._collection:
            return self._list_from_fallback(query, limit, min_importance)

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=limit,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.debug(f"Insight search failed, fallback: {e}")
            return self._list_from_fallback(query, limit, min_importance)

        insights = []
        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]

        for i, (iid, doc, meta) in enumerate(zip(ids, docs, metas)):
            imp = int(meta.get("importance", 5)) if meta else 5
            if imp < min_importance:
                continue
            tags_raw = meta.get("tags", "") if meta else ""
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
            insights.append(Insight(
                id=iid, text=doc, importance=imp, tags=tags,
                source=meta.get("source", "unknown") if meta else "unknown",
                created_at=meta.get("created_at", "") if meta else "",
                metadata=json.loads(meta.get("metadata_json", "{}")) if meta else {},
            ))
        return insights

    # ── List ───────────────────────────────────────────────

    def list_all(self, *, limit: int = 50, min_importance: int = 0) -> list[Insight]:
        """List all insights, newest first."""
        return self.search("", limit=limit, min_importance=min_importance)

    def list_by_tag(self, tag: str, *, limit: int = 20) -> list[Insight]:
        return [i for i in self.list_all(limit=limit) if tag in i.tags]

    # ── Fallback (no ChromaDB) ─────────────────────────────

    def _list_from_fallback(self, _query: str, limit: int, min_importance: int) -> list[Insight]:
        """Fallback: text file when ChromaDB unavailable."""
        fallback = self._chroma_dir / "insights.jsonl"
        if not fallback.exists():
            return []
        results = []
        for line in fallback.read_text().splitlines():
            try:
                d = json.loads(line)
                if d.get("importance", 5) >= min_importance:
                    results.append(Insight(**d))
            except Exception:
                pass
        results.sort(key=lambda i: i.created_at, reverse=True)
        return results[:limit]

    # ── Auto-extract from workflow ────────────────────────

    def extract_from_workflow(
        self,
        task: str,
        final_code: str,
        tester_review: str,
        score: float,
    ) -> list[str]:
        """Auto-extract key insights from a completed workflow.

        Uses regex heuristics to find patterns worth remembering.
        Returns list of added insight IDs.
        """
        insights = []
        text = f"{task}\n{tester_review}"

        # Pattern 1: technology/library mentions
        tech_matches = re.findall(
            r"(?:use|using|install|pip install|import)\s+([a-zA-Z][\w.-]+)",
            text, re.I,
        )
        for tech in set(tech_matches[:3]):
            insights.append(
                f"Technology used: {tech} — relevant for this project"
            )

        # Pattern 2: performance/scaling notes
        if re.search(r"(?:fast|slow|performance|optimiz|benchmark)", text, re.I):
            insights.append(
                f"Performance note (score={score:.0f}/10): {tester_review[:150]}"
            )

        # Pattern 3: architectural decisions
        arch_match = re.search(
            r"(?:architecture|pattern|design|структур|архитектур)[:\s]+(.+?)(?:\.|$)",
            text, re.I,
        )
        if arch_match:
            insights.append(f"Architecture: {arch_match.group(1).strip()}")

        # Pattern 4: error/lesson learned
        error_match = re.search(
            r"(?:error|bug|issue|fix|ошибк|баг|исправ)[:\s]+(.+?)(?:\.|$)",
            text, re.I,
        )
        if error_match:
            insights.append(f"Lesson learned: {error_match.group(1).strip()}")

        # Pattern 5: high-score solutions
        if score >= 8 and final_code:
            insights.append(
                f"High-quality solution (score={score:.0f}/10): {task[:100]}"
            )

        added = []
        for text in insights:
            iid = self.add(
                text, importance=7, source="workflow",
                tags=["auto", f"score_{int(score)}"],
            )
            if iid:
                added.append(iid)

        return added

    # ── Prompt injection ──────────────────────────────────

    def get_context_for_task(self, task: str, *, limit: int = 3) -> str:
        """Return relevant insights as a prompt block for a new task."""
        results = self.search(task, limit=limit, min_importance=5)
        if not results:
            return ""

        lines = ["[SHARED PROJECT INSIGHTS — relevant past knowledge]"]
        for i, ins in enumerate(results, 1):
            lines.append(f"{i}. [{ins.importance}/10] {ins.text}")
        lines.append("[/SHARED PROJECT INSIGHTS]")
        return "\n".join(lines)

    # ── Stats ─────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return summary statistics."""
        all_insights = self.list_all(limit=1000)
        if not all_insights:
            return {"total": 0}

        return {
            "total": len(all_insights),
            "avg_importance": sum(i.importance for i in all_insights) / len(all_insights),
            "top_tags": _top_tags(all_insights, 5),
            "newest": all_insights[0].created_at[:19] if all_insights else "",
            "sources": {
                src: sum(1 for i in all_insights if i.source == src)
                for src in set(i.source for i in all_insights)
            },
        }


# ── Helpers ────────────────────────────────────────────────

def _make_id() -> str:
    import uuid
    return f"ins_{uuid.uuid4().hex[:12]}"


def _top_tags(insights: list[Insight], n: int) -> list[tuple[str, int]]:
    from collections import Counter
    c = Counter()
    for ins in insights:
        for t in ins.tags:
            c[t] += 1
    return c.most_common(n)


# ═══════════════════════════════════════════════════════════════
# Module-level helpers
# ═══════════════════════════════════════════════════════════════

# ── Agents BLOCKED from insights (strict Tester isolation) ──
_TESTER_AGENT_IDS = {"tester", "tester-6a6ba59f", "tester-abc", "reviewer"}


def is_insight_blocked(agent_id: str) -> bool:
    """Check if *agent_id* is blocked from receiving shared insights.

    Tester and Reviewer agents MUST remain blind to project knowledge
    to ensure objective, unbiased code evaluation.
    """
    aid = agent_id.lower().replace("_", "-")
    return aid in _TESTER_AGENT_IDS or "tester" in aid


def get_project_insights() -> ProjectInsights | None:
    """Get insights for the currently active project."""
    try:
        from projects.project_context import get_current_project_id
        pid = get_current_project_id()
        if pid:
            return ProjectInsights(pid)
    except Exception:
        pass
    return None


def add_insight(text: str, importance: int = 5, source: str = "manual") -> str:
    """Add an insight to the current project. Returns ID."""
    pi = get_project_insights()
    if pi:
        return pi.add(text, importance=importance, source=source)
    return ""


def search_insights(query: str, limit: int = 5) -> list[Insight]:
    """Search insights in the current project."""
    pi = get_project_insights()
    if pi:
        return pi.search(query, limit=limit)
    return []


def get_insights_context(task: str, agent_id: str = "") -> str:
    """Get insight context for injection into agent prompts.

    If *agent_id* is a Tester/Reviewer, returns empty string
    (strict isolation — Tester must never see project knowledge).

    Args:
        task: The task description (used for semantic search).
        agent_id: The agent receiving the context. Blocked for testers.

    Returns:
        Insight block string, or empty string for blocked agents.
    """
    if agent_id and is_insight_blocked(agent_id):
        return ""

    pi = get_project_insights()
    if pi:
        return pi.get_context_for_task(task)
    return ""


def extract_workflow_insights(
    task: str,
    final_code: str,
    tester_review: str,
    score: float,
) -> list[str]:
    """Auto-extract insights after workflow completion.

    Called by the orchestrator.  Insights are saved to the
    active project's shared memory — Tester agents never see them.
    """
    pi = get_project_insights()
    if pi:
        return pi.extract_from_workflow(task, final_code, tester_review, score)
    return []
