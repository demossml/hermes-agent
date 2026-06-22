#!/usr/bin/env python3
"""Fast project detection from cwd — used by shell cd hook.

Checks if the current directory (or an ancestor) is inside
``~/.hermes/projects/<slug>/`` and, if so, writes that project_id
to ``~/.hermes/projects/.current_project``.

Designed for sub-100ms execution — no imports beyond stdlib + project_manager.
"""

import os, sys
from pathlib import Path


def main() -> None:
    cwd = os.getcwd()

    # Resolve hermes home
    hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    projects_dir = hermes_home / "projects"
    current_file = projects_dir / ".current_project"

    if not projects_dir.is_dir():
        sys.exit(0)

    cwd_path = Path(cwd).resolve()

    # Walk up to find a project directory
    current = cwd_path
    while current != current.parent:
        if current.parent == projects_dir.resolve():
            slug = current.name
            meta_path = current / "metadata.json"
            if meta_path.exists():
                # Found a project — write to .current_project
                current_file.parent.mkdir(parents=True, exist_ok=True)
                current_file.write_text(slug + "\n", encoding="utf-8")
                print(f"📁 {slug}", flush=True)
                sys.exit(0)
        # Also check if inside a project subdirectory
        try:
            rel = current.relative_to(projects_dir.resolve())
            parts = rel.parts
            if parts and not parts[0].startswith("."):
                slug = parts[0]
                meta_path = projects_dir / slug / "metadata.json"
                if meta_path.exists():
                    current_file.parent.mkdir(parents=True, exist_ok=True)
                    current_file.write_text(slug + "\n", encoding="utf-8")
                    print(f"📁 {slug}", flush=True)
                    sys.exit(0)
        except ValueError:
            pass
        current = current.parent

    # No project found — don't touch .current_project
    sys.exit(0)


if __name__ == "__main__":
    main()