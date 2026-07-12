#!/usr/bin/env python3
"""Start server, hit dashboard routes, capture evidence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("PORT", "8787"))
BASE = f"http://127.0.0.1:{PORT}"


def fetch(path: str) -> tuple[int, str]:
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "verify-launch"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def main() -> int:
    env = os.environ.copy()
    env.setdefault("LOCAL_WORKSPACE_DIR", str(ROOT.parent))
    # Default: live GitHub pull (public). Set GITHUB_PREFER_LOCAL=1 to force local clone.
    env.setdefault("GITHUB_PREFER_LOCAL", "0")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py"), str(PORT)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        # wait for up
        for _ in range(40):
            try:
                st, body = fetch("/api/healthz")
                if st == 200:
                    break
            except Exception:
                time.sleep(0.15)
        else:
            out = proc.stdout.read() if proc.stdout else ""
            print("SERVER_FAILED_TO_START")
            print(out)
            return 1

        st, healthz = fetch("/api/healthz")
        print(f"healthz status={st} body={healthz.strip()}")

        st, page = fetch("/")
        print(f"index status={st} len={len(page)}")
        checks = {
            "has_log_form": 'id="log-form"' in page,
            "has_history": 'id="session-list"' in page,
            "has_recovery": 'id="recovery-card"' in page or "Recovery status" in page,
            "has_charts": "chart-volume" in page and "chart-strength" in page,
            "mobile_viewport": "viewport" in page and "width=device-width" in page,
            "responsive_css_link": "styles.css" in page,
        }
        for k, v in checks.items():
            print(f"page_check {k}={v}")

        st, dash = fetch("/api/dashboard")
        data = json.loads(dash)
        print(f"dashboard status={st}")
        print(
            f"sessions={data.get('session_count')} volume={data.get('total_volume')} "
            f"recovery={data.get('recovery', {}).get('label')} "
            f"source={data.get('meta', {}).get('source')}"
        )
        print(
            f"has_volume_series={bool(data.get('volume_by_week'))} "
            f"has_strength={bool(data.get('strength_trends'))} "
            f"health_error={data.get('health', {}).get('error')!r}"
        )

        # CSS responsive check
        st, css = fetch("/styles.css")
        print(f"css status={st} has_media_800={'@media (min-width: 800px)' in css} has_media_600={'@media (min-width: 600px)' in css}")

        ok = (
            all(checks.values())
            and int(data.get("session_count") or 0) > 0
            and bool((data.get("recovery") or {}).get("label"))
        )
        print(f"LAUNCH_OK={ok}")
        return 0 if ok else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
