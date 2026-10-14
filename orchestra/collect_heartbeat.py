#!/usr/bin/env python3
"""One-shot Pi heartbeat collector → orchestra/data/heartbeat/latest.json.

Intended for systemd user timer (pi-heartbeat.timer) on prism-gateway.

Usage:
  python3 orchestra/collect_heartbeat.py
  python3 orchestra/collect_heartbeat.py --workspace /home/prism-agent/personal-workspace
  python3 orchestra/collect_heartbeat.py --print
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ORCHESTRA_DIR = Path(__file__).resolve().parent
ROOT = ORCHESTRA_DIR.parent
if str(ORCHESTRA_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRA_DIR))

from heartbeat import (  # noqa: E402
    atomic_write_json,
    collect_heartbeat,
    heartbeat_dir,
    latest_path,
)
from heartbeat_alerts import STATE_NAME, load_env_file, run_alerts  # noqa: E402

DEFAULT_ENV_FILE = Path.home() / ".config" / "workflow-scheduler.env"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect Pi heartbeat into Orchestra latest.json")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=ROOT,
        help="Monorepo root (default: parent of orchestra/)",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="Print JSON document to stdout after write",
    )
    parser.add_argument(
        "--no-alerts",
        action="store_true",
        help="Skip threshold alerts / #701 posting (env HEARTBEAT_ALERTS=0 does the same)",
    )
    args = parser.parse_args(argv)
    workspace = args.workspace.resolve()
    doc = collect_heartbeat(workspace)
    alerts_on = not args.no_alerts and os.environ.get("HEARTBEAT_ALERTS", "1") not in ("0", "false", "no")
    alert_note = ""
    if alerts_on:
        env_file = Path(os.environ.get("HEARTBEAT_ENV_FILE") or DEFAULT_ENV_FILE).expanduser()
        load_env_file(env_file)  # token for #701; values never printed
        try:
            res = run_alerts(doc, heartbeat_dir(workspace) / STATE_NAME)
            post = res.get("post") or {}
            alert_note = f" status={res['status']} changed={res['changed']}"
            if post:
                alert_note += f" posted={post.get('posted')} {post.get('skipped') or post.get('error') or ''}".rstrip()
        except Exception as e:  # noqa: BLE001 - alerting must never break collection
            alert_note = f" alerts_error={type(e).__name__}"
    path = latest_path(workspace)
    atomic_write_json(path, doc)
    print(f"[pi-heartbeat] wrote {path} ok={doc.get('ok')} host={doc.get('host')}{alert_note}", file=sys.stderr)
    if args.print:
        print(json.dumps(doc, indent=2, default=str))
    return 0 if doc.get("ok") is not False else 0  # always 0 — degraded is data, not collect failure


if __name__ == "__main__":
    raise SystemExit(main())
