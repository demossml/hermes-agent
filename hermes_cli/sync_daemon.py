"""Hermes Live Sync Daemon v2 — Tailscale-only bidirectional code sync.

REQUIRES Tailscale. On change: git add + commit + push.
Periodic pull every 5s. Status includes Tailscale IP, hostname, latency.
"""
from __future__ import annotations
import json, logging, os, subprocess, sys, time
from datetime import datetime
from threading import Thread, Event
from typing import Optional
logger = logging.getLogger(__name__)

# -- Tailscale --
def _ts(cmd, timeout=5):
    try:
        r = subprocess.run(["tailscale"] + cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except: return -1, "", ""
def _ts_ip():
    rc, out, _ = _ts(["ip", "-4"])
    return out if rc == 0 and out.startswith("100.") else None
def _ts_hostname():
    rc, out, _ = _ts(["status", "--json"])
    if rc == 0:
        try: return json.loads(out).get("Self", {}).get("HostName", "unknown")
        except: pass
    return os.uname().nodename if hasattr(os, "uname") else "unknown"
def _ts_peers():
    rc, out, _ = _ts(["status", "--json"])
    if rc != 0: return []
    try:
        data = json.loads(out)
        return [{"hostname": p.get("HostName", pid), "ip": (p.get("TailscaleIPs") or [None])[0], "online": p.get("Online", False)} for pid, p in data.get("Peer", {}).items()]
    except: return []
def _ts_must_be_connected():
    if not _ts(["version"])[0] == 0:
        return None, "Tailscale not installed. https://tailscale.com/download"
    ip = _ts_ip()
    if not ip: return None, "Tailscale not connected. Use: tailscale up"
    return ip, None
def _ts_latency_to(peer_ip):
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", "2", peer_ip], capture_output=True, text=True, timeout=3)
        import re
        for line in r.stdout.split("\n"):
            m = re.search(r"time=([0-9.]+)", line)
            if m: return float(m.group(1))
    except: pass
    return -1.0

# -- Git --
def _git(cmd, cwd, timeout=30):
    try:
        r = subprocess.run(["git"] + cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except: return -1, "", ""
def _has_changes(cwd):
    rc, out, _ = _git(["status", "--porcelain"], cwd)
    return rc == 0 and bool(out)
def _commit(cwd, branch):
    if not _has_changes(cwd): return False
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    host = _ts_hostname()
    tip = _ts_ip() or "no-ts"
    _git(["add", "-A"], cwd)
    rc, _, _ = _git(["commit", "-m", f"sync: {host} ({tip}) at {ts}"], cwd)
    return rc == 0
def _pull(cwd, branch):
    rc, _, _ = _git(["pull", "--rebase", "origin", branch], cwd, timeout=60)
    return rc == 0
def _push(cwd, branch):
    rc, _, _ = _git(["push", "origin", branch], cwd, timeout=30)
    return rc == 0
def _ensure_branch(cwd, branch):
    rc, _, _ = _git(["rev-parse", "--verify", branch], cwd)
    if rc != 0:
        rc2, _, _ = _git(["checkout", "-b", branch], cwd)
        return rc2 == 0
    return True

# -- FileWatcher --
class FileWatcher:
    def __init__(self, directory, callback, debounce_s=1.0):
        self._dir, self._callback, self._debounce = directory, callback, debounce_s
        self._stop, self._last_mtime = Event(), {}
    def start(self):
        t = Thread(target=self._watch, daemon=True); t.start(); return t
    def stop(self): self._stop.set()
    def _watch(self):
        while not self._stop.is_set():
            time.sleep(self._debounce)
            changed = False
            try:
                for root, dirs, files in os.walk(self._dir):
                    skip = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", ".next"}
                    dirs[:] = [d for d in dirs if d not in skip]
                    for fn in files:
                        fp = os.path.join(root, fn)
                        try: mtime = os.path.getmtime(fp)
                        except OSError: continue
                        prev = self._last_mtime.get(fp, 0)
                        if mtime > prev + 0.1: self._last_mtime[fp] = mtime; changed = True
            except: pass
            if changed:
                try: self._callback()
                except: pass

# -- PeriodicPuller --
class PeriodicPuller:
    def __init__(self, cwd, branch, interval_s=5.0):
        self._cwd, self._branch, self._interval = cwd, branch, interval_s
        self._stop, self._on_pull = Event(), None
    def on_pull(self, cb): self._on_pull = cb
    def start(self):
        t = Thread(target=self._run, daemon=True); t.start(); return t
    def stop(self): self._stop.set()
    def _run(self):
        while not self._stop.is_set():
            self._stop.wait(self._interval)
            if self._stop.is_set(): break
            try:
                had = _has_changes(self._cwd)
                if _pull(self._cwd, self._branch):
                    if _has_changes(self._cwd) != had and self._on_pull: self._on_pull()
            except: pass

# -- SyncDaemon --
class SyncDaemon:
    def __init__(self, directory, branch="hermes-live", debounce_s=2.0):
        self._dir = os.path.abspath(os.path.expanduser(directory))
        self._branch, self._debounce = branch, debounce_s
        self._watcher = self._puller = None
        self._pulled_files = self._pushed_commits = 0
        self._ts_ip = self._ts_hostname = None
        self._ts_peers = []

    def start(self, require_tailscale=True):
        self._ts_ip, err = _ts_must_be_connected()
        if require_tailscale and err:
            raise RuntimeError(f"Tailscale required for Live Sync.\n{err}")
        if self._ts_ip:
            self._ts_hostname = _ts_hostname()
            self._ts_peers = _ts_peers()
        if not os.path.isdir(os.path.join(self._dir, ".git")):
            raise RuntimeError(f"{self._dir} is not a git repo")
        _ensure_branch(self._dir, self._branch)
        _pull(self._dir, self._branch)
        if _has_changes(self._dir): _commit(self._dir, self._branch); _push(self._dir, self._branch)
        def on_change():
            if _commit(self._dir, self._branch): _push(self._dir, self._branch); self._pushed_commits += 1
        self._watcher = FileWatcher(self._dir, on_change, self._debounce)
        self._watcher.start()
        def on_remote(): self._pulled_files += 1
        self._puller = PeriodicPuller(self._dir, self._branch, 5.0)
        self._puller.on_pull(on_remote)
        self._puller.start()
        return self.status()

    def stop(self):
        if self._watcher: self._watcher.stop()
        if self._puller: self._puller.stop()

    def status(self):
        running = self._watcher is not None and not self._watcher._stop.is_set()
        online = [p for p in self._ts_peers if p.get("online") and p.get("ip")]
        info = {
            "running": running, "directory": self._dir, "branch": self._branch,
            "tailscale": {"ip": self._ts_ip, "hostname": self._ts_hostname, "peers_online": len(online)},
            "pushed_commits": self._pushed_commits, "pulled_updates": self._pulled_files,
            "has_uncommitted": _has_changes(self._dir) if os.path.isdir(self._dir) else False,
        }
        if online:
            lat = _ts_latency_to(online[0]["ip"])
            info["tailscale"]["latency_ms"] = round(lat, 1) if lat > 0 else None
        return info

    def acl_hint(self):
        if not self._ts_ip: return "N/A"
        online = [p for p in self._ts_peers if p.get("online")]
        lines = [f'  "{p["hostname"]} ({p["ip"]})"' for p in online[:8]]
        return "ACL hints for trusted devices:\n" + "\n".join(lines)
