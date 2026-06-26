"""
Context Manager v3 — save/restore conversation context across project switches.

Security: path traversal prevention, access control, insights isolation.
Session merge: append new messages, don't overwrite.
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
        saved = self.save_context(from_id)
        loaded = self.load_context(to_id, caller_id=caller_id)
        return {"saved": saved, "loaded": loaded, "from": from_id, "to": to_id}

    def save_context(self, context_id: str) -> dict:
        """Save current session context. Merges with existing session (append)."""
        _validate_id(context_id)
        slot = self._slot(context_id)
        slot.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        result = {"id": context_id, "saved_at": now, "messages": 0}

        # ── Messages: MERGE with existing, don't overwrite ──
        existing_session = self._load_json(slot / "session.json")
        new_msgs = self._capture_messages()
        if new_msgs:
            merged = self._merge_sessions(existing_session, new_msgs)
            (slot / "session.json").write_text(
                json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            result["messages"] = len(merged)

        # ── Workflow state ──────────────────────────────────
        wf = self._capture_workflow_state()
        if wf:
            (slot / "state.json").write_text(
                json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            result["workflows"] = wf.get("active_count", 0)

        # ── Insights: project-scoped ────────────────────────
        ins = self._capture_insights(context_id)
        if ins:
            (slot / "insights.json").write_text(
                json.dumps(ins, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            result["insights"] = len(ins)

        # ── Metadata ────────────────────────────────────────
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
        """Load saved context with access control.

        Access rules:
          - caller_id == "orchestrator" → always allowed
          - caller_id == context_id → allowed (own context)
          - everything else → DENIED
        """
        _validate_id(context_id)

        # ── BUG-v2-01 FIX: Simple access control ────────────
        if caller_id != "orchestrator" and caller_id != context_id:
            logger.warning(
                "Access denied: caller=%s tried context=%s", caller_id, context_id
            )
            return {"id": context_id, "messages": 0, "access_denied": True}

        slot = self._slot(context_id)
        result: dict = {"id": context_id, "messages": 0}

        for fname, key in [
            ("session.json", "session"),
            ("state.json", "state"),
            ("insights.json", "insights"),
        ]:
            data = self._load_json(slot / fname)
            if data:
                result[key] = data
                if key == "session":
                    result["messages"] = len(data)

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
        _validate_id(cid)
        slot = (self._root / cid).resolve()
        root_r = self._root.resolve()
        s, r = str(slot), str(root_r)
        if not (s.startswith(r + os.sep) or s == r):
            raise ValueError(f"Path traversal blocked: {slot}")
        return slot

    @staticmethod
    def _load_json(path: Path) -> Any:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None

    def _merge_sessions(self, existing: Any, new_msgs: list[dict]) -> list[dict]:
        """Merge new messages into existing session, deduplicating by content."""
        if not isinstance(existing, list):
            return list(new_msgs)
        existing_ids = {m.get("content", "")[:120] for m in existing}
        merged = list(existing)
        for m in new_msgs:
            key = m.get("content", "")[:120]
            if key and key not in existing_ids:
                merged.append(m)
                existing_ids.add(key)
        return merged[-100:]  # Keep last 100 max

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

    def _capture_insights(self, context_id: str) -> list[dict]:
        """Capture insights scoped to *context_id* (BUG-v2-02 fix)."""
        try:
            from projects.project_insights import ProjectInsights
            pi = ProjectInsights(context_id) if context_id != GLOBAL_KEY else None
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
