#!/usr/bin/env python3
"""Launcher for the Financial Command Center.

Usage:
  python3 launch.py
  python3 launch.py --port 8000 --offline
"""
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    server = os.path.join(REPO_ROOT, "financial-command", "server.py")
    cmd = [sys.executable, server, *sys.argv[1:]]
    print("Starting Financial Command Center…")
    raise SystemExit(subprocess.call(cmd, cwd=REPO_ROOT))


if __name__ == "__main__":
    main()
