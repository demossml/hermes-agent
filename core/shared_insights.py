"""
Shared Insights — cross-agent knowledge exchange for Hermes Multi-Agent.

When one agent learns something useful (a fix, a pattern, a pitfall),
other agents in the same project can access it automatically.

Piggybacks on ChromaDB long-term memory.  Zero extra dependencies.
Falls back to in-memory cache when ChromaDB is not installed.

Usage (called automatically by AgentRegistry)::

    from core.shared_insights import get_insights
    ins = get_insights()

    # Agent A discovers something
    ins.share("coder", "SQLite FTS5 requires content= for external content tables")

    # Agent B searches for relevant knowledge
    results = ins.search("researcher", "FTS5", k=3)

    # Auto-injected into agent context before call()
    context = ins.get_context_for("researcher", "FTS5 error debugging")
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Dict, List, Optional


class SharedInsights:
    """Cross-agent knowledge store.

    Simple key-value + search over shared agent discoveries.
    Falls back to in-memory list when ChromaDB is absent.
    """

    _instance: Optional["SharedInsights"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._entries: List[Dict[str, Any]] = []
        self._chroma_available: bool = False
        self._chroma_collection: Any = None
        self._try_init_chroma()

    @classmethod
    def get_instance(cls) -> "SharedInsights":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _try_init_chroma(self) -> None:
        """Try to initialise ChromaDB backend."""
        try:
            import chromadb
            import os
            home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
            path = os.path.join(home, "longterm_memory", "shared_insights")
            client = chromadb.PersistentClient(path=path)
            self._chroma_collection = client.get_or_create_collection(
                "shared_insights",
                metadata={"description": "Cross-agent shared knowledge"},
            )
            self._chroma_available = True
        except Exception:
            self._chroma_available = False

    # ── Share ──────────────────────────────────────────────

    def share(
        self,
        agent_id: str,
        insight: str,
        category: str = "general",
        importance: str = "medium",
    ) -> bool:
        """Share a discovery with all agents in the project.

        Args:
            agent_id:  Who discovered this.
            insight:   The knowledge text.
            category:  ``"fix"``, ``"pattern"``, ``"pitfall"``, ``"general"``.
            importance: ``"low"``, ``"medium"``, ``"high"``.

        Returns True on success.
        """
        entry = {
            "agent_id": agent_id,
            "insight": insight,
            "category": category,
            "importance": importance,
            "timestamp": datetime.now().isoformat(),
        }

        # In-memory fallback (always works)
        self._entries.append(entry)
        # Trim to 500 entries max
        if len(self._entries) > 500:
            self._entries = self._entries[-500:]

        # ChromaDB (best-effort)
        if self._chroma_available and self._chroma_collection:
            try:
                import uuid
                self._chroma_collection.add(
                    ids=[f"insight-{uuid.uuid4().hex[:12]}"],
                    documents=[insight],
                    metadatas=[{
                        "agent_id": agent_id,
                        "category": category,
                        "importance": importance,
                        "timestamp": entry["timestamp"],
                    }],
                )
            except Exception:
                pass

        return True

    # ── Search ─────────────────────────────────────────────

    def search(
        self,
        agent_id: str,
        query: str,
        k: int = 5,
    ) -> List[Dict[str, Any]]:
        """Search shared insights relevant to a query.

        Args:
            agent_id:  The agent requesting knowledge (for logging).
            query:     Search query.
            k:         Max results.

        Returns list of {agent_id, insight, category, importance, score}.
        """
        results: List[Dict[str, Any]] = []

        # ChromaDB semantic search (preferred)
        if self._chroma_available and self._chroma_collection:
            try:
                chroma_results = self._chroma_collection.query(
                    query_texts=[query],
                    n_results=min(k, 20),
                )
                if chroma_results and chroma_results.get("ids"):
                    ids_list = chroma_results["ids"]
                    docs_list = chroma_results.get("documents", [[]])
                    metas_list = chroma_results.get("metadatas", [[]])
                    distances = chroma_results.get("distances", [[]])

                    for i, ids in enumerate(ids_list):
                        for j, _ in enumerate(ids):
                            result = {
                                "agent_id": (metas_list[i][j] or {}).get("agent_id", "?"),
                                "insight": docs_list[i][j] if i < len(docs_list) and j < len(docs_list[i]) else "",
                                "category": (metas_list[i][j] or {}).get("category", "general"),
                                "importance": (metas_list[i][j] or {}).get("importance", "medium"),
                                "score": round(1.0 - (distances[i][j] if i < len(distances) and j < len(distances[i]) else 0.5), 3),
                            }
                            results.append(result)
                    return results[:k]
            except Exception:
                pass

        # In-memory keyword fallback
        query_lower = query.lower()
        scored = []
        for entry in self._entries:
            insight_lower = entry["insight"].lower()
            # Simple word-overlap score
            score = sum(
                1 for word in query_lower.split()
                if word in insight_lower
            )
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        for score, entry in scored[:k]:
            results.append({
                "agent_id": entry["agent_id"],
                "insight": entry["insight"],
                "category": entry["category"],
                "importance": entry["importance"],
                "score": min(score / max(len(query_lower.split()), 1), 1.0),
            })

        return results

    # ── Context injection ──────────────────────────────────

    def get_context_for(
        self,
        agent_id: str,
        query: str,
        k: int = 3,
    ) -> str:
        """Return a formatted context block for injection before call().

        Returns empty string if nothing relevant found.
        """
        results = self.search(agent_id, query, k=k)
        if not results:
            return ""

        lines = ["[SHARED INSIGHTS — knowledge from other agents]"]
        for r in results:
            cat_icon = {"fix": "🔧", "pattern": "🔄", "pitfall": "⚠️", "general": "💡"}.get(
                r["category"], "💡"
            )
            lines.append(f"{cat_icon} [{r['agent_id']}] {r['insight']}")

        return "\n".join(lines)

    # ── Stats ──────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "total_insights": len(self._entries),
            "chromadb_available": self._chroma_available,
            "agents_contributed": len(set(e["agent_id"] for e in self._entries)),
        }


def get_insights() -> SharedInsights:
    """Return the global SharedInsights singleton."""
    return SharedInsights.get_instance()
