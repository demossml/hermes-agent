"""
Real-time agent activity monitor for Hermes Multi-Agent.

Tracks what every sub-agent is doing — status, current task, timing,
call counts — with zero-token overhead.  Drives the ``/watch`` and
``/status`` CLI commands.

Usage (called automatically by AgentRegistry.call)::

    from core.agent_monitor import get_monitor
    mon = get_monitor()
    mon.start_task("coder", "write auth middleware")
    # ... agent works ...
    mon.end_task("coder", success=True)

CLI integration::

    /watch coder       → monitor only coder
    /watch all         → monitor all agents
    /watch off         → disable monitoring
    /status            → show who is doing what
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class AgentActivity:
    """Live snapshot of one agent's current activity."""

    agent_id: str
    status: str = "idle"          # idle | thinking | working | done | error
    task: str = ""                # current task description
    started_at: float = 0.0       # timestamp when task started
    finished_at: float = 0.0      # timestamp when task finished
    calls: int = 0                # total tracked calls
    successes: int = 0
    errors: int = 0
    last_error: str = ""
    total_ms: int = 0             # total wall-clock time

    @property
    def elapsed(self) -> float:
        """Seconds since task started (0 if idle/done)."""
        if self.status in ("idle", "done", "error"):
            return 0.0
        return time.time() - self.started_at

    @property
    def avg_latency_ms(self) -> int:
        """Average call latency in milliseconds."""
        if self.calls == 0:
            return 0
        return self.total_ms // self.calls

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "status": self.status,
            "task": self.task,
            "elapsed": round(self.elapsed, 1),
            "calls": self.calls,
            "successes": self.successes,
            "errors": self.errors,
            "last_error": self.last_error,
            "avg_latency_ms": self.avg_latency_ms,
        }


class AgentMonitor:
    """Singleton monitor that tracks all sub-agent activity in real time.

    Hooks into ``AgentRegistry.call()`` to automatically record start/end
    of every agent invocation.

    Zero-token overhead — pure in-memory, no LLM calls, no persistence.
    """

    _instance: Optional["AgentMonitor"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._activities: Dict[str, AgentActivity] = {}
        self._enabled: bool = True       # master switch
        self._watched: Optional[set] = None  # None = all, set = specific ids

    @classmethod
    def get_instance(cls) -> "AgentMonitor":
        """Return the global singleton (thread-safe)."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── Watch control ────────────────────────────────────────

    def watch(self, agent_id: Optional[str]) -> str:
        """Start watching one or all agents.

        Args:
            agent_id: ``"coder"``, ``"all"``, or ``None`` / ``"off"``.

        Returns a human-readable status message.
        """
        if agent_id in (None, "off", ""):
            self._watched = set()
            self._enabled = False
            return "Monitoring disabled"

        if agent_id == "all":
            self._watched = None   # None = watch everything
            self._enabled = True
            return "Monitoring ALL agents"

        # Watch specific agent
        if self._watched is None:
            # Was watching all — switch to specific set
            self._watched = set(self._activities.keys())
        self._watched.add(agent_id)
        self._enabled = True
        return f"Monitoring agent '{agent_id}'"

    def is_watching(self, agent_id: str) -> bool:
        """Check if this agent is currently being monitored."""
        if not self._enabled:
            return False
        if self._watched is None:
            return True   # watching all
        return agent_id in self._watched

    @property
    def watched_agents(self) -> list:
        """Return list of currently watched agent IDs."""
        if not self._enabled:
            return []
        if self._watched is None:
            return list(self._activities.keys())
        return sorted(self._watched)

    # ── Activity tracking ────────────────────────────────────

    def _get_or_create(self, agent_id: str) -> AgentActivity:
        if agent_id not in self._activities:
            self._activities[agent_id] = AgentActivity(agent_id=agent_id)
        return self._activities[agent_id]

    def start_task(self, agent_id: str, task: str) -> None:
        """Mark agent as working on a task. Called before call()."""
        if not self._enabled:
            return
        if not self.is_watching(agent_id):
            return
        act = self._get_or_create(agent_id)
        act.status = "thinking"
        act.task = task
        act.started_at = time.time()
        act.calls += 1

    def set_status(self, agent_id: str, status: str) -> None:
        """Update agent status (e.g. 'working' after thinking phase)."""
        if not self._enabled:
            return
        if not self.is_watching(agent_id):
            return
        act = self._get_or_create(agent_id)
        act.status = status

    def end_task(self, agent_id: str, success: bool = True,
                 error: str = "", elapsed_ms: int = 0) -> None:
        """Mark agent as done. Called after call() completes."""
        if not self._enabled:
            return
        if not self.is_watching(agent_id):
            return
        act = self._get_or_create(agent_id)
        act.finished_at = time.time()
        if success:
            act.status = "done"
            act.successes += 1
        else:
            act.status = "error"
            act.errors += 1
            act.last_error = error[:120]
        if elapsed_ms:
            act.total_ms += elapsed_ms

    # ── Status reporting ─────────────────────────────────────

    def get_agent_status(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get status dict for one agent, or None if not tracked."""
        act = self._activities.get(agent_id)
        if not act:
            return None
        return act.to_dict()

    def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status dicts for all tracked agents."""
        return {aid: act.to_dict() for aid, act in self._activities.items()}

    def get_active_count(self) -> int:
        """Count agents currently working (not idle/done/error)."""
        return sum(
            1 for act in self._activities.values()
            if act.status not in ("idle", "done", "error")
        )

    def format_status_line(self, agent_id: str) -> str:
        """Return a compact one-line status for an agent.

        Example: ``[coder] working · 2.3s · write auth middleware``
        """
        act = self._activities.get(agent_id)
        if not act:
            return f"[{agent_id}] no data"

        from core.response_formatter import get_activity_prefix

        prefix = get_activity_prefix(agent_id)

        if act.status in ("idle", "done") and act.calls == 0:
            return f"{prefix} idle"

        status_icon = {
            "thinking": "🧠",
            "working": "⚙️",
            "done": "✅",
            "error": "❌",
            "idle": "💤",
        }.get(act.status, "❓")

        elapsed_str = ""
        if act.status in ("thinking", "working"):
            elapsed_str = f" · {act.elapsed:.1f}s"

        task_str = f" · {act.task[:60]}" if act.task else ""

        return (
            f"{prefix} {status_icon} {act.status}{elapsed_str}{task_str}"
        )

    def format_status_report(self) -> str:
        """Return a multi-line status report for /status command."""
        if not self._enabled:
            return "  Monitoring is OFF. Use /watch <agent> or /watch all to enable."

        if not self._activities:
            return "  No agent activity recorded yet."

        active = [
            (aid, act) for aid, act in self._activities.items()
            if act.status in ("thinking", "working")
        ]
        done = [
            (aid, act) for aid, act in self._activities.items()
            if act.status not in ("thinking", "working")
        ]

        lines = []
        lines.append("")

        if active:
            lines.append(f"  🔴 Active ({len(active)}):")
            for aid, act in active:
                lines.append(f"    {self.format_status_line(aid)}")
        else:
            lines.append("  💤 All agents idle")

        if done:
            # Show recent done/error (those with calls > 0)
            recent = [(aid, act) for aid, act in done if act.calls > 0]
            if recent:
                lines.append(f"\n  ✅ Recent ({len(recent)}):")
                for aid, act in recent[:5]:  # top 5
                    lines.append(f"    {self.format_status_line(aid)}")

        watched = self.watched_agents
        if watched:
            watching = ", ".join(watched[:5])
            if len(watched) > 5:
                watching += f" +{len(watched)-5} more"
            lines.append(f"\n  👁 Watching: {watching}")

        return "\n".join(lines)


def get_monitor() -> AgentMonitor:
    """Return the global AgentMonitor singleton."""
    return AgentMonitor.get_instance()
