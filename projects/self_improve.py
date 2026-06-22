"""
Self-Improvement Loop — непрерывное обучение агентов внутри проекта.

После завершения workflow или исследования:
- Анализирует trajectory (траекторию действий агента)
- Извлекает уроки и лучшие практики
- Предлагает улучшения critical_rules, system prompt или skills
- Сохраняет версии промптов с возможностью отката

Архитектура:
  projects/self_improve.py  ← этот файл
  project_{id}_improvements ← ChromaDB-коллекция истории улучшений
  state/improvements/       ← версионированные промпты / правила / скиллы

Запуск:
  - Автоматически: после больших workflow (≥5 сообщений)
  - Вручную: /improve
"""

from __future__ import annotations

import json, logging, re, time, uuid
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
    ChromaSettings = None
    chromadb = None  # type: ignore


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _get_hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home()
    except ImportError:
        import os
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def _improvements_dir(project_id: str) -> Path:
    return _get_hermes_home() / "projects" / project_id / "state" / "improvements"


# ═══════════════════════════════════════════════════════════════
# Pattern Recognition — heuristic analysis без LLM
# ═══════════════════════════════════════════════════════════════

_PATTERNS = {
    "repeated_error": {
        "keywords": ["error", "fail", "traceback", "exception", "failed", "ошибка"],
        "weight": -0.5,
        "category": "code_restriction",
        "rule_template": "Avoid: {context}",
    },
    "retry_loop": {
        "keywords": ["retry", "try again", "attempt", "попытка", "повтор"],
        "weight": -0.3,
        "category": "custom",
        "rule_template": "Cache result of: {context}",
    },
    "successful_pattern": {
        "keywords": ["works", "success", "passed", "done", "работает", "готово"],
        "weight": +0.4,
        "category": "custom",
        "rule_template": "Always: {context}",
    },
    "tool_choice": {
        "keywords": ["terminal", "read_file", "write_file", "delegate", "web_search"],
        "weight": +0.2,
        "category": "delegate",
        "rule_template": "Prefer: {context}",
    },
    "communication_issue": {
        "keywords": ["misunderstood", "not what i meant", "wrong", "не то", "неправильно"],
        "weight": -0.4,
        "category": "language",
        "rule_template": "Clarify: {context}",
    },
    "performance_issue": {
        "keywords": ["slow", "timeout", "too long", "медленно", "долго"],
        "weight": -0.3,
        "category": "tool_restriction",
        "rule_template": "Optimize: {context}",
    },
}


def _extract_message_text(messages: list[dict]) -> list[str]:
    """Extract clean text from conversation messages."""
    texts = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
    return texts


def _classify_improvement_type(insight_text: str) -> str:
    """Quick heuristic: what kind of improvement does this insight suggest?"""
    t = insight_text.lower()
    if any(w in t for w in ["rule", "правило", "запрет", "restrict", "avoid", "never"]):
        return "rule"
    if any(w in t for w in ["prompt", "промпт", "system", "инструкция", "behave", "personality"]):
        return "prompt"
    if any(w in t for w in ["skill", "скилл", "workflow", "процедура", "how to", "recipe"]):
        return "skill"
    if any(w in t for w in ["tool", "инструмент", "terminal", "delegate", "search"]):
        return "tool_preference"
    return "general"


def _extract_context_around_match(text: str, keyword: str, window: int = 80) -> str:
    """Extract ±window chars around a keyword match for context."""
    idx = text.lower().find(keyword.lower())
    if idx == -1:
        return text[:window * 2]
    start = max(0, idx - window)
    end = min(len(text), idx + len(keyword) + window)
    return text[start:end].strip()


# ═══════════════════════════════════════════════════════════════
# ImprovementStore — persistent storage for improvements
# ═══════════════════════════════════════════════════════════════

class ImprovementStore:
    """Хранилище улучшений с версионированием.

    Directory layout::

        state/improvements/
        ├── index.json              # all improvements with metadata
        ├── rules/
        │   └── v0001_<hash>.json   # versioned rule sets
        ├── prompts/
        │   └── v0001_<hash>.json   # versioned system prompts
        └── skills/
            └── v0001_<hash>.md     # versioned skill docs
    """

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.root = _improvements_dir(project_id)
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in ["rules", "prompts", "skills"]:
            (self.root / sub).mkdir(exist_ok=True)
        self._index_path = self.root / "index.json"
        self._index = self._load_index()

    def _load_index(self) -> dict:
        if self._index_path.exists():
            try:
                return json.loads(self._index_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"version": 1, "items": [], "counters": {"rules": 0, "prompts": 0, "skills": 0}}

    def _save_index(self) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(
            json.dumps(self._index, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def add_improvement(
        self,
        imp_type: str,          # "rule" | "prompt" | "skill" | "general"
        title: str,
        content: str,
        source: str = "auto",   # "auto" | "manual" | "workflow"
        confidence: float = 0.5,
    ) -> dict[str, Any]:
        """Save a new improvement with version tracking."""
        counter_key = f"{imp_type}s" if imp_type != "general" else "general"
        self._index.setdefault("counters", {}).setdefault(imp_type, 0)
        self._index["counters"][imp_type] += 1
        version = self._index["counters"][imp_type]

        now = datetime.now(timezone.utc).isoformat()
        item_id = f"imp-{uuid.uuid4().hex[:8]}"

        # Save versioned file
        fname = f"v{version:04d}_{item_id}.json"
        fpath = self.root / f"{imp_type}s" / fname
        record = {
            "id": item_id,
            "type": imp_type,
            "title": title,
            "content": content,
            "source": source,
            "confidence": confidence,
            "version": version,
            "created_at": now,
            "applied": False,
            "applied_at": None,
            "rolled_back": False,
        }
        fpath.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

        # Update index
        self._index["items"].append({
            "id": item_id,
            "type": imp_type,
            "title": title,
            "version": version,
            "created_at": now,
            "applied": False,
        })
        self._index["version"] += 1
        self._save_index()

        logger.info(f"Improvement saved: {imp_type}/{item_id} — {title}")
        return record

    def list_improvements(
        self, imp_type: str | None = None, applied: bool | None = None,
    ) -> list[dict]:
        """List improvements, optionally filtered."""
        items = self._index.get("items", [])
        if imp_type:
            items = [i for i in items if i["type"] == imp_type]
        if applied is not None:
            items = [i for i in items if i.get("applied") == applied]
        return sorted(items, key=lambda i: i.get("created_at", ""), reverse=True)

    def mark_applied(self, item_id: str) -> bool:
        """Mark an improvement as applied."""
        now = datetime.now(timezone.utc).isoformat()
        for item in self._index.get("items", []):
            if item["id"] == item_id:
                item["applied"] = True
                item["applied_at"] = now
                self._save_index()
                return True
        return False

    def rollback(self, item_id: str) -> dict | None:
        """Roll back an applied improvement — mark as rolled back."""
        for item in self._index.get("items", []):
            if item["id"] == item_id:
                item["rolled_back"] = True
                item["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
                self._save_index()
                return item
        return None

    def get_latest(self, imp_type: str, applied_only: bool = False) -> dict | None:
        """Get the latest improvement of a given type."""
        items = self.list_improvements(imp_type=imp_type, applied=applied_only if applied_only else None)
        # Applied items first, then by version desc
        if applied_only:
            return items[0] if items else None
        # Get latest applied
        applied = [i for i in items if i.get("applied")]
        return applied[0] if applied else (items[0] if items else None)

    def get_stats(self) -> dict:
        """Return improvement statistics for the project."""
        items = self._index.get("items", [])
        return {
            "total": len(items),
            "applied": sum(1 for i in items if i.get("applied")),
            "rolled_back": sum(1 for i in items if i.get("rolled_back")),
            "pending": sum(1 for i in items if not i.get("applied") and not i.get("rolled_back")),
            "by_type": {
                t: sum(1 for i in items if i["type"] == t)
                for t in sorted(set(i["type"] for i in items))
            },
            "counters": self._index.get("counters", {}),
        }


# ═══════════════════════════════════════════════════════════════
# TrajectoryAnalyzer — the core analysis engine
# ═══════════════════════════════════════════════════════════════

class TrajectoryAnalyzer:
    """Анализирует trajectory агента и извлекает уроки.

    Работает БЕЗ вызова LLM — чисто эвристический анализ на основе
    паттернов в тексте сообщений.  Быстро, дёшево, детерминированно.

    Для глубокого семантического анализа можно подключить ChromaDB
    с эмбеддингами (опционально).
    """

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.store = ImprovementStore(project_id)

    def analyze_messages(
        self,
        messages: list[dict],
        workflow_name: str = "unnamed",
    ) -> dict[str, Any]:
        """Analyze a conversation trajectory and return insights.

        Parameters
        ----------
        messages : list[dict]
            Conversation messages with 'role' and 'content' keys.
        workflow_name : str
            Name of the workflow/research being analyzed.

        Returns
        -------
        dict
            ``insights`` — list of extracted insights
            ``improvements`` — list of concrete improvement proposals
            ``stats`` — trajectory statistics
        """
        texts = _extract_message_text(messages)
        if not texts:
            return {"insights": [], "improvements": [], "stats": {}}

        all_text = "\n".join(texts)
        insights = []
        improvements = []

        # ── Pattern matching ──────────────────────────────
        for pattern_name, pattern in _PATTERNS.items():
            for kw in pattern["keywords"]:
                if kw in all_text.lower():
                    ctx = _extract_context_around_match(all_text, kw)
                    insight = {
                        "pattern": pattern_name,
                        "keyword": kw,
                        "context": ctx[:200],
                        "weight": pattern["weight"],
                        "category": pattern["category"],
                    }
                    insights.append(insight)
                    # Generate improvement proposal for strong signals
                    if abs(pattern["weight"]) >= 0.3:
                        rule_text = pattern["rule_template"].format(context=ctx[:100])
                        improvements.append({
                            "type": "rule",
                            "title": f"Pattern: {pattern_name}",
                            "content": rule_text,
                            "confidence": abs(pattern["weight"]),
                            "category": pattern["category"],
                        })
                    break  # One match per pattern is enough

        # ── Statistics ────────────────────────────────────
        tool_calls = sum(1 for t in texts if "Invoking:" in t or "tool_calls" in t.lower())
        user_messages = sum(1 for m in messages if m.get("role") == "user")
        assistant_messages = sum(1 for m in messages if m.get("role") == "assistant")
        total_chars = sum(len(t) for t in texts)

        stats = {
            "message_count": len(messages),
            "user_messages": user_messages,
            "assistant_messages": assistant_messages,
            "tool_calls_approx": tool_calls,
            "total_chars": total_chars,
            "patterns_found": len(insights),
            "workflow_name": workflow_name,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }

        return {
            "insights": insights,
            "improvements": improvements,
            "stats": stats,
        }

    def analyze_and_save(
        self,
        messages: list[dict],
        workflow_name: str = "unnamed",
        auto_apply: bool = False,
    ) -> dict[str, Any]:
        """Analyze trajectory and persist improvement proposals.

        Parameters
        ----------
        messages : list[dict]
            Conversation messages.
        workflow_name : str
            Name for this analysis.
        auto_apply : bool
            If True, apply high-confidence (≥0.7) improvements automatically.

        Returns
        -------
        dict
            Full analysis result with saved improvement IDs.
        """
        result = self.analyze_messages(messages, workflow_name)

        saved_ids = []
        for imp in result["improvements"]:
            record = self.store.add_improvement(
                imp_type=imp["type"],
                title=imp["title"],
                content=imp["content"],
                source="auto",
                confidence=imp["confidence"],
            )
            saved_ids.append(record["id"])
            imp["saved_id"] = record["id"]

            # Auto-apply high-confidence rules
            if auto_apply and imp["confidence"] >= 0.7 and imp["type"] == "rule":
                self.store.mark_applied(record["id"])
                imp["auto_applied"] = True

        # ── Save summary to ChromaDB for semantic recall ──
        self._save_to_chroma(result, workflow_name)

        result["saved_improvement_ids"] = saved_ids
        result["store_stats"] = self.store.get_stats()
        return result

    def _save_to_chroma(self, result: dict, workflow_name: str) -> bool:
        """Save analysis summary to ChromaDB for future semantic recall."""
        if not HAS_CHROMA:
            return False
        try:
            from projects.project_manager import ProjectManager
            pm = ProjectManager()
            chroma_dir = pm.subdir_memory(self.project_id)
            client = chromadb.PersistentClient(
                path=str(chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            coll_name = f"project_{self.project_id}_improvements"
            coll = client.get_or_create_collection(
                name=coll_name,
                metadata={"hnsw:space": "cosine"},
            )

            summary = json.dumps({
                "workflow": workflow_name,
                "patterns": [i["pattern"] for i in result["insights"]],
                "improvement_count": len(result["improvements"]),
                "stats": result["stats"],
            }, ensure_ascii=False)

            doc_id = f"analysis-{int(time.time())}-{uuid.uuid4().hex[:6]}"
            coll.add(
                ids=[doc_id],
                documents=[summary],
                metadatas=[{
                    "workflow": workflow_name,
                    "timestamp": time.time(),
                    "improvement_count": len(result["improvements"]),
                }],
            )
            return True
        except Exception as e:
            logger.debug(f"ChromaDB save skipped: {e}")
            return False

    def recall_similar(self, query: str, n_results: int = 3) -> list[dict]:
        """Semantic recall: find similar past improvements."""
        if not HAS_CHROMA:
            return []
        try:
            from projects.project_manager import ProjectManager
            pm = ProjectManager()
            chroma_dir = pm.subdir_memory(self.project_id)
            if not (chroma_dir / "chroma.sqlite3").exists():
                return []

            client = chromadb.PersistentClient(
                path=str(chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            coll_name = f"project_{self.project_id}_improvements"

            try:
                coll = client.get_collection(name=coll_name)
            except Exception:
                return []

            results = coll.query(query_texts=[query], n_results=n_results)
            if not results.get("ids") or not results["ids"][0]:
                return []

            items = []
            for i, doc_id in enumerate(results["ids"][0]):
                doc = results["documents"][0][i] if results.get("documents") else ""
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                items.append({
                    "id": doc_id,
                    "text": (doc or "")[:300],
                    "metadata": meta,
                })
            return items
        except Exception as e:
            logger.debug(f"Recall failed: {e}")
            return []

    def generate_improvement_report(self) -> str:
        """Generate a human-readable improvement report for the project."""
        stats = self.store.get_stats()
        pending = self.store.list_improvements(applied=False)

        lines = [
            "=" * 60,
            f"Self-Improvement Report — {self.project_id}",
            "=" * 60,
            "",
            f"Total improvements:  {stats['total']}",
            f"  Applied:           {stats['applied']}",
            f"  Rolled back:       {stats['rolled_back']}",
            f"  Pending review:    {stats['pending']}",
            "",
        ]

        if stats["by_type"]:
            lines.append("By type:")
            for t, count in stats["by_type"].items():
                lines.append(f"  {t}: {count}")
            lines.append("")

        if pending:
            lines.append(f"Pending improvements ({len(pending)}):")
            lines.append("-" * 40)
            for p in pending[:10]:
                lines.append(f"  [{p['type']}] {p['title']}")
                lines.append(f"    id: {p['id']}  v{p['version']}  {p['created_at'][:19]}")
            lines.append("")
            lines.append("  Use /improve apply <id> to activate.")
            lines.append("  Use /improve rollback <id> to revert.")

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Module-level convenience
# ═══════════════════════════════════════════════════════════════

def analyze_project_trajectory(
    project_id: str,
    messages: list[dict],
    workflow_name: str = "unnamed",
    auto_apply: bool = False,
) -> dict[str, Any]:
    """One-shot: analyze trajectory for a project."""
    analyzer = TrajectoryAnalyzer(project_id)
    return analyzer.analyze_and_save(messages, workflow_name, auto_apply=auto_apply)


def get_improvement_report(project_id: str) -> str:
    """Get a human-readable improvement report."""
    analyzer = TrajectoryAnalyzer(project_id)
    return analyzer.generate_improvement_report()


def get_improvement_store(project_id: str) -> ImprovementStore:
    """Get the improvement store for a project."""
    return ImprovementStore(project_id)
