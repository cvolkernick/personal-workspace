#!/usr/bin/env python3
"""Refresh BTC/USD on the Pi into treasury/snapshots/coinbase_latest.json.

Issue #695 — Pi is the price producer. Public Coinbase spot, no CLI / node /
secrets. Liquid USDC/BTC balances stay Mac-CLI (out of scope); this only
patches ``btc_usd_price`` + ``as_of`` and preserves the rest of the file.

Usage:
  python3 -m treasury.coinbase_price_sync
  python3 -m treasury.coinbase_price_sync --print
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.adapters import (  # noqa: E402
    SNAPSHOTS_DIR,
    fetch_btc_usd_price,
    load_json,
    save_json,
)

OUT_PATH = SNAPSHOTS_DIR / "coinbase_latest.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def merge_btc_usd_price(
    existing: Optional[Dict[str, Any]],
    price: float,
    *,
    as_of: str,
    source: str = "live",
) -> Dict[str, Any]:
    """Patch price + as_of onto the existing snapshot; keep other keys."""
    out: Dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    out["btc_usd_price"] = price
    out["as_of"] = as_of
    out["source"] = source
    out.pop("btc_price_error", None)
    liquid_btc = out.get("liquid_btc")
    try:
        btc_f = float(liquid_btc) if liquid_btc is not None else None
    except (TypeError, ValueError):
        btc_f = None
    if btc_f is not None:
        out["liquid_btc_usd"] = btc_f * price
    return out


def refresh_coinbase_price_snapshot(
    *,
    path: Optional[Path] = None,
    timeout: float = 20.0,
) -> Dict[str, Any]:
    """Fetch public spot and write. On failure, leave as_of untouched."""
    dest = path or OUT_PATH
    price, err = fetch_btc_usd_price(timeout=timeout)
    if price is None:
        return {
            "ok": False,
            "error": err or "BTC-USD spot fetch failed",
            "path": str(dest),
        }
    existing = load_json(dest)
    merged = merge_btc_usd_price(existing, price, as_of=_now())
    save_json(dest, merged)
    return {
        "ok": True,
        "path": str(dest),
        "as_of": merged["as_of"],
        "btc_usd_price": price,
        "source": merged.get("source"),
        "preserved_keys": sorted(
            k for k in merged.keys() if k not in ("btc_usd_price", "as_of", "source", "liquid_btc_usd")
        ),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Pi BTC/USD price producer (#695)")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Snapshot path (default: treasury/snapshots/coinbase_latest.json)",
    )
    parser.add_argument("--print", action="store_true", dest="print_", help="Print full snapshot")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)

    report = refresh_coinbase_price_snapshot(path=args.out, timeout=args.timeout)
    if args.print_ and report.get("ok"):
        dest = Path(report["path"])
        print(json.dumps(load_json(dest) or report, indent=2))
    else:
        print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
