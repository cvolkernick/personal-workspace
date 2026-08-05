#!/usr/bin/env python3
"""Launcher for the Orchestra top-level command center.

Usage:
  python3 launch.py
  python3 launch.py --port 8790 --no-browser
"""
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    server = os.path.join(REPO_ROOT, "orchestra", "server.py")
    cmd = [sys.executable, server, *sys.argv[1:]]
    print("Starting Orchestrator…")
    raise SystemExit(subprocess.call(cmd, cwd=REPO_ROOT))


if __name__ == "__main__":
    main()
