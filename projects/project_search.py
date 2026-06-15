"""
Project Search Engine — unified search across all project data.

Searches across:
- ChromaDB long-term vector memory (per-project collections)
- DuckDB workflow history (workflows.duckdb)
- Project files on disk (via ripgrep)
- DuckDB chat history (chat_history.duckdb with project_id filter)

Usage::

    from projects.project_search import ProjectSearchEngine

    engine = ProjectSearchEngine()
    results = engine.search("auth bug", scope="current")   # current project only
    results = engine.search("refactor", scope="all")        # all projects

Each result is a dict:
    {"source": "ltm", "project_id": "my-app", "project_name": "My App",
     "title": "...", "snippet": "...", "score": 0.95}
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _get_hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home()
    except ImportError:
        import os
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


# ═══════════════════════════════════════════════════════════════
# ProjectSearchEngine
# ═══════════════════════════════════════════════════════════════

class ProjectSearchEngine:
    """Search across all data sources for one or all projects.

    Parameters
    ----------
    hermes_home : str or Path, optional
        Override Hermes home directory.
    """

    def __init__(self, hermes_home: str | Path | None = None):
        self._home = Path(hermes_home) if hermes_home else _get_hermes_home()
        self._projects_dir = self._home / "projects"

    # ── Public API ────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        scope: str = "current",
        project_id: str | None = None,
        max_per_source: int = 10,
        sources: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search across projects.

        Parameters
        ----------
        query : str
            Search query.
        scope : str
            ``"current"`` — only the active project.
            ``"all"`` — every known project.
        project_id : str, optional
            Explicit project to search (overrides scope).
        max_per_source : int
            Max results per data source per project.
        sources : list[str], optional
            Which sources to search.  Default: all available.
            Options: ``"ltm"``, ``"workflows"``, ``"files"``, ``"chat"``.

        Returns
        -------
        list[dict]
            Each result: source, project_id, project_name, title, snippet, score.
            Sorted by score descending.
        """
        if not query or not query.strip():
            return []

        query = query.strip()
        all_sources = sources or ["ltm", "workflows", "files", "chat"]

        # Resolve projects to search
        project_ids = self._resolve_projects(scope, project_id)
        if not project_ids:
            return []

        results: list[dict[str, Any]] = []
        for pid in project_ids:
            pname = self._get_project_name(pid)
            for source in all_sources:
                try:
                    if source == "ltm":
                        results.extend(
                            self._search_ltm(query, pid, pname, max_per_source)
                        )
                    elif source == "workflows":
                        results.extend(
                            self._search_workflows(query, pid, pname, max_per_source)
                        )
                    elif source == "files":
                        results.extend(
                            self._search_files(query, pid, pname, max_per_source)
                        )
                    elif source == "chat":
                        results.extend(
                            self._search_chat(query, pid, pname, max_per_source)
                        )
                except Exception as e:
                    logger.debug(
                        f"Search source '{source}' for project '{pid}' failed: {e}"
                    )

        # Sort by score descending
        results.sort(key=lambda r: r.get("score", 0), reverse=True)
        return results

    # ── Internal ─────────────────────────────────────────────

    def _resolve_projects(
        self, scope: str, project_id: str | None,
    ) -> list[str]:
        """Return list of project_ids to search."""
        if project_id:
            return [project_id]

        if scope == "current":
            try:
                from projects.project_context import get_current_project_id
                pid = get_current_project_id()
                return [pid] if pid else []
            except Exception:
                return []

        # scope == "all"
        try:
            from projects.project_manager import ProjectManager
            pm = ProjectManager(hermes_home=self._home)
            return [p["project_id"] for p in pm.list_projects()]
        except Exception:
            return []

    def _get_project_name(self, project_id: str) -> str:
        try:
            from projects.project_manager import ProjectManager
            pm = ProjectManager(hermes_home=self._home)
            proj = pm.get_project(project_id)
            return proj["name"] if proj else project_id
        except Exception:
            return project_id

    # ── Source: LTM (ChromaDB) ────────────────────────────

    def _search_ltm(
        self, query: str, project_id: str, project_name: str, limit: int,
    ) -> list[dict[str, Any]]:
        """Search ChromaDB collection for this project."""
        try:
            from memory import LongTermMemory, HAS_CHROMA
            if not HAS_CHROMA:
                return []

            collection_name = f"project_{project_id}"
            chroma_dir = self._projects_dir / project_id / "memory" / "chroma"

            if not chroma_dir.exists():
                return []

            import chromadb
            from chromadb.config import Settings as ChromaSettings

            client = chromadb.PersistentClient(
                path=str(chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            collection = client.get_collection(name=collection_name)

            raw = collection.query(
                query_texts=[query],
                n_results=limit,
                include=["documents", "metadatas", "distances"],
            )

            results = []
            if raw and raw.get("ids") and raw["ids"][0]:
                for mid, doc, meta, dist in zip(
                    raw["ids"][0],
                    raw["documents"][0],
                    raw["metadatas"][0],
                    raw["distances"][0],
                ):
                    # Cosine distance → score (1 - distance, clamped)
                    score = max(0.0, 1.0 - float(dist))
                    results.append({
                        "source": "ltm",
                        "project_id": project_id,
                        "project_name": project_name,
                        "title": meta.get("task_type", "memory"),
                        "snippet": doc[:300],
                        "score": round(score, 4),
                        "meta": {
                            "agent_id": meta.get("agent_id", ""),
                            "timestamp": meta.get("timestamp", ""),
                            "importance": meta.get("importance", 0),
                        },
                    })
            return results
        except Exception as e:
            logger.debug(f"LTM search failed for {project_id}: {e}")
            return []

    # ── Source: Workflows (DuckDB) ─────────────────────────

    def _search_workflows(
        self, query: str, project_id: str, project_name: str, limit: int,
    ) -> list[dict[str, Any]]:
        """Search DuckDB workflow history."""
        try:
            import duckdb
        except ImportError:
            return []

        db_path = self._home / "data" / "workflows.duckdb"
        if not db_path.exists():
            return []

        try:
            conn = duckdb.connect(str(db_path), read_only=True)
            like_query = f"%{query}%"
            rows = conn.execute(
                """SELECT id, task, status, iteration, max_iter,
                          coder_id, tester_id, final_review, updated_at
                   FROM workflows
                   WHERE (task ILIKE ? OR final_review ILIKE ?)
                   ORDER BY updated_at DESC
                   LIMIT ?""",
                [like_query, like_query, limit],
            ).fetchall()

            results = []
            for row in rows:
                tid, task, status, iteration, max_iter, coder, tester, review, updated = row
                snippet = (task or "")[:200]
                if review:
                    # Try to find the matching part in review
                    rl = (review or "").lower()
                    ql = query.lower()
                    idx = rl.find(ql)
                    if idx >= 0:
                        start = max(0, idx - 40)
                        snippet = review[start:start + 200]

                results.append({
                    "source": "workflows",
                    "project_id": project_id,
                    "project_name": project_name,
                    "title": f"Workflow: {status} ({iteration}/{max_iter})",
                    "snippet": snippet,
                    "score": 0.7,
                    "meta": {
                        "task_id": tid,
                        "coder": coder or "",
                        "tester": tester or "",
                        "updated": str(updated) if updated else "",
                    },
                })
            return results
        except Exception as e:
            logger.debug(f"Workflow search failed: {e}")
            return []

    # ── Source: Project files (ripgrep) ────────────────────

    def _search_files(
        self, query: str, project_id: str, project_name: str, limit: int,
    ) -> list[dict[str, Any]]:
        """Search project directory files with ripgrep."""
        project_dir = self._projects_dir / project_id
        if not project_dir.exists():
            return []

        # Skip binary/large dirs
        skip_dirs = ["chroma", "__pycache__", ".git", "node_modules", ".venv"]
        ignore_args = []
        for d in skip_dirs:
            ignore_args.extend(["--glob", f"!{d}/**"])

        try:
            result = subprocess.run(
                [
                    "rg", "--line-number", "--no-heading",
                    "--max-count", str(limit),
                    "--max-filesize", "1M",
                    *ignore_args,
                    "--", query,
                    str(project_dir),
                ],
                capture_output=True, text=True, timeout=10,
            )
            lines = result.stdout.strip().split("\n")[:limit]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            # rg not installed or timeout — skip file search
            return []
        except Exception:
            return []

        results = []
        for line in lines:
            if not line.strip():
                continue
            # rg output: path:line_number:content
            parts = line.split(":", 2)
            if len(parts) >= 3:
                fpath, lnum, content = parts[0], parts[1], parts[2]
                # Make path relative to project dir
                try:
                    rel_path = Path(fpath).relative_to(project_dir)
                except ValueError:
                    rel_path = Path(fpath).name
                results.append({
                    "source": "files",
                    "project_id": project_id,
                    "project_name": project_name,
                    "title": f"{rel_path}:{lnum}",
                    "snippet": content.strip()[:300],
                    "score": 0.5,
                    "meta": {"file": str(rel_path), "line": lnum},
                })
        return results[:limit]

    # ── Source: Chat History (DuckDB) ───────────────────────

    def _search_chat(
        self, query: str, project_id: str, project_name: str, limit: int,
    ) -> list[dict[str, Any]]:
        """Search DuckDB chat history filtered by project_id."""
        try:
            import duckdb
        except ImportError:
            return []

        db_path = self._home / "data" / "chat_history.duckdb"
        if not db_path.exists():
            return []

        try:
            conn = duckdb.connect(str(db_path), read_only=True)
            like_query = f"%{query}%"
            rows = conn.execute(
                """SELECT sender_name, text, timestamp, chat_title
                   FROM group_messages
                   WHERE (project_id = ? OR project_id IS NULL)
                     AND text ILIKE ?
                   ORDER BY timestamp DESC
                   LIMIT ?""",
                [project_id, like_query, limit],
            ).fetchall()

            results = []
            for row in rows:
                sender, text, ts, chat_title = row
                snippet = (text or "")[:300]
                results.append({
                    "source": "chat",
                    "project_id": project_id,
                    "project_name": project_name,
                    "title": f"Chat: {chat_title or '?'} / {sender or '?'}",
                    "snippet": snippet,
                    "score": 0.3,
                    "meta": {
                        "sender": sender or "",
                        "chat_title": chat_title or "",
                        "timestamp": str(ts) if ts else "",
                    },
                })
            return results
        except Exception as e:
            logger.debug(f"Chat search failed: {e}")
            return []


# ═══════════════════════════════════════════════════════════════
# Convenience function for CLI
# ═══════════════════════════════════════════════════════════════

def search_projects(
    query: str,
    *,
    scope: str = "current",
    project_id: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """One-liner: search across projects. Returns results sorted by score."""
    engine = ProjectSearchEngine()
    return engine.search(query, scope=scope, project_id=project_id, max_per_source=limit)
