"""Morpho borrow live APR poller — public GraphQL only, never invent, never scrape.

Product: Coinbase Morpho borrow — cbBTC collateral / USDC loan on Base.
Market: 0x9103c3b4e834476c9a62ea009ba2c884ee42e94e6e314a26f04d312434191836
Chain: Base 8453
API: https://api.morpho.org/graphql — marketById.state.avgBorrowApy (no auth).

Pinned field is Morpho-native ``avgBorrowApy`` (APY fraction). FCC plots it
on the Morpho borrow APR chip without converting APY↔APR (do not invent).

Soft-fail: return prior books APR when the feed misses; never write the
spectrum seed ~5%. Coinbase HTML is rejected. No LTV / repay / borrow
automation / trades.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from treasury.adapters import SNAPSHOTS_DIR, load_json, save_json
from treasury.morpho_hy_sync import MORPHO_GRAPHQL_URL, normalize_apy_fraction

# Canonical liquid cbBTC / USDC 86% LLTV market on Base (Morpho docs example;
# ~$1.3B borrowed — the Coinbase BTC-backed USDC borrow venue).
CBBTC_USDC_BASE_MARKET = (
    "0x9103c3b4e834476c9a62ea009ba2c884ee42e94e6e314a26f04d312434191836"
)
CBBTC_ADDRESS = "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf"
USDC_BASE_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BASE_CHAIN_ID = 8453
SNAPSHOT_NAME = "morpho_borrow_latest.json"
USER_AGENT = "fcc-morpho-borrow/1"
PRODUCT_NAME = "Coinbase Morpho borrow · cbBTC/USDC"
FIELD_NAME = "avgBorrowApy"

# Seed lives on the spectrum chip only — poller must never persist it.
SPECTRUM_SEED_APR = 0.05

MARKET_BORROW_QUERY = """
query MarketBorrowApy($marketId: String!, $chainId: Int!) {
  marketById(marketId: $marketId, chainId: $chainId) {
    marketId
    lltv
    loanAsset { symbol address }
    collateralAsset { symbol address }
    state {
      avgBorrowApy
      borrowApy
    }
  }
}
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot_path(path: Optional[Path] = None) -> Path:
    return path or (SNAPSHOTS_DIR / SNAPSHOT_NAME)


def parse_market_borrow_apy(
    payload: Any,
) -> Tuple[Optional[float], Optional[str], Dict[str, Any]]:
    """Extract avgBorrowApy from a Morpho GraphQL marketById payload.

    Returns (fraction or None, error or None, market_row).
    Rejects GraphQL errors, HTML, missing market, and non-numeric APR.
    Does not substitute the ~5% seed. Does not invent an APR conversion.
    """
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        stripped = payload.lstrip().lower()
        if stripped.startswith("<!doctype") or stripped.startswith("<html"):
            return None, "rejected Coinbase/HTML scrape — GraphQL JSON only", {}
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as e:
            return None, f"invalid GraphQL JSON: {e}", {}
    if not isinstance(payload, dict):
        return None, "GraphQL payload is not an object", {}
    if payload.get("errors"):
        return None, f"GraphQL errors: {payload.get('errors')!r}"[:240], {}
    data = payload.get("data")
    if not isinstance(data, dict):
        return None, "GraphQL data missing", {}
    market = data.get("marketById")
    if market is None:
        return None, "marketById missing", {}
    if not isinstance(market, dict):
        return None, "marketById is not an object", {}
    state = market.get("state")
    if not isinstance(state, dict):
        return None, "market state missing", market
    apr = normalize_apy_fraction(state.get("avgBorrowApy"))
    if apr is None:
        return None, "avgBorrowApy missing or unusable", market
    return apr, None, market


def fetch_morpho_borrow_apr(
    *,
    url: str = MORPHO_GRAPHQL_URL,
    market_id: str = CBBTC_USDC_BASE_MARKET,
    chain_id: int = BASE_CHAIN_ID,
    timeout: float = 15.0,
    opener=None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """POST Morpho GraphQL marketById. Soft-fail: (None, error)."""
    body = json.dumps(
        {
            "query": MARKET_BORROW_QUERY,
            "variables": {"marketId": market_id, "chainId": chain_id},
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
    apr, err, market_row = parse_market_borrow_apy(text)
    if apr is None:
        return None, err or "avgBorrowApy missing"
    loan = market_row.get("loanAsset") if isinstance(market_row.get("loanAsset"), dict) else {}
    coll = (
        market_row.get("collateralAsset")
        if isinstance(market_row.get("collateralAsset"), dict)
        else {}
    )
    return (
        {
            "source": "morpho_graphql",
            "product": PRODUCT_NAME,
            "market_id": market_row.get("marketId") or market_id,
            "chain_id": chain_id,
            "lltv": market_row.get("lltv"),
            "loan_asset": loan.get("symbol") or "USDC",
            "collateral_asset": coll.get("symbol") or "cbBTC",
            "apr": apr,
            "apy": apr,
            "apy_est": apr,
            "avg_borrow_apy": apr,
            "variable_apr": apr,
            "field": FIELD_NAME,
            "as_of": _now(),
        },
        None,
    )


def _prior_apr(row: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(row, dict):
        return None
    for key in ("apr", "variable_apr", "apy_est", "apy", "avg_borrow_apy"):
        n = normalize_apy_fraction(row.get(key))
        if n is not None:
            return n
    return None


def fetch_morpho_borrow(
    *,
    prefer_live: bool = True,
    prior: Optional[Dict[str, Any]] = None,
    snapshot: Optional[Path] = None,
    opener=None,
) -> Dict[str, Any]:
    """Live GraphQL when asked; soft-fail to prior books APR; never the ~5% seed.

    Writes the sidecar only when a real APR is present (live or preserved prior).
    """
    path = snapshot_path(snapshot)
    if prior is None:
        prior = load_json(path)
    prior_apr = _prior_apr(prior)

    empty = {
        "source": "empty",
        "product": PRODUCT_NAME,
        "market_id": CBBTC_USDC_BASE_MARKET,
        "chain_id": BASE_CHAIN_ID,
        "apr": None,
        "apy": None,
        "apy_est": None,
        "variable_apr": None,
        "as_of": _now(),
        "field": FIELD_NAME,
    }

    if prefer_live:
        live, err = fetch_morpho_borrow_apr(opener=opener)
        if live is not None and live.get("apr") is not None:
            save_json(path, live)
            return live
        if prior_apr is not None and isinstance(prior, dict):
            kept = dict(prior)
            kept["apr"] = prior_apr
            kept["apy"] = prior_apr
            kept["apy_est"] = prior_apr
            kept["variable_apr"] = prior_apr
            kept["source"] = prior.get("source") or "prior"
            kept["live_error"] = err or "live fetch failed"
            kept["as_of"] = prior.get("as_of") or _now()
            return kept
        empty["live_error"] = err or "live fetch failed"
        return empty

    if prior_apr is not None and isinstance(prior, dict):
        out = dict(prior)
        out["apr"] = prior_apr
        out["apy"] = prior_apr
        out["apy_est"] = prior_apr
        out["variable_apr"] = prior_apr
        out.setdefault("source", "snapshot")
        return out
    return empty


def main(argv: Optional[list] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Poll Morpho GraphQL marketById avgBorrowApy (cbBTC/USDC / Base)"
    )
    p.add_argument("--offline", action="store_true", help="Read sidecar only; no GraphQL")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)
    row = fetch_morpho_borrow(prefer_live=not args.offline, snapshot=args.out)
    print(
        json.dumps(
            {
                "ok": row.get("apr") is not None,
                "source": row.get("source"),
                "apr": row.get("apr"),
                "variable_apr": row.get("variable_apr"),
                "market_id": row.get("market_id"),
                "chain_id": row.get("chain_id"),
                "field": row.get("field"),
                "product": row.get("product"),
                "as_of": row.get("as_of"),
                "live_error": row.get("live_error"),
                "invented": False,
            },
            indent=2,
        )
    )
    return 0 if row.get("apr") is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
