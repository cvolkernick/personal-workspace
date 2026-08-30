"""Morpho Blue position poller — public GraphQL only, never invent, never scrape.

Reads the Coinbase Borrow smart wallet on Base (cbBTC / USDC 86% LLTV).
API: https://api.morpho.org/graphql — userByAddress.marketPositions (no auth).

Wallet address is config (same class as the Solana sleeve). The Coinbase
Loan Backup Link ``sig=`` is a write-portal credential — never stored, never
replayed. Repay / add collateral / loan protection stay app-only.

Soft-fail: keep the prior sidecar when live misses. Do not fall back to
Settings. Writes the sidecar only when a real position row is present.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from treasury.adapters import SNAPSHOTS_DIR, load_config, load_json, save_json
from treasury.morpho_borrow_sync import BASE_CHAIN_ID, CBBTC_USDC_BASE_MARKET
from treasury.morpho_hy_sync import MORPHO_GRAPHQL_URL, normalize_apy_fraction

# Coinbase Borrow SCW on Base (factory ERC-1967 → Coinbase Smart Wallet).
DEFAULT_WALLET = "0x5528C23727761a66B471859D68Ee58293E6aBfB1"
CBBTC_DECIMALS = 8
USDC_DECIMALS = 6
DEFAULT_LLTV = 0.86
SNAPSHOT_NAME = "morpho_position_latest.json"
USER_AGENT = "fcc-morpho-position/1"
PRODUCT_NAME = "Coinbase Morpho borrow position · cbBTC/USDC"

POSITION_QUERY = """
query UserMorphoPosition($address: String!, $chainId: Int!) {
  userByAddress(address: $address, chainId: $chainId) {
    address
    marketPositions {
      healthFactor
      priceVariationToLiquidationPrice
      market {
        marketId
        lltv
        loanAsset { symbol decimals }
        collateralAsset { symbol decimals }
        state { avgBorrowApy borrowApy }
      }
      state {
        collateral
        collateralUsd
        borrowAssets
        borrowAssetsUsd
        borrowShares
      }
    }
  }
}
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot_path(path: Optional[Path] = None) -> Path:
    return path or (SNAPSHOTS_DIR / SNAPSHOT_NAME)


def morpho_wallet_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = config if config is not None else load_config()
    raw = dict(cfg.get("morpho") or {})
    wallet = (raw.get("wallet") or DEFAULT_WALLET).strip()
    try:
        chain_id = int(raw.get("chain_id") or BASE_CHAIN_ID)
    except (TypeError, ValueError):
        chain_id = BASE_CHAIN_ID
    market_id = (raw.get("market_id") or CBBTC_USDC_BASE_MARKET).strip()
    return {
        "wallet": wallet,
        "chain_id": chain_id,
        "market_id": market_id,
        "notes": raw.get("notes")
        or "Coinbase Borrow SCW on Base. Read-only GraphQL. Writes app-only.",
    }


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_lltv(raw: Any, default: float = DEFAULT_LLTV) -> float:
    """Morpho GraphQL lltv is WAD (1e18) or a 0–1 fraction."""
    n = _f(raw)
    if n is None:
        return default
    if n > 1.0:
        n = n / 1e18
    if 0.0 < n <= 1.0:
        return n
    return default


def _raw_to_units(raw: Any, decimals: int) -> float:
    n = _f(raw)
    if n is None:
        return 0.0
    return n / (10 ** decimals)


def liquidation_price_btc_usd(
    principal_usdc: float,
    collateral_btc: float,
    lltv: float,
) -> Optional[float]:
    if principal_usdc <= 0 or collateral_btc <= 0 or lltv <= 0:
        return None
    return principal_usdc / (collateral_btc * lltv)


def _pick_market_position(
    positions: Any, market_id: str
) -> Optional[Dict[str, Any]]:
    if not isinstance(positions, list):
        return None
    wanted = market_id.lower()
    fallback: Optional[Dict[str, Any]] = None
    for row in positions:
        if not isinstance(row, dict):
            continue
        market = row.get("market") if isinstance(row.get("market"), dict) else {}
        mid = str(market.get("marketId") or "").lower()
        coll = market.get("collateralAsset") if isinstance(market.get("collateralAsset"), dict) else {}
        if mid == wanted:
            return row
        if str(coll.get("symbol") or "").upper() == "CBBTC" and fallback is None:
            fallback = row
    return fallback


def parse_user_position(
    payload: Any,
    *,
    wallet: str,
    market_id: str = CBBTC_USDC_BASE_MARKET,
    chain_id: int = BASE_CHAIN_ID,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Extract the cbBTC/USDC borrow position from userByAddress JSON.

    Returns (row or None, error or None). Rejects GraphQL errors and HTML.
    A user with no matching market is a real zero loan, not an error.
    """
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        stripped = payload.lstrip().lower()
        if stripped.startswith("<!doctype") or stripped.startswith("<html"):
            return None, "rejected Coinbase/HTML scrape — GraphQL JSON only"
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as e:
            return None, f"invalid GraphQL JSON: {e}"
    if not isinstance(payload, dict):
        return None, "GraphQL payload is not an object"
    if payload.get("errors"):
        return None, f"GraphQL errors: {payload.get('errors')!r}"[:240]
    data = payload.get("data")
    if not isinstance(data, dict):
        return None, "GraphQL data missing"
    user = data.get("userByAddress")
    if user is None:
        return None, "userByAddress missing"
    if not isinstance(user, dict):
        return None, "userByAddress is not an object"

    pos = _pick_market_position(user.get("marketPositions"), market_id)
    market = (pos or {}).get("market") if isinstance((pos or {}).get("market"), dict) else {}
    state = (pos or {}).get("state") if isinstance((pos or {}).get("state"), dict) else {}
    coll_asset = market.get("collateralAsset") if isinstance(market.get("collateralAsset"), dict) else {}
    loan_asset = market.get("loanAsset") if isinstance(market.get("loanAsset"), dict) else {}
    mstate = market.get("state") if isinstance(market.get("state"), dict) else {}

    try:
        coll_dec = int(coll_asset.get("decimals") or CBBTC_DECIMALS)
    except (TypeError, ValueError):
        coll_dec = CBBTC_DECIMALS
    try:
        loan_dec = int(loan_asset.get("decimals") or USDC_DECIMALS)
    except (TypeError, ValueError):
        loan_dec = USDC_DECIMALS

    collateral_btc = _raw_to_units(state.get("collateral"), coll_dec)
    principal = _raw_to_units(state.get("borrowAssets"), loan_dec)
    coll_usd = _f(state.get("collateralUsd"))
    if coll_usd is None:
        coll_usd = 0.0
    principal_usd = _f(state.get("borrowAssetsUsd"))
    if principal_usd is None:
        principal_usd = principal

    lltv = parse_lltv(market.get("lltv"))
    ltv = None
    if coll_usd > 0:
        ltv = principal_usd / coll_usd
    elif collateral_btc == 0 and principal_usd == 0:
        ltv = 0.0

    apr = normalize_apy_fraction(mstate.get("avgBorrowApy"))
    if apr is None:
        apr = normalize_apy_fraction(mstate.get("borrowApy"))

    health = _f((pos or {}).get("healthFactor"))
    drop = _f((pos or {}).get("priceVariationToLiquidationPrice"))
    liq = liquidation_price_btc_usd(principal_usd, collateral_btc, lltv)

    return (
        {
            "source": "morpho_graphql",
            "product": PRODUCT_NAME,
            "wallet": user.get("address") or wallet,
            "market_id": market.get("marketId") or market_id,
            "chain_id": chain_id,
            "lltv": lltv,
            "loan_asset": loan_asset.get("symbol") or "USDC",
            "collateral_asset": coll_asset.get("symbol") or "cbBTC",
            "loan_principal_usdc": principal_usd,
            "collateral_btc": collateral_btc,
            "collateral_btc_usd": coll_usd,
            "ltv": ltv,
            "health_factor": health,
            "price_variation_to_liquidation": drop,
            "liquidation_price_btc_usd": liq,
            "variable_apr": apr,
            "avg_borrow_apy": apr,
            "as_of": _now(),
        },
        None,
    )


def fetch_morpho_position_live(
    *,
    url: str = MORPHO_GRAPHQL_URL,
    wallet: Optional[str] = None,
    market_id: str = CBBTC_USDC_BASE_MARKET,
    chain_id: int = BASE_CHAIN_ID,
    timeout: float = 15.0,
    opener=None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """POST Morpho GraphQL userByAddress. Soft-fail: (None, error)."""
    addr = (wallet or DEFAULT_WALLET).strip()
    body = json.dumps(
        {
            "query": POSITION_QUERY,
            "variables": {"address": addr, "chainId": chain_id},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    urlopen = opener or urllib.request.urlopen
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            content_type = ""
            try:
                content_type = (resp.headers.get("Content-Type") or "").lower()
            except Exception:
                content_type = ""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, f"morpho graphql: {e}"[:240]
    if "html" in content_type:
        return None, "rejected HTML response — GraphQL JSON only"
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception as e:
        return None, f"decode failed: {e}"
    return parse_user_position(
        text, wallet=addr, market_id=market_id, chain_id=chain_id
    )


def _position_is_usable(row: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("source") in (None, "empty"):
        return False
    return any(
        row.get(k) is not None
        for k in (
            "loan_principal_usdc",
            "collateral_btc_usd",
            "ltv",
            "collateral_btc",
        )
    )


def _empty_result(
    cfg: Dict[str, Any], *, err: str, source: str = "empty"
) -> Dict[str, Any]:
    return {
        "source": source,
        "product": PRODUCT_NAME,
        "wallet": cfg.get("wallet"),
        "market_id": cfg.get("market_id") or CBBTC_USDC_BASE_MARKET,
        "chain_id": cfg.get("chain_id") or BASE_CHAIN_ID,
        "lltv": DEFAULT_LLTV,
        "loan_principal_usdc": None,
        "collateral_btc": None,
        "collateral_btc_usd": None,
        "ltv": None,
        "health_factor": None,
        "liquidation_price_btc_usd": None,
        "variable_apr": None,
        "as_of": _now(),
        "live_error": err,
    }


def fetch_morpho_position(
    *,
    prefer_live: bool = True,
    prior: Optional[Dict[str, Any]] = None,
    snapshot: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
    opener=None,
) -> Dict[str, Any]:
    """Live GraphQL when asked; soft-fail to prior sidecar; never Settings."""
    cfg = morpho_wallet_config(config)
    path = snapshot_path(snapshot)
    if prior is None:
        prior = load_json(path)

    empty = _empty_result(cfg, err="live fetch failed")

    if prefer_live:
        live, err = fetch_morpho_position_live(
            wallet=cfg["wallet"],
            market_id=cfg["market_id"],
            chain_id=cfg["chain_id"],
            opener=opener,
        )
        if live is not None and _position_is_usable(live):
            save_json(path, live)
            return live
        if _position_is_usable(prior) and isinstance(prior, dict):
            kept = dict(prior)
            kept["live_error"] = err or "live fetch failed"
            kept.setdefault("source", "prior")
            return kept
        empty["live_error"] = err or "live fetch failed"
        return empty

    if _position_is_usable(prior) and isinstance(prior, dict):
        out = dict(prior)
        out.setdefault("source", "snapshot")
        return out
    empty["live_error"] = "no morpho position snapshot"
    empty["source"] = "empty"
    return empty


def overlay_manual_with_position(
    manual: Dict[str, Any], position: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Copy live loan fields onto snapshot.coinbase_manual. Settings never win."""
    out = dict(manual or {})
    if not _position_is_usable(position):
        return out
    assert position is not None
    out["loan_principal_usdc"] = position.get("loan_principal_usdc")
    out["collateral_btc_usd"] = position.get("collateral_btc_usd")
    out["collateral_btc"] = position.get("collateral_btc")
    out["ltv"] = position.get("ltv")
    out["liquidation_price_btc_usd"] = position.get("liquidation_price_btc_usd")
    out["health_factor"] = position.get("health_factor")
    out["morpho_wallet"] = position.get("wallet")
    out["morpho_position_source"] = position.get("source")
    return out


def main(argv: Optional[list] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Poll Morpho GraphQL userByAddress for the Coinbase Borrow SCW"
    )
    p.add_argument("--offline", action="store_true", help="Read sidecar only; no GraphQL")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)
    row = fetch_morpho_position(prefer_live=not args.offline, snapshot=args.out)
    print(
        json.dumps(
            {
                "ok": _position_is_usable(row),
                "source": row.get("source"),
                "wallet": row.get("wallet"),
                "loan_principal_usdc": row.get("loan_principal_usdc"),
                "collateral_btc": row.get("collateral_btc"),
                "collateral_btc_usd": row.get("collateral_btc_usd"),
                "ltv": row.get("ltv"),
                "liquidation_price_btc_usd": row.get("liquidation_price_btc_usd"),
                "health_factor": row.get("health_factor"),
                "variable_apr": row.get("variable_apr"),
                "market_id": row.get("market_id"),
                "as_of": row.get("as_of"),
                "live_error": row.get("live_error"),
                "invented": False,
            },
            indent=2,
        )
    )
    return 0 if _position_is_usable(row) else 1


if __name__ == "__main__":
    raise SystemExit(main())
