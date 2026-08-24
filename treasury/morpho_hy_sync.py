"""Morpho HY vault APY poller — public GraphQL only, never invent, never scrape.

Product: Steakhouse High Yield USDC Edition on Coinbase (Base).
Vault: 0xbeeff2490FEffa212faC2f6553682C219E6a8845
API: https://api.morpho.org/graphql — vaultV2ByAddress.avgNetApy (no auth).

GraphQL avgNetApy is the **vault** rate (``vault_apy``) — reference only.
It is not the Coinbase One / in-app product rate. This poller never writes
``product_apy`` and never invents a One %. Soft-fail keeps prior vault
reference; never writes the spectrum seed 7%. Coinbase HTML is rejected.
No Morpho LTV / mint / trade.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from treasury.adapters import SNAPSHOTS_DIR, load_json, save_json

MORPHO_GRAPHQL_URL = "https://api.morpho.org/graphql"
STEAKHOUSE_HY_USDC_VAULT = "0xbeeff2490FEffa212faC2f6553682C219E6a8845"
BASE_CHAIN_ID = 8453
SNAPSHOT_NAME = "morpho_hy_latest.json"
USER_AGENT = "fcc-morpho-hy/1"

# Seed lives on the spectrum chip only — poller must never persist it.
SPECTRUM_SEED_APY = 0.07

VAULT_NE_PRODUCT_NOTE = (
    "vault GraphQL avgNetApy is vault reference only; ≠ Coinbase One product rate"
)

VAULT_V2_QUERY = """
query VaultV2Apy($address: String!, $chainId: Int!) {
  vaultV2ByAddress(address: $address, chainId: $chainId) {
    address
    name
    avgNetApy
  }
}
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot_path(path: Optional[Path] = None) -> Path:
    return path or (SNAPSHOTS_DIR / SNAPSHOT_NAME)


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_apy_fraction(value: Any) -> Optional[float]:
    """Store books APY as a 0–1 fraction. Reject missing / junk. Never invent.

    GraphQL avgNetApy is already a fraction (~0.029). Values in (1, 100]
    are treated as percent. Negatives and >100 are rejected.
    """
    n = _as_float(value)
    if n is None:
        return None
    if n < 0:
        return None
    if n > 100.0:
        return None
    if n > 1.0:
        n = n / 100.0
    return n


def parse_vault_v2_apy(payload: Any) -> Tuple[Optional[float], Optional[str], Dict[str, Any]]:
    """Extract avgNetApy from a Morpho GraphQL vaultV2ByAddress payload.

    Returns (fraction or None, error or None, vault_row).
    Rejects GraphQL errors, HTML, missing vault, and non-numeric APY.
    Does not substitute the 7% seed.
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
    vault = data.get("vaultV2ByAddress")
    if vault is None:
        return None, "vaultV2ByAddress missing", {}
    if not isinstance(vault, dict):
        return None, "vaultV2ByAddress is not an object", {}
    apy = normalize_apy_fraction(vault.get("avgNetApy"))
    if apy is None:
        return None, "avgNetApy missing or unusable", vault
    return apy, None, vault


def fetch_morpho_hy_apy(
    *,
    url: str = MORPHO_GRAPHQL_URL,
    vault: str = STEAKHOUSE_HY_USDC_VAULT,
    chain_id: int = BASE_CHAIN_ID,
    timeout: float = 15.0,
    opener=None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """POST Morpho GraphQL vaultV2ByAddress. Soft-fail: (None, error)."""
    body = json.dumps(
        {
            "query": VAULT_V2_QUERY,
            "variables": {"address": vault, "chainId": chain_id},
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
    apy, err, vault_row = parse_vault_v2_apy(text)
    if apy is None:
        return None, err or "avgNetApy missing"
    return (
        {
            "source": "morpho_graphql",
            "product": vault_row.get("name") or "Steakhouse High Yield USDC Edition",
            "vault": vault_row.get("address") or vault,
            "chain_id": chain_id,
            "vault_apy": apy,
            "apy": apy,
            "apy_est": apy,
            "avg_net_apy": apy,
            "product_apy": None,
            "field": "avgNetApy",
            "role": "vault_reference",
            "note": VAULT_NE_PRODUCT_NOTE,
            "as_of": _now(),
        },
        None,
    )


def _prior_apy(row: Optional[Dict[str, Any]]) -> Optional[float]:
    """Prior vault reference only. Never treat product_apy as vault APY."""
    if not isinstance(row, dict):
        return None
    for key in ("vault_apy", "apy_est", "apy", "avg_net_apy"):
        n = normalize_apy_fraction(row.get(key))
        if n is not None:
            return n
    return None


def _stamp_vault_fields(row: Dict[str, Any], apy: Optional[float]) -> Dict[str, Any]:
    """Write vault aliases. Never invent product_apy from GraphQL."""
    row["vault_apy"] = apy
    row["apy"] = apy
    row["apy_est"] = apy
    row["avg_net_apy"] = apy
    row["role"] = "vault_reference"
    row.setdefault("note", VAULT_NE_PRODUCT_NOTE)
    # This poller is vault-only. Never invent or preserve a GraphQL product_apy.
    row["product_apy"] = None
    return row


def fetch_morpho_hy(
    *,
    prefer_live: bool = True,
    prior: Optional[Dict[str, Any]] = None,
    snapshot: Optional[Path] = None,
    opener=None,
) -> Dict[str, Any]:
    """Live GraphQL when asked; soft-fail to prior books APY; never the 7% seed.

    Writes the sidecar only when a real APY is present (live or preserved prior).
    """
    path = snapshot_path(snapshot)
    if prior is None:
        prior = load_json(path)
    prior_apy = _prior_apy(prior)

    empty = _stamp_vault_fields(
        {
            "source": "empty",
            "product": "Steakhouse High Yield USDC Edition",
            "vault": STEAKHOUSE_HY_USDC_VAULT,
            "chain_id": BASE_CHAIN_ID,
            "as_of": _now(),
            "field": "avgNetApy",
        },
        None,
    )

    if prefer_live:
        live, err = fetch_morpho_hy_apy(opener=opener)
        if live is not None and (
            live.get("vault_apy") is not None or live.get("apy") is not None
        ):
            save_json(path, live)
            return live
        if prior_apy is not None and isinstance(prior, dict):
            kept = _stamp_vault_fields(dict(prior), prior_apy)
            kept["source"] = prior.get("source") or "prior"
            kept["live_error"] = err or "live fetch failed"
            kept["as_of"] = prior.get("as_of") or _now()
            return kept
        empty["live_error"] = err or "live fetch failed"
        return empty

    if prior_apy is not None and isinstance(prior, dict):
        out = _stamp_vault_fields(dict(prior), prior_apy)
        out.setdefault("source", "snapshot")
        return out
    return empty


def main(argv: Optional[list] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Poll Morpho GraphQL vaultV2 avgNetApy as vault_apy reference (Steakhouse HY USDC / Base)"
    )
    p.add_argument("--offline", action="store_true", help="Read sidecar only; no GraphQL")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)
    row = fetch_morpho_hy(prefer_live=not args.offline, snapshot=args.out)
    print(
        json.dumps(
            {
                "ok": row.get("vault_apy") is not None or row.get("apy") is not None,
                "source": row.get("source"),
                "vault_apy": row.get("vault_apy"),
                "apy": row.get("apy"),
                "apy_est": row.get("apy_est"),
                "product_apy": row.get("product_apy"),
                "vault": row.get("vault"),
                "product": row.get("product"),
                "role": row.get("role"),
                "as_of": row.get("as_of"),
                "live_error": row.get("live_error"),
                "invented": False,
            },
            indent=2,
        )
    )
    return 0 if row.get("vault_apy") is not None or row.get("apy") is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
