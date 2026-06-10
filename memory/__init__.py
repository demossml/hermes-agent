"""
Long-Term Vector Memory for Hermes Multi-Agent.

Uses ChromaDB to store embeddings of summaries, key insights, and
critical rules so agents remember important information across sessions.

Architecture
------------
- ChromaDB collection: ``hermes_longterm_memory``
- Each entry holds: document (text), embedding (auto-generated),
  and metadata (agent_id, subtree_id, timestamp, task_type, importance).
- Retrieval uses cosine similarity on the embedding space.
- Isolation: queries are filtered by ``subtree_id`` so each branch
  only sees its own memories, plus global rules (subtree_id="global").

Usage::

    ltm = LongTermMemory(persist_dir="~/.hermes/longterm_memory")
    ltm.add_memory("Fixed the auth bug", metadata={"agent_id": "coder", ...})
    results = ltm.search("auth bug", subtree_id="subtree-coder", top_k=5)
    insights = ltm.get_insights("coder", subtree_id="subtree-coder")
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── ChromaDB is an optional dependency ──────────────────────────
try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False
    ChromaSettings = None  # type: ignore[misc]
    chromadb = None  # type: ignore[misc]
    logger.warning(
        "ChromaDB not installed. Long-term memory is disabled. "
        "Install with: pip install chromadb"
    )

# Default embedding function — uses Chroma's built-in all-MiniLM-L6-v2
# (runs locally, no API key needed, 384-dimensional embeddings).
DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

COLLECTION_NAME = "hermes_longterm_memory"

# Importance levels (higher = more likely to be retrieved)
IMPORTANCE_HIGH = 10    # critical rules, violations
IMPORTANCE_MEDIUM = 5   # task summaries, key decisions
IMPORTANCE_LOW = 1      # general conversation


class LongTermMemory:
    """Vector-based long-term memory for Hermes sub-agents.

    Parameters
    ----------
    persist_dir : str or Path
        Directory where ChromaDB stores its data.
    embedding_model : str
        Sentence-transformers model name for embeddings
        (default: ``"all-MiniLM-L6-v2"``).
    """

    def __init__(
        self,
        persist_dir: str | Path = "~/.hermes/longterm_memory",
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    ):
        self._persist_dir = Path(persist_dir).expanduser().resolve()
        self._embedding_model = embedding_model

        if not HAS_CHROMA:
            self._client = None
            self._collection = None
            self._enabled = False
            return

        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._enabled = True

        try:
            self._client = chromadb.PersistentClient(
                path=str(self._persist_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(
                f"LongTermMemory initialised at {self._persist_dir} "
                f"(collection: {COLLECTION_NAME}, "
                f"count: {self._collection.count()})"
            )
        except Exception as e:
            logger.error(f"Failed to initialise ChromaDB: {e}")
            self._client = None
            self._collection = None
            self._enabled = False

    # ── Public API ────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """Whether long-term memory is available."""
        return self._enabled and self._collection is not None

    def add_memory(
        self,
        document: str,
        *,
        agent_id: str = "unknown",
        subtree_id: str = "global",
        task_type: str = "general",
        importance: int = IMPORTANCE_MEDIUM,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Store a memory in the vector database.

        Returns the memory ID on success, ``None`` on failure.
        """
        if not self.enabled:
            return None

        mem_id = f"mem-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        meta: dict[str, Any] = {
            "agent_id": agent_id,
            "subtree_id": subtree_id,
            "timestamp": now,
            "task_type": task_type,
            "importance": importance,
        }
        if metadata:
            meta.update(metadata)

        try:
            self._collection.add(
                ids=[mem_id],
                documents=[document],
                metadatas=[meta],
            )
            logger.debug(
                f"LongTermMemory: stored '{mem_id}' "
                f"(agent={agent_id}, subtree={subtree_id}, "
                f"importance={importance})"
            )
            return mem_id
        except Exception as e:
            logger.error(f"Failed to store memory: {e}")
            return None

    def search(
        self,
        query: str,
        *,
        subtree_id: str | None = None,
        agent_id: str | None = None,
        top_k: int = 5,
        min_importance: int = 0,
    ) -> list[dict[str, Any]]:
        """Search long-term memory by semantic similarity.

        Parameters
        ----------
        query : str
            Natural language query.
        subtree_id : str or None
            Filter to a specific subtree. Pass ``None`` to search
            globally (including global rules).
        agent_id : str or None
            Optional agent filter.
        top_k : int
            Number of results to return.
        min_importance : int
            Minimum importance threshold.

        Returns
        -------
        list[dict]
            Each result has: ``id``, ``document``, ``metadata``,
            ``distance`` (lower = more similar with cosine).
        """
        if not self.enabled:
            return []

        # Build Chroma where-filter
        where: dict[str, Any] | None = None
        conditions = []
        if subtree_id is not None:
            conditions.append({"subtree_id": subtree_id})
        if agent_id is not None:
            conditions.append({"agent_id": agent_id})
        if min_importance > 0:
            conditions.append({"importance": {"$gte": min_importance}})

        if len(conditions) == 1:
            where = conditions[0]
        elif len(conditions) > 1:
            where = {"$and": conditions}

        # If no subtree filter, search both global + agent-specific
        # by doing two queries and merging.
        if subtree_id is None:
            return self._search_multi_scope(
                query, agent_id=agent_id, top_k=top_k,
                min_importance=min_importance,
            )

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=top_k,
                where=where if where else None,
                include=["documents", "metadatas", "distances"],
            )

            if not results or not results.get("ids") or not results["ids"][0]:
                return []

            return [
                {
                    "id": mid,
                    "document": doc,
                    "metadata": meta,
                    "distance": dist,
                }
                for mid, doc, meta, dist in zip(
                    results["ids"][0],
                    results["documents"][0],
                    results["metadatas"][0],
                    results["distances"][0],
                )
            ]
        except Exception as e:
            logger.error(f"LongTermMemory search failed: {e}")
            return []

    def _search_multi_scope(
        self,
        query: str,
        agent_id: str | None = None,
        top_k: int = 5,
        min_importance: int = 0,
    ) -> list[dict[str, Any]]:
        """Search across global scope (no subtree filter).

        Queries the collection without subtree restriction, then
        sorts by distance and returns top_k.
        """
        where: dict[str, Any] | None = None
        conditions = []
        if agent_id is not None:
            conditions.append({"agent_id": agent_id})
        if min_importance > 0:
            conditions.append({"importance": {"$gte": min_importance}})

        if len(conditions) == 1:
            where = conditions[0]
        elif len(conditions) > 1:
            where = {"$and": conditions}

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=top_k * 2,  # over-fetch, then trim
                where=where if where else None,
                include=["documents", "metadatas", "distances"],
            )

            if not results or not results.get("ids") or not results["ids"][0]:
                return []

            items = [
                {
                    "id": mid,
                    "document": doc,
                    "metadata": meta,
                    "distance": dist,
                }
                for mid, doc, meta, dist in zip(
                    results["ids"][0],
                    results["documents"][0],
                    results["metadatas"][0],
                    results["distances"][0],
                )
            ]
            items.sort(key=lambda x: x["distance"])
            return items[:top_k]
        except Exception as e:
            logger.error(f"LongTermMemory multi-scope search failed: {e}")
            return []

    def get_insights(
        self,
        agent_id: str,
        subtree_id: str,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Get the most important recent insights for an agent.

        Returns entries with importance >= MEDIUM, sorted by
        recency (most recent first).
        """
        if not self.enabled:
            return []

        try:
            # ChromaDB's get() doesn't support compound where ($gte),
            # so we use query() with a broad query string that matches
            # all documents, filtered by subtree + importance.
            results = self._collection.query(
                query_texts=["important insight rule decision task"],
                n_results=top_k,
                where={
                    "$and": [
                        {"subtree_id": subtree_id},
                        {"importance": {"$gte": IMPORTANCE_MEDIUM}},
                    ],
                },
                include=["documents", "metadatas", "distances"],
            )

            if not results or not results.get("ids") or not results["ids"][0]:
                return []

            items = [
                {
                    "id": mid,
                    "document": doc,
                    "metadata": meta,
                    "distance": dist,
                }
                for mid, doc, meta, dist in zip(
                    results["ids"][0],
                    results["documents"][0],
                    results["metadatas"][0],
                    results["distances"][0],
                )
            ]
            # Sort by timestamp descending (most recent first)
            items.sort(
                key=lambda x: x["metadata"].get("timestamp", ""),
                reverse=True,
            )
            return items
        except Exception as e:
            logger.error(f"Failed to get insights: {e}")
            return []

    def add_global_rule(
        self,
        rule: str,
        agent_id: str = "global",
    ) -> str | None:
        """Store a critical rule in long-term memory.

        Rules are stored with subtree_id="global" and maximum
        importance so they are always retrieved.
        """
        return self.add_memory(
            f"[CRITICAL RULE] {rule}",
            agent_id=agent_id,
            subtree_id="global",
            task_type="rule",
            importance=IMPORTANCE_HIGH,
            metadata={"source": "critical_rules"},
        )

    def summarize_session_to_longterm(
        self,
        messages: list[dict[str, str]],
        agent_id: str,
        subtree_id: str,
    ) -> int:
        """Extract summaries and insights from a session and store them.

        This is a lightweight, rule-based extraction (no LLM call).
        For a full LLM-based summarization, see
        ``AgentRegistry._summarize_to_longterm()``.

        Returns the number of memories stored.
        """
        if not self.enabled or not messages:
            return 0

        count = 0
        # ── Extract key exchanges ──────────────────────────
        # Every exchange where the assistant's reply is
        # substantial (>200 chars) is saved as a memory.
        for i in range(len(messages) - 1):
            user_msg = messages[i]
            asst_msg = messages[i + 1]

            if user_msg.get("role") != "user":
                continue
            if asst_msg.get("role") != "assistant":
                continue

            reply = (asst_msg.get("content") or "").strip()
            if len(reply) < 200:
                continue

            # Build a concise summary: first sentence of the reply
            summary = reply.split(".")[0].strip()[:500]
            if summary:
                question = (user_msg.get("content") or "")[:200]
                self.add_memory(
                    f"Q: {question}\nA: {summary}",
                    agent_id=agent_id,
                    subtree_id=subtree_id,
                    task_type="exchange",
                    importance=IMPORTANCE_LOW,
                )
                count += 1

        return count

    def count(self) -> int:
        """Total number of stored memories."""
        if not self.enabled:
            return 0
        try:
            return self._collection.count()
        except Exception:
            return 0

    def clear_subtree(self, subtree_id: str) -> int:
        """Delete all memories for a given subtree. Returns count deleted."""
        if not self.enabled:
            return 0
        try:
            results = self._collection.get(
                where={"subtree_id": subtree_id},
                include=[],
            )
            ids = results.get("ids", []) if results else []
            if ids:
                self._collection.delete(ids=ids)
                logger.info(
                    f"LongTermMemory: cleared {len(ids)} entries "
                    f"from subtree '{subtree_id}'"
                )
            return len(ids)
        except Exception as e:
            logger.error(f"Failed to clear subtree: {e}")
            return 0
