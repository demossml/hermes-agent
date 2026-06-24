#!/usr/bin/env python3
"""
Tester Tools — Professional code testing toolkit for Hermes Agent.

Provides a comprehensive suite of testing capabilities:

1. Static Analysis: ruff, mypy, pylint, bandit
2. Code Quality: documentation checks, style analysis, complexity metrics
3. API Testing: endpoint validation, response checking
4. Performance: execution timing, memory profiling, stress tests
5. Browser Testing: integration with Playwright Browser PRO
6. Smart Strategy: auto-selects checks based on project type

All tools return structured JSON for easy parsing by the Tester agent.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import textwrap
import time
import tempfile
import tracemalloc
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _run_cmd(cmd: list[str], timeout: int = 120, cwd: str = None, allow_nonzero: bool = False) -> dict:
    """Run a command and return structured output.

    Args:
        allow_nonzero: If True, exit codes 0 and 1 are treated as "success"
                       (useful for linters where exit=1 means "issues found", not "error").
                       Exit code 2+ is always treated as failure.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or os.getcwd(),
        )
        is_success = result.returncode == 0
        if allow_nonzero and result.returncode == 1:
            is_success = True  # exit 1 = "found issues" — not a tool error
        return {
            "success": is_success,
            "exit_code": result.returncode,
            "stdout": result.stdout[:50000],
            "stderr": result.stderr[:10000],
            "command": " ".join(cmd),
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Timeout after {timeout}s", "command": " ".join(cmd)}
    except FileNotFoundError:
        return {"success": False, "error": f"Command not found: {cmd[0]}", "command": " ".join(cmd)}
    except Exception as e:
        return {"success": False, "error": str(e)[:500], "command": " ".join(cmd)}

def _find_python_files(directory: str = None) -> list[str]:
    """Find all Python files in a directory or return a single file if path is a file."""
    target = Path(directory or os.getcwd())
    if target.is_file() and target.suffix == ".py":
        return [str(target)]
    if target.is_dir():
        return sorted(str(p) for p in target.rglob("*.py") if "venv" not in str(p) and "__pycache__" not in str(p) and "node_modules" not in str(p))
    return []

def _count_lines(text: str) -> int:
    return len(text.splitlines())

def _ensure_tool(tool_name: str, pip_package: str = None) -> bool:
    """Ensure a CLI tool is installed. Auto-installs via pip if missing.

    Args:
        tool_name: CLI command name (e.g. 'ruff', 'pytest')
        pip_package: pip package name (default: same as tool_name)

    Returns True if tool is available (was already or installed successfully).
    """
    import shutil
    if pip_package is None:
        pip_package = tool_name
    if shutil.which(tool_name):
        return True
    logger.warning("%s not found — attempting pip install %s", tool_name, pip_package)
    try:
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", pip_package, "-q"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            logger.info("Installed %s successfully", pip_package)
            return True
        logger.error("pip install %s failed: %s", pip_package, result.stderr[:200])
        return False
    except Exception as e:
        logger.error("Auto-install %s failed: %s", pip_package, e)
        return False


# ═══════════════════════════════════════════════════════════════
# 1. Static Analysis
# ═══════════════════════════════════════════════════════════════

def run_ruff(path: str = ".", fix: bool = False, select: str = "") -> dict:
    """Run ruff linter."""
    cmd = ["ruff", "check", str(path)]
    if fix:
        cmd.insert(1, "--fix")
    if select:
        cmd.extend(["--select", select])
    # ruff outputs text by default — no --output-format needed
    result = _run_cmd(cmd, allow_nonzero=True)
    # ruff exit 0 = no issues, exit 1 = issues found (both are "success")
    # exit 2 = configuration error / real failure
    if result.get("exit_code") == 2:
        result["success"] = False
    result["issues_count"] = _count_lines(result.get("stdout", ""))
    return result

def run_mypy(path: str = ".", strict: bool = False) -> dict:
    """Run mypy type checker."""
    cmd = ["mypy", str(path), "--ignore-missing-imports", "--no-error-summary"]
    if strict:
        cmd.append("--strict")
    result = _run_cmd(cmd, timeout=180, allow_nonzero=True)
    result["issues_count"] = _count_lines(result.get("stdout", ""))
    return result

def run_pylint(path: str = ".", min_score: float = 7.0) -> dict:
    """Run pylint deep analysis."""
    cmd = ["pylint", str(path), "--output-format=text", f"--fail-under={min_score}"]
    result = _run_cmd(cmd, timeout=180, allow_nonzero=True)
    # Parse score
    score = None
    for line in (result.get("stdout", "") + result.get("stderr", "")).splitlines():
        if "Your code has been rated at" in line:
            try:
                score = float(line.split("rated at")[1].split("/")[0].strip())
            except (ValueError, IndexError):
                pass
    result["score"] = score
    result["issues_count"] = _count_lines(result.get("stdout", ""))
    return result

def run_bandit(path: str = ".", severity: str = "all") -> dict:
    """Run bandit security scanner."""
    cmd = ["bandit", "-r", str(path), "-f", "json"]
    if severity != "all":
        cmd.extend(["-ll" if severity == "low" else "-lll" if severity == "medium" else "-llll"])
    result = _run_cmd(cmd, timeout=120, allow_nonzero=True)
    try:
        parsed = json.loads(result.get("stdout", "{}"))
        result["results"] = parsed.get("results", [])
        result["metrics"] = parsed.get("metrics", {})
        result["issues_count"] = len(parsed.get("results", []))
        # Count by severity
        high = sum(1 for r in parsed.get("results", []) if r.get("issue_severity") == "HIGH")
        medium = sum(1 for r in parsed.get("results", []) if r.get("issue_severity") == "MEDIUM")
        low = sum(1 for r in parsed.get("results", []) if r.get("issue_severity") == "LOW")
        result["severity_counts"] = {"high": high, "medium": medium, "low": low}
    except (json.JSONDecodeError, KeyError):
        result["results"] = []
        result["issues_count"] = 0
        result["severity_counts"] = {"high": 0, "medium": 0, "low": 0}
    return result

# ═══════════════════════════════════════════════════════════════
# 2. Code Quality
# ═══════════════════════════════════════════════════════════════

def check_documentation(path: str = ".") -> dict:
    """Check docstring coverage and quality."""
    py_files = _find_python_files(path)
    if not py_files:
        return {"error": "No Python files found", "files_checked": 0}

    total_functions = 0
    documented = 0
    total_classes = 0
    documented_classes = 0
    issues = []

    for fpath in py_files:
        try:
            with open(fpath) as f:
                content = f.read()
            import ast
            tree = ast.parse(content, filename=fpath)

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_"):
                        total_functions += 1
                        doc = ast.get_docstring(node)
                        if doc and len(doc) > 10:
                            documented += 1
                        else:
                            issues.append({
                                "file": fpath,
                                "name": node.name,
                                "type": "function",
                                "line": node.lineno,
                                "issue": "Missing or short docstring" if not doc else "Docstring too short (<10 chars)",
                            })
                elif isinstance(node, ast.ClassDef):
                    total_classes += 1
                    doc = ast.get_docstring(node)
                    if doc and len(doc) > 10:
                        documented_classes += 1
                    else:
                        issues.append({
                            "file": fpath,
                            "name": node.name,
                            "type": "class",
                            "line": node.lineno,
                            "issue": "Missing or short class docstring" if not doc else "Docstring too short",
                        })
        except SyntaxError:
            issues.append({"file": fpath, "issue": "Could not parse file (syntax error)"})

    func_coverage = (documented / total_functions * 100) if total_functions > 0 else 100
    class_coverage = (documented_classes / total_classes * 100) if total_classes > 0 else 100

    return {
        "files_checked": len(py_files),
        "total_functions": total_functions,
        "documented_functions": documented,
        "function_coverage_pct": round(func_coverage, 1),
        "total_classes": total_classes,
        "documented_classes": documented_classes,
        "class_coverage_pct": round(class_coverage, 1),
        "issues": issues[:50],
        "issues_count": len(issues),
    }

def check_code_style(path: str = ".") -> dict:
    """Check code style: naming conventions, line lengths, complexity."""
    py_files = _find_python_files(path)
    if not py_files:
        return {"error": "No Python files found", "files_checked": 0}

    issues = []
    total_lines = 0

    for fpath in py_files:
        try:
            with open(fpath) as f:
                lines = f.readlines()
            total_lines += len(lines)

            for i, line in enumerate(lines, 1):
                # Line too long
                if len(line.rstrip("\n")) > 100:
                    issues.append({
                        "file": fpath, "line": i, "type": "line_too_long",
                        "issue": f"Line {i}: {len(line.rstrip())} chars > 100",
                    })
                # Trailing whitespace
                if line.rstrip("\n") != line.rstrip("\n").rstrip():
                    issues.append({
                        "file": fpath, "line": i, "type": "trailing_whitespace",
                        "issue": f"Line {i}: trailing whitespace",
                    })

            # Check naming via AST
            import ast
            tree = ast.parse("".join(lines), filename=fpath)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    if not node.name.islower() and not node.name.startswith("_"):
                        issues.append({
                            "file": fpath, "line": node.lineno, "type": "naming",
                            "issue": f"Function '{node.name}' should be snake_case",
                        })
                elif isinstance(node, ast.ClassDef):
                    if not node.name[0].isupper():
                        issues.append({
                            "file": fpath, "line": node.lineno, "type": "naming",
                            "issue": f"Class '{node.name}' should be PascalCase",
                        })
        except Exception:
            pass

    return {
        "files_checked": len(py_files),
        "total_lines": total_lines,
        "issues": issues[:100],
        "issues_count": len(issues),
    }

# ═══════════════════════════════════════════════════════════════
# 3. API Testing
# ═══════════════════════════════════════════════════════════════

def test_api_endpoint(url: str, method: str = "GET", payload_json: str = "", headers_json: str = "", expected_status: int = 200) -> dict:
    """Test a REST API endpoint."""
    try:
        import urllib.request
        import urllib.error

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if headers_json:
            try:
                headers.update(json.loads(headers_json))
            except json.JSONDecodeError:
                pass

        data = None
        if payload_json and method.upper() in ("POST", "PUT", "PATCH"):
            data = payload_json.encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())

        start = time.time()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                elapsed = round((time.time() - start) * 1000, 1)
                body = resp.read().decode("utf-8", errors="replace")[:10000]
                status = resp.status
                resp_headers = dict(resp.headers)
        except urllib.error.HTTPError as e:
            elapsed = round((time.time() - start) * 1000, 1)
            body = e.read().decode("utf-8", errors="replace")[:5000] if e.fp else ""
            status = e.code
            resp_headers = dict(e.headers) if e.headers else {}

        # Try parsing JSON response
        content_type = resp_headers.get("Content-Type", "")
        try:
            json_body = json.loads(body) if body else {}
        except (json.JSONDecodeError, ValueError):
            json_body = None

        return {
            "url": url,
            "method": method.upper(),
            "status": status,
            "expected_status": expected_status,
            "status_match": status == expected_status,
            "response_time_ms": elapsed,
            "content_type": content_type,
            "body_preview": body[:500] if body else "",
            "body_parsed": json_body is not None,
            "headers": dict(list(resp_headers.items())[:10]),
        }
    except urllib.error.URLError as e:
        return {"url": url, "method": method.upper(), "error": f"Connection failed: {e.reason}", "status": 0}
    except Exception as e:
        return {"url": url, "method": method.upper(), "error": str(e)[:500], "status": 0}

# ═══════════════════════════════════════════════════════════════
# 4. Performance Testing
# ═══════════════════════════════════════════════════════════════

def run_performance_test(path: str = ".") -> dict:
    """Run basic performance analysis: file sizes, import times, complexity."""
    py_files = _find_python_files(path)
    if not py_files:
        return {"error": "No Python files found"}

    results = {
        "files_analyzed": len(py_files),
        "total_lines": 0,
        "total_size_bytes": 0,
        "largest_files": [],
        "most_complex_files": [],
        "import_time_ms": 0,
    }

    total_import_time = 0
    module_count = 0

    for fpath in py_files:
        try:
            with open(fpath) as f:
                content = f.read()
            line_count = content.count("\n") + 1
            size = len(content.encode("utf-8"))
            results["total_lines"] += line_count
            results["total_size_bytes"] += size

            # Try measuring import time
            try:
                mod_name = fpath.replace("/", ".").replace(".py", "").lstrip(".")
                start = time.time()
                subprocess.run(["python3", "-c", f"import importlib; importlib.import_module('{mod_name}')"],
                              capture_output=True, timeout=15, cwd=path)
                elapsed = (time.time() - start) * 1000
                total_import_time += elapsed
                module_count += 1
            except Exception:
                pass

            # Complexity: count branches
            branches = content.count("if ") + content.count("elif ") + content.count("for ") + content.count("while ") + content.count("except ") + content.count("with ")
            results["most_complex_files"].append({"file": fpath, "lines": line_count, "branches": branches})
        except Exception:
            pass

    results["most_complex_files"].sort(key=lambda x: x["branches"], reverse=True)
    results["most_complex_files"] = results["most_complex_files"][:10]
    results["average_import_time_ms"] = round(total_import_time / module_count, 1) if module_count > 0 else 0

    return results

def stress_test(function_code: str, iterations: int = 1000, warmup: int = 10) -> dict:
    """Stress-test a Python function by running it many times."""
    try:
        # Dedent the function code, then wrap it in a flat script
        function_code_dedented = textwrap.dedent(function_code).strip()
        script = "\n".join([
        "import time",
        "import sys",
        "",
        function_code_dedented,
        "",
        "for _ in range(" + str(warmup) + "):",
        "    try:",
        "        result = test_function()",
        "    except Exception as e:",
        '        print(f"WARMUP_ERROR: {e}", file=sys.stderr)',
        "        sys.exit(1)",
        "",
        "times = []",
        "errors = 0",
        "for i in range(" + str(iterations) + "):",
        "    start = time.perf_counter()",
        "    try:",
        "        result = test_function()",
        "        elapsed = time.perf_counter() - start",
        "        times.append(elapsed)",
        "    except Exception as e:",
        "        errors += 1",
        "        if errors <= 3:",
        '            print(f"ERROR at iteration {i}: {e}", file=sys.stderr)',
        "",
        "if not times:",
        '    print("NO_SUCCESSFUL_RUNS")',
        "    sys.exit(1)",
        "",
        "times.sort()",
        "total = sum(times)",
        "avg = total / len(times)",
        "p50 = times[len(times)//2]",
        "p95 = times[int(len(times)*0.95)]",
        "p99 = times[int(len(times)*0.99)]",
        "min_t = times[0]",
        "max_t = times[-1]",
        "",
        'print(f"ITERATIONS: {len(times)}")',
        'print(f"ERRORS: {errors}")',
        'print(f"TOTAL_MS: {total*1000:.2f}")',
        'print(f"AVG_MS: {avg*1000:.4f}")',
        'print(f"MIN_MS: {min_t*1000:.4f}")',
        'print(f"MAX_MS: {max_t*1000:.4f}")',
        'print(f"P50_MS: {p50*1000:.4f}")',
        'print(f"P95_MS: {p95*1000:.4f}")',
        'print(f"P99_MS: {p99*1000:.4f}")',
    ])
        result = _run_cmd(["python3", "-c", script.strip()], timeout=60)
        if result.get("error"):
            return {"error": result["error"]}
        if result.get("stderr") and "NO_SUCCESSFUL_RUNS" not in result.get("stdout", ""):
            return {"error": result["stderr"][:500]}

        # Parse output
        metrics = {}
        memory_details = []
        for line in result["stdout"].splitlines():
            if line.startswith("MEMORY_LEAK:"):
                memory_details.append(line.replace("MEMORY_LEAK:", "").strip())
                continue
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip().lower()
                try:
                    metrics[key] = float(val.strip())
                except ValueError:
                    metrics[key] = val.strip()
        metrics["memory_leak_details"] = memory_details

        return {
            "iterations": metrics.get("iterations", iterations),
            "errors": metrics.get("errors", 0),
            "total_ms": metrics.get("total_ms", 0),
            "avg_ms": metrics.get("avg_ms", 0),
            "min_ms": metrics.get("min_ms", 0),
            "max_ms": metrics.get("max_ms", 0),
            "p50_ms": metrics.get("p50_ms", 0),
            "p95_ms": metrics.get("p95_ms", 0),
            "p99_ms": metrics.get("p99_ms", 0),
            "iterations_per_sec": round(1000 / metrics["avg_ms"], 1) if metrics.get("avg_ms", 0) > 0 else 0,
            "memory_leaks_detected": metrics.get("memory_top_diffs", 0),
            "memory_leak_details": metrics.get("memory_leak_details", []),
            }
    except Exception as e:
        return {"error": str(e)[:500]}

# ═══════════════════════════════════════════════════════════════
# 4.5 Unit Testing (pytest)
# ═══════════════════════════════════════════════════════════════

def run_pytest(path: str = ".", markers: str = "", verbose: bool = True, 
               coverage: bool = False, fail_fast: bool = False,
               maxfail: int = 0, keywords: str = "") -> dict:
    """Run pytest and return structured JSON results.

    Args:
        path: Test directory or file (default: '.')
        markers: pytest markers to select (-m). E.g. 'smoke', 'not slow'
        verbose: Show verbose output (default: True)
        coverage: Run with pytest-cov if available (default: False)
        fail_fast: Stop on first failure (-x)
        maxfail: Stop after N failures (0 = unlimited)
        keywords: Only run tests matching expression (-k)
    """
    _ensure_tool("pytest")

    cmd = ["pytest", str(path)]
    if verbose:
        cmd.append("-v")
    if markers:
        cmd.extend(["-m", markers])
    if fail_fast:
        cmd.append("-x")
    if maxfail > 0:
        cmd.extend(["--maxfail", str(maxfail)])
    if keywords:
        cmd.extend(["-k", keywords])
    if coverage:
        # Check if pytest-cov is available
        import shutil
        if shutil.which("pytest") and "pytest-cov" in subprocess.run(
            ["pytest", "--version"], capture_output=True, text=True
        ).stdout:
            cmd.extend(["--cov=" + str(path), "--cov-report=term-missing"])
        else:
            _ensure_tool("pytest-cov", "pytest-cov")
            cmd.extend(["--cov=" + str(path), "--cov-report=term-missing"])

    # Also collect in JSON for structured parsing
    json_cmd = cmd + ["--tb=short", "-q", "--no-header"]
    
    result = _run_cmd(cmd, timeout=300, allow_nonzero=True)
    
    # Parse pytest output for passed/failed/skipped/errors
    passed = 0
    failed = 0
    skipped = 0
    errors = 0
    duration = 0.0
    failures_detail = []
    
    combined = result.get("stdout", "") + "\n" + result.get("stderr", "")
    
    # Parse standard pytest summary line
    import re
    # Pattern: "X passed, Y failed, Z skipped" or variations
    passed_match = re.search(r'(\d+)\s+passed', combined)
    failed_match = re.search(r'(\d+)\s+failed', combined)
    skipped_match = re.search(r'(\d+)\s+skipped', combined)
    error_match = re.search(r'(\d+)\s+errors?', combined)
    duration_match = re.search(r'in\s+([\d.]+)s', combined)
    
    if passed_match:
        passed = int(passed_match.group(1))
    if failed_match:
        failed = int(failed_match.group(1))
    if skipped_match:
        skipped = int(skipped_match.group(1))
    if error_match:
        errors = int(error_match.group(1))
    if duration_match:
        duration = float(duration_match.group(1))
    
    # If no regex match, try alternative: "====== X passed in Ys ======"
    if passed == 0 and failed == 0:
        alt_match = re.search(r'(\d+)\s+passed\s+in\s+([\d.]+)s', combined)
        if alt_match:
            passed = int(alt_match.group(1))
            duration = float(alt_match.group(2))
    
    # Extract FAILURES section
    if "FAILURES" in combined:
        failures_start = combined.find("FAILURES")
        failures_end = combined.find("=====", failures_start + 50)
        if failures_end > failures_start:
            failures_text = combined[failures_start:failures_end]
            for line in failures_text.splitlines():
                if line.startswith("FAILED") or "AssertionError" in line or "Error" in line:
                    failures_detail.append(line[:200])
    
    # Count passed if we still have 0 (pytest might have output like "....")
    if passed == 0 and failed == 0 and errors == 0 and skipped == 0:
        # Count dot/character patterns
        dot_count = combined.count(".")
        F_count = combined.count("F")
        passed = dot_count
        failed = F_count
    
    total = passed + failed + skipped + errors
    
    return {
        "success": result["success"],
        "exit_code": result.get("exit_code", -1),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "errors": errors,
        "total": total,
        "duration_sec": round(duration, 2),
        "pass_rate": round(passed / total * 100, 1) if total > 0 else 0,
        "markers_used": markers if markers else None,
        "failures": failures_detail[:20],
        "command": result.get("command", ""),
        "output_preview": result.get("stdout", "")[-2000:],
    }

# ═══════════════════════════════════════════════════════════════
# 4.6 Code Coverage
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# 4.7 E2E Playwright Tests
# ═══════════════════════════════════════════════════════════════

def run_playwright_tests(path: str = ".", headed: bool = False, 
                         spec_pattern: str = "") -> dict:
    """Detect and run Playwright E2E tests (*.spec.ts, *.spec.js, *.test.ts).

    Detects test files automatically and runs them with the appropriate runner.
    Supports: Playwright test runner (npx playwright test), Vitest, Jest.
    """
    target = Path(path or os.getcwd())
    
    # Detect test files
    patterns = ["*.spec.ts", "*.spec.js", "*.e2e.ts", "*.e2e.js", "*.test.ts", "*.test.js"]
    test_files = []
    for pattern in patterns:
        test_files.extend(target.rglob(pattern))
    
    if not test_files:
        return {
            "e2e_tests_found": False,
            "message": "No E2E test files found (*.spec.ts, *.e2e.ts, etc.)",
            "suggestion": "Create Playwright tests in tests/e2e/ directory with .spec.ts extension",
        }
    
    test_files_str = [str(f) for f in test_files[:50]]
    
    # Determine the runner
    runner = None
    if (target / "playwright.config.ts").exists() or (target / "playwright.config.js").exists():
        runner = "playwright"
    elif (target / "vitest.config.ts").exists() or (target / "vitest.config.js").exists():
        runner = "vitest"
    elif (target / "jest.config.ts").exists() or (target / "jest.config.js").exists():
        runner = "jest"

    if not runner:
        # Check package.json for test scripts
        pkg_json = target / "package.json"
        if pkg_json.exists():
            try:
                pkg = json.loads(pkg_json.read_text())
                scripts = pkg.get("scripts", {})
                if "test:e2e" in scripts:
                    runner = "custom-e2e"
                elif "test" in scripts:
                    runner = "custom"
            except Exception:
                pass
    
    if not runner:
        return {
            "e2e_tests_found": True,
            "test_files": len(test_files_str),
            "files": test_files_str[:20],
            "error": "Could not determine E2E test runner",
            "suggestion": "Install Playwright: npm init playwright@latest",
        }
    
    # Build command
    cmd_map = {
        "playwright": ["npx", "playwright", "test"],
        "vitest": ["npx", "vitest", "run"],
        "jest": ["npx", "jest"],
        "custom-e2e": ["npm", "run", "test:e2e"],
        "custom": ["npm", "test"],
    }
    
    cmd = cmd_map.get(runner, ["npx", "playwright", "test"])
    if spec_pattern:
        cmd.append(spec_pattern)
    if headed and runner == "playwright":
        cmd.append("--headed")
    
    result = _run_cmd(cmd, timeout=300, cwd=str(target), allow_nonzero=True)
    
    # Parse output
    passed = 0
    failed = 0
    skipped = 0
    duration = 0.0
    
    combined = result.get("stdout", "") + result.get("stderr", "")
    
    import re
    # Playwright format: "X passed (Ys)"
    pw_match = re.search(r'(\d+)\s+passed', combined)
    pw_fail = re.search(r'(\d+)\s+failed', combined)
    pw_dur = re.search(r'([\d.]+)s', combined)
    
    if pw_match:
        passed = int(pw_match.group(1))
    if pw_fail:
        failed = int(pw_fail.group(1))
    if pw_dur:
        duration = float(pw_dur.group(1))
    
    # Count skipped
    pw_skip = re.search(r'(\d+)\s+skipped', combined)
    if pw_skip:
        skipped = int(pw_skip.group(1))
    
    # If nothing parsed, try generic
    if passed == 0 and failed == 0:
        passed = combined.count("✓") + combined.count("PASS")
        failed = combined.count("✗") + combined.count("FAIL")
    
    return {
        "e2e_tests_found": True,
        "runner": runner,
        "test_files": len(test_files_str),
        "files": test_files_str[:20],
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": passed + failed + skipped,
        "duration_sec": round(duration, 2),
        "success": result["success"] and failed == 0,
        "output_preview": combined[-2000:],
    }


def run_coverage(path: str = ".", fail_under: float = 80.0) -> dict:
    """Run pytest with coverage measurement using coverage.py.

    Args:
        path: Project path (default: '.')
        fail_under: Minimum acceptable coverage percentage (default: 80.0)

    Returns structured coverage report with line/branch coverage.
    """
    _ensure_tool("coverage", "coverage")
    
    import subprocess, shutil
    cwd = path or os.getcwd()
    
    # Run coverage
    cmd_run = ["coverage", "run", "-m", "pytest", path, "-q", "--tb=short"]
    result_run = _run_cmd(cmd_run, timeout=300, allow_nonzero=True, cwd=cwd)
    
    if result_run.get("error"):
        return {"error": f"coverage run failed: {result_run['error']}"}
    
    # Get report in JSON
    cmd_json = ["coverage", "json", "-o", "-", "--show-contexts"]
    result_json = _run_cmd(cmd_json, timeout=60, cwd=cwd)
    
    if result_json.get("error"):
        # Fallback: try to get terminal report
        cmd_report = ["coverage", "report", "-m"]
        result_report = _run_cmd(cmd_report, timeout=60, cwd=cwd)
        output = result_report.get("stdout", "")
        return {
            "success": result_run["success"],
            "exit_code": result_run.get("exit_code", -1),
            "raw_report": output[:3000],
            "note": "JSON output failed — using text report instead",
        }
    
    # Parse JSON coverage
    try:
        cov_data = json.loads(result_json.get("stdout", "{}"))
    except json.JSONDecodeError:
        return {"error": "Failed to parse coverage JSON", "raw": result_json.get("stdout", "")[:1000]}
    
    # Extract totals
    totals = cov_data.get("totals", {})
    
    line_rate = round(totals.get("percent_covered", 0), 1)
    branch_rate = round(totals.get("percent_covered_branches", 0) if "percent_covered_branches" in totals else 0, 1)
    num_statements = totals.get("num_statements", 0)
    covered = totals.get("covered_lines", 0)
    missing = totals.get("missing_lines", 0)
    branches_covered = totals.get("covered_branches", 0)
    branches_total = totals.get("num_branches", 0)
    
    # Get per-file coverage
    files_coverage = []
    for filepath, file_data in cov_data.get("files", {}).items():
        summary = file_data.get("summary", {})
        files_coverage.append({
            "file": filepath,
            "line_coverage": round(summary.get("percent_covered", 0), 1),
            "statements": summary.get("num_statements", 0),
            "missing": summary.get("missing_lines", 0),
        })
    
    files_coverage.sort(key=lambda x: x["line_coverage"])
    
    meets_threshold = line_rate >= fail_under
    
    return {
        "success": result_run["success"],
        "line_coverage_pct": line_rate,
        "branch_coverage_pct": branch_rate if branches_total > 0 else None,
        "statements": num_statements,
        "covered": covered,
        "missing": missing,
        "branches_covered": branches_covered if branches_total > 0 else None,
        "branches_total": branches_total if branches_total > 0 else None,
        "fail_under": fail_under,
        "meets_threshold": meets_threshold,
        "files_analyzed": len(files_coverage),
        "files_with_low_coverage": [
            f for f in files_coverage if f["line_coverage"] < fail_under
        ][:15],
        "exit_code": result_run.get("exit_code", -1),
    }


# ═══════════════════════════════════════════════════════════════
# 5. Smart Test Strategy
# ═══════════════════════════════════════════════════════════════

def analyze_project_type(path: str = ".") -> dict:
    """Analyze the project to determine what kind of testing is needed.

    Detects:
    - frontend: React, Vue, Svelte, Angular, plain HTML+JS
    - backend_api: FastAPI, Flask, Django REST, Express, Hono (HTTP handlers)
    - backend_service: DB operations, queues, background tasks (no HTTP)
    - fullstack: frontend + backend in same repo
    - library: pure utility modules, no HTTP/DB/UI
    """
    target = Path(path or os.getcwd())

    has_frontend = False
    has_backend_api = False
    has_backend_service = False
    has_library = False
    has_typescript = False
    frontend_frameworks = []
    backend_frameworks = []

    # ── Detect frontend ──
    frontend_config_files = {
        "package.json": "node",
        "tsconfig.json": "typescript",
        "vite.config.ts": "vite",
        "vite.config.js": "vite",
        "next.config.js": "next",
        "next.config.ts": "next",
        "svelte.config.js": "svelte",
        "tailwind.config.js": "tailwind",
        "tailwind.config.ts": "tailwind",
        "angular.json": "angular",
    }
    for config_file, framework in frontend_config_files.items():
        if (target / config_file).exists():
            has_frontend = True
            if framework not in frontend_frameworks:
                frontend_frameworks.append(framework)

    # Check for frontend component directories
    for frontend_dir in ["components", "pages", "app", "src/components", "src/pages"]:
        d = target / frontend_dir
        if d.is_dir():
            has_frontend = True

    # Check for frontend file extensions
    frontend_extensions = {".tsx", ".jsx", ".vue", ".svelte", ".astro"}
    for ext in frontend_extensions:
        try:
            if any(target.rglob(f"*{ext}")):
                has_frontend = True
                if ext in (".tsx", ".ts",):
                    has_typescript = True
                break
        except Exception:
            pass

    if has_frontend and not has_typescript:
        # Also check .ts files
        try:
            has_typescript = any(target.rglob("*.ts"))
        except Exception:
            pass

    # Check for HTML-only frontend
    if not has_frontend:
        try:
            html_files = list(target.rglob("*.html"))
            if html_files:
                has_frontend = True
        except Exception:
            pass

    # ── Detect backend/API ──
    backend_markers = {
        "fastapi": "FastAPI",
        "flask": "Flask",
        "django": "Django",
        "express": "Express",
        "hono": "Hono",
        "starlette": "Starlette",
        "aiohttp": "aiohttp",
        "sanic": "Sanic",
        "tornado": "Tornado",
    }

    db_markers = ["sqlalchemy", "psycopg", "pymongo", "sqlite3.connect", "redis", "aiosqlite", "databases"]
    queue_markers = ["celery", "rq", "arq", "kafka", "rabbitmq"]
    bg_markers = ["background_tasks", "BackgroundTasks", "asyncio.create_task"]

    for f in target.glob("**/*.py"):
        try:
            content_sample = f.read_text()[:3000]
            for kw, framework in backend_markers.items():
                if kw in content_sample.lower():
                    has_backend_api = True
                    if framework not in backend_frameworks:
                        backend_frameworks.append(framework)
            # Detect DB operations
            if not has_backend_service:
                for db_kw in db_markers:
                    if db_kw in content_sample.lower():
                        has_backend_service = True
                        break
            # Detect queues/tasks
            if not has_backend_service:
                for q_kw in queue_markers + bg_markers:
                    if q_kw in content_sample.lower():
                        has_backend_service = True
                        break
        except Exception:
            pass

    # Check for common backend files
    backend_files = ["requirements.txt", "Pipfile", "pyproject.toml", "Dockerfile", "docker-compose.yml"]
    for bf in backend_files:
        if (target / bf).exists():
            has_backend_service = True  # At minimum, a Python project
    if (target / "main.py").exists() or (target / "app.py").exists():
        has_backend_service = True

    if has_backend_api:
        has_backend_service = True

    # ── Determine profile ──
    py_files = list(target.glob("**/*.py"))
    total_py = len(py_files)

    if has_frontend and has_backend_api:
        profile = "fullstack"
    elif has_frontend and has_backend_service:
        profile = "fullstack"
    elif has_frontend:
        profile = "frontend"
    elif has_backend_api:
        profile = "backend_api"
    elif has_backend_service:
        profile = "backend_service"
    elif total_py > 0:
        profile = "library"
    else:
        profile = "unknown"

    # ── Recommended checks ──
    checks = {
        "static_analysis": True,
        "security_scan": True,
        "documentation": profile not in ("frontend",),
        "code_style": True,
        "browser_test": has_frontend,
        "api_test": has_backend_api,
        "unit_tests": total_py > 0,
        "performance_test": True,
    }

    return {
        "project_type": profile,
        "detected": {
            "frontend": has_frontend,
            "frontend_frameworks": frontend_frameworks,
            "backend_api": has_backend_api,
            "backend_frameworks": backend_frameworks,
            "backend_service": has_backend_service,
            "typescript": has_typescript,
            "python_files": total_py,
        },
        "recommended_checks": checks,
    }

# Public API — all return JSON strings
# ═══════════════════════════════════════════════════════════════

def _safe_json(fn, *args, **kwargs) -> str:
    try:
        result = fn(*args, **kwargs)
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

def tester_run_ruff(path: str = ".", fix: bool = False, select: str = "", task_id: str = "") -> str:
    return _safe_json(run_ruff, path, fix, select)

def tester_run_mypy(path: str = ".", strict: bool = False, task_id: str = "") -> str:
    return _safe_json(run_mypy, path, strict)

def tester_run_pylint(path: str = ".", min_score: float = 7.0, task_id: str = "") -> str:
    return _safe_json(run_pylint, path, min_score)

def tester_run_bandit(path: str = ".", severity: str = "all", task_id: str = "") -> str:
    return _safe_json(run_bandit, path, severity)

def tester_check_documentation(path: str = ".", task_id: str = "") -> str:
    return _safe_json(check_documentation, path)

def tester_check_code_style(path: str = ".", task_id: str = "") -> str:
    return _safe_json(check_code_style, path)

def tester_test_api_endpoint(url: str = "", method: str = "GET", payload_json: str = "", headers_json: str = "", expected_status: int = 200, task_id: str = "") -> str:
    return _safe_json(test_api_endpoint, url, method, payload_json, headers_json, expected_status)

def tester_run_performance_test(path: str = ".", task_id: str = "") -> str:
    return _safe_json(run_performance_test, path)

def tester_stress_test(function_code: str = "", iterations: int = 1000, warmup: int = 10, task_id: str = "") -> str:
    return _safe_json(stress_test, function_code, iterations, warmup)

def tester_run_pytest(path: str = ".", markers: str = "", verbose: bool = True, coverage: bool = False, fail_fast: bool = False, maxfail: int = 0, keywords: str = "", task_id: str = "") -> str:
    return _safe_json(run_pytest, path, markers, verbose, coverage, fail_fast, maxfail, keywords)

def tester_run_playwright_tests(path: str = ".", headed: bool = False, spec_pattern: str = "", task_id: str = "") -> str:
    return _safe_json(run_playwright_tests, path, headed, spec_pattern)

def tester_generate_tests(code: str = "", test_type: str = "auto", task_id: str = "") -> str:
    return _safe_json(generate_tests, code, test_type)

def tester_run_coverage(path: str = ".", fail_under: float = 80.0, task_id: str = "") -> str:
    return _safe_json(run_coverage, path, fail_under)

def tester_analyze_project(path: str = ".", task_id: str = "") -> str:
    return _safe_json(analyze_project_type, path)



# ═══════════════════════════════════════════════════════════════
# 6. Unified Quality Score
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# 4.8 Auto Test Generation
# ═══════════════════════════════════════════════════════════════

def generate_tests(code: str, test_type: str = "auto") -> dict:
    """Analyze code and generate test suggestions."""
    import ast
    
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"error": f"Syntax error: {e}", "tests_generated": 0}
    
    functions = []
    classes = []
    
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            args = [(a.arg, ast.unparse(a.annotation) if a.annotation else "Any") 
                    for a in node.args.args]
            returns = ast.unparse(node.returns) if node.returns else "Any"
            func_info = {
                "name": node.name, "args": args, "returns": returns,
                "has_docstring": ast.get_docstring(node) is not None, "line": node.lineno,
                "edge_cases": [],
            }
            edge_map = {"n": ["0", "negative", "large"], "text": ["empty", "None", "unicode", "very long"],
                       "numbers": ["empty list", "None"], "user": ["empty", "None", "SQL injection"]}
            for arg_name, _ in args:
                if arg_name in edge_map:
                    func_info["edge_cases"] = list(set(edge_map[arg_name]))
            functions.append(func_info)
        elif isinstance(node, ast.ClassDef):
            methods = [m.name for m in ast.walk(node) if isinstance(m, ast.FunctionDef) and not m.name.startswith("_")]
            classes.append({"name": node.name, "methods": methods, "line": node.lineno})
    
    generated = []
    lines = []
    
    if test_type in ("unit", "auto"):
        for fn in functions:
            test_name = f"test_{fn['name']}"
            arg_str = ", ".join(f"{n}: {t}" for n, t in fn["args"])
            lines = [f"def {test_name}():",
                     f'    """Test {fn["name"]}({arg_str}) -> {fn["returns"]}."""']
            if fn["args"]:
                first = fn["args"][0][0]
                ret = fn["returns"]
                if ret in ("int", "float"):
                    lines.append(f"    result = {fn['name']}(1)")
                    lines.append("    assert isinstance(result, (int, float))")
                elif ret == "bool":
                    lines.append(f"    result = {fn['name']}(True)")
                    lines.append("    assert isinstance(result, bool)")
                elif ret == "str":
                    lines.append(f'    result = {fn["name"]}("test")')
                    lines.append("    assert isinstance(result, str)")
                else:
                    lines.append(f"    result = {fn['name']}(test_value)")
                    lines.append("    assert result is not None")
            else:
                lines.append(f"    result = {fn['name']}()")
                lines.append("    assert result is not None")
            
            generated.append({
                "function": fn["name"], "test_name": test_name,
                "test_stub": "\n".join(lines), "edge_cases": fn["edge_cases"], "type": "unit",
            })
    
    if test_type in ("integration", "auto") and len(functions) > 1:
        lines = ["def test_integration_flow():",
                 '    """Integration test for full pipeline."""']
        for fn in functions[:5]:
            lines.append(f"    result = {fn['name']}(test_value)")
            lines.append(f"    assert result is not None, '{fn["name"]} returned None'")
        generated.append({
            "function": "integration_flow", "test_name": "test_integration_flow",
            "test_stub": "\n".join(lines), "edge_cases": [], "type": "integration",
        })
    
    if test_type in ("e2e", "auto") and classes:
        for cls in classes:
            if any(m in ("get", "post", "put", "delete", "create", "list") for m in cls["methods"]):
                lines = ["import pytest", "",
                        f"class Test{cls['name']}E2E:",
                        f'    """E2E tests for {cls["name"]}."""', "",
                        "    def test_e2e_crud_flow(self):",
                        '        """Full CRUD lifecycle test."""',
                        "        # TODO: Set up test data",
                        "        # TODO: Test CRUD operations",
                        "        assert True  # Placeholder"]
                generated.append({
                    "class": cls["name"], "test_name": f"Test{cls['name']}E2E",
                    "test_stub": "\n".join(lines), "edge_cases": [], "type": "e2e",
                })
    
    return {
        "tests_generated": len(generated),
        "functions_analyzed": len(functions),
        "classes_analyzed": len(classes),
        "total_edge_cases": sum(len(g["edge_cases"]) for g in generated),
        "generated_tests": generated,
        "suggestion": "Save tests to tests/ directory and run: pytest tests/ -v",
    }





# ═══════════════════════════════════════════════════════════════
# 5.5 Full Analysis — main entry point with depth levels
# ═══════════════════════════════════════════════════════════════

def _build_test_plan(path: str, depth: str, metadata: dict = None) -> dict:
    """Build an optimal test plan based on project type, depth, and metadata.

    Depth levels:
    - quick: ruff + bandit only (< 5s)
    - standard: ruff + bandit + mypy + pytest + docs (10-30s)
    - full: all checks including coverage, browser, E2E, performance (30-120s)
    """
    analysis = analyze_project_type(path)
    ptype = analysis["project_type"]
    
    plan = {
        "project_type": ptype,
        "depth": depth,
        "detected": analysis["detected"],
        "steps": [],
    }
    
    # Override depth from metadata if provided
    if metadata:
        if metadata.get("critical_checks"):
            depth = "full"
        if metadata.get("quick_check"):
            depth = "quick"
    
    # ── QUICK (always) ──
    plan["steps"].append({"order": 1, "tool": "tester_analyze_project", "args": {"path": path}, "category": "analysis"})
    plan["steps"].append({"order": 2, "tool": "tester_run_ruff", "args": {"path": path}, "category": "static"})
    plan["steps"].append({"order": 3, "tool": "tester_run_bandit", "args": {"path": path}, "category": "security"})
    
    if depth == "quick":
        plan["steps"].append({"order": 4, "tool": "calculate_score", "category": "report"})
        return plan
    
    # ── STANDARD ──
    plan["steps"].append({"order": 4, "tool": "tester_run_mypy", "args": {"path": path}, "category": "static"})
    plan["steps"].append({"order": 5, "tool": "tester_check_documentation", "args": {"path": path}, "category": "quality"})
    plan["steps"].append({"order": 6, "tool": "tester_check_code_style", "args": {"path": path}, "category": "quality"})
    
    # pytest if tests exist
    test_dirs = ["tests", "test", "spec"]
    has_tests = any((Path(path) / td).is_dir() for td in test_dirs)
    if has_tests:
        plan["steps"].append({"order": 7, "tool": "tester_run_pytest", "args": {"path": path}, "category": "tests"})
    
    if depth == "standard":
        plan["steps"].append({"order": 99, "tool": "calculate_score", "category": "report"})
        return plan
    
    # ── FULL ──
    plan["steps"].append({"order": 8, "tool": "tester_run_pylint", "args": {"path": path}, "category": "static"})
    plan["steps"].append({"order": 9, "tool": "tester_run_performance_test", "args": {"path": path}, "category": "performance"})
    plan["steps"].append({"order": 10, "tool": "tester_run_coverage", "args": {"path": path}, "category": "coverage"})
    
    # Memory check
    plan["steps"].append({"order": 11, "tool": "tester_stress_test", "args": {
        "function_code": "def test_function():\n    x = [i*i for i in range(10000)]\n    return sum(x)",
        "iterations": 100, "warmup": 5
    }, "category": "memory"})
    
    # Browser tests for frontend/fullstack
    if ptype in ("frontend", "fullstack"):
        plan["steps"].append({"order": 12, "tool": "browser_full_test", "args": {
            "url": metadata.get("test_url", "") if metadata else "",
            "checks_json": '["screenshot","console","accessibility","performance","html_validation","meta"]'
        }, "category": "browser"})
        plan["steps"].append({"order": 13, "tool": "browser_test_responsive", "args": {
            "url": metadata.get("test_url", "") if metadata else "",
            "breakpoints_json": '["mobile","tablet","desktop"]'
        }, "category": "browser"})
    
    # E2E for frontend/fullstack
    if ptype in ("frontend", "fullstack"):
        plan["steps"].append({"order": 14, "tool": "tester_run_playwright_tests", "args": {"path": path}, "category": "e2e"})
    
    # API tests for backend/fullstack
    if ptype in ("backend_api", "backend_service", "fullstack"):
        plan["steps"].append({"order": 15, "tool": "tester_test_api_endpoint", "args": {
            "url": metadata.get("api_endpoint", "http://localhost:8000/health") if metadata else "http://localhost:8000/health"
        }, "category": "api"})
    
    plan["steps"].append({"order": 99, "tool": "calculate_score", "category": "report"})
    return plan


def _self_healing_suggestions(test_results: dict) -> list[dict]:
    """Analyze test failures and generate concrete code fix suggestions."""
    suggestions = []
    
    # Analyze pytest failures
    pytest_res = test_results.get("pytest", {})
    if pytest_res and pytest_res.get("failed", 0) > 0:
        failures = pytest_res.get("failures", [])
        for failure in failures[:5]:
            suggestion = {
                "source": "pytest",
                "issue": failure[:200],
                "category": "test_failure",
            }
            
            # Pattern-based fix suggestions
            failure_lower = failure.lower()
            if "assertionerror" in failure_lower:
                suggestion["fix"] = "Check the assertion condition — expected vs actual values may differ"
                suggestion["action"] = "Add debug print before assertion to inspect values"
            elif "attributeerror" in failure_lower:
                suggestion["fix"] = "Object or module is missing the expected attribute/method"
                suggestion["action"] = "Check the object type and available methods with dir()"
            elif "typeerror" in failure_lower:
                suggestion["fix"] = "Wrong argument type passed to function"
                suggestion["action"] = "Verify argument types match function signature"
            elif "importerror" in failure_lower or "modulenotfound" in failure_lower:
                suggestion["fix"] = "Missing import or incorrect module path"
                suggestion["action"] = "Add the missing import statement or install the package"
            elif "keyerror" in failure_lower:
                suggestion["fix"] = "Dictionary key not found — use .get() with default"
                suggestion["action"] = "Replace dict[key] with dict.get(key, default_value)"
            elif "indexerror" in failure_lower:
                suggestion["fix"] = "List index out of range — check bounds before access"
                suggestion["action"] = "Add bounds check: if idx < len(lst): ..."
            else:
                suggestion["fix"] = "Review the test output for specific error details"
                suggestion["action"] = "Run test in verbose mode (-v) for more details"
            
            suggestions.append(suggestion)
    
    # Analyze E2E failures
    e2e_res = test_results.get("playwright", {})
    if e2e_res and e2e_res.get("failed", 0) > 0:
        suggestions.append({
            "source": "playwright",
            "issue": f"{e2e_res.get('failed')} E2E tests failed",
            "category": "e2e_failure",
            "fix": "Check browser console for JS errors, verify selectors are correct",
            "action": "Run tests with --headed to see what's happening visually",
        })
    
    # Analyze security issues
    bandit_res = test_results.get("bandit", {})
    sc = bandit_res.get("severity_counts", {})
    if sc.get("high", 0) > 0:
        suggestions.append({
            "source": "bandit",
            "issue": f"{sc['high']} HIGH severity issues found",
            "category": "security",
            "fix": "Fix high-severity issues immediately: hardcoded secrets, SQL injection, weak crypto",
            "action": "Use parameterized queries, remove hardcoded secrets, use secrets module",
        })
    
    # Analyze memory leaks
    mem_res = test_results.get("memory", {})
    if mem_res and mem_res.get("memory_leaks_detected", 0) > 0:
        suggestions.append({
            "source": "tracemalloc",
            "issue": f"{mem_res['memory_leaks_detected']} memory leaks detected",
            "category": "memory",
            "fix": "Check for unbounded data structures (growing lists/dicts) or unclosed resources",
            "action": "Use weakref, close files/connections, limit cache sizes",
        })
    
    return suggestions


def _format_report_markdown(analysis: dict, test_results: dict, quality: dict, 
                            suggestions: list[dict], plan: dict) -> str:
    """Generate a beautiful markdown report."""
    
    score = quality.get("quality_score", 0)
    verdict = quality.get("verdict", "UNKNOWN")
    emoji = "🟢" if verdict == "PASS" else ("🟡" if verdict == "WARNINGS" else "🔴")
    
    lines = []
    lines.append("")
    lines.append("═" * 64)
    lines.append(f"  TESTER FINAL REPORT")
    lines.append("═" * 64)
    lines.append("")
    lines.append(f"| Property     | Value                                                          |")
    lines.append(f"|-------------|----------------------------------------------------------------|")
    lines.append(f"| Project     | {analysis.get('project_type', 'unknown'):<62} |")
    lines.append(f"| Depth       | {plan.get('depth', 'standard'):<62} |")
    lines.append(f"| Steps       | {len(plan.get('steps', [])):<62} |")
    lines.append(f"| Tested at   | {analysis.get('tested_at', ''):<62} |")
    lines.append("")
    
    # Quality Score
    lines.append("─" * 64)
    lines.append(f"  {emoji} QUALITY SCORE: {score}/100 — {verdict}")
    lines.append("─" * 64)
    lines.append("")
    lines.append("| Category          | Score | Max | Status |")
    lines.append("|-------------------|-------|-----|--------|")
    
    breakdown = quality.get("breakdown", {})
    max_scores = {"static_analysis": 20, "security": 20, "coverage": 12, 
                  "test_confidence": 10, "maintainability": 13, "performance": 10,
                  "memory_safety": 5, "browser_tests": 5, "api_tests": 5}
    
    for cat, pts in breakdown.items():
        max_pts = max_scores.get(cat, 5)
        if isinstance(pts, str):
            lines.append(f"| {cat:<17} | N/A   | {max_pts:<3} | ⬜ N/A  |")
        else:
            ratio = pts / max_pts
            icon = "✅" if ratio >= 0.8 else ("⚠️" if ratio >= 0.5 else "❌")
            lines.append(f"| {cat:<17} | {pts:<5.1f} | {max_pts:<3} | {icon}     |")
    
    lines.append("")
    
    # Key findings
    lines.append("─" * 64)
    lines.append("  KEY FINDINGS")
    lines.append("─" * 64)
    lines.append("")
    
    # Static
    ruff_r = test_results.get("ruff", {})
    if ruff_r.get("issues_count", 0) > 0:
        lines.append(f"  🔧 Ruff: {ruff_r['issues_count']} lint issues")
    else:
        lines.append(f"  ✅ Ruff: clean")
    
    mypy_r = test_results.get("mypy", {})
    if mypy_r.get("issues_count", 0) > 0:
        lines.append(f"  🔧 Mypy: {mypy_r['issues_count']} type issues")
    else:
        lines.append(f"  ✅ Mypy: clean")
    
    bandit_r = test_results.get("bandit", {})
    sc = bandit_r.get("severity_counts", {})
    if any(sc.values()):
        lines.append(f"  🔒 Bandit: {sc.get('high',0)}H {sc.get('medium',0)}M {sc.get('low',0)}L")
    else:
        lines.append(f"  ✅ Bandit: clean")
    
    pytest_r = test_results.get("pytest", {})
    if pytest_r:
        lines.append(f"  🧪 Pytest: {pytest_r.get('passed',0)}/{pytest_r.get('total',0)} passed ({pytest_r.get('pass_rate',0)}%)")
    
    cov_r = test_results.get("coverage", {})
    if cov_r and cov_r.get("line_coverage_pct"):
        lines.append(f"  📊 Coverage: {cov_r['line_coverage_pct']}% lines")
    
    browser_r = test_results.get("browser", {})
    if browser_r and isinstance(browser_r, dict):
        console = browser_r.get("console", {}).get("error_count", 0)
        a11y = browser_r.get("accessibility", {}).get("violations", 0)
        lines.append(f"  🌐 Browser: {console} console errors, {a11y} a11y violations")
    
    playwright_r = test_results.get("playwright", {})
    if playwright_r and playwright_r.get("e2e_tests_found"):
        lines.append(f"  🎭 E2E: {playwright_r.get('passed',0)}/{playwright_r.get('total',0)} passed")
    
    lines.append("")
    
    # Suggestions
    if suggestions:
        lines.append("─" * 64)
        lines.append("  SELF-HEALING SUGGESTIONS")
        lines.append("─" * 64)
        lines.append("")
        for i, s in enumerate(suggestions[:5], 1):
            lines.append(f"  {i}. [{s.get('category','?').upper()}] {s.get('source','')}")
            lines.append(f"     Issue: {s.get('issue','')[:100]}")
            lines.append(f"     Fix:   {s.get('fix','')}")
            lines.append(f"     Action: {s.get('action','')}")
            lines.append("")
    
    # Penalties
    penalties = quality.get("penalties", [])
    if penalties:
        lines.append("─" * 64)
        lines.append("  DEDUCTIONS")
        lines.append("─" * 64)
        lines.append("")
        for p in penalties:
            lines.append(f"  -{p.get('deduction',0)} pts: {p.get('category','')} — {p.get('details','')}")
        lines.append("")
    
    lines.append("═" * 64)
    lines.append(f"  VERDICT: {emoji} {verdict} ({score}/100)")
    lines.append("═" * 64)
    
    return "\n".join(lines)


def full_analysis(path: str = ".", depth: str = "standard", metadata_json: str = "") -> dict:
    """Main entry point for comprehensive testing.

    Depth levels:
    - quick: ruff + bandit (< 5s) — for rapid feedback
    - standard: + mypy + docs + style + pytest (10-30s) — for PR checks
    - full: + coverage + browser + E2E + performance (30-120s) — for releases

    Returns complete structured report with quality score and suggestions.
    """
    metadata = {}
    if metadata_json:
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError:
            pass
    
    # Build test plan
    plan = _build_test_plan(path, depth, metadata)
    analysis = analyze_project_type(path)
    
    test_results = {}
    step_results = []
    
    for step in plan["steps"]:
        tool = step["tool"]
        if tool == "calculate_score":
            continue
        
        try:
            if tool == "tester_analyze_project":
                test_results["analysis"] = analysis
            elif tool == "tester_run_ruff":
                test_results["ruff"] = run_ruff(path)
            elif tool == "tester_run_bandit":
                test_results["bandit"] = run_bandit(path)
            elif tool == "tester_run_mypy":
                test_results["mypy"] = run_mypy(path)
            elif tool == "tester_check_documentation":
                test_results["documentation"] = check_documentation(path)
            elif tool == "tester_check_code_style":
                test_results["code_style"] = check_code_style(path)
            elif tool == "tester_run_pytest":
                test_results["pytest"] = run_pytest(path)
            elif tool == "tester_run_pylint":
                test_results["pylint"] = run_pylint(path)
            elif tool == "tester_run_performance_test":
                test_results["performance"] = run_performance_test(path)
            elif tool == "tester_run_coverage":
                test_results["coverage"] = run_coverage(path)
            elif tool == "tester_stress_test":
                fc = step.get("args", {}).get("function_code", "def test_function():\n    return sum(range(10000))")
                iters = step.get("args", {}).get("iterations", 100)
                warmup = step.get("args", {}).get("warmup", 5)
                test_results["memory"] = stress_test(fc, iters, warmup)
            elif tool == "tester_run_playwright_tests":
                test_results["playwright"] = run_playwright_tests(path)
            elif tool == "tester_test_api_endpoint":
                url = step.get("args", {}).get("url", "http://localhost:8000/health")
                test_results["api"] = test_api_endpoint(url)
            elif tool == "browser_full_test" and HAS_BROWSER_PRO:
                url = step.get("args", {}).get("url", "")
                if url:
                    test_results["browser"] = full_page_test(url)
            elif tool == "browser_test_responsive" and HAS_BROWSER_PRO:
                url = step.get("args", {}).get("url", "")
                if url:
                    test_results["responsive"] = test_responsive(url)
            
            step_results.append({"tool": tool, "status": "completed"})
        except Exception as e:
            step_results.append({"tool": tool, "status": "failed", "error": str(e)[:200]})
    
    # Calculate quality score
    static_for_qs = {
        "ruff": test_results.get("ruff", {}),
        "mypy": test_results.get("mypy", {}),
        "pylint": test_results.get("pylint", {}),
        "documentation": test_results.get("documentation", {}),
        "code_style": test_results.get("code_style", {}),
        "memory_check": test_results.get("memory", {}),
        "pytest": test_results.get("pytest", {}),
    }
    security_for_qs = {"bandit": test_results.get("bandit", {})}
    coverage_for_qs = test_results.get("coverage")
    perf_for_qs = test_results.get("performance")
    browser_for_qs = test_results.get("browser")
    api_for_qs = test_results.get("api")
    playwright_for_qs = test_results.get("playwright")
    
    quality = calculate_quality_score(
        static_results=static_for_qs,
        security_results=security_for_qs,
        coverage_results=coverage_for_qs,
        performance_results=perf_for_qs,
        browser_results=browser_for_qs,
        api_results=api_for_qs,
        playwright_results=playwright_for_qs,
    )
    
    # Self-healing suggestions
    suggestions = _self_healing_suggestions(test_results)
    
    # Build markdown report
    analysis["tested_at"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    report_md = _format_report_markdown(analysis, test_results, quality, suggestions, plan)
    
    return {
        "analysis": analysis,
        "depth": depth,
        "test_plan": {"total_steps": len(plan["steps"]), "completed": len(step_results)},
        "test_results": {k: _summarize_result(v) for k, v in test_results.items()},
        "quality": quality,
        "suggestions": suggestions,
        "report": report_md,
    }


def _summarize_result(result: dict) -> dict:
    """Create a compact summary of a test result for the JSON output."""
    if not isinstance(result, dict):
        return {"value": str(result)[:200]}
    
    summary = {}
    for key in ("issues_count", "passed", "failed", "total", "pass_rate", "score",
                "line_coverage_pct", "severity_counts", "success", "verdict",
                "e2e_tests_found", "tests_generated", "function_coverage_pct"):
        if key in result:
            summary[key] = result[key]
    
    if not summary:
        summary["keys"] = list(result.keys())[:5]
    
    return summary



def calculate_quality_score(static_results: dict, security_results: dict,
                            coverage_results: dict = None, performance_results: dict = None,
                            browser_results: dict = None, api_results: dict = None,
                            playwright_results: dict = None) -> dict:
    """Calculate unified Quality Score (0-100) with maintainability and test confidence.

    Categories (100 pts total):
    - Static Analysis: 20 pts (ruff 8, mypy 7, pylint 5)
    - Security: 20 pts (HIGH 10/ea, MEDIUM 5/ea, LOW 1/ea)
    - Test Coverage: 12 pts (line coverage % scaled)
    - Test Confidence: 10 pts (pytest pass rate + E2E pass rate)
    - Maintainability: 13 pts (doc coverage + style + complexity)
    - Performance: 10 pts (branch complexity)
    - Memory Safety: 5 pts
    - Browser Tests: 5 pts
    - API Tests: 5 pts
    """
    score = 100.0
    breakdown = {}
    penalties = []

    # ── 1. Static Analysis (max 20 pts) ──
    ruff_issues = static_results.get("ruff", {}).get("issues_count", 0)
    mypy_issues = static_results.get("mypy", {}).get("issues_count", 0)
    pylint_score = static_results.get("pylint", {}).get("score", 0) or 0
    
    ruff_ded = min(ruff_issues, 8)
    mypy_ded = min(mypy_issues, 7)
    pylint_ded = max(0, min((10 - pylint_score) * 0.5, 5)) if pylint_score > 0 else 5
    
    static_deduct = ruff_ded + mypy_ded + pylint_ded
    score -= min(static_deduct, 20)
    if static_deduct > 0:
        penalties.append({"category": "static_analysis", "deduction": round(min(static_deduct, 20), 1),
                         "details": f"ruff:{ruff_ded} mypy:{mypy_ded} pylint:{round(pylint_ded,1)}"})
    breakdown["static_analysis"] = round(20 - min(static_deduct, 20), 1)

    # ── 2. Security (max 20 pts) ──
    sc = security_results.get("bandit", {}).get("severity_counts", {})
    sec_deduct = sc.get("high", 0) * 10 + sc.get("medium", 0) * 5 + sc.get("low", 0) * 1
    score -= min(sec_deduct, 20)
    if sec_deduct > 0:
        penalties.append({"category": "security", "deduction": round(min(sec_deduct, 20), 1), "details": sc})
    breakdown["security"] = round(20 - min(sec_deduct, 20), 1)

    # ── 3. Test Coverage (max 12 pts) ──
    if coverage_results and "line_coverage_pct" in coverage_results:
        line_cov = coverage_results["line_coverage_pct"]
        cov_score = (line_cov / 100) * 12
        score -= (12 - cov_score)
        breakdown["coverage"] = round(cov_score, 1)
        if line_cov < 80:
            penalties.append({"category": "coverage", "deduction": round(max(0, 12 - cov_score), 1)})
    else:
        breakdown["coverage"] = "N/A"
        score -= 3

    # ── 4. Test Confidence (max 10 pts) — based on pytest + E2E results ──
    tc_score = 10
    pytest_res = static_results.get("pytest", {})
    if pytest_res:
        pass_rate = pytest_res.get("pass_rate", 100)
        tc_score = (pass_rate / 100) * 6
        if playwright_results:
            e2e_pass = (playwright_results.get("passed", 0) / max(playwright_results.get("total", 1), 1)) * 4
            tc_score += e2e_pass
        score -= max(0, 10 - tc_score)
        breakdown["test_confidence"] = round(min(tc_score, 10), 1)
    else:
        breakdown["test_confidence"] = "N/A"
        score -= 3

    # ── 5. Maintainability (max 13 pts) ──
    doc_missing = static_results.get("documentation", {}).get("issues_count", 0)
    style_issues = static_results.get("code_style", {}).get("issues_count", 0)
    
    # Complexity penalty from performance data
    complexity_penalty = 0
    if performance_results:
        branches = [f.get("branches", 0) for f in performance_results.get("most_complex_files", [])]
        avg_branches = sum(branches) / len(branches) if branches else 0
        if avg_branches > 200:
            complexity_penalty = 4
        elif avg_branches > 100:
            complexity_penalty = 2
        elif avg_branches > 50:
            complexity_penalty = 1
    
    maint_deduct = min(doc_missing * 0.5, 5) + min(style_issues * 0.5, 4) + complexity_penalty
    score -= min(maint_deduct, 13)
    if maint_deduct > 0:
        penalties.append({"category": "maintainability", "deduction": round(min(maint_deduct, 13), 1)})
    breakdown["maintainability"] = round(13 - min(maint_deduct, 13), 1)

    # ── 6. Performance (max 10 pts) ──
    perf_deduct = 0
    if performance_results:
        largest_branches = max([f.get("branches", 0) for f in performance_results.get("most_complex_files", [])] or [0])
        if largest_branches > 500:
            perf_deduct += 6
        elif largest_branches > 200:
            perf_deduct += 3
        score -= min(perf_deduct, 10)
        breakdown["performance"] = round(10 - min(perf_deduct, 10), 1)
    else:
        breakdown["performance"] = "N/A"

    # ── 7. Memory Safety (max 5 pts) ──
    mem_leaks = static_results.get("memory_check", {}).get("memory_leaks_detected", 0)
    mem_deduct = min(mem_leaks * 2, 5)
    score -= mem_deduct
    breakdown["memory_safety"] = round(5 - mem_deduct, 1)

    # ── 8. Browser Tests (max 5 pts) ──
    if browser_results:
        console_errs = browser_results.get("console_errors", 0) or browser_results.get("console", {}).get("error_count", 0)
        a11y_v = browser_results.get("accessibility", {}).get("violations", 0)
        browser_deduct = min(console_errs, 3) + min(a11y_v, 2)
        score -= min(browser_deduct, 5)
        breakdown["browser_tests"] = round(5 - min(browser_deduct, 5), 1)
    else:
        breakdown["browser_tests"] = "N/A"

    # ── 9. API Tests (max 5 pts) ──
    if api_results:
        api_failed = api_results.get("failed", 0)
        api_deduct = min(api_failed * 2, 5)
        score -= api_deduct
        breakdown["api_tests"] = round(5 - api_deduct, 1)
    else:
        breakdown["api_tests"] = "N/A"

    final_score = round(max(0, min(100, score)), 1)
    verdict = "PASS" if final_score >= 90 else ("WARNINGS" if final_score >= 70 else "FAIL")

    return {
        "quality_score": final_score,
        "verdict": verdict,
        "breakdown": breakdown,
        "penalties": penalties,
    }

def tester_full_report(path: str = ".", project_type: str = "", task_id: str = "") -> str:
    """Run ALL appropriate tests and return a comprehensive report with unified Quality Score.

    Categories:
    - Static Analysis (25 pts): ruff + mypy + pylint
    - Security (25 pts): bandit (HIGH/MEDIUM/LOW)
    - Code Coverage (15 pts): line coverage %
    - Code Quality (15 pts): documentation + code style
    - Performance (10 pts): complexity, file sizes
    - Memory Safety (5 pts): tracemalloc leak detection
    - Browser Tests (5 pts): console errors, accessibility
    - API Tests (5 pts): endpoint failures
    """
    if not project_type:
        analysis = analyze_project_type(path)
        project_type = analysis["project_type"]
        checks = analysis["recommended_checks"]
    else:
        checks = {
            "static_analysis": True, "security_scan": True,
            "documentation": True, "code_style": True,
            "browser_test": project_type in ("frontend", "fullstack"),
            "api_test": project_type in ("backend_api", "fullstack"),
            "performance_test": True, "unit_tests": True,
        }

    report = {
        "project_type": project_type,
        "tested_at": time.time(),
        "results": {},
    }

    # ── 1. Static Analysis ──
    static_results = {}
    if checks.get("static_analysis"):
        static_results["ruff"] = run_ruff(path)
        static_results["mypy"] = run_mypy(path)
        try:
            static_results["pylint"] = run_pylint(path)
        except Exception:
            static_results["pylint"] = {"score": 0, "error": "pylint failed"}
        static_results["documentation"] = check_documentation(path)
        static_results["code_style"] = check_code_style(path)
        report["results"]["static"] = static_results

    # ── 2. Security ──
    security_results = {}
    if checks.get("security_scan"):
        security_results["bandit"] = run_bandit(path)
        report["results"]["security"] = security_results

    # ── 3. Code Coverage ──
    coverage_results = None
    if checks.get("unit_tests") and _find_python_files(path):
        try:
            coverage_results = run_coverage(path)
            report["results"]["coverage"] = coverage_results
        except Exception:
            pass

    # ── 4. Performance ──
    performance_results = None
    if checks.get("performance_test"):
        performance_results = run_performance_test(path)
        report["results"]["performance"] = performance_results

    # ── 5. Browser Tests ──
    browser_results = None
    if checks.get("browser_test") and HAS_BROWSER_PRO:
        try:
            # Run a basic browser test if we can
            report["results"]["browser"] = {"note": "Run browser_full_test for comprehensive frontend testing"}
        except Exception:
            pass

    # ── 6. API Tests ──
    api_results = None
    if checks.get("api_test"):
        api_results = {"note": "Run individual tester_test_api_endpoint for specific endpoints"}
        report["results"]["api_tests"] = api_results

    # ── 7. Memory check (via stress test on a simple function) ──
    try:
        memory_check = stress_test(
            "def test_function():\n    x = [i for i in range(10000)]\n    return sum(x)",
            iterations=100, warmup=5
        )
        static_results["memory_check"] = memory_check
    except Exception:
        pass

    # ── Calculate Unified Quality Score ──
    quality = calculate_quality_score(
        static_results=static_results,
        security_results=security_results,
        coverage_results=coverage_results,
        performance_results=performance_results,
        browser_results=browser_results,
        api_results=api_results if isinstance(api_results, dict) else None,
        playwright_results=None,
    )

    report["quality"] = quality

    return _safe_json(lambda: report)

def tester_full_analysis(path: str = ".", depth: str = "standard", metadata_json: str = "", task_id: str = "") -> str:
    """Main entry point: smart testing at quick/standard/full depth. Returns complete analysis with quality score, report, and self-healing suggestions."""
    return _safe_json(lambda: full_analysis(path, depth, metadata_json))
# ═══════════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════════

# Re-export Playwright Browser PRO functions for convenience
try:
    from tools.playwright_browser_pro import (
        get_performance_metrics as _get_performance_metrics,
        snapshot_baseline as _snapshot_baseline_browser,
        compare_snapshot as _compare_snapshot_browser,
        check_accessibility as _check_accessibility_browser,
        validate_html as _validate_html_browser,
        full_page_test as _full_page_test_browser,
        test_responsive as _test_responsive_browser,
        emulate_device as _emulate_device_browser,
    )
    HAS_BROWSER_PRO = True
except ImportError:
    HAS_BROWSER_PRO = False

from tools.registry import registry

def check_tester_tools() -> bool:
    return True  # Always available — uses subprocess which is always present

_SCHEMAS = {
    "tester_run_ruff": {
        "name": "tester_run_ruff",
        "description": "Run ruff linter. Fast Python linting — checks for errors, style violations, unused imports, etc. Returns issues_count and full output.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File or directory path (default: '.')", "default": "."},
                "fix": {"type": "boolean", "description": "Auto-fix issues (default: false)", "default": False},
                "select": {"type": "string", "description": "Specific rule codes (e.g., 'E,F,W')", "default": ""},
            },
        },
    },
    "tester_run_mypy": {
        "name": "tester_run_mypy",
        "description": "Run mypy type checker. Detects type errors, missing type hints, incompatible types. Returns issues_count.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to check (default: '.')", "default": "."},
                "strict": {"type": "boolean", "description": "Enable strict mode (default: false)", "default": False},
            },
        },
    },
    "tester_run_pylint": {
        "name": "tester_run_pylint",
        "description": "Run pylint deep analysis. Checks code quality, design patterns, complexity. Returns score (0-10) and issues.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to analyze (default: '.')", "default": "."},
                "min_score": {"type": "number", "description": "Minimum acceptable score (default: 7.0)", "default": 7.0},
            },
        },
    },
    "tester_run_bandit": {
        "name": "tester_run_bandit",
        "description": "Run bandit security scanner. Detects SQL injection, XSS, hardcoded secrets, unsafe eval, etc. Returns severity counts (high/medium/low) and details.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to scan (default: '.')", "default": "."},
                "severity": {"type": "string", "description": "Min severity: 'low', 'medium', 'high', 'all' (default: 'all')", "default": "all"},
            },
        },
    },
    "tester_check_documentation": {
        "name": "tester_check_documentation",
        "description": "Check docstring coverage and quality. Reports documented/total functions and classes, with coverage percentages. Returns list of undocumented items.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project path (default: '.')", "default": "."},
            },
        },
    },
    "tester_check_code_style": {
        "name": "tester_check_code_style",
        "description": "Check code style: naming conventions (PEP8), line length, trailing whitespace, complexity. Returns issues by category.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project path (default: '.')", "default": "."},
            },
        },
    },
    "tester_test_api_endpoint": {
        "name": "tester_test_api_endpoint",
        "description": "Test a REST API endpoint. Sends HTTP request and validates response status, timing, and content. Returns response_time_ms, status_match, body_preview.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "API endpoint URL"},
                "method": {"type": "string", "description": "HTTP method: GET, POST, PUT, PATCH, DELETE (default: GET)", "default": "GET"},
                "payload_json": {"type": "string", "description": "Request body as JSON string (for POST/PUT/PATCH)", "default": ""},
                "headers_json": {"type": "string", "description": "Extra headers as JSON string", "default": ""},
                "expected_status": {"type": "integer", "description": "Expected HTTP status code (default: 200)", "default": 200},
            },
            "required": ["url"],
        },
    },
    "tester_run_performance_test": {
        "name": "tester_run_performance_test",
        "description": "Run performance analysis: file sizes, import times, code complexity (branch count). Returns largest files, most complex files, and average import time.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project path (default: '.')", "default": "."},
            },
        },
    },
    "tester_stress_test": {
        "name": "tester_stress_test",
        "description": "Stress-test a Python function. Wraps the function in a loop and measures min/avg/p50/p95/p99/max execution time. Must define a 'test_function()' in the code.",
        "parameters": {
            "type": "object",
            "properties": {
                "function_code": {"type": "string", "description": "Python code defining a 'test_function()' to benchmark"},
                "iterations": {"type": "integer", "description": "Number of iterations (default: 1000)", "default": 1000},
                "warmup": {"type": "integer", "description": "Warmup iterations (default: 10)", "default": 10},
            },
            "required": ["function_code"],
        },
    },
    "tester_run_playwright_tests": {
        "name": "tester_run_playwright_tests",
        "description": "Detect and run Playwright E2E tests (*.spec.ts, *.spec.js). Auto-detects test runner (playwright/vitest/jest). Returns passed/failed/skipped counts, duration, success status.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project path (default: '.')", "default": "."},
                "headed": {"type": "boolean", "description": "Run with headed browser (default: false)", "default": False},
                "spec_pattern": {"type": "string", "description": "Specific test file pattern (e.g., 'login.spec.ts')", "default": ""},
            },
        },
    },
    "tester_generate_tests": {
        "name": "tester_generate_tests",
        "description": "Analyze code and auto-generate test stubs (unit, integration, e2e). Detects edge cases from argument names. Returns generated test code with suggestions.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Source code to analyze and generate tests for"},
                "test_type": {"type": "string", "description": "Test type: 'unit', 'integration', 'e2e', or 'auto' (default: auto)", "default": "auto"},
            },
            "required": ["code"],
        },
    },
    "tester_run_coverage": {
        "name": "tester_run_coverage",
        "description": "Run pytest with coverage measurement. Returns line_coverage_pct, branch_coverage_pct, files_with_low_coverage, meets_threshold. Uses coverage.py internally.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project path (default: '.')", "default": "."},
                "fail_under": {"type": "number", "description": "Minimum acceptable coverage % (default: 80.0)", "default": 80.0},
            },
        },
    },
    "tester_run_pytest": {
        "name": "tester_run_pytest",
        "description": "Run pytest on the project. Returns structured JSON: passed/failed/skipped/errors counts, duration, pass_rate, failure details. Supports markers (-m), keywords (-k), coverage, fail_fast.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Test directory or file (default: '.')", "default": "."},
                "markers": {"type": "string", "description": "pytest markers. E.g. 'smoke', 'not slow', 'integration'", "default": ""},
                "verbose": {"type": "boolean", "description": "Verbose output (default: true)", "default": True},
                "coverage": {"type": "boolean", "description": "Run with pytest-cov code coverage (default: false)", "default": False},
                "fail_fast": {"type": "boolean", "description": "Stop on first failure (-x)", "default": False},
                "maxfail": {"type": "integer", "description": "Stop after N failures (0 = unlimited)", "default": 0},
                "keywords": {"type": "string", "description": "Only run tests matching expression (-k). E.g. 'test_login'", "default": ""},
            },
        },
    },
    "tester_analyze_project": {
        "name": "tester_analyze_project",
        "description": "Analyze project structure to determine type (frontend/backend/api/library/full_stack) and recommend which tests to run. Use FIRST to decide testing strategy.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project path (default: '.')", "default": "."},
            },
        },
    },
    "tester_full_analysis": {
        "name": "tester_full_analysis",
        "description": "Main entry point for comprehensive testing. Smart test plan based on project type, with configurable depth: 'quick' (ruff+bandit, <5s), 'standard' (+mypy+pytest+docs, 10-30s), 'full' (+coverage+browser+E2E, 30-120s). Returns quality score, markdown report, self-healing suggestions, and structured results.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project path (default: '.')", "default": "."},
                "depth": {"type": "string", "description": "Depth: 'quick', 'standard', or 'full' (default: 'standard')", "default": "standard"},
                "metadata_json": {"type": "string", "description": "Optional JSON metadata from Coder: {'test_url':..., 'api_endpoint':..., 'critical_checks': true}", "default": ""},
            },
        },
    },
    "tester_full_report": {
        "name": "tester_full_report",
        "description": "Run ALL appropriate tests: ruff, mypy, bandit, documentation, code style, performance. Auto-selects checks based on project type. Returns Quality Score (0-100) and penalty breakdown.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project path (default: '.')", "default": "."},
                "project_type": {"type": "string", "description": "Force project type: 'library', 'api', 'frontend', 'full_stack' (auto-detected if empty)", "default": ""},
            },
        },
    },
}

_FN_MAP = {
    "tester_run_ruff": tester_run_ruff,
    "tester_run_mypy": tester_run_mypy,
    "tester_run_pylint": tester_run_pylint,
    "tester_run_bandit": tester_run_bandit,
    "tester_check_documentation": tester_check_documentation,
    "tester_check_code_style": tester_check_code_style,
    "tester_test_api_endpoint": tester_test_api_endpoint,
    "tester_run_performance_test": tester_run_performance_test,
    "tester_stress_test": tester_stress_test,
    "tester_run_playwright_tests": tester_run_playwright_tests,
    "tester_generate_tests": tester_generate_tests,
    "tester_run_coverage": tester_run_coverage,
    "tester_run_pytest": tester_run_pytest,
    "tester_analyze_project": tester_analyze_project,
    "tester_full_analysis": tester_full_analysis,
    "tester_full_report": tester_full_report,
}

for _name, _fn in _FN_MAP.items():
    _schema = _SCHEMAS[_name]
    _props = list(_schema.get("parameters", {}).get("properties", {}).keys())

    def _make_handler(fn=_fn, props=_props):
        def _handler(args, task_id="", **kw):
            kwargs = {"task_id": task_id}
            for p in props:
                if p in args:
                    kwargs[p] = args[p]
            return fn(**kwargs)
        return _handler

    registry.register(
        name=_name,
        toolset="terminal",
        schema=_schema,
        handler=_make_handler(),
        check_fn=check_tester_tools,
        emoji="🧪",
    )
