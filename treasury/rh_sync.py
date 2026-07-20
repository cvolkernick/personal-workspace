#!/usr/bin/env python3
"""Write Robinhood dual-account snapshot from MCP-shaped JSON.

Live Robinhood Agentic Trading uses the HTTP MCP server:
  https://agent.robinhood.com/mcp/trading
  (configured as ``[mcp_servers.robinhood-trading]`` in ``~/.grok/config.toml``)

Agents should:
  1. get_accounts
  2. get_portfolio for primary margin + agentic account
  3. optional get_equity_positions for each
  4. pipe combined JSON into this script, or call build_robinhood_dual_snapshot

Usage:
  python3 treasury/rh_sync.py --from-live-fixture   # rewrite from last known shape
  python3 treasury/rh_sync.py --json path.json
  cat envelope.json | python3 treasury/rh_sync.py --stdin

Envelope shape (agent-written)::

  {
    "primary_portfolio": { "data": { ... get_portfolio ... } },
    "agentic_portfolio": { "data": { ... } },
    "primary_account": "5QW39737",
    "agentic_account": "674601752",
    "primary_positions": [ ... ],
    "agentic_positions": [ ... ],
    "accounts": [ ... get_accounts.accounts ... ]
  }
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.adapters import (  # noqa: E402
    SNAPSHOTS_DIR,
    build_robinhood_dual_snapshot,
    load_config,
    load_json,
    write_robinhood_snapshot,
)


def _load_envelope(path: Optional[Path], use_stdin: bool) -> Dict[str, Any]:
    if use_stdin:
        return json.load(sys.stdin)
    if path:
        data = load_json(path)
        if not data:
            raise SystemExit(f"could not read {path}")
        return data
    raise SystemExit("pass --json, --stdin, or --from-config-accounts")


def write_from_envelope(env: Dict[str, Any], *, out: Optional[Path] = None) -> Path:
    cfg = load_config()
    rh = cfg.get("robinhood") or {}
    snap = build_robinhood_dual_snapshot(
        primary_portfolio=env.get("primary_portfolio") or env.get("primary") or {},
        agentic_portfolio=env.get("agentic_portfolio") or env.get("agentic"),
        primary_account=env.get("primary_account") or rh.get("account_number"),
        agentic_account=env.get("agentic_account") or rh.get("agentic_account_number"),
        primary_positions=env.get("primary_positions"),
        agentic_positions=env.get("agentic_positions"),
        accounts=env.get("accounts"),
        source=env.get("source") or "live",
        notes=env.get("notes"),
    )
    return write_robinhood_snapshot(snap, path=out)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", type=Path, help="Envelope JSON path")
    p.add_argument("--stdin", action="store_true", help="Read envelope from stdin")
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Output path (default {SNAPSHOTS_DIR / 'robinhood_latest.json'})",
    )
    p.add_argument(
        "--print",
        action="store_true",
        help="Print snapshot JSON to stdout",
    )
    args = p.parse_args(argv)

    if not args.json and not args.stdin:
        p.error("provide --json PATH or --stdin with MCP envelope")

    env = _load_envelope(args.json, args.stdin)
    path = write_from_envelope(env, out=args.out)
    snap = load_json(path) or {}
    if args.print:
        print(json.dumps(snap, indent=2))
    else:
        print(
            json.dumps(
                {
                    "ok": True,
                    "path": str(path),
                    "primary_bp": snap.get("buying_power"),
                    "primary_value": snap.get("total_value"),
                    "agentic_bp": (snap.get("agentic") or {}).get("buying_power"),
                    "agentic_value": (snap.get("agentic") or {}).get("total_value"),
                    "positions": len(snap.get("positions") or []),
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
