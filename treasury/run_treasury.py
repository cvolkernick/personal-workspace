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
from treasury.solana_sync import overlay_solana_snapshot  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate dual-venue treasury policy")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use Coinbase/YNAB snapshot files only (no live CLI/API)",
    )
    parser.add_argument(
        "--skip-ynab",
        action="store_true",
        help="Do not call YNAB live (use one_card snapshot if present)",
    )
    parser.add_argument(
        "--skip-coinbase",
        action="store_true",
        help="Do not call Coinbase CLI (keep coinbase_latest.json; Solana can still be live)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=SNAPSHOTS_DIR / "treasury_latest.json",
        help="Output path for full evaluation JSON",
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    live = not args.offline
    snap = build_snapshot(
        config=cfg,
        prefer_live_coinbase=live and not args.skip_coinbase,
        prefer_live_ynab=live and not args.skip_ynab,
        prefer_live_expenses=live,
        prefer_live_solana=live,
    )
    result = evaluate_treasury(snap, policy=cfg.get("policy") or {})
    out = {
        "snapshot": snap,
        "evaluation": result,
    }
    overlay_solana_snapshot(out)
    save_json(args.out, out)
    # Also publish to financial-command UI path
    dash_out = ROOT / "financial-command" / "treasury_latest.json"
    save_json(dash_out, out)

    stress = result["stress"]["overall"]
    n = len(result["actions"])
    print(json.dumps({"ok": True, "stress": stress, "actions": n, "out": str(args.out)}, indent=2))
    for a in result["actions"][:8]:
        print(f"  P{a['priority']} [{a['actor']}] {a['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
