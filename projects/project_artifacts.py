"""
Project-level artifact storage — code, workflows, tests, reports.

Every project gets auto-created ``code/`` and ``state/`` directories.
All generated code, test results, and workflow snapshots live here
so nothing is lost between sessions or project switches.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any


def get_project_artifact_dirs(project_id: str) -> tuple[Path, Path]:
    """Return (code_dir, state_dir) for a project. Creates if missing.

    Standard layout::

        ~/.hermes/projects/<slug>/
        ├── code/           ← generated code, .py files
        │   ├── latest.py
        │   ├── final.py
        │   └── best_v1_20260617.py
        └── state/          ← workflows, tests, reports, snapshots
            ├── workflow_latest.json
            └── report_20260617.md
    """
    home = os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
    base = Path(home) / "projects" / project_id
    code_dir = base / "code"
    state_dir = base / "state"
    code_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    return code_dir, state_dir


def save_code(project_id: str, filename: str, code: str) -> Path:
    """Save generated code to the project's code/ directory.

    Also updates ``latest.py`` symlink-style (a copy).
    """
    code_dir, _ = get_project_artifact_dirs(project_id)
    path = code_dir / filename
    path.write_text(code, encoding="utf-8")
    # Update latest pointer
    latest = code_dir / "latest.py"
    latest.write_text(code, encoding="utf-8")
    return path


def save_state(project_id: str, filename: str, data: str | dict) -> Path:
    """Save state/report to the project's state/ directory."""
    _, state_dir = get_project_artifact_dirs(project_id)
    path = state_dir / filename
    import json
    if isinstance(data, dict):
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(data, encoding="utf-8")
    return path


def get_recent_files(project_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return recently modified files in code/ and state/ directories.

    Returns list of {path, name, size, modified, type}.
    """
    code_dir, state_dir = get_project_artifact_dirs(project_id)
    files: list[dict[str, Any]] = []

    for directory, ftype in [(code_dir, "code"), (state_dir, "state")]:
        if not directory.exists():
            continue
        for f in sorted(directory.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.is_file():
                st = f.stat()
                files.append({
                    "path": str(f),
                    "name": f.name,
                    "size": st.st_size,
                    "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
                    "type": ftype,
                })

    files.sort(key=lambda x: x["modified"], reverse=True)
    return files[:limit]


def get_last_file(project_id: str, file_type: str = "code") -> Path | None:
    """Return the most recently modified file of given type.

    Args:
        project_id: Project slug.
        file_type: ``"code"`` or ``"state"``.
    """
    recent = get_recent_files(project_id, limit=20)
    for f in recent:
        if f["type"] == file_type:
            return Path(f["path"])
    return None


def format_recent_files_report(project_id: str, limit: int = 8) -> str:
    """Return a human-readable report of recent project files."""
    files = get_recent_files(project_id, limit=limit)
    if not files:
        return f"  No files in project '{project_id}' yet."

    lines = [f"  📁 Project '{project_id}' — recent files:"]
    for f in files:
        icon = "📄" if f["type"] == "code" else "📊"
        size_kb = f["size"] / 1024
        size_str = f"{size_kb:.1f}KB" if size_kb < 1024 else f"{size_kb/1024:.1f}MB"
        mod = f["modified"][:19].replace("T", " ")
        lines.append(f"    {icon} {f['name']:<24} {size_str:>8}  {mod}")
    return "\n".join(lines)
