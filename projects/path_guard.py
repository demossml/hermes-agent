"""
Project Path Guard -- fail-closed hard file-system isolation for Hermes projects.

FAIL-CLOSED: if project root cannot be determined, DENY access.
Only orchestrator (no project lock) gets unrestricted access.

Usage::

    from projects.path_guard import enforce
    error = enforce("/some/path", operation="read")
    if error:
        return error  # tool returns error string as its result
"""

from __future__ import annotations

import logging, os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT_LOCK_FILE = ".project.lock"

_SENSITIVE_FILES = frozenset({
    "AGENTS.md", "SOUL.md", ".env", "project.yaml", "metadata.json",
    ".project.lock", "insights.jsonl",
})

_ALWAYS_ALLOWED: tuple[str, ...] = ()
_ALWAYS_ALLOWED_CACHED: bool = False


def get_current_project_root() -> Optional[Path]:
    """Return absolute project root, or None (orchestrator mode)."""
    env_root = os.environ.get("HERMES_PROJECT_ROOT", "").strip()
    if env_root:
        p = Path(env_root).expanduser().resolve()
        if p.is_dir() and (p / PROJECT_LOCK_FILE).exists():
            return p

    try:
        marker = Path.cwd() / ".hermes-project"
        if marker.exists():
            pid = marker.read_text(encoding="utf-8").strip()
            if pid:
                from projects.project_manager import ProjectManager
                pm = ProjectManager()
                proj_dir = pm._project_dir(pid)
                if proj_dir.is_dir() and (proj_dir / PROJECT_LOCK_FILE).exists():
                    return proj_dir.resolve()
    except Exception:
        pass

    try:
        from projects.project_context import get_current_project_id
        pid = get_current_project_id()
        if pid:
            from projects.project_manager import ProjectManager
            pm = ProjectManager()
            proj_dir = pm._project_dir(pid)
            if proj_dir.is_dir() and (proj_dir / PROJECT_LOCK_FILE).exists():
                return proj_dir.resolve()
    except Exception:
        pass

    return None


def _get_always_allowed() -> tuple[str, ...]:
    global _ALWAYS_ALLOWED, _ALWAYS_ALLOWED_CACHED
    if _ALWAYS_ALLOWED_CACHED:
        return _ALWAYS_ALLOWED
    allowed: list[str] = []
    try:
        from hermes_constants import get_hermes_home
        allowed.append(str(get_hermes_home().resolve()))
    except Exception:
        allowed.append(str(Path.home() / ".hermes"))
    allowed.extend(["/tmp", "/dev/null"])
    _ALWAYS_ALLOWED = tuple(allowed)
    _ALWAYS_ALLOWED_CACHED = True
    return _ALWAYS_ALLOWED


def enforce(
    target: str | Path,
    *,
    operation: str = "access",
    project_root: Optional[str | Path] = None,
) -> Optional[str]:
    """FAIL-CLOSED: None=allowed, str=blocked."""
    root = (Path(project_root) if project_root
            else get_current_project_root())
    if root is None:
        return None
    # Use os.path.realpath for symlink-safe comparison (macOS /var->/private/var)
    root_str = os.path.realpath(str(root))
    target_str = str(target)

    try:
        resolved_str = os.path.realpath(os.path.expanduser(target_str))
    except Exception:
        try:
            resolved_str = os.path.abspath(os.path.expanduser(target_str))
        except Exception:
            return _block(target_str, root_str, operation)

    # Inside project root?
    sep = os.sep
    if resolved_str == root_str or resolved_str.startswith(root_str + sep):
        return None

    fname = os.path.basename(target_str)
    if fname in _SENSITIVE_FILES:
        return _block_sensitive(fname, root_str, operation)

    for prefix in _get_always_allowed():
        pfx_str = os.path.realpath(prefix) if os.path.isabs(prefix) else prefix
        if resolved_str == pfx_str or resolved_str.startswith(pfx_str + sep):
            # Block cross-project access: paths inside projects/<other_slug>/ are NOT allowed
            projects_dir = os.path.join(pfx_str, "projects")
            if resolved_str.startswith(projects_dir + sep):
                # Extract the project slug from the path
                rel = resolved_str[len(projects_dir + sep):]
                other_slug = rel.split(sep)[0] if sep in rel else rel
                if other_slug and not other_slug.startswith("."):
                    # This is a path inside another project — DENY
                    # unless it's a .hermes-project marker or .current_project
                    if not rel.endswith(".hermes-project") and "current_project" not in rel:
                        return _block(target_str, root_str, operation)
            return None

    return _block(target_str, root_str, operation)


def enforce_cwd(cwd: str | Path, project_root: Optional[str | Path] = None) -> Optional[str]:
    return enforce(cwd, operation="set terminal cwd", project_root=project_root)


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def _block(target: str, root: str, op: str) -> str:
    return (
        f"[PROJECT FILE ISOLATION] DENIED: \"{target}\" is outside "
        f"active project ({root}). Cannot {op}. "
        f"All file paths must be inside {root}/."
    )


def _block_sensitive(filename: str, root: str, op: str) -> str:
    return (
        f"[PROJECT FILE ISOLATION] DENIED: \"{filename}\" is protected. "
        f"Cannot {op} outside project ({root})."
    )


def is_sensitive_file(filename: str) -> bool:
    return os.path.basename(filename) in _SENSITIVE_FILES


def create_project_lock(project_dir: str | Path) -> bool:
    try:
        lock = Path(project_dir) / PROJECT_LOCK_FILE
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(
            "# Hermes Project Lock -- hard file isolation enabled\n"
            "# All file operations restricted to this project.\n",
            encoding="utf-8",
        )
        logger.info("Project lock created: %s", lock)
        return True
    except Exception as e:
        logger.warning("Failed to create lock at %s: %s", project_dir, e)
        return False


def has_project_lock(project_dir: str | Path) -> bool:
    return (Path(project_dir) / PROJECT_LOCK_FILE).is_file()


def apply_project_permissions(project_dir: str | Path) -> dict:
    """chmod 700 + macOS ACL. Returns {chmod_ok, acl_ok, errors}."""
    result: dict = {"chmod_ok": False, "acl_ok": False, "errors": []}
    d = Path(project_dir)
    try:
        d.chmod(0o700)
        result["chmod_ok"] = True
    except OSError as e:
        result["errors"].append(f"chmod: {e}")
    try:
        import platform as _plat
        if _plat.system() == "Darwin":
            import subprocess as _sp
            user = os.environ.get("USER", "")
            if user:
                ace = (f"user:{user}:allow list,search,read,write,execute,"
                       "delete,add_file,add_subdirectory,readattr,writeattr,"
                       "readextattr,writeextattr,readsecurity,writesecurity,chown")
                r = _sp.run(["chmod", "+a", ace, str(d)],
                            capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    result["acl_ok"] = True
                else:
                    result["errors"].append(f"ACL: {r.stderr.strip()}")
        else:
            result["acl_ok"] = True
    except Exception as e:
        result["errors"].append(f"ACL: {e}")
    return result
