"""
hermes sync — Live code sync between VS Code and Hermes.
"""
import argparse, os, sys, time
from pathlib import Path

def build_sync_parser(subparsers, *, cmd_sync):
    p = subparsers.add_parser("sync", help="Live bidirectional code sync via git")
    sp = p.add_subparsers(dest="action")

    start = sp.add_parser("start", help="Start sync daemon")
    start.add_argument("--dir", default=os.getcwd(), help="Project directory")
    start.add_argument("--branch", default="hermes-live", help="Git branch")

    stop = sp.add_parser("stop", help="Stop sync daemon")

    sp.add_parser("status", help="Show sync status")
    sp.add_parser("once", help="Run one sync cycle")

    p.set_defaults(func=cmd_sync)

def cmd_sync(args):
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from hermes_cli.sync_daemon import SyncDaemon, _pull, _commit, _push, _has_changes

    action = getattr(args, 'action', 'status')

    if action == 'start':
        d = getattr(args, 'dir', os.getcwd())
        b = getattr(args, 'branch', 'hermes-live')
        print(f"Sync daemon starting: {d} [{b}]")
        daemon = SyncDaemon(d, b)
        try:
            daemon.start()
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            daemon.stop()
            print("Sync stopped.")

    elif action == 'stop':
        print("Sync daemon stop requested. Use Ctrl+C in the sync terminal.")

    elif action == 'once':
        d = os.getcwd()
        b = 'hermes-live'
        print(f"One-shot sync: {d} [{b}]")
        _pull(d, b)
        if _has_changes(d):
            _commit(d, b)
            _push(d, b)
        print("Done.")

    else:
        print("Sync status: use hermes sync start / stop / status")
