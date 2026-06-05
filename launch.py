#!/usr/bin/env python3
"""Simple launcher for the Personal Command Center.

Usage:
  python3 launch.py
"""
import os
import subprocess
import sys
import time
import webbrowser

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_URL = "http://localhost:8000/dashboard/index.html"

def main():
    print("🚀 Starting Personal Command Center server on port 8000...")
    print("   The dashboard will open in your browser shortly.")
    print("   Press Ctrl+C to stop.\n")

    # Start the server in background
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", "8000"],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        # Give server time to start
        time.sleep(1.0)
        print(f"   Opening {DASHBOARD_URL}")
        webbrowser.open(DASHBOARD_URL)

        # Wait for user to stop
        server.wait()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.terminate()
        server.wait()

if __name__ == "__main__":
    main()