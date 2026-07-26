#!/usr/bin/env python3
"""Sync Braiins Pool (Slush) mining stats into treasury/snapshots/braiins_latest.json.

Auth: Pool-Auth-Token from (first match wins):
  1. env BRAIINS_POOL_TOKEN
  2. ~/.config/braiins/token  (file, single line)
  3. treasury/config.json → braiins.token  (discouraged; prefer file/env)

Create a token: Braiins Pool → Settings → Access Profiles → Allow access to web APIs
→ Generate New token. Docs: https://academy.braiins.com/en/braiins-pool/monitoring/

Rate limit: ~1 request / 5 seconds (we sleep between calls).

Usage:
  python3 treasury/braiins_sync.py
  python3 treasury/braiins_sync.py --print
  BRAIINS_POOL_TOKEN=... python3 treasury/braiins_sync.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.adapters import SNAPSHOTS_DIR, load_config, load_json, save_json  # noqa: E402

BASE = "https://pool.braiins.com"
COIN = "btc"
REQUEST_GAP_S = 5.1
TOKEN_PATHS = (
    Path.home() / ".config" / "braiins" / "token",
    Path.home() / ".config" / "braiins" / "pool_token",
)
OUT_PATH = SNAPSHOTS_DIR / "braiins_latest.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_token() -> Tuple[Optional[str], str]:
    env = (os.environ.get("BRAIINS_POOL_TOKEN") or "").strip()
    if env:
        return env, "env:BRAIINS_POOL_TOKEN"
    for p in TOKEN_PATHS:
        if p.is_file():
            try:
                tok = p.read_text(encoding="utf-8").strip().splitlines()[0].strip()
            except OSError:
                continue
            if tok:
                return tok, str(p)
    cfg = load_config()
    brai = cfg.get("braiins") or {}
    tok = (brai.get("token") or brai.get("pool_token") or "").strip()
    if tok:
        return tok, "treasury/config.json:braiins.token"
    return None, "missing"


def _get(url: str, token: str, timeout: float = 30.0) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    req = urllib.request.Request(
        url,
        headers={
            "Pool-Auth-Token": token,
            "Accept": "application/json",
            "User-Agent": "personal-workspace-treasury/braiins_sync",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        return None, f"HTTP {e.code}: {body or e.reason}"
    except urllib.error.URLError as e:
        return None, f"URL error: {e.reason}"
    except TimeoutError:
        return None, "request timed out"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"invalid JSON: {e}"
    if not isinstance(data, dict):
        return None, "response is not a JSON object"
    return data, None


def _sats_to_btc(sats: Any) -> Optional[float]:
    try:
        return int(sats) / 1e8
    except (TypeError, ValueError):
        return None


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _latest_payout(payouts: Dict[str, Any]) -> Dict[str, Any]:
    """Pick most recent confirmed/queued payout from onchain + lightning lists."""
    rows: List[Dict[str, Any]] = []
    for kind in ("onchain", "lightning"):
        for row in payouts.get(kind) or []:
            if not isinstance(row, dict):
                continue
            r = dict(row)
            r["_kind"] = kind
            rows.append(r)
    if not rows:
        return {}
    rows.sort(key=lambda r: int(r.get("resolved_at_ts") or r.get("requested_at_ts") or 0), reverse=True)
    best = rows[0]
    btc = _sats_to_btc(best.get("amount_sats"))
    ts = best.get("resolved_at_ts") or best.get("requested_at_ts")
    at = None
    if ts:
        try:
            at = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            at = str(ts)
    dest = best.get("destination") or ""
    # Redact middle of address for snapshot hygiene
    if isinstance(dest, str) and len(dest) > 16 and not dest.startswith("ln"):
        dest = dest[:8] + "…" + dest[-6:]
    return {
        "kind": best.get("_kind"),
        "status": best.get("status"),
        "amount_btc": btc,
        "amount_sats": best.get("amount_sats"),
        "fee_sats": best.get("fee_sats"),
        "destination_redacted": dest,
        "tx_id": best.get("tx_id"),
        "at": at,
        "trigger_type": best.get("trigger_type"),
    }


def fetch_snapshot(token: str, *, coin: str = COIN, sleep_s: float = REQUEST_GAP_S) -> Dict[str, Any]:
    """Pull profile + workers + recent payouts (with rate-limit gaps)."""
    out: Dict[str, Any] = {
        "ok": False,
        "as_of": _now(),
        "source": "braiins_pool_api",
        "coin": coin,
    }
    endpoints = {
        "profile": f"{BASE}/accounts/profile/json/{coin}/",
        "workers": f"{BASE}/accounts/workers/json/{coin}",
        "payouts": f"{BASE}/accounts/payouts/json/{coin}",
    }
    raw: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    first = True
    for name, url in endpoints.items():
        if not first:
            time.sleep(sleep_s)
        first = False
        data, err = _get(url, token)
        if err:
            errors[name] = err
        else:
            raw[name] = data

    if "profile" not in raw and errors:
        out["error"] = "; ".join(f"{k}: {v}" for k, v in errors.items())
        out["errors"] = errors
        return out

    profile = raw.get("profile") or {}
    coin_block = profile.get(coin) or profile.get(coin.upper()) or {}
    if not isinstance(coin_block, dict):
        coin_block = {}

    workers_root = (raw.get("workers") or {}).get(coin) or {}
    workers = workers_root.get("workers") if isinstance(workers_root, dict) else {}
    if not isinstance(workers, dict):
        workers = {}
    worker_summary = []
    for wname, w in list(workers.items())[:40]:
        if not isinstance(w, dict):
            continue
        worker_summary.append(
            {
                "name": wname,
                "state": w.get("state"),
                "hash_rate_5m": w.get("hash_rate_5m"),
                "hash_rate_60m": w.get("hash_rate_60m"),
                "hash_rate_24h": w.get("hash_rate_24h"),
                "hash_rate_unit": w.get("hash_rate_unit"),
                "last_share": w.get("last_share"),
            }
        )

    payouts = raw.get("payouts") or {}
    last_pay = _latest_payout(payouts if isinstance(payouts, dict) else {})

    out.update(
        {
            "ok": True,
            "username": profile.get("username"),
            "hash_rate_unit": coin_block.get("hash_rate_unit") or "Gh/s",
            "hash_rate_5m": _f(coin_block.get("hash_rate_5m")),
            "hash_rate_60m": _f(coin_block.get("hash_rate_60m")),
            "hash_rate_24h": _f(coin_block.get("hash_rate_24h")),
            "hash_rate_yesterday": _f(coin_block.get("hash_rate_yesterday")),
            "ok_workers": coin_block.get("ok_workers"),
            "low_workers": coin_block.get("low_workers"),
            "off_workers": coin_block.get("off_workers"),
            "dis_workers": coin_block.get("dis_workers"),
            "current_balance_btc": _f(coin_block.get("current_balance")),
            "today_reward_btc": _f(coin_block.get("today_reward")),
            "estimated_reward_btc": _f(coin_block.get("estimated_reward")),
            "all_time_reward_btc": _f(coin_block.get("all_time_reward")),
            "workers": worker_summary,
            "worker_count": len(workers),
            "last_payout": last_pay or None,
            "last_payout_btc": last_pay.get("amount_btc") if last_pay else None,
            "last_payout_at": last_pay.get("at") if last_pay else None,
        }
    )
    if errors:
        out["partial_errors"] = errors
    # Keep compact raw slices for debugging (not full payout history dump)
    out["raw_keys"] = sorted(raw.keys())
    return out


def write_snapshot(snap: Dict[str, Any], path: Optional[Path] = None) -> Path:
    out = path or OUT_PATH
    save_json(out, snap)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--print", action="store_true", help="Print snapshot JSON to stdout")
    p.add_argument("--out", type=Path, default=None, help=f"Output path (default {OUT_PATH})")
    p.add_argument(
        "--no-sleep",
        action="store_true",
        help="Skip inter-request sleep (only for tests / single endpoint)",
    )
    args = p.parse_args(argv)

    token, source = resolve_token()
    if not token:
        msg = {
            "ok": False,
            "error": (
                "No Braiins Pool token. Create one at pool.braiins.com → "
                "Settings → Access Profiles → Allow access to web APIs → Generate token, then:\n"
                "  mkdir -p ~/.config/braiins && chmod 700 ~/.config/braiins\n"
                "  echo 'YOUR_TOKEN' > ~/.config/braiins/token && chmod 600 ~/.config/braiins/token\n"
                "  python3 treasury/braiins_sync.py\n"
                "Or: BRAIINS_POOL_TOKEN=... python3 treasury/braiins_sync.py"
            ),
            "token_source": source,
            "as_of": _now(),
        }
        if args.print:
            print(json.dumps(msg, indent=2))
        else:
            print(json.dumps({"ok": False, "error": msg["error"].split("\n")[0]}, indent=2), file=sys.stderr)
            print(msg["error"], file=sys.stderr)
        # Still write a stub so FCC can surface the setup hint
        write_snapshot(
            {
                "ok": False,
                "as_of": _now(),
                "source": "braiins_pool_api",
                "error": "token_missing",
                "setup_hint": msg["error"],
            },
            path=args.out,
        )
        return 2

    sleep_s = 0.0 if args.no_sleep else REQUEST_GAP_S
    snap = fetch_snapshot(token, sleep_s=sleep_s)
    snap["token_source"] = source
    path = write_snapshot(snap, path=args.out)

    if args.print:
        # Never print the token
        print(json.dumps(snap, indent=2))
    else:
        summary = {
            "ok": snap.get("ok"),
            "path": str(path),
            "username": snap.get("username"),
            "hash_rate_24h": snap.get("hash_rate_24h"),
            "hash_rate_unit": snap.get("hash_rate_unit"),
            "today_reward_btc": snap.get("today_reward_btc"),
            "last_payout_btc": snap.get("last_payout_btc"),
            "ok_workers": snap.get("ok_workers"),
            "off_workers": snap.get("off_workers"),
            "error": snap.get("error"),
            "token_source": source,
        }
        print(json.dumps(summary, indent=2))
    return 0 if snap.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
