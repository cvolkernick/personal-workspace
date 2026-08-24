"""USDG HY live APY poller — public GraphQL only, never invent, never scrape.

Product: Steakhouse USDG on Robinhood (Robinhood Chain / RH Earn).
Vault: 0xBeEff033F34C046626B8D0A041844C5d1A5409dd
API: https://api.morpho.org/graphql — vaultV2ByAddress.avgNetApy (no auth).

Soft-fail: return prior books APY when the feed misses; never write the
spectrum seed 7%. Never invent a post-Gold rate (0% or otherwise).
Robinhood HTML is rejected. No trades / mint / Earn deposit.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from treasury.adapters import SNAPSHOTS_DIR, load_json, save_json
from treasury.morpho_hy_sync import (
    MORPHO_GRAPHQL_URL,
    VAULT_V2_QUERY,
    normalize_apy_fraction,
    parse_vault_v2_apy,
)

STEAKHOUSE_USDG_VAULT = "0xBeEff033F34C046626B8D0A041844C5d1A5409dd"
ROBINHOOD_CHAIN_ID = 4663
SNAPSHOT_NAME = "usdg_hy_latest.json"
USER_AGENT = "fcc-usdg-hy/1"
PRODUCT_NAME = "Steakhouse USDG"

# Seed lives on the spectrum chip only — poller must never persist it.
# Gold-cancel caveat: do not invent a post-Gold rate either.
SPECTRUM_SEED_APY = 0.07


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot_path(path: Optional[Path] = None) -> Path:
    return path or (SNAPSHOTS_DIR / SNAPSHOT_NAME)


def fetch_usdg_hy_apy(
    *,
    url: str = MORPHO_GRAPHQL_URL,
    vault: str = STEAKHOUSE_USDG_VAULT,
    chain_id: int = ROBINHOOD_CHAIN_ID,
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
        return None, f"usdg graphql: {e}"[:240]
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
            "product": vault_row.get("name") or PRODUCT_NAME,
            "vault": vault_row.get("address") or vault,
            "chain_id": chain_id,
            "apy": apy,
            "apy_est": apy,
            "avg_net_apy": apy,
            "field": "avgNetApy",
            "as_of": _now(),
        },
        None,
    )


def _prior_apy(row: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(row, dict):
        return None
    for key in ("apy_est", "apy", "avg_net_apy"):
        n = normalize_apy_fraction(row.get(key))
        if n is not None:
            return n
    return None


def fetch_usdg_hy(
    *,
    prefer_live: bool = True,
    prior: Optional[Dict[str, Any]] = None,
    snapshot: Optional[Path] = None,
    opener=None,
) -> Dict[str, Any]:
    """Live GraphQL when asked; soft-fail to prior books APY; never the 7% seed.

    Writes the sidecar only when a real APY is present (live or preserved prior).
    Never invents a post-Gold rate.
    """
    path = snapshot_path(snapshot)
    if prior is None:
        prior = load_json(path)
    prior_apy = _prior_apy(prior)

    empty = {
        "source": "empty",
        "product": PRODUCT_NAME,
        "vault": STEAKHOUSE_USDG_VAULT,
        "chain_id": ROBINHOOD_CHAIN_ID,
        "apy": None,
        "apy_est": None,
        "as_of": _now(),
        "field": "avgNetApy",
    }

    if prefer_live:
        live, err = fetch_usdg_hy_apy(opener=opener)
        if live is not None and live.get("apy") is not None:
            save_json(path, live)
            return live
        if prior_apy is not None and isinstance(prior, dict):
            kept = dict(prior)
            kept["apy"] = prior_apy
            kept["apy_est"] = prior_apy
            kept["source"] = prior.get("source") or "prior"
            kept["live_error"] = err or "live fetch failed"
            kept["as_of"] = prior.get("as_of") or _now()
            return kept
        empty["live_error"] = err or "live fetch failed"
        return empty

    if prior_apy is not None and isinstance(prior, dict):
        out = dict(prior)
        out["apy"] = prior_apy
        out["apy_est"] = prior_apy
        out.setdefault("source", "snapshot")
        return out
    return empty


def main(argv: Optional[list] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Poll Morpho GraphQL vaultV2 avgNetApy (Steakhouse USDG / Robinhood Chain)"
    )
    p.add_argument("--offline", action="store_true", help="Read sidecar only; no GraphQL")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)
    row = fetch_usdg_hy(prefer_live=not args.offline, snapshot=args.out)
    print(
        json.dumps(
            {
                "ok": row.get("apy") is not None,
                "source": row.get("source"),
                "apy": row.get("apy"),
                "apy_est": row.get("apy_est"),
                "vault": row.get("vault"),
                "product": row.get("product"),
                "as_of": row.get("as_of"),
                "live_error": row.get("live_error"),
                "invented": False,
                "invented_post_gold": False,
            },
            indent=2,
        )
    )
    return 0 if row.get("apy") is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
