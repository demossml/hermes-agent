#!/usr/bin/env python3
"""
Hermes Tailscale Server — standalone entry point.

Usage:
    python3 hermes_serve.py --port 8787
    python3 hermes_serve.py --port 8787 --host 100.x.x.x

Start the Hermes JSON-RPC 2.0 server on the Tailscale interface.
"""
import argparse
import sys

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Hermes Tailscale Server")
    p.add_argument("--port", type=int, default=8787, help="Port to listen on")
    p.add_argument("--host", type=str, default="", help="Host to bind (auto-detects Tailscale IP)")
    args = p.parse_args()

    sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
    from hermes_cli.tailscale_server import start_tailscale_server
    start_tailscale_server(port=args.port)
