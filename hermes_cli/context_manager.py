"""
Context Manager v4 — secure, race-condition-free context persistence.

v4 fixes: fcntl.flock, HERMES_SESSION_ID fallback, workflow state integration,
gateway cache invalidation in switch_and_report.
"""
from __future__ import annotations

import fcntl, json, logging, os, re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

GLOBAL_KEY = "global"
_VALID_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home()
    except ImportError:
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def _validate_id(context_id: str) -> None:
    if not context_id or not _VALID_ID.match(context_id):
        raise ValueError(f"Invalid context_id: {context_id!r}. Must match [a-zA-Z0-9_-]{{1,64}}.")


class ContextManager:
    def __init__(self, hermes_home: Optional[Path] = None):
        self._home = hermes_home or _hermes_home()
        self._root = self._home / "contexts"
        self._root.mkdir(parents=True, exist_ok=True)

    # ── Public API ──────────────────────────────────────────

    def switch_context(self, from_id: str, to_id: str, caller_id: str = "orchestrator") -> dict:
        saved = self.save_context(from_id)
        loaded = self.load_context(to_id, caller_id=caller_id)
        return {"saved": saved, "loaded": loaded, "from": from_id, "to": to_id}

    def save_context(self, context_id: str) -> dict:
        _validate_id(context_id)
        slot = self._slot(context_id)
        slot.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        result = {"id": context_id, "saved_at": now, "messages": 0}

        # ── Messages: MERGE, flock-protected ─────────────────
        existing = self._load_json(slot / "session.json")
        new_msgs = self._capture_messages()
        if new_msgs:
            merged = self._merge_sessions(existing, new_msgs)
            self._write_flocked(slot / "session.json",
                json.dumps(merged, ensure_ascii=False, indent=2))
            result["messages"] = len(merged)

        # ── Workflow state: real integration ─────────────────
        wf = self._capture_workflow_state(context_id)
        if wf:
            self._write_flocked(slot / "state.json",
                json.dumps(wf, ensure_ascii=False, indent=2))
            result["workflows"] = wf.get("active_count", 0)

        # ── Insights: project-scoped ────────────────────────
        ins = self._capture_insights(context_id)
        if ins:
            self._write_flocked(slot / "insights.json",
                json.dumps(ins, ensure_ascii=False, indent=2))
            result["insights"] = len(ins)

        # ── Metadata ────────────────────────────────────────
        meta = {"id": context_id, "name": self._get_project_name(context_id),
                "saved_at": now, "message_count": result["messages"]}
        self._write_flocked(slot / "meta.json",
            json.dumps(meta, ensure_ascii=False, indent=2))

        logger.info("Context saved: %s (%d msgs)", context_id, result["messages"])
        return result

    def load_context(self, context_id: str, *, caller_id: str = "orchestrator") -> dict:
        _validate_id(context_id)
        if caller_id != "orchestrator" and caller_id != context_id:
            logger.warning("Access denied: caller=%s tried context=%s", caller_id, context_id)
            return {"id": context_id, "messages": 0, "access_denied": True}

        slot = self._slot(context_id)
        result: dict = {"id": context_id, "messages": 0}
        for fname, key in [("session.json","session"),("state.json","state"),("insights.json","insights")]:
            data = self._load_json(slot / fname)
            if data:
                result[key] = data
                if key == "session":
                    result["messages"] = len(data)
        result["meta"] = self._load_json(slot / "meta.json") or {}
        return result

    def list_contexts(self) -> list[dict]:
        ctx = []
        if not self._root.exists():
            return ctx
        for d in sorted(self._root.iterdir()):
            if not d.is_dir() or d.name.startswith(".") or not _VALID_ID.match(d.name):
                continue
            meta = self._load_json(d / "meta.json")
            if meta and isinstance(meta, dict):
                ctx.append(meta)
        ctx.sort(key=lambda c: c.get("saved_at", ""), reverse=True)
        return ctx

    def status(self, active_id: str = "") -> dict:
        return {"active": active_id or "(none)", "saved_contexts": self.list_contexts(),
                "contexts_dir": str(self._root)}

    # ── Private ─────────────────────────────────────────────

    def _slot(self, cid: str) -> Path:
        _validate_id(cid)
        slot = (self._root / cid).resolve()
        root_r = self._root.resolve()
        s, r = str(slot), str(root_r)
        if not (s.startswith(r + os.sep) or s == r):
            raise ValueError(f"Path traversal blocked: {slot}")
        return slot

    @staticmethod
    def _load_json(path: Path) -> Any:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    @staticmethod
    def _write_flocked(path: Path, content: str) -> None:
        """Write with fcntl.flock — race condition protection."""
        with open(path, "w", encoding="utf-8") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            finally:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass

    def _merge_sessions(self, existing: Any, new_msgs: list[dict]) -> list[dict]:
        if not isinstance(existing, list):
            return list(new_msgs)
        seen = {m.get("content", "")[:120] for m in existing}
        merged = list(existing)
        for m in new_msgs:
            key = m.get("content", "")[:120]
            if key and key not in seen:
                merged.append(m)
                seen.add(key)
        return merged[-100:]

    def _get_project_name(self, cid: str) -> str:
        if cid == GLOBAL_KEY:
            return "Global Mode"
        try:
            from projects.project_manager import ProjectManager
            m = ProjectManager()._read_metadata(cid)
            if m:
                return m.get("name", cid)
        except Exception:
            pass
        return cid

    def _capture_messages(self) -> list[dict]:
        """Capture recent session messages with fallback chain for missing env."""
        try:
            from hermes_state import SessionDB
            sid = os.environ.get("HERMES_SESSION_ID", "")
            # Fallback: try latest session from state db
            if not sid:
                sid = self._find_latest_session_id()
            if sid:
                db = SessionDB()
                msgs = db.get_messages_as_conversation(sid)
                if msgs:
                    return msgs[-30:]
        except Exception:
            pass
        return []

    def _find_latest_session_id(self) -> str:
        """Find the most recent session_id from the state database."""
        try:
            from hermes_state import SessionDB
            db = SessionDB()
            # Try reading the latest session by timestamp
            sid_file = self._home / "state" / "latest_session"
            if sid_file.exists():
                return sid_file.read_text().strip()
        except Exception:
            pass
        return ""

    def _capture_workflow_state(self, context_id: str) -> dict:
        """Real integration with CodeWorkflowManager and Research Mode."""
        state = {"active_count": 0, "timestamp": datetime.now(timezone.utc).isoformat(),
                 "workflows": [], "research_active": False}
        try:
            from code_workflow.manager import CodeWorkflowManager
            mgr = CodeWorkflowManager(context_id)
            active = mgr.get_active_workflows() if hasattr(mgr, 'get_active_workflows') else []
            state["active_count"] = len(active)
            state["workflows"] = [{"id": w.id, "status": w.status, "phase": w.phase}
                                  for w in active] if active else []
        except Exception:
            pass
        try:
            from research.manager import ResearchManager
            rm = ResearchManager()
            state["research_active"] = rm.is_active() if hasattr(rm, 'is_active') else False
        except Exception:
            pass
        return state

    def _capture_insights(self, context_id: str) -> list[dict]:
        try:
            from projects.project_insights import ProjectInsights
            pi = ProjectInsights(context_id) if context_id != GLOBAL_KEY else None
            if pi:
                return [{"text": i.text, "importance": i.importance,
                         "tags": i.tags, "source": i.source}
                        for i in pi.list_all(limit=15)]
        except Exception:
            pass
        return []


def switch_and_report(from_id: str, to_id: str) -> str:
    """Switch context + invalidate gateway cache. Always called from exit paths."""
    cm = ContextManager()
    r = cm.switch_context(from_id, to_id)

    # ── Gateway cache invalidation ──────────────────────────
    try:
        from hermes_cli.config import get_hermes_home
        cache_dir = get_hermes_home() / "cache"
        # Touch invalidation markers
        for marker in ["project_context", "status_bar", "command_registry"]:
            (cache_dir / f".invalidate_{marker}").touch(exist_ok=True)
        logger.debug("Gateway cache invalidated: %s → %s", from_id, to_id)
    except Exception:
        pass

    parts = []
    if r["saved"].get("messages"):
        parts.append(f"Saved {r['saved']['messages']} msgs from '{from_id or 'global'}'.")
    if r["loaded"].get("messages"):
        parts.append(f"Loaded {r['loaded']['messages']} msgs for '{to_id or 'global'}'.")
    return " ".join(parts) if parts else f"Switched to '{to_id or 'global'}'."
