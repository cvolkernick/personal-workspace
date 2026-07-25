#!/usr/bin/env python3
"""Launch Orchestrator — prefer Pi backend, fall back to local server.

Usage:
  python3 launch.py
  ORCHESTRATOR_URL=http://192.168.100.98:8790/ python3 launch.py
  python3 launch.py --local          # force local server
"""
from __future__ import annotations

import os
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PI = (
    os.environ.get("ORCHESTRATOR_URL", "http://192.168.100.98:8790/").rstrip("/")
    + "/"
)


def _health_ok(base: str, timeout: float = 3.0) -> bool:
    url = base.rstrip("/") + "/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= int(resp.status) < 300
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        OSError,
        ValueError,
    ):
        return False


def main() -> int:
    argv = list(sys.argv[1:])
    force_local = "--local" in argv

    if not force_local:
        if _health_ok(DEFAULT_PI):
            print(f"Pi Orchestrator is up — opening {DEFAULT_PI}")
            try:
                webbrowser.open(DEFAULT_PI)
            except Exception:
                pass
            print(DEFAULT_PI)
            return 0
        print(f"Pi not reachable at {DEFAULT_PI} — starting local server…")

    server = os.path.join(REPO_ROOT, "orchestra", "server.py")
    cmd = [sys.executable, server, *argv]
    if "--local" not in cmd:
        cmd.append("--local")
    if "--no-browser" not in cmd and force_local:
        pass  # allow browser by default for local
    print("Starting Orchestrator…")
    return int(subprocess.call(cmd, cwd=REPO_ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
