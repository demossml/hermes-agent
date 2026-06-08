#!/usr/bin/env python3
"""Install agent-mention hook to ~/.hermes/hooks/"""
import shutil
from pathlib import Path

HOOKS_SRC = Path(__file__).resolve().parent / "hooks"
HOOKS_DST = Path.home() / ".hermes" / "hooks"


def install():
    HOOKS_DST.mkdir(parents=True, exist_ok=True)

    hook_src = HOOKS_SRC / "agent-mention"
    hook_dst = HOOKS_DST / "agent-mention"

    if hook_dst.exists():
        shutil.rmtree(hook_dst)

    shutil.copytree(hook_src, hook_dst)
    print(f"Hook installed: {hook_dst}")
    print("Verify:")
    print("  hermes hooks list")
    print("  hermes hooks doctor")


if __name__ == "__main__":
    install()
