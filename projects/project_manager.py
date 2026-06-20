"""
Project Manager — multi-project isolation for Hermes Agent.

Each project is a fully self-contained directory:

    ~/.hermes/projects/<slug>/
    ├── metadata.json
    ├── data/                  (DuckDB + observers)
    ├── memory/chroma/         (ChromaDB)
    ├── state/workflows/       (snapshots)
    └── agents/                (YAML configs)
"""

from __future__ import annotations

import json, logging, re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False; ChromaSettings = None; chromadb = None  # type: ignore


def _get_hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home()
    except ImportError:
        import os
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    translit = {"а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"yo","ж":"zh","з":"z","и":"i","й":"y","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f","х":"h","ц":"ts","ч":"ch","ш":"sh","щ":"sch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya"}
    for cyr, lat in translit.items():
        slug = slug.replace(cyr, lat)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    return slug.strip("-") or "untitled-project"


def _ensure_unique_id(base_slug: str, existing_ids: set[str]) -> str:
    if base_slug not in existing_ids:
        return base_slug
    i = 2
    while f"{base_slug}-{i}" in existing_ids:
        i += 1
    return f"{base_slug}-{i}"


def _new_metadata(project_id: str, name: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {"project_id": project_id, "name": name,
            "subtree_session_id": f"project-{project_id}",
            "chroma_collection": f"project_{project_id}",
            "created_at": now, "updated_at": now}


class ProjectManager:
    """Manage Hermes projects with isolated memory and sessions."""

    def __init__(self, hermes_home: str | Path | None = None):
        self._home = Path(hermes_home) if hermes_home else _get_hermes_home()
        self._projects_dir = self._home / "projects"
        self._projects_dir.mkdir(parents=True, exist_ok=True)
        self._current_file = self._projects_dir / ".current_project"

    @property
    def projects_dir(self) -> Path:
        return self._projects_dir

    def _project_dir(self, project_id: str) -> Path:
        return self._projects_dir / project_id

    def _metadata_path(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "metadata.json"

    def _ensure_subdirs(self, project_id: str) -> None:
        root = self._project_dir(project_id)
        for sub in ["data", "memory/chroma", "state/workflows", "agents", "code"]:
            (root / sub).mkdir(parents=True, exist_ok=True)

    def ensure_project_structure(self, project_id: str, name: str | None = None) -> dict[str, Any]:
        """Ensure a project has complete structure — idempotent, safe to re-run.

        Creates or repairs:
        - ``metadata.json`` — project identity (id, name, subtree, chroma, timestamps)
        - ``project.yaml`` — project configuration (prefix, emoji, auto_save, etc.)
        - All subdirectories (data/, memory/chroma/, state/workflows/, agents/, code/)

        Returns the complete metadata dict.  Never overwrites existing
        user-configured values in project.yaml.
        """
        import yaml

        root = self._project_dir(project_id)
        self._ensure_subdirs(project_id)

        # ── metadata.json ──────────────────────────────────
        meta_path = root / "metadata.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        else:
            meta = {}

        # Fill in missing metadata fields
        display_name = name or meta.get("name") or project_id
        now = datetime.now(timezone.utc).isoformat()
        meta.setdefault("project_id", project_id)
        meta.setdefault("name", display_name)
        meta.setdefault("subtree_session_id", f"project-{project_id}")
        meta.setdefault("chroma_collection", f"project_{project_id}")
        if "created_at" not in meta:
            meta["created_at"] = now
        meta["updated_at"] = now

        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # ── project.yaml ───────────────────────────────────
        yaml_path = root / "project.yaml"
        if yaml_path.exists():
            try:
                with open(yaml_path) as f:
                    config = yaml.safe_load(f) or {}
            except Exception:
                config = {}
        else:
            config = {}

        # Defaults — never overwrite existing values
        config.setdefault("show_project_prefix", True)
        config.setdefault("prefix_emoji", True)
        config.setdefault("auto_save_code", True)
        config.setdefault("auto_save_state", True)
        config.setdefault("default_language", "python")
        config.setdefault("description", f"Project: {display_name}")

        with open(yaml_path, "w") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

        logger.info(
            f"Project '{project_id}' structure ensured "
            f"(metadata={'repaired' if not meta_path.exists() else 'ok'}, "
            f"yaml={'created' if not yaml_path.exists() else 'ok'})"
        )

        # ── SOUL.md for project agents (idempotent) ─────────
        soul_path = root / "agents" / "SOUL.md"
        if not soul_path.exists():
            soul_path.write_text(
                f"# {display_name}\n\n"
                f"Project ID: `{project_id}`\n\n"
                f"All agents in this project share:\n"
                f"- Memory: `project-{project_id}/` subtree\n"
                f"- ChromaDB: `project_{project_id}` collection\n"
                f"- Data: `~/.hermes/projects/{project_id}/data/`\n\n"
                f"## Rules\n\n"
                f"- Work STRICTLY within this project\n"
                f"- Do NOT access other projects' data\n"
                f"- Use the project's memory and tools\n",
                encoding="utf-8",
            )
            logger.info(f"Project '{project_id}' SOUL.md created")

        return meta

    def subdir_data(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "data"

    def subdir_memory(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "memory" / "chroma"

    def subdir_state(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "state"

    def subdir_agents(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "agents"

    def _read_metadata(self, project_id: str) -> dict[str, Any] | None:
        path = self._metadata_path(project_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read metadata for '{project_id}': {e}")
            return None

    def _write_metadata(self, project_id: str, meta: dict[str, Any]) -> None:
        path = self._metadata_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _list_ids(self) -> list[str]:
        if not self._projects_dir.exists():
            return []
        return sorted(d.name for d in self._projects_dir.iterdir()
                      if d.is_dir() and (d / "metadata.json").exists()
                      and not d.name.startswith("."))

    def _read_current(self) -> str | None:
        try:
            if self._current_file.exists():
                return self._current_file.read_text(encoding="utf-8").strip() or None
        except OSError:
            pass
        return None

    def _write_current(self, project_id: str) -> None:
        self._current_file.write_text(project_id + "\n", encoding="utf-8")

    def _ensure_chroma_collection(self, project_id: str) -> bool:
        if not HAS_CHROMA:
            return True
        collection_name = f"project_{project_id}"
        chroma_dir = self.subdir_memory(project_id)
        chroma_dir.mkdir(parents=True, exist_ok=True)
        try:
            client = chromadb.PersistentClient(path=str(chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False))
            client.get_or_create_collection(name=collection_name,
                metadata={"hnsw:space": "cosine"})
            return True
        except Exception as e:
            logger.warning(f"ChromaDB init failed for '{project_id}': {e}")
            return False

    # ── Shared Insights ──────────────────────────────────────

    def ensure_insights_collection(self, project_id: str) -> bool:
        """Create/get the shared insights ChromaDB collection."""
        if not HAS_CHROMA:
            return False
        coll_name = f"project_{project_id}_insights"
        chroma_dir = self.subdir_memory(project_id)
        try:
            client = chromadb.PersistentClient(path=str(chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False))
            client.get_or_create_collection(name=coll_name,
                metadata={"hnsw:space": "cosine"})
            return True
        except Exception as e:
            logger.warning(f"Insights ChromaDB init failed for '{project_id}': {e}")
            return False

    def add_insight(
        self, project_id: str, text: str,
        importance: float = 0.5, source: str = "manual",
    ) -> str | None:
        """Add an insight to the project's shared knowledge base.

        Returns the insight ID or None on failure.
        """
        if not HAS_CHROMA or not text.strip():
            return None
        try:
            self.ensure_insights_collection(project_id)
            coll_name = f"project_{project_id}_insights"
            chroma_dir = self.subdir_memory(project_id)
            client = chromadb.PersistentClient(path=str(chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False))
            coll = client.get_or_create_collection(name=coll_name,
                metadata={"hnsw:space": "cosine"})

            import uuid, time
            doc_id = f"insight-{project_id}-{uuid.uuid4().hex[:8]}"
            coll.add(
                ids=[doc_id],
                documents=[text],
                metadatas=[{
                    "importance": importance,
                    "source": source,
                    "timestamp": time.time(),
                }],
            )
            logger.info(f"Insight added to '{project_id}': {text[:60]}...")
            return doc_id
        except Exception as e:
            logger.warning(f"Failed to add insight to '{project_id}': {e}")
            return None

    def get_relevant_insights(
        self, project_id: str, query: str, n_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve top-N relevant insights for a task context."""
        if not HAS_CHROMA:
            return []
        try:
            coll_name = f"project_{project_id}_insights"
            chroma_dir = self.subdir_memory(project_id)
            if not (chroma_dir / "chroma.sqlite3").exists():
                return []
            client = chromadb.PersistentClient(path=str(chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False))
            try:
                coll = client.get_collection(name=coll_name)
            except Exception:
                return []

            results = coll.query(query_texts=[query], n_results=n_results)
            if not results.get("ids") or not results["ids"][0]:
                return []

            insights = []
            for i, doc_id in enumerate(results["ids"][0]):
                doc = results["documents"][0][i] if results.get("documents") and results["documents"][0] else ""
                meta = results["metadatas"][0][i] if results.get("metadatas") and results["metadatas"][0] else {}
                insights.append({
                    "id": doc_id, "text": doc,
                    "importance": meta.get("importance", 0.5),
                    "source": meta.get("source", "unknown"),
                })
            return insights
        except Exception as e:
            logger.debug(f"Failed to get insights for '{project_id}': {e}")
            return insights

    # ── Research Memory ─────────────────────────────────────

    def save_research_history(
        self, project_id: str, topic: str, report_text: str,
    ) -> str | None:
        """Save completed research to project history.

        Returns the filepath or None on failure.
        """
        import re
        from datetime import datetime

        history_dir = self._project_dir(project_id) / "research" / "history"
        history_dir.mkdir(parents=True, exist_ok=True)

        safe_topic = re.sub(r"[^a-z0-9_а-яё-]+", "_", topic.lower().strip())[:40]
        date_str = datetime.now().strftime("%Y%m%d")
        fname = f"{safe_topic}_{date_str}.md"
        fpath = history_dir / fname

        # Avoid overwriting — append counter if exists
        if fpath.exists():
            base = fname.replace(".md", "")
            i = 2
            while (history_dir / f"{base}_{i}.md").exists():
                i += 1
            fpath = history_dir / f"{base}_{i}.md"

        fpath.write_text(report_text, encoding="utf-8")
        logger.info("Research history saved: %s", fpath)

        # Also store in vector memory
        self._add_to_research_memory(project_id, topic, report_text, str(fpath))
        return str(fpath)

    def _add_to_research_memory(
        self, project_id: str, topic: str, text: str, filepath: str,
    ) -> None:
        """Index research in ChromaDB for semantic recall."""
        if not HAS_CHROMA:
            return
        try:
            coll_name = f"project_{project_id}_research_memory"
            chroma_dir = self.subdir_memory(project_id)
            client = chromadb.PersistentClient(path=str(chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False))
            coll = client.get_or_create_collection(name=coll_name,
                metadata={"hnsw:space": "cosine"})

            import uuid, time
            doc_id = f"research-{project_id}-{uuid.uuid4().hex[:8]}"
            coll.add(
                ids=[doc_id],
                documents=[f"{topic}\n\n{text[:2000]}"],
                metadatas=[{
                    "topic": topic,
                    "filepath": filepath,
                    "timestamp": time.time(),
                }],
            )
            logger.debug("Research indexed: %s → %s", topic[:40], doc_id)
        except Exception as e:
            logger.debug("Failed to index research: %s", e)

    def recall_research(
        self, project_id: str, query: str, n_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Semantic recall of past research relevant to query."""
        if not HAS_CHROMA:
            return []
        try:
            coll_name = f"project_{project_id}_research_memory"
            chroma_dir = self.subdir_memory(project_id)
            if not (chroma_dir / "chroma.sqlite3").exists():
                return []
            client = chromadb.PersistentClient(path=str(chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False))
            try:
                coll = client.get_collection(name=coll_name)
            except Exception:
                return []

            results = coll.query(query_texts=[query], n_results=n_results)
            if not results.get("ids") or not results["ids"][0]:
                return []

            items = []
            for i, doc_id in enumerate(results["ids"][0]):
                doc = results["documents"][0][i] if results["documents"] else ""
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                items.append({
                    "id": doc_id,
                    "text": (doc or "")[:300],
                    "topic": meta.get("topic", ""),
                    "filepath": meta.get("filepath", ""),
                })
            return items
        except Exception as e:
            logger.debug("Research recall failed: %s", e)
            return []

    def create_project(self, name: str) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("Project name must not be empty")
        existing = set(self._list_ids())
        project_id = _ensure_unique_id(_slugify(name), existing)
        meta = self.ensure_project_structure(project_id, name=name)
        self._ensure_chroma_collection(project_id)
        self._write_current(project_id)
        meta["project_dir"] = str(self._project_dir(project_id))
        logger.info(f"Project '{name}' created (id={project_id})")
        return dict(meta)

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        meta = self._read_metadata(project_id)
        if meta:
            meta["project_dir"] = str(self._project_dir(project_id))
        return meta

    def list_projects(self, include_archived: bool = False) -> list[dict[str, Any]]:
        items = []
        for pid in self._list_ids():
            meta = self._read_metadata(pid)
            if meta:
                if not include_archived and meta.get("archived"):
                    continue
                meta["project_dir"] = str(self._project_dir(pid))
                items.append(meta)
        items.sort(key=lambda p: p.get("created_at", ""), reverse=True)
        return items

    def get_current_project(self) -> dict[str, Any] | None:
        current_id = self._read_current()
        if not current_id:
            return None
        return self._read_metadata(current_id)

    def switch_project(self, project_id: str) -> dict[str, Any]:
        meta = self._read_metadata(project_id)
        if not meta:
            available = ", ".join(self._list_ids()) or "(none)"
            raise ValueError(f"Project '{project_id}' not found. Available: {available}")
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_metadata(project_id, meta)
        self._write_current(project_id)
        meta["project_dir"] = str(self._project_dir(project_id))
        logger.info(f"Switched to project '{project_id}'")
        return dict(meta)

    def delete_project(self, project_id: str) -> bool:
        meta = self._read_metadata(project_id)
        if not meta:
            return False
        self._metadata_path(project_id).unlink(missing_ok=True)
        if self._read_current() == project_id:
            self._write_current("")
        logger.info(f"Project '{project_id}' deleted")
        return True

    def rename_project(self, project_id: str, new_name: str) -> dict[str, Any]:
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("New project name must not be empty")
        meta = self._read_metadata(project_id)
        if not meta:
            available = ", ".join(self._list_ids()) or "(none)"
            raise ValueError(f"Project '{project_id}' not found. Available: {available}")
        meta["name"] = new_name
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_metadata(project_id, meta)
        meta["project_dir"] = str(self._project_dir(project_id))
        logger.info(f"Project '{project_id}' renamed to '{new_name}'")
        return dict(meta)

    def archive_project(self, project_id: str) -> dict[str, Any]:
        meta = self._read_metadata(project_id)
        if not meta:
            available = ", ".join(self._list_ids()) or "(none)"
            raise ValueError(f"Project '{project_id}' not found. Available: {available}")
        meta["archived"] = True
        meta["archived_at"] = datetime.now(timezone.utc).isoformat()
        meta["updated_at"] = meta["archived_at"]
        self._write_metadata(project_id, meta)
        if self._read_current() == project_id:
            self._write_current("")
        meta["project_dir"] = str(self._project_dir(project_id))
        logger.info(f"Project '{project_id}' archived")
        return dict(meta)

    def unarchive_project(self, project_id: str) -> dict[str, Any]:
        meta = self._read_metadata(project_id)
        if not meta:
            raise ValueError(f"Project '{project_id}' not found.")
        meta.pop("archived", None)
        meta.pop("archived_at", None)
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_metadata(project_id, meta)
        meta["project_dir"] = str(self._project_dir(project_id))
        logger.info(f"Project '{project_id}' unarchived")
        return dict(meta)

    # ── Per-project config ──────────────────────────────────

    def get_config(self, project_id: str, key: str, default: Any = None) -> Any:
        """Read a per-project config value from metadata."""
        meta = self._read_metadata(project_id)
        if not meta:
            return default
        return meta.get("config", {}).get(key, default)

    def set_config(self, project_id: str, key: str, value: Any) -> dict[str, Any]:
        """Set a per-project config value in metadata."""
        meta = self._read_metadata(project_id)
        if not meta:
            raise ValueError(f"Project '{project_id}' not found.")
        meta.setdefault("config", {})[key] = value
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_metadata(project_id, meta)
        meta["project_dir"] = str(self._project_dir(project_id))
        return dict(meta)

    def get_show_project_prefix(self, project_id: str) -> bool:
        """Check if the project prefix should be shown.
        
        Returns True by default (prefix is visible).  
        Checks per-project config first, then global Hermes env.
        """
        import os
        # Global override: HERMES_NO_PROJECT_PREFIX=1 disables everywhere
        if os.environ.get("HERMES_NO_PROJECT_PREFIX", "").strip() in ("1", "true", "yes"):
            return False
        # Per-project config
        return self.get_config(project_id, "show_project_prefix", True)
