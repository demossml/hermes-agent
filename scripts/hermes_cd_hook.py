#!/usr/bin/env python3
"""Fast project detection from cwd — used by shell cd hook.

Checks if the current directory (or an ancestor) corresponds to a
Hermes project via TWO strategies:

1. **Marker file:** walks UP the tree looking for ``.hermes-project``
   (contains project slug).  Fastest — single stat per directory.
2. **Hermes projects dir:** checks if cwd is inside
   ``~/.hermes/projects/<slug>/``.

When found, writes the project slug to
``~/.hermes/projects/.current_project`` — the CLI picks it up via
mtime tracking within 500ms.

Sub-100ms execution for Strategy 1 (marker).  Strategy 2 adds
~20ms for Path operations.
"""

import os, sys
from pathlib import Path


def _find_marker(cwd: Path) -> str | None:
    """Walk up from *cwd* looking for a .hermes-project marker file."""
    current = cwd
    while current != current.parent:
        marker = current / ".hermes-project"
        try:
            if marker.is_file():
                slug = marker.read_text(encoding="utf-8").strip()
                if slug:
                    return slug
        except Exception:
            pass
        current = current.parent
    return None


def _find_in_projects_dir(cwd: Path, projects_dir: Path) -> str | None:
    """Check if *cwd* is inside ~/.hermes/projects/<slug>/."""
    try:
        projects_resolved = projects_dir.resolve()
    except Exception:
        return None

    current = cwd
    while current != current.parent:
        # Direct child of projects_dir?
        try:
            if current.parent == projects_resolved:
                slug = current.name
                if (current / "metadata.json").exists():
                    return slug
        except Exception:
            pass

        # Inside a project subdirectory?
        try:
            rel = current.relative_to(projects_resolved)
            parts = rel.parts
            if parts and not parts[0].startswith("."):
                slug = parts[0]
                if (projects_dir / slug / "metadata.json").exists():
                    return slug
        except ValueError:
            pass

        current = current.parent
    return None


def main() -> None:
    cwd = os.getcwd()
    cwd_path = Path(cwd).resolve()

    # ═══ Strategy 1: .hermes-project marker ═══════════════
    slug = _find_marker(cwd_path)
    if slug:
        hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
        current_file = hermes_home / "projects" / ".current_project"
        current_file.parent.mkdir(parents=True, exist_ok=True)
        current_file.write_text(slug + "\n", encoding="utf-8")
        print(f"📁 {slug} (marker)", flush=True)
        sys.exit(0)

    # ═══ Strategy 2: Hermes projects directory ════════════
    hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    projects_dir = hermes_home / "projects"
    if not projects_dir.is_dir():
        sys.exit(0)

    slug = _find_in_projects_dir(cwd_path, projects_dir)
    if slug:
        current_file = projects_dir / ".current_project"
        current_file.parent.mkdir(parents=True, exist_ok=True)
        current_file.write_text(slug + "\n", encoding="utf-8")
        print(f"📁 {slug}", flush=True)
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()