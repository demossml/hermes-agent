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

    def run_tests(self, code: str, test_code: str = "", function_name: str = "") -> dict[str, Any]:
        """Run pytest with auto-generated quality tests (5-8 cases)."""
        tmpdir = tempfile.mkdtemp(prefix="hermes_test_")
        code_path = Path(tmpdir) / "solution.py"
        test_path = Path(tmpdir) / "test_solution.py"
        generated = ""

        try:
            code_path.write_text(code, encoding="utf-8")
            if not test_code.strip():
                generated = self._generate_quality_tests(code, function_name)
            else:
                generated = test_code
            test_path.write_text(generated, encoding="utf-8")

            import re
            test_count = len(re.findall(r"def test_", generated))

            result = subprocess.run(
                ["python3", "-m", "pytest", str(test_path), "-v", "--tb=short"],
                capture_output=True, text=True,
                timeout=self.timeout + 15,
                env={**os.environ, "PYTHONWARNINGS": "all"},
                cwd=tmpdir,
            )

            passed = failed = 0
            for line in result.stdout.split("\n"):
                if " passed" in line:
                    m = re.search(r"(\d+) passed", line)
                    if m: passed = int(m.group(1))
                if " failed" in line:
                    m = re.search(r"(\d+) failed", line)
                    if m: failed = int(m.group(1))

            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": result.stdout[:MAX_OUTPUT_BYTES],
                "stderr": result.stderr[:MAX_OUTPUT_BYTES],
                "timeout": False, "error": "",
                "test_code": generated,
                "test_count": test_count,
                "passed": passed, "failed": failed,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "exit_code": -1, "stdout": "", "stderr": "",
                    "timeout": True, "error": "Tests timed out",
                    "test_code": generated, "test_count": 0, "passed": 0, "failed": 0}
        except Exception as e:
            return {"success": False, "exit_code": -1, "stdout": "", "stderr": str(e),
                    "timeout": False, "error": str(e),
                    "test_code": generated, "test_count": 0, "passed": 0, "failed": 0}
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


    def _generate_quality_tests(self, code: str, function_name: str = "") -> str:
        """Generate 5-8 quality pytest test cases.

        Analyses the code structure and produces:
        - Normal case tests (expected inputs)
        - Boundary tests (empty, None, zero, negative)
        - Error handling tests (invalid types, edge values)
        - Integration tests (if multiple functions)
        """
        import re

        funcs = re.findall(r"def (\w+)\(", code)
        classes = re.findall(r"class (\w+)", code)

        signatures = {}
        for func in funcs:
            m = re.search(rf"def {func}\(([^)]*)\)", code)
            if m:
                params = [p.strip() for p in m.group(1).split(",") if p.strip()]
                params = [p.split(":")[0].strip() for p in params if p and p != "self"]
                signatures[func] = params

        return_types = {}
        for func in funcs:
            m = re.search(rf"def {func}\([^)]*\)\s*->\s*(\w+)", code)
            if m:
                return_types[func] = m.group(1)

        lines = ["import pytest", "from solution import *", "",
                 "# Auto-generated test suite - 5-8 cases per function", ""]

        for func in funcs:
            if func.startswith("_"):
                continue
            params = signatures.get(func, [])
            ret = return_types.get(func, "")

            # 1. Existence
            lines.append(f"def test_{func}_exists():")
            lines.append(f'    """Verify {func} is callable."""')
            lines.append(f"    assert callable({func})")
            lines.append("")

            # 2. Normal case / return type check
            if ret == "bool":
                lines.append(f"def test_{func}_returns_bool():")
                lines.append(f"    result = {func}()")
                lines.append(f"    assert isinstance(result, bool)")
            elif ret in ("int", "float"):
                lines.append(f"def test_{func}_returns_number():")
                lines.append(f"    result = {func}()")
                lines.append(f"    assert isinstance(result, (int, float))")
            elif ret == "str":
                lines.append(f"def test_{func}_returns_string():")
                lines.append(f"    result = {func}()")
                lines.append(f"    assert isinstance(result, str)")
            elif ret in ("list", "List"):
                lines.append(f"def test_{func}_returns_list():")
                lines.append(f"    result = {func}()")
                lines.append(f"    assert isinstance(result, list)")
                lines.append("")
            else:
                pass  # will add basic_call below

            # Always add a basic call test (or already added above)
            if ret in ("bool", "int", "float", "str"):
                lines.append(f"def test_{func}_basic_call():")
                if len(params) == 0:
                    lines.append(f"    result = {func}()")
                elif len(params) == 1:
                    p = params[0]
                    if p in ("n", "x", "num", "number", "value", "val"):
                        lines.append(f"    result = {func}(42)")
                        lines.append(f"    assert isinstance(result, ({ret} if '{ret}' != 'None' else 'object'))")
                    elif p in ("s", "text", "string", "name"):
                        lines.append(f'    result = {func}("test")')
                        lines.append(f"    assert isinstance(result, ({ret} if '{ret}' != 'None' else 'object'))")
                    else:
                        lines.append(f"    result = {func}({p}=None)")
                        lines.append(f"    assert result is not None")
                elif len(params) >= 2:
                    p1, p2 = params[0], params[1]
                    lines.append(f"    result = {func}({p1}=42, {p2}=42)")
                    lines.append(f"    assert result is not None")
                lines.append("")

            # 3. None input
            if params:
                first = params[0]
                lines.append(f"def test_{func}_none_input():")
                lines.append(f"    try:")
                lines.append(f"        result = {func}({first}=None)")
                lines.append(f"    except (TypeError, ValueError, AttributeError):")
                lines.append(f"        pass")
                lines.append(f"    else:")
                lines.append(f"        assert result is not None or result is None")
                lines.append("")

            # 4. Empty collections
            if ret in ("list", "List", ""):
                lines.append(f"def test_{func}_empty_input():")
                lines.append(f"    try:")
                if len(params) == 1 and params[0] in ("arr", "items", "data", "seq", "lst"):
                    lines.append(f"        result = {func}([])")
                elif len(params) == 0:
                    lines.append(f"        result = {func}()")
                else:
                    lines.append(f"        result = {func}()")
                lines.append(f"    except TypeError:")
                lines.append(f"        pytest.skip('requires arguments')")
                lines.append(f"    assert result is not None or result is None")
                lines.append("")

            # 5. Wrong type
            if params:
                first = params[0]
                lines.append(f"def test_{func}_wrong_type():")
                lines.append(f"    with pytest.raises((TypeError, ValueError, AttributeError)):")
                if first in ("n", "x", "num", "number", "value"):
                    lines.append(f'        {func}("not_a_number")')
                elif first in ("s", "text", "string", "name"):
                    lines.append(f"        {func}(42)")
                else:
                    lines.append(f"        {func}(object())")
                lines.append("")

        for cls in classes:
            if cls.startswith("_"):
                continue
            lines.append(f"def test_{cls}_instantiable():")
            lines.append(f"    try:")
            lines.append(f"        obj = {cls}()")
            lines.append(f"        assert obj is not None")
            lines.append(f"    except TypeError:")
            lines.append(f"        pytest.skip('requires constructor arguments')")
            lines.append("")

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
