
"""
Hermes Live Sync Daemon — bidirectional real-time code sync via git.

Watches a project directory for file changes. On change:
  1. git add -A
  2. git commit -m "sync: auto-commit at {timestamp}"
  3. git pull --rebase origin <branch>
  4. git push origin <branch>

Also runs a periodic pull (every 5s) to receive remote changes.

Usage:
    python3 -m hermes_cli.sync_daemon --dir ~/project --branch hermes-live
"""
from __future__ import annotations

import logging, os, subprocess, sys, time
from datetime import datetime
from pathlib import Path
from threading import Thread, Event
from typing import Optional

logger = logging.getLogger(__name__)

# ── Git helpers ──────────────────────────────────────────────

def _git(cmd: list[str], cwd: str, timeout: int = 30) -> tuple[int, str, str]:
    try:
        r = subprocess.run(["git"] + cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as e:
        return -1, "", str(e)

def _has_changes(cwd: str) -> bool:
    rc, out, _ = _git(["status", "--porcelain"], cwd)
    return rc == 0 and bool(out)

def _commit(cwd: str, branch: str) -> bool:
    if not _has_changes(cwd):
        return False
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    host = os.uname().nodename if hasattr(os, 'uname') else 'unknown'
    _git(["add", "-A"], cwd)
    rc, _, _ = _git(["commit", "-m", f"sync: {host} at {ts}"], cwd)
    return rc == 0

def _pull(cwd: str, branch: str) -> bool:
    rc, out, err = _git(["pull", "--rebase", "origin", branch], cwd, timeout=60)
    if rc != 0:
        logger.warning("Pull failed: %s %s", out, err)
    return rc == 0

def _push(cwd: str, branch: str) -> bool:
    rc, out, err = _git(["push", "origin", branch], cwd, timeout=30)
    if rc != 0:
        logger.warning("Push failed: %s %s", out, err)
    return rc == 0

def _ensure_branch(cwd: str, branch: str) -> bool:
    rc, _, _ = _git(["rev-parse", "--verify", branch], cwd)
    if rc != 0:
        rc2, _, _ = _git(["checkout", "-b", branch], cwd)
        return rc2 == 0
    return True

# ── File watcher ─────────────────────────────────────────────

class FileWatcher:
    def __init__(self, directory: str, callback, debounce_s: float = 1.0):
        self._dir = directory
        self._callback = callback
        self._debounce = debounce_s
        self._stop = Event()
        self._last_mtime: dict[str, float] = {}

    def start(self):
        t = Thread(target=self._watch, daemon=True)
        t.start()
        return t

    def stop(self):
        self._stop.set()

    def _watch(self):
        last_scan = time.time()
        while not self._stop.is_set():
            time.sleep(self._debounce)
            changed = False
            try:
                for root, dirs, files in os.walk(self._dir):
                    if '.git' in dirs: dirs.remove('.git')
                    if '__pycache__' in dirs: dirs.remove('__pycache__')
                    if 'node_modules' in dirs: dirs.remove('node_modules')
                    for fn in files:
                        fp = os.path.join(root, fn)
                        try:
                            mtime = os.path.getmtime(fp)
                        except OSError:
                            continue
                        prev = self._last_mtime.get(fp, 0)
                        if mtime > prev + 0.1:
                            self._last_mtime[fp] = mtime
                            changed = True
            except Exception:
                pass
            if changed:
                try:
                    self._callback()
                except Exception:
                    pass

# ── Periodic pull ────────────────────────────────────────────

class PeriodicPuller:
    def __init__(self, cwd: str, branch: str, interval_s: float = 5.0):
        self._cwd = cwd
        self._branch = branch
        self._interval = interval_s
        self._stop = Event()
        self._on_pull: Optional[callable] = None

    def on_pull(self, callback):
        self._on_pull = callback

    def start(self):
        t = Thread(target=self._run, daemon=True)
        t.start()
        return t

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            self._stop.wait(self._interval)
            if self._stop.is_set():
                break
            try:
                had_changes = _has_changes(self._cwd)
                pulled = _pull(self._cwd, self._branch)
                if pulled:
                    now_has = _has_changes(self._cwd)
                    if now_has != had_changes and self._on_pull:
                        self._on_pull()
            except Exception:
                pass

# ── Sync daemon ──────────────────────────────────────────────

class SyncDaemon:
    def __init__(self, directory: str, branch: str = "hermes-live", debounce_s: float = 2.0):
        self._dir = os.path.abspath(os.path.expanduser(directory))
        self._branch = branch
        self._debounce = debounce_s
        self._watcher: Optional[FileWatcher] = None
        self._puller: Optional[PeriodicPuller] = None
        self._pulled_files = 0
        self._pushed_commits = 0

    def start(self):
        if not os.path.isdir(os.path.join(self._dir, '.git')):
            raise RuntimeError(f"{self._dir} is not a git repository")

        _ensure_branch(self._dir, self._branch)
        logger.info("Sync daemon starting: %s on branch %s", self._dir, self._branch)

        # Initial sync
        _pull(self._dir, self._branch)
        if _has_changes(self._dir):
            _commit(self._dir, self._branch)
            _push(self._dir, self._branch)

        # File watcher — local changes → commit + push
        def on_local_change():
            if _commit(self._dir, self._branch):
                _push(self._dir, self._branch)
                self._pushed_commits += 1
                logger.info("Pushed commit #%d", self._pushed_commits)

        self._watcher = FileWatcher(self._dir, on_local_change, self._debounce)
        self._watcher_thread = self._watcher.start()

        # Periodic pull — remote changes
        def on_remote_change():
            self._pulled_files += 1
            logger.info("Pulled remote changes (#%d)", self._pulled_files)

        self._puller = PeriodicPuller(self._dir, self._branch, interval_s=5.0)
        self._puller.on_pull(on_remote_change)
        self._puller_thread = self._puller.start()

        logger.info("Sync daemon running (watch + pull every 5s)")

    def stop(self):
        if self._watcher:
            self._watcher.stop()
        if self._puller:
            self._puller.stop()
        logger.info("Sync daemon stopped")

    def status(self) -> dict:
        return {
            "directory": self._dir,
            "branch": self._branch,
            "running": self._watcher is not None and not self._watcher._stop.is_set(),
            "pushed_commits": self._pushed_commits,
            "pulled_updates": self._pulled_files,
            "has_uncommitted": _has_changes(self._dir) if os.path.isdir(self._dir) else False,
        }


# ── CLI entry ────────────────────────────────────────────────

def main():
    import argparse
    p = argparse.ArgumentParser(description="Hermes Live Sync Daemon")
    p.add_argument("--dir", required=True, help="Project directory to sync")
    p.add_argument("--branch", default="hermes-live", help="Git branch (default: hermes-live)")
    p.add_argument("--once", action="store_true", help="Do one sync cycle and exit")
    sp = p.add_subparsers(dest="cmd")
    sp.add_parser("start")
    sp.add_parser("stop")
    sp.add_parser("status")

    args = p.parse_args()

    if args.once:
        _pull(args.dir, args.branch)
        if _has_changes(args.dir):
            _commit(args.dir, args.branch)
            _push(args.dir, args.branch)
        print("Sync done.")
        return

    daemon = SyncDaemon(args.dir, args.branch)
    try:
        daemon.start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        daemon.stop()
