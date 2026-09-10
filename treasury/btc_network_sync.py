#!/usr/bin/env python3
"""Bitcoin network hashrate + difficulty from mempool.space (public, no auth).

Writes treasury/snapshots/btc_network_latest.json. Used by FCC Bitcoin tab charts.

Usage:
  python3 treasury/btc_network_sync.py
  python3 treasury/btc_network_sync.py --print
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.adapters import SNAPSHOTS_DIR, load_json, save_json  # noqa: E402

# mempool documents 1m/3m/6m/1y/2y/3y; omit/all returns the full series so the
# FCC 5y range is not truncated. /5y currently aliases to all-time as well.
HASHRATE_URL = "https://mempool.space/api/v1/mining/hashrate/all"
ADJUST_URL = "https://mempool.space/api/v1/difficulty-adjustment"
WINDOW = "all"
OUT_PATH = SNAPSHOTS_DIR / "btc_network_latest.json"
STALE_S = 6 * 3600
UA = "personal-workspace-treasury/btc_network_sync"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _unix_to_iso(ts: Any) -> Optional[str]:
    n = _f(ts)
    if n is None:
        return None
    # mempool estimatedRetargetDate is ms; unix seconds are 1e9-scale.
    if n > 1e12:
        n = n / 1000.0
    try:
        return datetime.fromtimestamp(n, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def format_hashrate_hs(hs: Any) -> str:
    n = _f(hs)
    if n is None:
        return "—"
    abs_n = abs(n)
    if abs_n >= 1e18:
        return f"{n / 1e18:.1f} EH/s"
    if abs_n >= 1e15:
        return f"{n / 1e15:.1f} PH/s"
    if abs_n >= 1e12:
        return f"{n / 1e12:.1f} TH/s"
    if abs_n >= 1e9:
        return f"{n / 1e9:.1f} GH/s"
    return f"{n:.0f} H/s"


def format_difficulty(d: Any) -> str:
    n = _f(d)
    if n is None:
        return "—"
    abs_n = abs(n)
    if abs_n >= 1e12:
        return f"{n / 1e12:.1f} T"
    if abs_n >= 1e9:
        return f"{n / 1e9:.1f} B"
    if abs_n >= 1e6:
        return f"{n / 1e6:.1f} M"
    return f"{n:,.0f}"


def _get_json(url: str, timeout: float = 20.0) -> Tuple[Any, Optional[str]]:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": UA},
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
    return data, None


def _series_hashrate(rows: Any) -> List[Dict[str, float]]:
    out: List[Dict[str, float]] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        t = _f(row.get("timestamp") if row.get("timestamp") is not None else row.get("t"))
        v = _f(row.get("avgHashrate") if row.get("avgHashrate") is not None else row.get("v"))
        if t is None or v is None:
            continue
        out.append({"t": int(t), "v": v})
    out.sort(key=lambda p: p["t"])
    return out


def _series_difficulty(rows: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        t = _f(row.get("time") if row.get("time") is not None else row.get("t"))
        v = _f(row.get("difficulty") if row.get("difficulty") is not None else row.get("v"))
        if t is None or v is None:
            continue
        point: Dict[str, Any] = {"t": int(t), "v": v}
        h = _i(row.get("height"))
        adj = _f(row.get("adjustment"))
        if h is not None:
            point["height"] = h
        if adj is not None:
            point["adjustment"] = adj
        out.append(point)
    out.sort(key=lambda p: p["t"])
    return out


def normalize_adjustment(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    remaining_s = _f(raw.get("remainingTime"))
    change = _f(raw.get("difficultyChange"))
    progress = _f(raw.get("progressPercent"))
    return {
        "progress_percent": progress,
        "difficulty_change_pct": change,
        "remaining_blocks": _i(raw.get("remainingBlocks")),
        "remaining_time_s": remaining_s,
        "estimated_retarget_at": _unix_to_iso(raw.get("estimatedRetargetDate")),
        "next_retarget_height": _i(raw.get("nextRetargetHeight")),
        "previous_retarget_pct": _f(raw.get("previousRetarget")),
    }


def normalize_network(
    hashrate_raw: Any,
    adjustment_raw: Any = None,
    *,
    as_of: Optional[str] = None,
    source: str = "mempool.space",
) -> Dict[str, Any]:
    if not isinstance(hashrate_raw, dict):
        return {
            "ok": False,
            "status": "error",
            "error": "hashrate payload is not an object",
            "as_of": as_of or _now_iso(),
            "source": source,
        }
    hr_series = _series_hashrate(hashrate_raw.get("hashrates"))
    diff_series = _series_difficulty(hashrate_raw.get("difficulty"))
    current_hr = _f(hashrate_raw.get("currentHashrate"))
    current_diff = _f(hashrate_raw.get("currentDifficulty"))
    if current_hr is None and hr_series:
        current_hr = hr_series[-1]["v"]
    if current_diff is None and diff_series:
        current_diff = diff_series[-1]["v"]
    if not hr_series and not diff_series and current_hr is None and current_diff is None:
        return {
            "ok": False,
            "status": "error",
            "error": "mempool payload had no hashrate or difficulty",
            "as_of": as_of or _now_iso(),
            "source": source,
        }
    payload: Dict[str, Any] = {
        "ok": True,
        "status": "live",
        "as_of": as_of or _now_iso(),
        "source": source,
        "window": WINDOW,
        "current_hashrate": current_hr,
        "current_difficulty": current_diff,
        "hashrate_label": format_hashrate_hs(current_hr),
        "difficulty_label": format_difficulty(current_diff),
        "hashrate": hr_series,
        "difficulty": diff_series,
        "adjustment": normalize_adjustment(adjustment_raw),
    }
    return payload


def fetch_btc_network(*, timeout: float = 20.0) -> Dict[str, Any]:
    hr_raw, hr_err = _get_json(HASHRATE_URL, timeout=timeout)
    if hr_err:
        return {
            "ok": False,
            "status": "error",
            "error": f"hashrate fetch failed: {hr_err}",
            "as_of": _now_iso(),
            "source": "mempool.space",
        }
    adj_raw, adj_err = _get_json(ADJUST_URL, timeout=timeout)
    if adj_err:
        adj_raw = None
    out = normalize_network(hr_raw, adj_raw)
    if adj_err and out.get("ok"):
        out["adjustment_error"] = adj_err
    return out


def write_btc_network_snapshot(data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    dest = path or OUT_PATH
    save_json(dest, data)
    return dest


def load_btc_network_snapshot(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    data = load_json(path or OUT_PATH)
    return data if isinstance(data, dict) else None


def _snapshot_age_s(data: Dict[str, Any]) -> Optional[float]:
    as_of = data.get("as_of")
    if not as_of:
        return None
    try:
        dt = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (_now() - dt).total_seconds())


def _snapshot_covers_window(snap: Optional[Dict[str, Any]]) -> bool:
    """3y snapshots cannot serve the 5y chart range — refetch even if age-fresh."""
    return bool(snap and snap.get("ok") and snap.get("window") == WINDOW)


def btc_network_payload(
    *,
    refresh_if_stale: bool = True,
    stale_s: float = STALE_S,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Serve cached snapshot; optionally refresh from mempool.space when stale/missing."""
    snap = load_btc_network_snapshot(path)
    age = _snapshot_age_s(snap) if snap else None
    fresh = bool(
        snap
        and snap.get("ok")
        and age is not None
        and age < stale_s
        and _snapshot_covers_window(snap)
    )
    if fresh or not refresh_if_stale:
        if snap:
            return snap
        return {
            "ok": False,
            "status": "missing",
            "error": "no btc_network_latest.json — run: python3 treasury/btc_network_sync.py",
        }
    live = fetch_btc_network()
    if live.get("ok"):
        write_btc_network_snapshot(live, path)
        return live
    if snap:
        snap = dict(snap)
        snap["refresh_error"] = live.get("error")
        return snap
    return live


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sync Bitcoin network hashrate + difficulty")
    parser.add_argument("--print", action="store_true", help="print JSON to stdout")
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="do not write treasury/snapshots/btc_network_latest.json",
    )
    args = parser.parse_args(argv)
    data = fetch_btc_network()
    if not args.no_write:
        write_btc_network_snapshot(data)
    if args.print:
        slim = {k: v for k, v in data.items() if k not in ("hashrate", "difficulty")}
        slim["hashrate_points"] = len(data.get("hashrate") or [])
        slim["difficulty_points"] = len(data.get("difficulty") or [])
        print(json.dumps(slim, indent=2))
    if not data.get("ok"):
        sys.stderr.write(f"[btc_network] {data.get('error') or 'sync failed'}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
