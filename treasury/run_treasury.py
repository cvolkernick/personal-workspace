#!/usr/bin/env python3
"""Build treasury snapshot, evaluate policy, write dashboard JSON.

Usage:
  python3 treasury/run_treasury.py
  python3 treasury/run_treasury.py --offline   # skip live Coinbase CLI
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.adapters import (  # noqa: E402
    SNAPSHOTS_DIR,
    build_snapshot,
    load_config,
    save_json,
)
from treasury.policy import evaluate_treasury  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate dual-venue treasury policy")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use Coinbase snapshot file only (no live CLI)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=SNAPSHOTS_DIR / "treasury_latest.json",
        help="Output path for full evaluation JSON",
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    snap = build_snapshot(config=cfg, prefer_live_coinbase=not args.offline)
    policy = {**(cfg.get("policy") or {}), **(snap.get("policy_overrides") or {})}
    # policy_overrides already in snap; evaluate uses explicit policy arg
    result = evaluate_treasury(snap, policy=cfg.get("policy") or {})
    out = {
        "snapshot": snap,
        "evaluation": result,
    }
    save_json(args.out, out)
    # Also publish to dashboard-readable path
    dash_out = ROOT / "dashboard" / "treasury_latest.json"
    save_json(dash_out, out)

    stress = result["stress"]["overall"]
    n = len(result["actions"])
    print(json.dumps({"ok": True, "stress": stress, "actions": n, "out": str(args.out)}, indent=2))
    for a in result["actions"][:8]:
        print(f"  P{a['priority']} [{a['actor']}] {a['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
