"""Read-only Solana book: public RPC + Jupiter prices, whitelist only.

Never requests a private key. Whitelist SOL + USDC. NFT/meme dust ignored.
JR-strcUSX retired 2026-10-10 (Solstice wound down).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from treasury.adapters import SNAPSHOTS_DIR, load_config, load_json, save_json

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
DEFAULT_WALLET = "CzuRxF4H7qtcCbP37zcLu9DTgcHySmi8NcTsrS6W7bDm"
DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
DEFAULT_PRICE_URL = "https://lite-api.jup.ag/price/v3"
SNAPSHOT_NAME = "solana_latest.json"
# Prism-only copy. Mac -> Pi push (rh_snapshot_sync, even pre-#1147 Mac code)
# only ever copies ``solana_latest.json``, so a pushed file can never land
# here. FCC overlay and offline merges prefer this copy (see
# ``prism_copy_wins``) so a stale Mac push cannot flicker the Solana block.
PRISM_SNAPSHOT_NAME = "solana_latest.prism.json"
PRISM_HOST_TAG = "prism"
# Prism copy keeps winning over a newer pushed/merged block while it is this
# fresh (FCC stale window). Past it, a newer non-prism copy may take over.
PRISM_FRESH_HOURS = 6.0

DEFAULT_WHITELIST: List[Dict[str, str]] = [
    {"symbol": "SOL", "mint": WSOL_MINT, "role": "gas"},
    {"symbol": "USDC", "mint": USDC_MINT, "role": "onchain_stable"},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or x == "":
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def solana_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = config if config is not None else load_config()
    raw = dict(cfg.get("solana") or {})
    whitelist = raw.get("whitelist") or DEFAULT_WHITELIST
    if not isinstance(whitelist, list) or not whitelist:
        whitelist = list(DEFAULT_WHITELIST)
    return {
        "wallet": (raw.get("wallet") or DEFAULT_WALLET).strip(),
        "rpc_url": (raw.get("rpc_url") or DEFAULT_RPC).rstrip("/"),
        "price_url": (raw.get("price_url") or DEFAULT_PRICE_URL).rstrip("/"),
        "whitelist": whitelist,
        "counts_toward_hy": bool(raw.get("counts_toward_hy", False)),
        "counts_toward_ltv_defense": bool(raw.get("counts_toward_ltv_defense", False)),
        "counts_toward_working_usdc": bool(raw.get("counts_toward_working_usdc", False)),
        "notes": raw.get("notes")
        or "Public RPC only. Whitelist SOL + USDC. NFT/meme dust ignored.",
    }


def _rpc(
    url: str,
    method: str,
    params: List[Any],
    *,
    timeout: float = 20.0,
) -> Dict[str, Any]:
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "fcc-solana/1"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"rpc {method}: non-object response")
    if data.get("error"):
        raise RuntimeError(f"rpc {method}: {data['error']}")
    return data


def fetch_jupiter_prices(
    mints: List[str],
    *,
    price_url: str = DEFAULT_PRICE_URL,
    timeout: float = 15.0,
) -> Tuple[Dict[str, float], Optional[str]]:
    ids = [m for m in mints if m]
    if not ids:
        return {}, None
    q = urllib.parse.urlencode({"ids": ",".join(ids)})
    req = urllib.request.Request(
        f"{price_url}?{q}",
        headers={"User-Agent": "fcc-solana/1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        return {}, str(e)[:240]
    out: Dict[str, float] = {}
    if not isinstance(body, dict):
        return {}, "jupiter: unexpected payload"
    for mint, row in body.items():
        if not isinstance(row, dict):
            continue
        px = row.get("usdPrice")
        if px is None:
            px = row.get("price")
        try:
            out[mint] = float(px)
        except (TypeError, ValueError):
            continue
    return out, None


def parse_token_accounts(rpc_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten jsonParsed getTokenAccountsByOwner result."""
    value = ((rpc_result.get("result") or {}).get("value")) or []
    rows: List[Dict[str, Any]] = []
    if not isinstance(value, list):
        return rows
    for acc in value:
        if not isinstance(acc, dict):
            continue
        parsed = (((acc.get("account") or {}).get("data") or {}).get("parsed") or {})
        info = parsed.get("info") or {}
        ta = info.get("tokenAmount") or {}
        mint = info.get("mint") or ""
        rows.append(
            {
                "mint": mint,
                "amount": _f(ta.get("uiAmount")),
                "decimals": int(ta.get("decimals") or 0),
                "ata": acc.get("pubkey"),
            }
        )
    return rows


def _empty_result(cfg: Dict[str, Any], *, err: str, source: str = "empty") -> Dict[str, Any]:
    return {
        "source": source,
        "as_of": _now(),
        "wallet": cfg.get("wallet"),
        "rpc_url": cfg.get("rpc_url"),
        "sol": 0.0,
        "sol_usd_price": None,
        "sol_usd": 0.0,
        "usdc": 0.0,
        "book_usd": 0.0,
        "counts_toward_hy": False,
        "counts_toward_ltv_defense": False,
        "counts_toward_working_usdc": False,
        "holdings": [],
        "ignored_token_accounts": 0,
        "slot": None,
        "live_error": err,
    }


def normalize_solana_book(
    *,
    wallet: str,
    sol_lamports: int,
    token_rows: List[Dict[str, Any]],
    prices: Dict[str, float],
    whitelist: List[Dict[str, str]],
    slot: Optional[int] = None,
    rpc_url: str = DEFAULT_RPC,
    source: str = "live",
    price_error: Optional[str] = None,
) -> Dict[str, Any]:
    by_mint: Dict[str, float] = {}
    for row in token_rows:
        mint = row.get("mint") or ""
        if not mint:
            continue
        by_mint[mint] = by_mint.get(mint, 0.0) + _f(row.get("amount"))

    sol_amt = _f(sol_lamports) / 1_000_000_000.0
    sol_px = prices.get(WSOL_MINT)
    holdings: List[Dict[str, Any]] = []
    usdc = 0.0
    matched = 0

    for spec in whitelist:
        symbol = spec.get("symbol") or "?"
        mint = spec.get("mint") or ""
        role = spec.get("role") or "other"
        if symbol.upper() == "SOL" or mint == WSOL_MINT:
            amt = sol_amt
            px = sol_px
        else:
            amt = by_mint.get(mint, 0.0)
            px = prices.get(mint)
            if mint == USDC_MINT:
                usdc = amt
        usd = (amt * px) if px is not None else None
        holdings.append(
            {
                "symbol": symbol,
                "mint": mint,
                "role": role,
                "amount": amt,
                "usd_price": px,
                "usd": usd,
            }
        )
        if symbol.upper() != "SOL":
            if mint in by_mint:
                matched += 1

    ignored = max(0, len(token_rows) - matched)
    book_usd = 0.0
    for h in holdings:
        if h.get("usd") is not None:
            book_usd += float(h["usd"])

    out: Dict[str, Any] = {
        "source": source,
        "as_of": _now(),
        "wallet": wallet,
        "rpc_url": rpc_url,
        "slot": slot,
        "sol": sol_amt,
        "sol_usd_price": sol_px,
        "sol_usd": (sol_amt * sol_px) if sol_px is not None else 0.0,
        "usdc": usdc,
        "book_usd": book_usd,
        "counts_toward_hy": False,
        "counts_toward_ltv_defense": False,
        "counts_toward_working_usdc": False,
        "holdings": holdings,
        "ignored_token_accounts": ignored,
        "token_account_count": len(token_rows),
        "explorer": f"https://solscan.io/account/{wallet}",
    }
    if price_error:
        out["price_error"] = price_error
    return out


def fetch_solana_live(
    *,
    config: Optional[Dict[str, Any]] = None,
    timeout: float = 20.0,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    cfg = solana_config(config)
    wallet = cfg["wallet"]
    rpc = cfg["rpc_url"]
    try:
        bal = _rpc(rpc, "getBalance", [wallet], timeout=timeout)
        slot = ((bal.get("result") or {}).get("context") or {}).get("slot")
        lamports = int((bal.get("result") or {}).get("value") or 0)
        classic = _rpc(
            rpc,
            "getTokenAccountsByOwner",
            [wallet, {"programId": TOKEN_PROGRAM}, {"encoding": "jsonParsed"}],
            timeout=timeout,
        )
        t22 = _rpc(
            rpc,
            "getTokenAccountsByOwner",
            [wallet, {"programId": TOKEN_2022_PROGRAM}, {"encoding": "jsonParsed"}],
            timeout=timeout,
        )
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError, TypeError, ValueError) as e:
        return None, str(e)[:400]

    rows = parse_token_accounts(classic) + parse_token_accounts(t22)
    mints = [WSOL_MINT]
    for spec in cfg["whitelist"]:
        mint = spec.get("mint")
        if mint and mint not in mints:
            mints.append(mint)
    prices, price_err = fetch_jupiter_prices(mints, price_url=cfg["price_url"], timeout=min(15.0, timeout))
    book = normalize_solana_book(
        wallet=wallet,
        sol_lamports=lamports,
        token_rows=rows,
        prices=prices,
        whitelist=cfg["whitelist"],
        slot=slot,
        rpc_url=rpc,
        source="live",
        price_error=price_err,
    )
    return book, None


def write_solana_snapshot(data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    out = path or (SNAPSHOTS_DIR / SNAPSHOT_NAME)
    save_json(out, data)
    return out


def _parse_as_of(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t


def is_prism_host() -> bool:
    """Prism systemd units set FCC_HOST_TAG=prism; the Mac plist sets mac."""
    return (os.environ.get("FCC_HOST_TAG") or "").strip().lower() == PRISM_HOST_TAG


def prism_copy_wins(
    prism: Optional[Dict[str, Any]],
    other: Optional[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    fresh_hours: float = PRISM_FRESH_HOURS,
) -> bool:
    """Should prism's own Solana copy replace ``other`` (pushed file or merged block)?

    - prism copy without a parseable as_of never wins.
    - other missing / no as_of -> prism wins.
    - prism as_of >= other as_of -> prism wins (drop the older Mac copy).
    - other is newer: a newer *prism-produced* block (live merge on prism)
      is kept; any other newer copy (a Mac push) loses while the prism copy is
      inside ``fresh_hours``. Only a dead prism producer lets the Mac back in.
    """
    if not isinstance(prism, dict):
        return False
    p_at = _parse_as_of(prism.get("as_of"))
    if p_at is None:
        return False
    o_at = _parse_as_of(other.get("as_of")) if isinstance(other, dict) else None
    if o_at is None:
        return True
    if p_at >= o_at:
        return True
    if str(other.get("producer_host") or "").lower() == PRISM_HOST_TAG:
        return False
    now = now or datetime.now(timezone.utc)
    age_h = max(0.0, (now - p_at).total_seconds() / 3600.0)
    return age_h <= fresh_hours


def load_preferred_solana(
    *,
    snapshot_path: Optional[Path] = None,
    prism_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """prism copy when it wins over solana_latest.json, else solana_latest.json."""
    pushed = load_json(snapshot_path or (SNAPSHOTS_DIR / SNAPSHOT_NAME))
    prism = load_json(prism_path or (SNAPSHOTS_DIR / PRISM_SNAPSHOT_NAME))
    if prism and prism_copy_wins(prism, pushed or None, now=now):
        return prism
    return pushed or prism or None


def write_prism_copy(data: Dict[str, Any], path: Optional[Path] = None) -> Optional[Path]:
    """Write the prism-only copy. Live reads only; never re-stamp a fallback."""
    if not isinstance(data, dict) or data.get("source") != "live" or not data.get("as_of"):
        return None
    out = dict(data)
    out["producer_host"] = PRISM_HOST_TAG
    p = path or (SNAPSHOTS_DIR / PRISM_SNAPSHOT_NAME)
    save_json(p, out)
    return p


def overlay_solana_snapshot(
    treasury: Dict[str, Any],
    *,
    snapshot_path: Optional[Path] = None,
    prism_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Fill or correct snapshot.solana before FCC serves / a merge writes it.

    1. Missing/empty block: fill from the preferred file (prism copy when it
       wins, else solana_latest.json). A refresh on a host whose
       build_snapshot does not know Solana used to blank the SOL chip.
    2. Present block: replaced only by prism's own copy, when
       ``prism_copy_wins`` (e.g. the Mac pushed treasury_latest.json with its
       own, older or stale-code Solana block). Otherwise never overwritten.

    An explicit ``snapshot_path`` without ``prism_path`` keeps the legacy
    single-file behavior (no prism lookup).
    """
    if not isinstance(treasury, dict):
        return treasury
    snap = treasury.get("snapshot")
    if not isinstance(snap, dict):
        snap = {}
        treasury["snapshot"] = snap
    sol = snap.get("solana")
    missing = not isinstance(sol, dict) or not sol or not sol.get("as_of")
    use_prism = snapshot_path is None or prism_path is not None
    prism = (
        load_json(prism_path or (SNAPSHOTS_DIR / PRISM_SNAPSHOT_NAME)) if use_prism else None
    )
    chosen: Optional[Dict[str, Any]] = None
    if missing:
        if use_prism:
            chosen = load_preferred_solana(
                snapshot_path=snapshot_path, prism_path=prism_path, now=now
            )
        else:
            chosen = load_json(snapshot_path)
    elif prism and prism_copy_wins(prism, sol, now=now):
        if prism.get("as_of") != sol.get("as_of") or sol.get("producer_host") != PRISM_HOST_TAG:
            chosen = prism
    if chosen and chosen.get("as_of"):
        snap["solana"] = chosen
        meta = snap.get("meta") if isinstance(snap.get("meta"), dict) else {}
        snap["meta"] = meta
        meta["solana_source"] = chosen.get("source")
        meta["solana_sidecar"] = True
        if chosen.get("producer_host") == PRISM_HOST_TAG:
            meta["solana_producer"] = PRISM_HOST_TAG
    return treasury


def fetch_solana(
    *,
    prefer_live: bool = True,
    config: Optional[Dict[str, Any]] = None,
    snapshot_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Live RPC read with file fallback. Offline / consumer uses snapshot only."""
    cfg = solana_config(config)
    snap = snapshot_path or (SNAPSHOTS_DIR / SNAPSHOT_NAME)
    err = None
    if prefer_live:
        live, err = fetch_solana_live(config=config)
        if live is not None:
            live["counts_toward_hy"] = False
            live["counts_toward_ltv_defense"] = False
            live["counts_toward_working_usdc"] = bool(cfg["counts_toward_working_usdc"])
            if snapshot_path is None and is_prism_host():
                live["producer_host"] = PRISM_HOST_TAG
                write_prism_copy(live)
            write_solana_snapshot(live, snap)
            return live
    # Default path: prism copy beats a stale Mac-pushed solana_latest.json.
    file_data = load_preferred_solana() if snapshot_path is None else load_json(snap)
    if file_data:
        out = dict(file_data)
        out["source"] = out.get("source") or "snapshot"
        if prefer_live and out.get("source") == "live":
            out["source"] = "snapshot"
        out["counts_toward_hy"] = False
        out["counts_toward_ltv_defense"] = False
        if err:
            out["live_error"] = err
        return out
    return _empty_result(cfg, err=err or "no solana snapshot — run treasury/solana_sync.py")


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Refresh Solana book snapshot (public RPC)")
    p.add_argument("--offline", action="store_true", help="Read snapshot only")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument(
        "--prism-copy",
        action="store_true",
        help=f"Also write {PRISM_SNAPSHOT_NAME} (prism producer; implied by FCC_HOST_TAG=prism)",
    )
    args = p.parse_args(argv)
    book = fetch_solana(prefer_live=not args.offline)
    path = write_solana_snapshot(book, args.out)
    prism_out = None
    if args.out is None and (args.prism_copy or is_prism_host()):
        prism_out = write_prism_copy(book)
    print(
        json.dumps(
            {
                "ok": book.get("source") not in (None, "empty"),
                "source": book.get("source"),
                "wallet": book.get("wallet"),
                "book_usd": book.get("book_usd"),
                "sol": book.get("sol"),
                "usdc": book.get("usdc"),
                "out": str(path),
                "prism_copy": str(prism_out) if prism_out else None,
                "live_error": book.get("live_error"),
            },
            indent=2,
        )
    )
    return 0 if book.get("source") not in (None, "empty") else 1


if __name__ == "__main__":
    raise SystemExit(main())
