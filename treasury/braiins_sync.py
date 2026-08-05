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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
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


def _payout_rows(payouts: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for kind in ("onchain", "lightning"):
        for row in payouts.get(kind) or []:
            if not isinstance(row, dict):
                continue
            r = dict(row)
            r["_kind"] = kind
            rows.append(r)
    rows.sort(
        key=lambda r: int(r.get("resolved_at_ts") or r.get("requested_at_ts") or 0),
        reverse=True,
    )
    return rows


def _latest_payout(payouts: Dict[str, Any]) -> Dict[str, Any]:
    """Pick most recent confirmed/queued payout from onchain + lightning lists."""
    rows = _payout_rows(payouts)
    if not rows:
        return {}
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


def _daily_rewards_avg(rewards_payload: Dict[str, Any], coin: str = COIN, days: int = 14) -> Optional[float]:
    """Average total_reward over recent complete days (skip incomplete today if tiny)."""
    block = rewards_payload.get(coin) or rewards_payload.get(coin.upper()) or {}
    daily = block.get("daily_rewards") if isinstance(block, dict) else None
    if not isinstance(daily, list) or not daily:
        return None
    vals: List[float] = []
    for row in daily:
        if not isinstance(row, dict):
            continue
        v = _f(row.get("total_reward"))
        if v is None or v <= 0:
            continue
        vals.append(v)
        if len(vals) >= days:
            break
    if not vals:
        return None
    return sum(vals) / len(vals)


def _next_0900_utc_after(dt: datetime) -> datetime:
    """Braiins evaluates payout rules once daily at 09:00 UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    day = dt.astimezone(timezone.utc).date()
    candidate = datetime(day.year, day.month, day.day, 9, 0, tzinfo=timezone.utc)
    if dt <= candidate:
        return candidate
    nxt = day + timedelta(days=1)
    return datetime(nxt.year, nxt.month, nxt.day, 9, 0, tzinfo=timezone.utc)


def _infer_payout_outlook(
    payouts: Dict[str, Any],
    *,
    balance_btc: Optional[float],
    daily_reward_avg_btc: Optional[float],
    threshold_override: Optional[float] = None,
) -> Dict[str, Any]:
    """Infer next payout from history + balance.

    Braiins does not expose payout-rule settings via public API. The web UI shows
    progress toward threshold / schedule; we reconstruct from:
      - config override: treasury/config.json → braiins.payout_threshold_btc (preferred when set)
      - confirmed payout amounts (threshold ≈ median; lag after owner changes UI rule)
      - intervals between payouts
      - current balance + avg daily rewards
    Actual send runs at the next 09:00 UTC evaluation after the rule is met.

    Owner 2026-08-05: account threshold raised 0.005 → 0.01 BTC. Set via config so
    FCC ETA/progress do not stay stuck on historical ~0.005 medians until the first
    0.01 payout confirms. Pool free on-chain floor is still 0.005 (Braiins docs).
    """
    rows = _payout_rows(payouts)
    confirmed = [r for r in rows if (r.get("status") or "").lower() == "confirmed"]
    amounts = [_sats_to_btc(r.get("amount_sats")) for r in confirmed]
    amounts = [a for a in amounts if a is not None and a > 0]
    timestamps: List[int] = []
    for r in confirmed:
        ts = r.get("resolved_at_ts") or r.get("requested_at_ts")
        try:
            timestamps.append(int(ts))
        except (TypeError, ValueError):
            pass
    timestamps.sort()
    intervals_d: List[float] = []
    for a, b in zip(timestamps, timestamps[1:]):
        intervals_d.append((b - a) / 86400.0)

    # Pool free on-chain floor (Braiins docs). Account rule may be higher — use config.
    FREE_ONCHAIN = 0.005
    thr: Optional[float] = threshold_override
    if thr is None and amounts:
        med = float(median(amounts))
        # Snap common pool defaults when history clearly clusters there
        if 0.0045 <= med <= 0.0055:
            thr = FREE_ONCHAIN
        elif 0.0095 <= med <= 0.0105:
            thr = 0.01
        else:
            thr = round(med, 8)
    if thr is None:
        thr = FREE_ONCHAIN

    bal = balance_btc if balance_btc is not None else 0.0
    remaining = max(0.0, thr - bal)
    progress = min(1.0, bal / thr) if thr > 0 else None
    days_to: Optional[float] = None
    next_est: Optional[str] = None
    next_eval: Optional[str] = None
    rate = daily_reward_avg_btc
    if rate and rate > 0 and remaining > 0:
        days_to = remaining / rate
        hit = datetime.now(timezone.utc) + timedelta(days=days_to)
        fire = _next_0900_utc_after(hit)
        next_est = fire.isoformat()
        next_eval = fire.isoformat()
    elif remaining <= 0:
        # Already at/over threshold — next daily evaluation
        fire = _next_0900_utc_after(datetime.now(timezone.utc))
        days_to = max(0.0, (fire - datetime.now(timezone.utc)).total_seconds() / 86400.0)
        next_est = fire.isoformat()
        next_eval = fire.isoformat()

    interval_med = float(median(intervals_d)) if intervals_d else None
    return {
        "rule_inferred": "threshold",
        "threshold_btc": thr,
        "threshold_source": (
            "config" if threshold_override is not None
            else "payout_history_median" if amounts else "braiins_free_onchain_default"
        ),
        "balance_btc": bal,
        "remaining_btc": round(remaining, 8),
        "progress_pct": round(progress * 100, 1) if progress is not None else None,
        "daily_reward_avg_btc": round(rate, 8) if rate else None,
        "days_to_threshold_est": round(days_to, 1) if days_to is not None else None,
        "next_payout_est_at": next_est,
        "next_payout_eval_note": "Braiins evaluates payout rules daily at 09:00 UTC",
        "median_payout_interval_days": round(interval_med, 1) if interval_med is not None else None,
        "confirmed_payout_count": len(confirmed),
        "median_payout_btc": round(float(median(amounts)), 8) if amounts else None,
    }


def fetch_snapshot(token: str, *, coin: str = COIN, sleep_s: float = REQUEST_GAP_S) -> Dict[str, Any]:
    """Pull profile + workers + payouts + recent rewards (with rate-limit gaps)."""
    out: Dict[str, Any] = {
        "ok": False,
        "as_of": _now(),
        "source": "braiins_pool_api",
        "coin": coin,
    }
    today = date.today()
    from_d = (today - timedelta(days=45)).isoformat()
    to_d = today.isoformat()
    endpoints = {
        "profile": f"{BASE}/accounts/profile/json/{coin}/",
        "workers": f"{BASE}/accounts/workers/json/{coin}",
        "payouts": f"{BASE}/accounts/payouts/json/{coin}",
        "rewards": f"{BASE}/accounts/rewards/json/{coin}?from={from_d}&to={to_d}",
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
    balance = _f(coin_block.get("current_balance"))
    daily_avg = _daily_rewards_avg(raw.get("rewards") or {}, coin=coin, days=14)
    cfg = load_config()
    thr_override = None
    brai_cfg = cfg.get("braiins") or {}
    if brai_cfg.get("payout_threshold_btc") is not None:
        thr_override = _f(brai_cfg.get("payout_threshold_btc"))
    outlook = _infer_payout_outlook(
        payouts if isinstance(payouts, dict) else {},
        balance_btc=balance,
        daily_reward_avg_btc=daily_avg,
        threshold_override=thr_override,
    )

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
            "current_balance_btc": balance,
            "today_reward_btc": _f(coin_block.get("today_reward")),
            "estimated_reward_btc": _f(coin_block.get("estimated_reward")),
            "all_time_reward_btc": _f(coin_block.get("all_time_reward")),
            "workers": worker_summary,
            "worker_count": len(workers),
            "last_payout": last_pay or None,
            "last_payout_btc": last_pay.get("amount_btc") if last_pay else None,
            "last_payout_at": last_pay.get("at") if last_pay else None,
            "daily_reward_avg_btc": daily_avg,
            "payout_outlook": outlook,
            "next_payout_est_at": outlook.get("next_payout_est_at"),
            "next_payout_threshold_btc": outlook.get("threshold_btc"),
            "next_payout_progress_pct": outlook.get("progress_pct"),
            "days_to_next_payout_est": outlook.get("days_to_threshold_est"),
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
