"""Read-only Solana book: public RPC + Jupiter prices, whitelist only.

Never requests a private key. JR-strcUSX is DC-credit parlay — not HY / LTV
defense and not working USDC.
"""

from __future__ import annotations

import json
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
JR_STRCUSX_MINT = "BQ6LPc68knpko292UsMLbQYfaHhWD7S84sA98632hrzX"
DEFAULT_WALLET = "CzuRxF4H7qtcCbP37zcLu9DTgcHySmi8NcTsrS6W7bDm"
DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
DEFAULT_PRICE_URL = "https://lite-api.jup.ag/price/v3"
SNAPSHOT_NAME = "solana_latest.json"

DEFAULT_WHITELIST: List[Dict[str, str]] = [
    {"symbol": "SOL", "mint": WSOL_MINT, "role": "gas"},
    {"symbol": "USDC", "mint": USDC_MINT, "role": "onchain_stable"},
    {"symbol": "JR-strcUSX", "mint": JR_STRCUSX_MINT, "role": "dc_credit_parlay"},
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
        or "Public RPC only. JR-strcUSX is DC-credit parlay, never HY/LTV defense.",
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
        "jr_strcusx": 0.0,
        "jr_strcusx_usd_price": None,
        "jr_strcusx_usd": 0.0,
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
    jr_amt = 0.0
    jr_px = prices.get(JR_STRCUSX_MINT)
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
            if mint == JR_STRCUSX_MINT:
                jr_amt = amt
                jr_px = px
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
        "jr_strcusx": jr_amt,
        "jr_strcusx_usd_price": jr_px,
        "jr_strcusx_usd": (jr_amt * jr_px) if jr_px is not None else 0.0,
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


def overlay_solana_snapshot(
    treasury: Dict[str, Any],
    *,
    snapshot_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """If snapshot.solana is missing/empty, fill from solana_latest.json.

    Refresh on a host whose build_snapshot does not know Solana used to rewrite
    treasury_latest.json with snapshot.solana = {} and blank the SOL chip.
    Never overwrites a present as_of block.
    """
    if not isinstance(treasury, dict):
        return treasury
    snap = treasury.get("snapshot")
    if not isinstance(snap, dict):
        snap = {}
        treasury["snapshot"] = snap
    sol = snap.get("solana")
    missing = not isinstance(sol, dict) or not sol or not sol.get("as_of")
    if not missing:
        return treasury
    file_data = load_json(snapshot_path or (SNAPSHOTS_DIR / SNAPSHOT_NAME))
    if file_data and file_data.get("as_of"):
        snap["solana"] = file_data
        meta = snap.get("meta") if isinstance(snap.get("meta"), dict) else {}
        snap["meta"] = meta
        meta["solana_source"] = file_data.get("source")
        meta["solana_sidecar"] = True
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
            write_solana_snapshot(live, snap)
            return live
    file_data = load_json(snap)
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
    args = p.parse_args(argv)
    book = fetch_solana(prefer_live=not args.offline)
    path = write_solana_snapshot(book, args.out)
    print(
        json.dumps(
            {
                "ok": book.get("source") not in (None, "empty"),
                "source": book.get("source"),
                "wallet": book.get("wallet"),
                "book_usd": book.get("book_usd"),
                "sol": book.get("sol"),
                "usdc": book.get("usdc"),
                "jr_strcusx": book.get("jr_strcusx"),
                "out": str(path),
                "live_error": book.get("live_error"),
            },
            indent=2,
        )
    )
    return 0 if book.get("source") not in (None, "empty") else 1


if __name__ == "__main__":
    raise SystemExit(main())
