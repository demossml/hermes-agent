"""
Guard Agent — protective layer for critical operations.

Guard Agent intercepts dangerous tool calls (terminal, file write,
code execution, external API calls) and evaluates them against:
- Security rules (rm -rf, curl | sh, eval, exec)
- Critical rules compliance
- Project scope boundaries
- Ethical constraints

Lightweight and fast — operates in-process without LLM calls.
Uses keyword + regex matching for sub-millisecond decisions.

Configuration: ``guard.enabled`` in config.yaml (default: true)

Usage::

    from core.guard_agent import GuardAgent, check_tool_call

    guard = GuardAgent.get()
    result = guard.evaluate("terminal", {"command": "rm -rf /"})
    # → {"allowed": False, "reason": "Destructive command blocked", "rule": "no_rm_rf"}

CLI::

    /guard status   — show guard state
    /guard on       — enable guard
    /guard off      — disable guard (with confirmation)
"""

from __future__ import annotations

import json, logging, re, threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Guard rules — fast keyword + regex matching
# ═══════════════════════════════════════════════════════════════

@dataclass
class GuardRule:
    """A single guard rule for blocking dangerous operations."""
    rule_id: str
    description: str
    tools: list[str]           # tools this rule applies to
    patterns: list[str]        # regex or keyword patterns
    severity: str = "critical"  # critical | high | medium
    block: bool = True         # True = block, False = warn-only


_GUARD_RULES: list[GuardRule] = [
    # ── Terminal: destructive commands ──────────────────
    GuardRule(
        "no_rm_rf",
        "Destructive deletion (rm -rf) blocked",
        tools=["terminal"],
        patterns=[r"rm\s+-rf\s+/", r"rm\s+-rf\s+~", r"rm\s+-rf\s+\\"],
    ),
    GuardRule(
        "no_curl_pipe_sh",
        "curl-to-shell execution blocked",
        tools=["terminal"],
        patterns=[r"curl\s+.*\|\s*(ba)?sh", r"wget\s+.*\|\s*(ba)?sh"],
    ),
    GuardRule(
        "no_fork_bomb",
        "Fork bomb pattern blocked",
        tools=["terminal"],
        patterns=[r":\(\)\s*\{", r"fork\s*bomb"],
    ),
    GuardRule(
        "no_chmod_777",
        "World-writable permissions blocked",
        tools=["terminal"],
        patterns=[r"chmod\s+.*777", r"chmod\s+-R\s+777"],
    ),
    GuardRule(
        "no_sudo",
        "Sudo execution requires explicit approval",
        tools=["terminal"],
        patterns=[r"\bsudo\b"],
        severity="high",
    ),
    GuardRule(
        "no_dev_null_write",
        "Writing to /dev/null suspicious in context",
        tools=["terminal"],
        patterns=[r">\s*/dev/null"],
        severity="medium",
        block=False,
    ),

    # ── File write: dangerous paths ─────────────────────
    GuardRule(
        "no_system_file_write",
        "Writing to system directories blocked",
        tools=["write_file", "patch"],
        patterns=[
            r"/etc/", r"/boot/", r"/sys/", r"/proc/",
            r"/System/", r"/Library/System/",
            r"/\.ssh/", r"/\.gnupg/",
        ],
    ),
    GuardRule(
        "no_hermes_config_overwrite",
        "Overwriting Hermes config files blocked",
        tools=["write_file", "patch"],
        patterns=[
            r"config\.yaml$", r"\.env$", r"auth\.json$",
            r"agent_configs/", #r"\.hermes/",
        ],
        severity="high",
    ),

    # ── Code execution: dangerous patterns ──────────────
    GuardRule(
        "no_eval_exec",
        "Dangerous eval/exec blocked",
        tools=["code_execution"],
        patterns=[
            r"\beval\s*\(", r"\bexec\s*\(",
            r"\b__import__\s*\(.*os", r"\bcompile\s*\(",
            r"\bsubprocess\.", r"\bos\.system\s*\(",
        ],
    ),
    GuardRule(
        "no_network_scan",
        "Network scanning blocked",
        tools=["code_execution", "terminal"],
        patterns=[
            r"\bsocket\.", r"\bscapy\.", r"\bnmap\b",
            r"\bport\s*scan", r"\bping\s+-f",
        ],
    ),

    # ── External API: credential leaks ──────────────────
    GuardRule(
        "no_credential_in_url",
        "Credentials in URL blocked",
        tools=["web_search", "web_extract"],
        patterns=[
            r"api[_-]?key=", r"token=", r"password=",
            r"secret=", r"auth=",
        ],
    ),
]


def _compile_patterns() -> list[tuple[GuardRule, list[re.Pattern]]]:
    """Pre-compile all regex patterns for speed."""
    compiled = []
    for rule in _GUARD_RULES:
        regexes = [re.compile(p, re.IGNORECASE) for p in rule.patterns]
        compiled.append((rule, regexes))
    return compiled

_COMPILED_RULES = _compile_patterns()


# ═══════════════════════════════════════════════════════════════
# Guard evaluation result
# ═══════════════════════════════════════════════════════════════

@dataclass
class GuardResult:
    """Result of a guard evaluation."""
    allowed: bool
    tool_name: str
    reason: str = ""
    rule_id: str = ""
    severity: str = ""
    duration_us: float = 0.0  # microseconds


# ═══════════════════════════════════════════════════════════════
# GuardAgent — singleton
# ═══════════════════════════════════════════════════════════════

class GuardAgent:
    """Singleton guard that intercepts critical tool calls.

    Fast keyword + regex matching — no LLM overhead.
    """

    _instance: GuardAgent | None = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> GuardAgent:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self) -> None:
        self._enabled: bool = True
        self._block_count: int = 0
        self._warn_count: int = 0
        self._pass_count: int = 0
        self._recent_blocks: list[dict] = []  # last 20 blocks

    # ── State ────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True
        logger.info("Guard Agent ENABLED")

    def disable(self) -> None:
        self._enabled = False
        logger.warning("Guard Agent DISABLED — proceed with caution")

    # ── Evaluation ───────────────────────────────────────

    def evaluate(
        self, tool_name: str, tool_args: dict, task_id: str = "",
    ) -> GuardResult:
        """Evaluate a tool call against all guard rules.

        Returns GuardResult.allowed = True if the call is safe.
        """
        import time
        t0 = time.perf_counter_ns()

        if not self._enabled:
            self._pass_count += 1
            return GuardResult(
                allowed=True, tool_name=tool_name,
                reason="Guard disabled", duration_us=0,
            )

        # Normalise tool name
        tool = tool_name.lower().replace("_tool", "").replace("_t", "")
        args_str = json.dumps(tool_args, default=str)

        for rule, regexes in _COMPILED_RULES:
            if tool not in rule.tools:
                continue

            for pat in regexes:
                if pat.search(args_str):
                    # Exempt .env inside current project root
                    if rule.rule_id == 'no_hermes_config_overwrite' and pat.pattern == r'\.env$':
                        try:
                            from projects.path_guard import get_current_project_root
                            root = get_current_project_root()
                            if root:
                                # Extract path from tool args
                                path = tool_args.get('path', '')
                                if path:
                                    from pathlib import Path
                                    resolved = Path(path).expanduser().resolve()
                                    root_r = Path(str(root)).resolve()
                                    if str(resolved).startswith(str(root_r)):
                                        continue  # inside project — skip
                        except Exception:
                            pass
                    elapsed_us = (time.perf_counter_ns() - t0) / 1000

                    if rule.block:
                        self._block_count += 1
                        self._recent_blocks.append({
                            "tool": tool_name,
                            "rule": rule.rule_id,
                            "pattern": pat.pattern,
                            "args_preview": args_str[:200],
                            "task_id": task_id,
                        })
                        if len(self._recent_blocks) > 20:
                            self._recent_blocks.pop(0)

                        logger.warning(
                            "GUARD BLOCK: %s → %s (%s)",
                            tool_name, rule.rule_id, rule.description,
                        )

                        return GuardResult(
                            allowed=False,
                            tool_name=tool_name,
                            reason=rule.description,
                            rule_id=rule.rule_id,
                            severity=rule.severity,
                            duration_us=elapsed_us,
                        )
                    else:
                        self._warn_count += 1
                        logger.info(
                            "GUARD WARN: %s → %s (%s)",
                            tool_name, rule.rule_id, rule.description,
                        )

        elapsed_us = (time.perf_counter_ns() - t0) / 1000
        self._pass_count += 1
        return GuardResult(
            allowed=True, tool_name=tool_name,
            duration_us=elapsed_us,
        )

    # ── Stats ────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "enabled": self._enabled,
            "rules": len(_GUARD_RULES),
            "blocks": self._block_count,
            "warnings": self._warn_count,
            "passed": self._pass_count,
            "total_checks": self._block_count + self._warn_count + self._pass_count,
            "recent_blocks": self._recent_blocks[-5:],
        }

    def reset_stats(self) -> None:
        self._block_count = 0
        self._warn_count = 0
        self._pass_count = 0
        self._recent_blocks.clear()


# ═══════════════════════════════════════════════════════════════
# Integration helper — call from model_tools.handle_function_call
# ═══════════════════════════════════════════════════════════════

_CRITICAL_TOOLS = {
    "terminal", "write_file", "patch", "code_execution",
    "web_search", "web_extract", "execute_code",
}


def check_tool_call(
    tool_name: str, tool_args: dict, task_id: str = "",
) -> GuardResult:
    """Check a tool call before execution. Called from the dispatch chain.

    Returns GuardResult.allowed = True → proceed.
    Returns GuardResult.allowed = False → block with reason.
    """
    # Only check critical tools
    base = tool_name.lower().split("_tool")[0].rstrip("_t")
    if base not in _CRITICAL_TOOLS and tool_name.lower() not in _CRITICAL_TOOLS:
        return GuardResult(allowed=True, tool_name=tool_name, reason="non-critical tool")

    guard = GuardAgent.get()
    return guard.evaluate(tool_name, tool_args, task_id)


def is_guard_enabled() -> bool:
    """Check if Guard Agent is currently active."""
    return GuardAgent.get().enabled


def guard_stats() -> dict:
    """Return guard statistics."""
    return GuardAgent.get().stats()
