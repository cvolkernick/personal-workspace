#!/usr/bin/env python3
"""#1304 parity harness: agent plan_exercises == daily-tasks TRAINING lifts.

Runs the real DashboardHandler in-process on a spare loopback port as one
user, with the auth check stubbed and no cookies. The plan write guard is
ON for the whole run (``FITDASH_PARITY=1``): workout_plan saves are refused
for any day except the current training day (04:00 cutoff), and logged.

    FITDASH_PARITY_USER=<user_id> python3 scripts/parity_1304.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["FITDASH_PARITY"] = "1"  # before any store import


def _walk(obj, out):
    if isinstance(obj, dict):
        out.append(obj)
        for v in obj.values():
            _walk(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, out)


def main() -> int:
    import server as s
    from rt_dashboard.daily_plan_tasks import lift_name_from_title, session_header_from_lifts
    from rt_dashboard.workout_plan_store import plan_write_guard

    import rt_dashboard.agent_plan as ap

    # One generation per (user, day) for the whole run, so both endpoints see
    # the same plan when the guard keeps it out of the store.
    real_gen, memo = ap.ensure_today_grok_plan, {}

    def _once(*a, **k):
        key = (a[0] if a else k.get("user_id"), k.get("day"))
        if key not in memo:
            memo[key] = real_gen(*a, **k)
        return memo[key]

    ap.ensure_today_grok_plan = _once
    uid = os.environ.get("FITDASH_PARITY_USER") or ""
    if not uid:
        print("FITDASH_PARITY_USER required", file=sys.stderr)
        return 2
    user = {"user_id": uid, "id": uid, "email": "", "display_name": "parity"}
    s._session_user_from_headers = lambda _h: user
    s.DashboardHandler._require_user = lambda _self: user
    with plan_write_guard():
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), s.DashboardHandler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            get = lambda p: json.load(urlopen(f"http://127.0.0.1:{port}{p}", timeout=120))
            wo = get("/api/agent/today")["today"]["workout"]
            tasks = get("/api/daily-tasks")["daily_tasks"]
        finally:
            httpd.shutdown()
    plan = [e.get("name") for e in wo.get("plan_exercises") or []]
    nodes: list = []
    _walk(tasks, nodes)
    lifts, header = [], None
    for n in nodes:
        slug, title = str(n.get("slug") or ""), str(n.get("title") or "")
        if slug.startswith("ex-") and title:
            name = lift_name_from_title(title) or title
            if name not in lifts:
                lifts.append(name)
        if slug == "train-session" and title:
            header = title
    m = re.match(r"Complete today's (\S+) session \((\d+) lifts", header or "")
    want = session_header_from_lifts(lifts, wo.get("session_type") or "")
    parity = bool(plan) and plan == lifts
    head_ok = bool(m) and (m.group(1), int(m.group(2))) == want
    print(json.dumps({"plan_exercises": plan, "training_lifts": lifts, "header": header,
                      "parity": parity, "header_ok": head_ok}, indent=1))
    return 0 if parity and head_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
