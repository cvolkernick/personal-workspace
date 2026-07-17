"""Live + file snapshot adapters for Coinbase liquid balances and Robinhood portfolio/BP."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

TREASURY_DIR = Path(__file__).resolve().parent
SNAPSHOTS_DIR = TREASURY_DIR / "snapshots"
CONFIG_PATH = TREASURY_DIR / "config.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or CONFIG_PATH
    data = load_json(p)
    if not data:
        return {
            "policy": {},
            "coinbase_manual": {},
            "robinhood": {"account_number": ""},
        }
    return data


def _parse_coinbase_balance_payload(payload: Dict[str, Any]) -> Dict[str, float]:
    """Extract liquid USDC/USD/BTC available from coinbase balance JSON."""
    totals = {"USDC": 0.0, "USD": 0.0, "BTC": 0.0}
    accounts = payload.get("accounts") or []
    for a in accounts:
        cur = (a.get("currency") or "").upper()
        if cur not in totals:
            continue
        try:
            totals[cur] += float((a.get("available_balance") or {}).get("value") or 0)
        except (TypeError, ValueError):
            continue
    return totals


def fetch_coinbase_liquid_live(
    *,
    timeout: float = 30.0,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Run `coinbase balance --paginate` and extract liquid USDC/BTC.

    Returns (result, error). result has liquid_usdc, liquid_btc, raw_currencies, source.
    """
    try:
        proc = subprocess.run(
            ["coinbase", "balance", "--paginate"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return None, "coinbase CLI not found"
    except subprocess.TimeoutExpired:
        return None, "coinbase balance timed out"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "coinbase balance failed").strip()
        return None, err[:500]

    # --paginate may emit multiple JSON objects; merge accounts
    text = proc.stdout.strip()
    if not text:
        return None, "empty coinbase balance output"

    accounts = []
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError as e:
            return None, f"invalid JSON from coinbase: {e}"
        if isinstance(obj, dict) and "accounts" in obj:
            accounts.extend(obj.get("accounts") or [])
        idx = end

    merged = {"accounts": accounts}
    totals = _parse_coinbase_balance_payload(merged)
    result = {
        "source": "live",
        "as_of": _now(),
        "liquid_usdc": totals["USDC"] + totals["USD"],
        "liquid_btc": totals["BTC"],
        "by_currency": totals,
        "account_count": len(accounts),
    }
    return result, None


def fetch_coinbase_liquid(
    *,
    prefer_live: bool = True,
    snapshot_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Live Coinbase liquid read with file fallback."""
    snap = snapshot_path or (SNAPSHOTS_DIR / "coinbase_latest.json")
    err = None
    if prefer_live:
        live, err = fetch_coinbase_liquid_live()
        if live is not None:
            save_json(snap, live)
            return live
    file_data = load_json(snap)
    if file_data:
        out = dict(file_data)
        out["source"] = out.get("source") or "snapshot"
        if err:
            out["live_error"] = err
        return out
    return {
        "source": "empty",
        "as_of": _now(),
        "liquid_usdc": 0.0,
        "liquid_btc": 0.0,
        "live_error": err or "no snapshot",
    }


def fetch_robinhood_from_file(snapshot_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    snap = snapshot_path or (SNAPSHOTS_DIR / "robinhood_latest.json")
    return load_json(snap)


def normalize_robinhood_payload(data: Dict[str, Any], *, source: str = "snapshot") -> Dict[str, Any]:
    """Normalize MCP/API portfolio payload into treasury fields."""
    # Accept either flat treasury shape or raw MCP data envelope
    body: Dict[str, Any] = data
    inner = data.get("data")
    if isinstance(inner, dict) and (
        "total_value" in inner or "buying_power" in inner or "equity_value" in inner
    ):
        body = inner

    bp_obj = body.get("buying_power")
    if isinstance(bp_obj, dict):
        bp = float(bp_obj.get("buying_power") or 0)
        unlev = bp_obj.get("unleveraged_buying_power")
        unlev = None if unlev is None else float(unlev)
    else:
        bp = float(bp_obj or body.get("buying_power_value") or 0)
        unlev = body.get("unleveraged_buying_power")
        unlev = None if unlev is None else float(unlev)

    return {
        "source": source,
        "as_of": data.get("as_of") or body.get("as_of") or _now(),
        "total_value": float(body.get("total_value") or 0),
        "equity_value": float(body.get("equity_value") or 0),
        "cash": float(body.get("cash") or 0),
        "buying_power": bp,
        "unleveraged_buying_power": unlev,
        "margin_use": body.get("margin_use"),
        "currency": body.get("currency") or "USD",
        "account_number_last4": data.get("account_number_last4"),
    }


def write_robinhood_snapshot(payload: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Persist a Robinhood portfolio payload for dashboard/adapters."""
    snap = path or (SNAPSHOTS_DIR / "robinhood_latest.json")
    if "buying_power" in payload and "total_value" in payload and "source" in payload:
        save_json(snap, payload)
        return snap
    norm = normalize_robinhood_payload(payload, source=payload.get("source") or "live")
    save_json(snap, norm)
    return snap


def fetch_robinhood(
    *,
    prefer_live_file: bool = True,
    snapshot_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Read Robinhood portfolio/BP from snapshot (written by agent/MCP or tests).

    Live MCP is session-bound; CLI automation uses the last written snapshot.
    """
    snap = snapshot_path or (SNAPSHOTS_DIR / "robinhood_latest.json")
    file_data = load_json(snap) if prefer_live_file or True else None
    if file_data:
        if "buying_power" in file_data and not isinstance(file_data.get("buying_power"), dict):
            out = dict(file_data)
            out.setdefault("source", "snapshot")
            return out
        return normalize_robinhood_payload(file_data, source=file_data.get("source") or "snapshot")
    return {
        "source": "empty",
        "as_of": _now(),
        "total_value": 0.0,
        "equity_value": 0.0,
        "cash": 0.0,
        "buying_power": 0.0,
        "unleveraged_buying_power": None,
        "margin_use": None,
        "live_error": "no robinhood snapshot — write via agent MCP get_portfolio",
    }


def build_snapshot(
    *,
    config: Optional[Dict[str, Any]] = None,
    prefer_live_coinbase: bool = True,
) -> Dict[str, Any]:
    """Merge live/file venue reads with human-editable manual fields."""
    cfg = config if config is not None else load_config()
    cb = fetch_coinbase_liquid(prefer_live=prefer_live_coinbase)
    rh = fetch_robinhood()
    manual = dict(cfg.get("coinbase_manual") or {})
    return {
        "as_of": _now(),
        "coinbase": cb,
        "coinbase_manual": manual,
        "robinhood": rh,
        "policy_overrides": cfg.get("policy") or {},
        "meta": {
            "config_path": str(CONFIG_PATH),
            "coinbase_source": cb.get("source"),
            "robinhood_source": rh.get("source"),
        },
    }
