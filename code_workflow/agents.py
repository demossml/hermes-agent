"""
Workflow agents — CoderAgent and TesterAgent encapsulate the
agent-specific configuration, prompt engineering, and communication
with the AgentRegistry.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Frontend detection — tells Tester when to use browser tools
# ═══════════════════════════════════════════════════════════════

_FRONTEND_KEYWORDS = [
    # Languages / frameworks
    "react", "vue", "svelte", "angular", "next.js", "nextjs", "nuxt",
    "gatsby", "remix", "astro", "solid.js", "preact", "jquery",
    # Markup / styling
    "html", "css", "scss", "sass", "less", "tailwind", "bootstrap",
    "material-ui", "mui", "chakra", "styled-components",
    # DOM / browser APIs
    "document.", "window.", "dom", "browser", "frontend",
    "getelementbyid", "queryselector", "addeventlistener",
    "localstorage", "sessionstorage", "fetch(", "xmlhttprequest",
    # Rendering / components
    "render", "component", "jsx", "tsx", "usestate", "useeffect",
    "usememo", "usecallback", "props", "lifecycle", "virtual dom",
    # Build / dev servers
    "webpack", "vite", "parcel", "esbuild", "localhost:",
    "npm run dev", "npm start", "yarn dev", "pnpm dev",
]


def detect_frontend_type(code: str) -> dict[str, Any]:
    """Analyse code and return frontend metadata for the tester.

    Returns a dict with:
    - is_frontend: bool
    - score: int (number of keyword matches)
    - matched_keywords: list[str]
    - frontend_type: str (react/vue/html/unknown)
    - needs_browser_test: bool
    - suggested_test_url: str (localhost URL if detected)
    """
    code_lower = code.lower()
    matched = [kw for kw in _FRONTEND_KEYWORDS if kw in code_lower]
    score = len(matched)

    # Detect framework
    fw_type = "unknown"
    if any(w in code_lower for w in ["react", "jsx", "tsx", "usestate", "useeffect"]):
        fw_type = "react"
    elif "vue" in code_lower:
        fw_type = "vue"
    elif "svelte" in code_lower:
        fw_type = "svelte"
    elif any(w in code_lower for w in ["angular", "ngmodule", "ngcomponent"]):
        fw_type = "angular"
    elif any(w in code_lower for w in ["html", "css", "tailwind", "bootstrap"]):
        fw_type = "html"

    # Detect dev server port
    test_url = ""
    import re
    port_match = re.search(r"localhost:(\d+)", code_lower)
    if port_match:
        test_url = f"http://localhost:{port_match.group(1)}"
    elif fw_type == "react":
        test_url = "http://localhost:3000"
    elif fw_type == "vue":
        test_url = "http://localhost:5173"
    elif fw_type == "svelte":
        test_url = "http://localhost:5173"

    needs_browser = (
        score >= 3  # strong signal
        or fw_type != "unknown"  # known framework
        or any(w in code_lower for w in ["html", "dom", "render", "browser", "frontend"])
    )

    return {
        "is_frontend": needs_browser,
        "score": score,
        "matched_keywords": matched[:10],
        "frontend_type": fw_type,
        "needs_browser_test": needs_browser,
        "suggested_test_url": test_url,
    }


# ═══════════════════════════════════════════════════════════════
# Base
# ═══════════════════════════════════════════════════════════════


class WorkflowAgent:
    """Base for workflow agents (Coder, Tester, Reviewer, Optimizer…).

    Subclasses define ``_build_config()`` returning a config dict
    suitable for ``registry.create()``.
    """

    agent_type: str = "worker"

    def __init__(self, agent_id: str, registry: Any, task_id: str):
        self.agent_id = agent_id
        self.registry = registry
        self.task_id = task_id

    def ensure_created(self) -> None:
        """Create the agent in the registry if it does not exist."""
        if self.agent_id not in self.registry._agents:
            cfg = self._build_config()
            self.registry.create(self.agent_id, cfg)
            logger.info(
                "Created %s (subtree=%s...)",
                self.agent_id,
                self.registry._agents[self.agent_id]
                .get("subtree_session_id", "?")[:20],
            )

    async def send(self, message: str, session_id: str = "") -> str:
        """Send a message to the agent and return the reply."""
        return await self.registry.call(
            self.agent_id,
            session_id or self.task_id,
            message,
            caller_id="orchestrator",
        )

    def _build_config(self) -> dict:
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════
# Coder
# ═══════════════════════════════════════════════════════════════


class CoderAgent(WorkflowAgent):
    """Expert software engineer — writes production-quality code only.

    Every response MUST include structured metadata so the Tester
    knows exactly how to test the code (browser? unit tests? both?).
    """

    agent_type = "coder"

    # ── Metadata schema fields ──────────────────────────
    METADATA_FIELDS = [
        "language",           # python, javascript, typescript, html, ...
        "code_type",          # frontend_react, backend_fastapi, script, library, cli, api, ...
        "needs_browser_test", # true/false — should Tester use Playwright?
        "test_url",           # http://localhost:3000 — dev server URL
        "expected_behavior",  # "Renders a button that increments counter"
        "critical_checks",    # ["check console errors", "verify button click works"]
        "entry_point",        # "src/App.tsx" — which file to run/test
        "dependencies",       # ["react", "react-dom"] — npm/pip packages needed
    ]

    def __init__(
        self,
        agent_id: str,
        registry: Any,
        task_id: str,
        language: str | None = None,
    ):
        super().__init__(agent_id, registry, task_id)
        self.language = language
        self.subtree_session_id = f"subtree-{agent_id}-{task_id}"
        self.last_metadata: dict[str, Any] = {}

    async def write_code(self, task: str, session_id: str = "") -> str:
        """Ask the coder to write code for a task.

        Returns the code WITH metadata block appended.
        """
        lang = f"\nLanguage: {self.language}" if self.language else ""
        msg = (
            f"Code task{lang}:\n\n{task}\n\n"
            "IMPORTANT: After your code, append a METADATA block:\n"
            "```metadata\n"
            "language: <python|javascript|typescript|html|...>\n"
            "code_type: <frontend_react|backend_fastapi|script|library|api|cli|...>\n"
            "needs_browser_test: <true|false>\n"
            "test_url: <http://localhost:PORT or empty>\n"
            "expected_behavior: <one-line description>\n"
            "critical_checks:\n"
            "  - <check 1>\n"
            "  - <check 2>\n"
            "entry_point: <main file to run>\n"
            "dependencies:\n"
            "  - <package name>\n"
            "```"
        )
        response = await self.send(msg, session_id)
        self.last_metadata = parse_coder_metadata(response)
        return response

    async def fix_code(
        self, code: str, review: str, task: str, session_id: str = "",
    ) -> str:
        """Ask the coder to fix issues found in review.

        Returns fixed code WITH updated metadata block.
        """
        msg = (
            "Your code was reviewed. Fix ALL issues listed below.\n\n"
            f"Review feedback:\n{review}\n\n"
            f"Original task:\n{task}\n\n"
            "Rewrite the code with all fixes applied.\n\n"
            "IMPORTANT: After your code, append an UPDATED METADATA block:\n"
            "```metadata\n"
            "language: ...\n"
            "code_type: ...\n"
            "needs_browser_test: ...\n"
            "test_url: ...\n"
            "expected_behavior: ...\n"
            "critical_checks:\n"
            "  - ...\n"
            "entry_point: ...\n"
            "dependencies:\n"
            "  - ...\n"
            "```"
        )
        response = await self.send(msg, session_id)
        self.last_metadata = parse_coder_metadata(response)
        return response

    def get_metadata(self) -> dict[str, Any]:
        """Return parsed metadata from the last coder response."""
        return dict(self.last_metadata)

    def _build_config(self) -> dict:
        return {
            "system_prompt": _CODER_PROMPT,
            "parent_id": "orchestrator",
            "subtree_session_id": self.subtree_session_id,
            "description": f"Dynamic coder for task {self.task_id}",
            "max_iterations": 8,
            "critical_rules": [
                "Output code followed by a METADATA block "
                "(```metadata ... ```).",
                "The metadata block is REQUIRED — the Tester "
                "needs it to know how to test your code.",
                "Every function and class must have a docstring.",
                "All function signatures must have type hints.",
                "Handle edge cases: empty inputs, None, invalid types.",
                "For frontend code: set needs_browser_test=true "
                "and provide test_url.",
            ],
            "rule_reminder_every": 0,
        }


# ═══════════════════════════════════════════════════════════════
# Coder metadata parser
# ═══════════════════════════════════════════════════════════════

def parse_coder_metadata(response: str) -> dict[str, Any]:
    """Extract structured metadata from a coder's response.

    Parses the `` ```metadata ... ``` `` block from the response.
    Returns a dict with all recognised fields, or empty dict if
    no metadata block found.
    """
    import re

    # Find metadata block
    match = re.search(
        r"```metadata\s*\n(.*?)```",
        response,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return {}

    block = match.group(1).strip()
    result: dict[str, Any] = {}

    # Parse simple key: value lines
    for line in block.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # List continuation (indented with -)
        if line.startswith("-"):
            value = line.lstrip("- ").strip()
            # Find the last list-type key and append
            for key in reversed(list(result.keys())):
                if isinstance(result[key], list):
                    result[key].append(value)
                    break
            continue

        # key: value
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower().replace(" ", "_")
            value = value.strip()

            # Boolean parsing
            if value.lower() in ("true", "yes"):
                result[key] = True
            elif value.lower() in ("false", "no"):
                result[key] = False
            elif key in ("critical_checks", "dependencies"):
                # Start a list — values come on subsequent lines
                result[key] = []
                if value:
                    result[key].append(value)
            else:
                result[key] = value

    # Normalise keys
    if "code_type" not in result and "type" in result:
        result["code_type"] = result.pop("type")

    return result


def _metadata_to_frontend_info(meta: dict[str, Any]) -> dict[str, Any]:
    """Convert coder metadata into frontend_info dict for TesterAgent.

    When the coder provides explicit metadata, it takes priority
    over heuristic detection — more precise, no guessing.
    """
    needs_browser = meta.get("needs_browser_test", False)
    if isinstance(needs_browser, str):
        needs_browser = needs_browser.lower() in ("true", "yes", "1")

    code_type = meta.get("code_type", meta.get("type", "unknown"))
    test_url = meta.get("test_url", "")

    # Derive frontend_type from code_type
    fw_type = "unknown"
    ct_lower = str(code_type).lower()
    if "react" in ct_lower:
        fw_type = "react"
    elif "vue" in ct_lower:
        fw_type = "vue"
    elif "svelte" in ct_lower:
        fw_type = "svelte"
    elif "angular" in ct_lower:
        fw_type = "angular"
    elif any(w in ct_lower for w in ("html", "css", "frontend")):
        fw_type = "html"

    if not test_url and fw_type == "react":
        test_url = "http://localhost:3000"
    elif not test_url and fw_type in ("vue", "svelte"):
        test_url = "http://localhost:5173"

    return {
        "is_frontend": needs_browser or fw_type != "unknown",
        "score": 99,  # coder metadata = highest confidence
        "matched_keywords": [f"coder:{code_type}"],
        "frontend_type": fw_type,
        "needs_browser_test": needs_browser or fw_type != "unknown",
        "suggested_test_url": test_url,
        "_source": "coder_metadata",
        "expected_behavior": meta.get("expected_behavior", ""),
        "critical_checks": meta.get("critical_checks", []),
        "entry_point": meta.get("entry_point", ""),
        "language": meta.get("language", ""),
    }


# ═══════════════════════════════════════════════════════════════
# Prompt Engineer
# ═══════════════════════════════════════════════════════════════


class PromptEngineer(WorkflowAgent):
    """Expert at crafting the perfect coding prompt for the Coder.

    Takes the user's raw request and produces a detailed,
    structured prompt covering: requirements, edge cases,
    type hints, docstrings, error handling, language best
    practices, and test expectations.
    """

    agent_type = "prompt_engineer"

    def __init__(self, agent_id: str, registry: Any, task_id: str):
        super().__init__(agent_id, registry, task_id)
        self.subtree_session_id = f"subtree-{agent_id}-{task_id}"

    async def engineer_prompt(
        self, user_request: str, language: str | None = None,
    ) -> str:
        """Transform a raw user request into a polished coder prompt."""
        lang_hint = f"\nTarget language: {language}" if language else ""
        msg = (
            f"Transform this raw request into a detailed, "
            f"professional coding prompt for an expert software "
            f"engineer.{lang_hint}\n\n"
            f"Your prompt MUST include:\n"
            f"- Precise task description\n"
            f"- Input/output specifications with types\n"
            f"- Edge cases to handle (empty, None, invalid)\n"
            f"- Required docstrings and type hints\n"
            f"- Error handling expectations\n"
            f"- Language-specific best practices\n"
            f"- Performance constraints (if applicable)\n\n"
            f"Output ONLY the prompt text. No explanations.\n\n"
            f"User request:\n{user_request}"
        )
        return await self.send(msg)

    def _build_config(self) -> dict:
        return {
            "system_prompt": _PROMPT_ENGINEER_PROMPT,
            "parent_id": "orchestrator",
            "subtree_session_id": self.subtree_session_id,
            "description": f"Prompt engineer for task {self.task_id}",
            "max_iterations": 3,
            "normal_tools": [],
            "critical_rules": [
                "Output ONLY the prompt text — no commentary.",
                "Include: task description, input/output types, "
                "edge cases, docstrings, error handling.",
                "Adapt style to the target language's conventions.",
            ],
            "rule_reminder_every": 0,
        }


# ═══════════════════════════════════════════════════════════════
# Tester
# ═══════════════════════════════════════════════════════════════


class TesterAgent(WorkflowAgent):
    """Senior code tester + security reviewer with sandboxed execution.

    Automatically detects frontend code (React, Vue, HTML, etc.) and
    uses Playwright Browser Tool to verify rendering, console errors,
    and DOM behaviour — not just backend logic.
    """

    agent_type = "tester"

    def __init__(self, agent_id: str, registry: Any, task_id: str):
        super().__init__(agent_id, registry, task_id)
        # STRICT isolation: tester MUST NOT share memory with coder or orchestrator
        self.subtree_session_id = f"tester-isolated-{task_id}"

    async def review_code(
        self, code: str, session_id: str = "",
        coder_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Review code — run real tests first, then LLM review.

        For frontend code: auto-detects frameworks and adds browser
        testing instructions.  For backend code: uses sandbox execution.

        If *coder_metadata* is provided (from CoderAgent.get_metadata()),
        it overrides the heuristic detection and provides precise
        testing instructions.
        """
        # ── Metadata from coder (preferred) ─────────────────
        if coder_metadata:
            frontend_info = _metadata_to_frontend_info(coder_metadata)
        else:
            frontend_info = detect_frontend_type(code)
        browser_hint = ""
        if frontend_info["needs_browser_test"]:
            browser_hint = (
                "\n\n🌐 FRONTEND CODE DETECTED\n"
                f"Type: {frontend_info['frontend_type']}\n"
                f"Keywords: {', '.join(frontend_info['matched_keywords'][:5])}\n"
                f"Score: {frontend_info['score']}\n\n"
                "BROWSER TESTING INSTRUCTIONS:\n"
                "1. Use `browser_test_page(url)` to verify rendering\n"
                f"   Suggested URL: {frontend_info['suggested_test_url'] or 'http://localhost:3000'}\n"
                "2. Use `browser_get_page_info()` to check meta tags and content\n"
                "3. Use `browser_console('error')` to collect JS errors\n"
                "4. Use `pw_browser_evaluate(js)` to test DOM interactions\n"
                "5. Check for: blank pages, console errors, missing elements,\n"
                "   broken links, incorrect meta tags, CSP violations\n\n"
                "Include a ## Browser Test Results section in your review.\n"
            )

        # ── Real execution first ────────────────────────────
        exec_report = ""
        try:
            from code_workflow.runner import get_runner
            runner = get_runner()
            report = runner.run_full_check(code)
            exec_report = json.dumps(report, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.debug("CodeRunner unavailable: %s", e)

        # ── Build review message ────────────────────────────
        parts = []
        if exec_report:
            parts.append(
                "FIRST — here are the ACTUAL test results from running "
                "this code in a sandbox. Use these results in your review.\n\n"
                f"```json\n{exec_report}\n```"
            )
        if browser_hint:
            parts.append(browser_hint)

        parts.append(
            "Now review the code. You do NOT know the original user "
            "task — judge the code on its own merits.\n\n"
            f"```\n{code[:2500]}\n```"
        )

        msg = "\n\n".join(parts)
        return await self.send(msg, session_id)

    def generate_and_run_tests(
        self, code: str, function_name: str = "",
    ) -> dict[str, Any]:
        """Auto-generate quality pytest tests and run them in a sandbox.

        Returns::

            {
                "test_code": str,       # the generated test code
                "test_count": int,      # number of test functions
                "passed": int,          # tests passed
                "failed": int,          # tests failed
                "pytest_output": str,   # raw pytest output
                "success": bool,        # all tests passed
            }
        """
        try:
            from code_workflow.runner import get_runner
            runner = get_runner()
            result = runner.run_tests(code, function_name=function_name)
            return {
                "test_code": result.get("test_code", ""),
                "test_count": result.get("test_count", 0),
                "passed": result.get("passed", 0),
                "failed": result.get("failed", 0),
                "pytest_output": result.get("stdout", ""),
                "success": result.get("success", False),
            }
        except Exception as e:
            logger.debug("generate_and_run_tests failed: %s", e)
            return {
                "test_code": "", "test_count": 0,
                "passed": 0, "failed": 0,
                "pytest_output": str(e), "success": False,
            }

    def _build_config(self) -> dict:
        return {
            "system_prompt": _TESTER_PROMPT,
            "parent_id": "orchestrator",
            "subtree_session_id": self.subtree_session_id,
            "description": f"Dynamic tester for task {self.task_id}",
            "max_iterations": 5,
            "enabled_toolsets": [
                "browser",   # 🎭 Playwright browser for frontend testing
            ],
            "critical_rules": [
                "You do NOT know the original user request. "
                "Judge ONLY the code provided to you.",
                "NEVER ask the coder or user for context — "
                "you work with the code text alone.",
                "Report specific issues with suggested fixes.",
                "Check security, correctness, edge cases, "
                "performance, style.",
                "Use the exact output format: Review Result, "
                "sections, Summary.",
                # ── Browser testing rules ──
                "If the code contains HTML/CSS/JS/frontend "
                "frameworks, ALWAYS use browser_test_page() "
                "and browser_console() to check for rendering "
                "errors, console errors, and broken elements.",
                "When browser testing, include a ## Browser Test "
                "Results section with: URL tested, status code, "
                "console errors found, and rendering assessment.",
            ],
            "rule_reminder_every": 0,
        }


# ═══════════════════════════════════════════════════════════════
# Prompts
# ═══════════════════════════════════════════════════════════════

_CODER_PROMPT = (
    "You are an expert SOFTWARE ENGINEER. Your ONLY job is "
    "to write production-quality code.\n\n"
    "REQUIREMENTS:\n"
    "- Output code, then a METADATA block. The metadata block "
    "is MANDATORY — format:\n"
    "```metadata\n"
    "language: python|javascript|typescript|html|...\n"
    "code_type: frontend_react|backend_fastapi|script|library|api|cli\n"
    "needs_browser_test: true|false\n"
    "test_url: http://localhost:3000  (if needs_browser_test=true)\n"
    "expected_behavior: Brief description of what the code should do\n"
    "critical_checks:\n"
    "  - What the tester MUST verify\n"
    "  - E.g., 'check console errors', 'verify button click'\n"
    "entry_point: main.py  (which file to run)\n"
    "dependencies:\n"
    "  - package-name\n"
    "```\n"
    "- Every function and class MUST have a docstring "
    "describing parameters, return values, and behaviour.\n"
    "- Use type hints on ALL function signatures.\n"
    "- Handle edge cases: empty inputs, None values, "
    "invalid types, boundary conditions.\n"
    "- Raise descriptive exceptions for invalid inputs.\n"
    "- Follow the language's standard style guide "
    "(PEP 8 for Python, etc.).\n"
    "- Write readable, self-documenting code with "
    "meaningful variable names.\n"
    "- Prefer standard library over external dependencies "
    "unless the task specifies otherwise.\n\n"
    "A separate tester agent will review your code and "
    "use your metadata to run the RIGHT tests (browser, "
    "unit tests, security scan). Good metadata = better testing.\n\n"
    "For FRONTEND code (React, Vue, HTML, CSS):\n"
    "- Set needs_browser_test: true\n"
    "- Provide the dev server URL as test_url\n"
    "- List specific DOM checks in critical_checks\n\n"
    "For BACKEND code (API, CLI, library):\n"
    "- Set needs_browser_test: false\n"
    "- Provide expected_behavior as a clear description\n"
    "- List edge cases to test in critical_checks"
)

_TESTER_PROMPT = (
    "You are a SENIOR CODE TESTER and SECURITY REVIEWER.\n"
    "You do NOT know the original user task — you only "
    "see the code AND the automated test results that were "
    "already run in a sandbox.\n\n"
    "\u26a0\ufe0f IMPORTANT: The code has ALREADY been executed in "
    "a sandbox. The test results (pytest output, mypy, "
    "security scan) are in the message above the code. "
    "Use these REAL results — do NOT speculate about "
    "whether the code runs or not.\n\n"
    "\U0001f310 FRONTEND CODE: If the message indicates frontend "
    "code (HTML/CSS/React/Vue/etc.), you MUST use the "
    "browser testing tools:\n"
    "- `browser_test_page(url)` — open page + screenshot + console\n"
    "- `browser_get_page_info()` — meta tags + content stats\n"
    "- `browser_console('error')` — JS errors + uncaught exceptions\n"
    "- `browser_wait_for_selector(sel)` — verify elements render\n"
    "- `pw_browser_evaluate(js)` — test DOM interactions\n"
    "Include a ## Browser Test Results section.\n\n"
    "YOUR MISSION:\n"
    "1. SYNTAX: Check the actual execution output. Did it "
    "compile? Any syntax errors?\n"
    "2. TESTS: Did pytest pass? How many passed/failed? "
    "Quote the actual test output.\n"
    "3. BROWSER (if frontend): Did the page render? Any "
    "console errors? Missing elements? Use real browser output.\n"
    "4. EDGE CASES: What inputs would break this code? "
    "Empty lists? None? Negative numbers?\n"
    "5. TYPES: Did mypy find type errors? Quote them.\n"
    "6. SECURITY: Any dangerous patterns (eval, exec, "
    "subprocess, hardcoded secrets)?\n"
    "7. PERFORMANCE: O(n\u00b2)? Unnecessary allocations?\n"
    "8. STYLE: Naming, docstrings, type hints, PEP 8.\n\n"
    "OUTPUT FORMAT:\n"
    "## Review Result: \u2705 PASS or \u274c FAIL\n\n"
    "### Execution Results\n"
    "- Syntax: PASS/FAIL (with error if any)\n"
    "- Tests: X passed, Y failed\n"
    "- Types: PASS/FAIL (with mypy output if any)\n"
    "- Security: X issues found\n\n"
    "### Browser Test Results  ← ONLY if frontend code detected\n"
    "- URL tested: ...\n"
    "- Status code: ...\n"
    "- Console errors: X found (list them)\n"
    "- Rendering: PASS/FAIL (page loaded? elements visible?)\n\n"
    "### Security Issues\n"
    "- (specific issue)\n\n"
    "### Edge Case Issues\n"
    "- (specific issue)\n\n"
    "### Performance Issues\n"
    "- (specific issue)\n\n"
    "### Style Issues\n"
    "- (specific issue)\n\n"
    "### Summary\n"
    "Brief overall assessment. Quote the actual test "
    "output — do NOT make up results."
)


_PROMPT_ENGINEER_PROMPT = (
    "You are an EXPERT PROMPT ENGINEER for programming tasks.\n"
    "Your job is to take a raw, underspecified user request "
    "and turn it into a precise, complete prompt that a "
    "senior software engineer can execute flawlessly.\n\n"
    "Your prompts MUST include:\n"
    "- EXACT task description — no ambiguity\n"
    "- Input types, output types, and expected behaviour\n"
    "- Edge cases: empty inputs, None, invalid types, "
    "boundary values\n"
    "- Required docstrings and type hints\n"
    "- Error handling expectations (exceptions, fallbacks)\n"
    "- Language-specific best practices (PEP 8, etc.)\n"
    "- Performance notes if relevant (Big O, memory)\n\n"
    "Output ONLY the prompt text. No 'Here is a prompt:' "
    "prefixes — just the raw prompt that will be sent to the "
    "coder."
)
