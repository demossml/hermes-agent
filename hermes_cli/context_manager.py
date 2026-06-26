"""
Context Manager — save/restore conversation context (secured).

Security:
  - context_id validated against [a-zA-Z0-9_-]+ (path traversal prevention)
  - _slot() uses Path.resolve() and checks path is inside contexts root
  - load_context() accepts caller_id for access control
"""
from __future__ import annotations

import json, logging, os, re
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
    """Raise ValueError if context_id is invalid (path traversal prevention)."""
    if not context_id or not _VALID_ID.match(context_id):
        raise ValueError(
            f"Invalid context_id: {context_id!r}. "
            f"Must match [a-zA-Z0-9_-]{{1,64}}."
        )


class ContextManager:
    def __init__(self, hermes_home: Optional[Path] = None):
        self._home = hermes_home or _hermes_home()
        self._root = self._home / "contexts"
        self._root.mkdir(parents=True, exist_ok=True)

    # ── Public API ──────────────────────────────────────────

    def switch_context(
        self, from_id: str, to_id: str, caller_id: str = "orchestrator"
    ) -> dict:
        """Save *from_id*, load *to_id*. Returns summary dict."""
        saved = self.save_context(from_id)
        loaded = self.load_context(to_id, caller_id=caller_id)
        return {"saved": saved, "loaded": loaded, "from": from_id, "to": to_id}

    def save_context(self, context_id: str) -> dict:
        """Save current session context for *context_id*."""
        _validate_id(context_id)
        slot = self._slot(context_id)
        slot.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc).isoformat()
        result = {"id": context_id, "saved_at": now, "messages": 0}

        # Messages
        msgs = self._capture_messages()
        if msgs:
            (slot / "session.json").write_text(
                json.dumps(msgs, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            result["messages"] = len(msgs)

        # Workflow state
        wf = self._capture_workflow_state()
        if wf:
            (slot / "state.json").write_text(
                json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            result["workflows"] = wf.get("active_count", 0)

        # Insights
        ins = self._capture_insights()
        if ins:
            (slot / "insights.json").write_text(
                json.dumps(ins, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            result["insights"] = len(ins)

        # Metadata
        meta = {
            "id": context_id, "name": self._get_project_name(context_id),
            "saved_at": now, "message_count": result["messages"],
        }
        (slot / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        logger.info("Context saved: %s (%d msgs)", context_id, result["messages"])
        return result

    def load_context(
        self, context_id: str, *, caller_id: str = "orchestrator"
    ) -> dict:
        """Load saved context. Enforces access control."""
        _validate_id(context_id)

        # Access control: caller must be orchestrator or own the context
        if caller_id != "orchestrator" and caller_id != context_id:
            if not self._can_access(caller_id, context_id):
                logger.warning(
                    "Access denied: %s tried to load context %s",
                    caller_id, context_id,
                )
                return {"id": context_id, "messages": 0, "access_denied": True}

        slot = self._slot(context_id)
        result: dict = {"id": context_id, "messages": 0}

        for fname, key in [
            ("session.json", "session"),
            ("state.json", "state"),
            ("insights.json", "insights"),
        ]:
            fp = slot / fname
            if fp.exists():
                try:
                    result[key] = json.loads(fp.read_text(encoding="utf-8"))
                    if key == "session":
                        result["messages"] = len(result[key])
                except Exception:
                    pass

        mf = slot / "meta.json"
        if mf.exists():
            try:
                result["meta"] = json.loads(mf.read_text(encoding="utf-8"))
            except Exception:
                pass

        return result

    def list_contexts(self) -> list[dict]:
        ctx = []
        if not self._root.exists():
            return ctx
        for d in sorted(self._root.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            if not _VALID_ID.match(d.name):
                continue
            mf = d / "meta.json"
            if mf.exists():
                try:
                    ctx.append(json.loads(mf.read_text(encoding="utf-8")))
                except Exception:
                    pass
        ctx.sort(key=lambda c: c.get("saved_at", ""), reverse=True)
        return ctx

    def status(self, active_id: str = "") -> dict:
        return {
            "active": active_id or "(none)",
            "saved_contexts": self.list_contexts(),
            "contexts_dir": str(self._root),
        }

    # ── Private ─────────────────────────────────────────────

    def _slot(self, cid: str) -> Path:
        """Return safe slot directory. Raises on path traversal attempt."""
        _validate_id(cid)
        slot = (self._root / cid).resolve()
        root_resolved = self._root.resolve()
        if not str(slot).startswith(str(root_resolved) + os.sep) and slot != root_resolved:
            raise ValueError(
                f"Path traversal blocked: {slot} is outside {root_resolved}"
            )
        return slot

    def _can_access(self, caller_id: str, target_id: str) -> bool:
        """Check if caller_id can access target_id's context."""
        try:
            from projects.project_isolation import check_project_access
            return check_project_access(caller_id, target_id)
        except Exception:
            pass
        return caller_id == target_id

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
        """Capture recent session messages (last 30)."""
        try:
            from hermes_state import SessionDB
            sid = os.environ.get("HERMES_SESSION_ID", "")
            if sid:
                db = SessionDB()
                return db.get_messages_as_conversation(sid)[-30:]
        except Exception:
            pass
        return []

    def _capture_workflow_state(self) -> dict:
        return {
            "active_count": 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _capture_insights(self) -> list[dict]:
        try:
            from projects.project_insights import get_project_insights
            pi = get_project_insights()
            if pi:
                return [
                    {"text": i.text, "importance": i.importance,
                     "tags": i.tags, "source": i.source}
                    for i in pi.list_all(limit=15)
                ]
        except Exception:
            pass
        return []


def switch_and_report(from_id: str, to_id: str) -> str:
    cm = ContextManager()
    r = cm.switch_context(from_id, to_id)
    parts = []
    if r["saved"].get("messages"):
        parts.append(f"Saved {r['saved']['messages']} msgs from '{from_id or 'global'}'.")
    if r["loaded"].get("messages"):
        parts.append(f"Loaded {r['loaded']['messages']} msgs for '{to_id or 'global'}'.")
    return " ".join(parts) if parts else f"Switched to '{to_id or 'global'}'."
