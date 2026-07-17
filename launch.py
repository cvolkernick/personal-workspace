#!/usr/bin/env python3
"""Launcher for the Financial / Personal Command Center.

Usage:
  python3 launch.py
"""
import os
import subprocess
import sys
import time
import webbrowser

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_URL = "http://localhost:8000/financial-command/index.html"


def main():
    print("Starting Financial Command Center on port 8000...")
    print("  Refreshing treasury evaluation (Coinbase live + RH snapshot)...")
    try:
        subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "treasury", "run_treasury.py")],
            cwd=REPO_ROOT,
            check=False,
            timeout=60,
        )
    except Exception as e:
        print(f"  treasury refresh skipped: {e}")

    print("  Dashboard will open in your browser. Press Ctrl+C to stop.\n")

    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", "8000"],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        time.sleep(1.0)
        print(f"  Opening {DASHBOARD_URL}")
        webbrowser.open(DASHBOARD_URL)
        server.wait()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.terminate()
        server.wait()


if __name__ == "__main__":
    main()
