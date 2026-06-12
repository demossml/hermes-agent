"""
Hermes DuckDB utilities — profile-aware database path resolution.

Usage::

    from hermes_tools.db import get_hermes_db_path

    db_path = get_hermes_db_path()           # default profile
    db_path = get_hermes_db_path("bot-1")    # named profile/clone

    import duckdb
    conn = duckdb.connect(str(db_path))
"""

from pathlib import Path
import os


def get_hermes_db_path(profile_name: str | None = None) -> Path:
    """Return the DuckDB path for a given Hermes profile or clone.

    Uses ``HERMES_HOME`` and ``HERMES_PROFILE`` environment variables
    when available, falling back to sensible defaults.

    Args:
        profile_name: Profile/clone name.  ``None`` or ``"default"``
                      resolves to the main Hermes data directory.

    Returns:
        Absolute path to ``<data_dir>/evotor.duckdb``,
        with parent directories created as needed.
    """
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))

    if not profile_name:
        profile_name = os.environ.get("HERMES_PROFILE", "default")

    if profile_name in ("default", "") or not profile_name:
        db_dir = home / "data"
    else:
        db_dir = home / "profiles" / profile_name / "data"

    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "evotor.duckdb"


def get_hermes_db_connection(profile_name: str | None = None, *, read_only: bool = False):
    """Return a DuckDB connection for the given profile.

    Args:
        profile_name: Profile/clone name.
        read_only:    Open in read-only mode (default False).

    Returns:
        ``duckdb.DuckDBPyConnection`` or ``None`` if DuckDB is not installed.
    """
    try:
        import duckdb
    except ImportError:
        return None

    path = get_hermes_db_path(profile_name)
    return duckdb.connect(str(path), read_only=read_only)
