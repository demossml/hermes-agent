"""
Context Manager — save/restore conversation context across project switches.
"""
from __future__ import annotations
import json, logging, os, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)
GLOBAL_KEY = "global"

def _hermes_home() -> Path:
    try: from hermes_constants import get_hermes_home; return get_hermes_home()
    except ImportError: return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))

class ContextManager:
    def __init__(self, hermes_home=None):
        self._home = hermes_home or _hermes_home()
        self._root = self._home / "contexts"
        self._root.mkdir(parents=True, exist_ok=True)

    def switch_context(self, from_id: str, to_id: str) -> dict:
        saved = self.save_context(from_id)
        loaded = self.load_context(to_id)
        return {"saved": saved, "loaded": loaded, "from": from_id, "to": to_id}

    def save_context(self, context_id: str) -> dict:
        slot = self._slot(context_id); slot.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        result = {"id": context_id, "saved_at": now, "messages": 0}
        msgs = self._capture_messages()
        if msgs: (slot / "session.json").write_text(json.dumps(msgs, ensure_ascii=False, indent=2), encoding="utf-8"); result["messages"] = len(msgs)
        wf = self._capture_workflow_state()
        if wf: (slot / "state.json").write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8"); result["workflows"] = wf.get("active_count", 0)
        ins = self._capture_insights()
        if ins: (slot / "insights.json").write_text(json.dumps(ins, ensure_ascii=False, indent=2), encoding="utf-8"); result["insights"] = len(ins)
        name = self._get_project_name(context_id)
        meta = {"id": context_id, "name": name, "saved_at": now, "message_count": result["messages"]}
        (slot / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Context saved: %s (%d msgs)", context_id, result["messages"])
        return result

    def load_context(self, context_id: str) -> dict:
        slot = self._slot(context_id)
        result = {"id": context_id, "messages": 0}
        for fname, key in [("session.json","session"),("state.json","state"),("insights.json","insights")]:
            fp = slot / fname
            if fp.exists():
                try: result[key] = json.loads(fp.read_text(encoding="utf-8")); result["messages"] = len(result.get("session",[])) if key=="session" else result["messages"]
                except: pass
        mf = slot / "meta.json"
        if mf.exists():
            try: result["meta"] = json.loads(mf.read_text(encoding="utf-8"))
            except: pass
        return result

    def list_contexts(self) -> list[dict]:
        ctx = []
        if not self._root.exists(): return ctx
        for d in sorted(self._root.iterdir()):
            if not d.is_dir(): continue
            mf = d / "meta.json"
            if mf.exists():
                try: ctx.append(json.loads(mf.read_text(encoding="utf-8")))
                except: pass
        ctx.sort(key=lambda c: c.get("saved_at",""), reverse=True)
        return ctx

    def status(self, active_id="") -> dict:
        return {"active": active_id or "(none)", "saved_contexts": self.list_contexts(), "contexts_dir": str(self._root)}

    def _slot(self, cid): return self._root / (cid or GLOBAL_KEY)
    def _get_project_name(self, cid):
        if cid == GLOBAL_KEY: return "Global Mode"
        try:
            from projects.project_manager import ProjectManager
            m = ProjectManager()._read_metadata(cid)
            if m: return m.get("name", cid)
        except: pass
        return cid

    def _capture_messages(self):
        try:
            from hermes_state import SessionStore
            sid = os.environ.get("HERMES_SESSION_ID","")
            if sid:
                msgs = SessionStore().get_messages_as_conversation(sid)
                return msgs[-30:] if msgs else []
        except: pass
        return []

    def _capture_workflow_state(self):
        return {"active_count": 0, "timestamp": datetime.now(timezone.utc).isoformat()}

    def _capture_insights(self):
        try:
            from projects.project_insights import get_project_insights
            pi = get_project_insights()
            if pi:
                return [{"text":i.text,"importance":i.importance,"tags":i.tags,"source":i.source} for i in pi.list_all(limit=15)]
        except: pass
        return []

def switch_and_report(from_id: str, to_id: str) -> str:
    cm = ContextManager()
    r = cm.switch_context(from_id, to_id)
    parts = []
    if r["saved"].get("messages"): parts.append(f"Saved {r['saved']['messages']} msgs from '{from_id or 'global'}'.")
    if r["loaded"].get("messages"): parts.append(f"Loaded {r['loaded']['messages']} msgs for '{to_id or 'global'}'.")
    return " ".join(parts) if parts else f"Switched to '{to_id or 'global'}'."
