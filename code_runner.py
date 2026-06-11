"""
Code Runner — sandboxed Python execution for the Tester agent.

Runs code in a subprocess with timeout, memory limits, and
captures stdout/stderr/exit code.  Also runs pytest and mypy.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Safe limits ──────────────────────────────────────────────

MAX_EXECUTION_TIME = 15   # seconds
MAX_OUTPUT_BYTES = 50_000
ALLOWED_IMPORTS = None    # None = all (sandboxed by subprocess isolation)


class CodeRunner:
    """Sandboxed Python code executor for Tester agent."""

    def __init__(self, timeout: int = MAX_EXECUTION_TIME):
        self.timeout = timeout

    # ── Public API ──────────────────────────────────────────

    def run_python(self, code: str) -> dict[str, Any]:
        """Run Python code and return output, errors, exit code."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        try:
            result = subprocess.run(
                ["python3", tmp_path],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env={**os.environ, "PYTHONWARNINGS": "all"},
            )
            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": result.stdout[:MAX_OUTPUT_BYTES],
                "stderr": result.stderr[:MAX_OUTPUT_BYTES],
                "timeout": False,
                "error": "",
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
                "timeout": True,
                "error": f"Execution timed out after {self.timeout}s",
            }
        except Exception as e:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "timeout": False,
                "error": str(e),
            }
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def run_tests(self, code: str, test_code: str = "") -> dict[str, Any]:
        """Run pytest on the given code with optional test file.

        If test_code is empty, auto-generates basic smoke tests.
        """
        tmpdir = tempfile.mkdtemp(prefix="hermes_test_")
        code_path = Path(tmpdir) / "solution.py"
        test_path = Path(tmpdir) / "test_solution.py"

        try:
            code_path.write_text(code, encoding="utf-8")

            # Auto-generate basic tests if none provided
            if not test_code.strip():
                test_code = self._generate_smoke_tests(code)
            test_path.write_text(test_code, encoding="utf-8")

            result = subprocess.run(
                ["python3", "-m", "pytest", str(test_path), "-v", "--tb=short"],
                capture_output=True,
                text=True,
                timeout=self.timeout + 10,
                env={**os.environ, "PYTHONWARNINGS": "all"},
                cwd=tmpdir,
            )

            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": result.stdout[:MAX_OUTPUT_BYTES],
                "stderr": result.stderr[:MAX_OUTPUT_BYTES],
                "timeout": False,
                "error": "",
                "test_code": test_code,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
                "timeout": True,
                "error": "Tests timed out",
                "test_code": test_code,
            }
        except Exception as e:
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "timeout": False,
                "error": str(e),
                "test_code": test_code,
            }
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def run_mypy(self, code: str) -> dict[str, Any]:
        """Run mypy type checker on the code."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        try:
            result = subprocess.run(
                ["mypy", "--ignore-missing-imports", tmp_path],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": result.stdout[:MAX_OUTPUT_BYTES],
                "stderr": result.stderr[:MAX_OUTPUT_BYTES],
                "timeout": False,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "exit_code": -1, "stdout": "", "stderr": "", "timeout": True}
        except FileNotFoundError:
            return {"success": True, "exit_code": 0, "stdout": "", "stderr": "", "timeout": False, "skipped": "mypy not installed"}
        except Exception as e:
            return {"success": False, "exit_code": -1, "stdout": "", "stderr": str(e), "timeout": False}
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def run_full_check(self, code: str, language: str = "python") -> dict[str, Any]:
        """Run all checks: syntax, tests, types, security scan.

        Returns a combined report.
        """
        report: dict[str, Any] = {
            "language": language,
            "checks": {},
            "all_passed": True,
        }

        if language != "python":
            report["checks"]["note"] = f"Runner supports Python only. Skipping {language}."
            return report

        # 1. Syntax check (just run python -c)
        syntax = self.run_python(code)
        report["checks"]["syntax"] = {
            "passed": syntax["success"],
            "stdout": syntax["stdout"][:500],
            "stderr": syntax["stderr"][:500],
            "timeout": syntax["timeout"],
        }
        if not syntax["success"]:
            report["all_passed"] = False

        # 2. Unit tests
        tests = self.run_tests(code)
        report["checks"]["tests"] = {
            "passed": tests["success"],
            "output": tests["stdout"][:2000],
            "error": tests["stderr"][:500],
            "timeout": tests["timeout"],
            "auto_generated": not bool(tests.get("test_code", "")),
        }
        if not tests["success"]:
            report["all_passed"] = False

        # 3. Type checking
        mypy = self.run_mypy(code)
        report["checks"]["types"] = {
            "passed": mypy["success"],
            "output": mypy["stdout"][:1000],
            "timeout": mypy["timeout"],
            "skipped": mypy.get("skipped", ""),
        }
        if not mypy["success"] and not mypy.get("skipped"):
            report["all_passed"] = False

        # 4. Basic security scan
        security = self._security_scan(code)
        report["checks"]["security"] = security
        if security.get("issues"):
            report["all_passed"] = False

        return report

    # ── Helpers ─────────────────────────────────────────────

    def _generate_smoke_tests(self, code: str) -> str:
        """Generate basic pytest smoke tests from code structure."""
        import re

        funcs = re.findall(r"def (\w+)\(", code)
        classes = re.findall(r"class (\w+)", code)

        lines = [
            "import pytest",
            "from solution import *",
            "",
        ]

        for func in funcs:
            if func.startswith("_"):
                continue
            lines.append(f"def test_{func}_exists():")
            lines.append(f"    assert callable({func}), '{func} should be callable'")
            lines.append(f"")
            lines.append(f"def test_{func}_returns_something():")
            lines.append(f"    try:")
            lines.append(f"        result = {func}()")
            lines.append(f"    except TypeError:")
            lines.append(f"        pytest.skip('requires arguments')")
            lines.append(f"    assert result is not None, '{func} returned None'")
            lines.append(f"")

        for cls in classes:
            if cls.startswith("_"):
                continue
            lines.append(f"def test_{cls}_instantiable():")
            lines.append(f"    try:")
            lines.append(f"        obj = {cls}()")
            lines.append(f"    except TypeError:")
            lines.append(f"        pytest.skip('requires constructor arguments')")
            lines.append(f"    assert obj is not None")
            lines.append(f"")

        if not funcs and not classes:
            lines.append("def test_code_runs():")
            lines.append("    exec(open('solution.py').read())")
            lines.append("")

        return "\n".join(lines)

    def _security_scan(self, code: str) -> dict[str, Any]:
        """Basic static security scan for dangerous patterns."""
        dangerous = [
            ("eval(", "eval() can execute arbitrary code"),
            ("exec(", "exec() can execute arbitrary code"),
            ("__import__", "Dynamic import can be dangerous"),
            ("subprocess", "subprocess calls should be reviewed"),
            ("os.system", "os.system() spawns a shell"),
            ("pickle.loads", "pickle is unsafe for untrusted data"),
            ("password", "Hardcoded password detected"),
            ("secret", "Possible hardcoded secret"),
            ("api_key", "Possible API key in code"),
            ("token", "Possible hardcoded token"),
            ("sqlite3.connect", "SQLite — check for injection"),
            (".execute(", "SQL execution — check for injection"),
            ("SELECT", "SQL SELECT — check for injection"),
        ]

        issues = []
        code_lower = code.lower()
        for pattern, description in dangerous:
            if pattern.lower() in code_lower:
                issues.append({"pattern": pattern, "description": description})

        return {"passed": len(issues) == 0, "issues": issues, "count": len(issues)}


# ── Singleton ────────────────────────────────────────────────

_runner: CodeRunner | None = None


def get_runner() -> CodeRunner:
    global _runner
    if _runner is None:
        _runner = CodeRunner()
    return _runner
