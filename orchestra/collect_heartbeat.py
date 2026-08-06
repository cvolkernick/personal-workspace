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
import sys
from pathlib import Path

ORCHESTRA_DIR = Path(__file__).resolve().parent
ROOT = ORCHESTRA_DIR.parent
if str(ORCHESTRA_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRA_DIR))

from heartbeat import latest_path, write_heartbeat  # noqa: E402


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
    args = parser.parse_args(argv)
    workspace = args.workspace.resolve()
    doc = write_heartbeat(workspace)
    path = latest_path(workspace)
    print(f"[pi-heartbeat] wrote {path} ok={doc.get('ok')} host={doc.get('host')}", file=sys.stderr)
    if args.print:
        print(json.dumps(doc, indent=2, default=str))
    return 0 if doc.get("ok") is not False else 0  # always 0 — degraded is data, not collect failure


if __name__ == "__main__":
    raise SystemExit(main())
