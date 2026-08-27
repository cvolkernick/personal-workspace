"""Solstice JR-strcUSX live epoch APY — on-chain AccountingState, never HTML.

Issue #309 left the chip at ~20% docs_target because api.solstice.finance/v1
is partner Bearer + mint/lock instructions, and attestation.solstice.finance
is HTML (scrape rejected).

Chris 2026-08-27 (#Orchestration): poll the live JR APY the site shows and
update Interest Spectrum. The page https://app.solstice.finance/strcusx does
not expose a JSON APY field. It reads STRC-USX ``AccountingState`` over
Solana RPC (15s) and computes ``juniorApy`` as:

    31_536_000 / vesting_duration_s * junior_vesting / (junior_assets - junior_vesting)

Pinned public account (same program the app uses):
  program  YStrYqRmio4eMQ3sKefRxiEwjMCsXpf4nrx7WRTQ2xw
  accounting  McUBNzVj8z4Pk76Lw1PuqFRJjfzAqskgpsg7zFZ978i
  strategy name  STRC-USX

Public ``getAccountInfo`` only. Do not use the app's Helius URL. Soft-fail
keeps a prior live quote; never persist the ~20% docs_target. No HTML scrape.
No partner key. No mint. No wallet notional.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from treasury.adapters import SNAPSHOTS_DIR, load_json, save_json
from treasury.morpho_hy_sync import normalize_apy_fraction

# Spectrum chip fallback only — populate path must never persist this.
DOCS_TARGET_APY = 0.20

PRODUCT_NAME = "JR-strcUSX"
DOCS_STRCUSX_URL = (
    "https://docs.solstice.finance/solstice-for-users/yieldvault/strcusx"
)
DOCS_YIELD_APY_URL = (
    "https://docs.solstice.finance/solstice-for-users/yieldvault/strcusx/yield-and-apy"
)
DOCS_API_URL = "https://docs.solstice.finance/solstice-for-builders/apis"
SOLSTICE_API_URL = "https://api.solstice.finance/v1/"
ATTESTATION_URL = "https://attestation.solstice.finance/"
APP_STRCUSX_URL = "https://app.solstice.finance/strcusx"

# STRC yield_strategy program (NEXT_PUBLIC_STRC_PROGRAM_ID in the app).
STRC_PROGRAM_ID = "YStrYqRmio4eMQ3sKefRxiEwjMCsXpf4nrx7WRTQ2xw"
STRATEGY_NAME = "STRC-USX"
# PDA [ACCOUNTING, "STRC-USX"] — bump 254. Public getAccountInfo works on
# api.mainnet-beta.solana.com (getProgramAccounts on that RPC is empty).
ACCOUNTING_PUBKEY = "McUBNzVj8z4Pk76Lw1PuqFRJjfzAqskgpsg7zFZ978i"
DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
SNAPSHOT_NAME = "solstice_jr_latest.json"
USER_AGENT = "fcc-solstice-jr/1"

# Same constant the app uses (365d, not 365.25).
SECONDS_PER_YEAR = 31_536_000
# Decode sanity: launch prints were ~152% (1.52). Reject likely-corrupt.
MAX_APY_FRACTION = 20.0

ACCOUNTING_DISCRIMINATOR = bytes([9, 238, 56, 53, 228, 92, 217, 40])
ACCOUNTING_MIN_LEN = 137  # through vesting_end_time

APY_FIELD = "jr_strcusx_apy"
APY_ALIASES = ("jr_strcusx_apy", "solstice_apy", "strcusx_apy")
FIELD_NAME = "juniorApy"

SOURCE_BLOCKER = (
    "no live STRC-USX AccountingState APY. "
    f"docs target ~20% at {DOCS_YIELD_APY_URL} (not a live print). "
    f"app {APP_STRCUSX_URL} computes juniorApy on-chain (HTML scrape rejected). "
    f"REST {SOLSTICE_API_URL} is partner Bearer + instruction endpoints "
    f"only ({DOCS_API_URL})."
)

# Explicit JR APY keys only. Generic "apy" is rejected (eUSX / senior / mix).
_PARSE_PATHS: tuple[tuple[str, ...], ...] = (
    ("jr_strcusx_apy",),
    ("solstice_apy",),
    ("strcusx_apy",),
    ("jr_apy",),
    ("junior_apy",),
    ("juniorApy",),
    ("data", "jr_strcusx_apy"),
    ("data", "solstice_apy"),
    ("data", "strcusx_apy"),
    ("data", "jr_apy"),
    ("data", "junior_apy"),
    ("data", "juniorApy"),
    ("vault", "jr_strcusx_apy"),
    ("strcusx", "jr_strcusx_apy"),
    ("strcusx", "junior_apy"),
    ("strcusx", "juniorApy"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot_path(path: Optional[Path] = None) -> Path:
    return path or (SNAPSHOTS_DIR / SNAPSHOT_NAME)


def _dig(root: Any, path: Iterable[str]) -> Any:
    cur = root
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _u64(raw: bytes, off: int) -> int:
    return int.from_bytes(raw[off : off + 8], "little")


def _u128(raw: bytes, off: int) -> int:
    return int.from_bytes(raw[off : off + 16], "little")


def epoch_apy_fraction(
    vesting_amount: int,
    assets_ex_vesting: int,
    duration_s: int,
) -> Optional[float]:
    """App formula ``j(e, t, n)``. Returns a 0+ fraction (may exceed 1.0)."""
    if vesting_amount < 0 or assets_ex_vesting <= 0 or duration_s <= 0:
        return None
    apy = (SECONDS_PER_YEAR / float(duration_s)) * (
        float(vesting_amount) / float(assets_ex_vesting)
    )
    if apy < 0 or apy > MAX_APY_FRACTION:
        return None
    return apy


def decode_accounting_state(raw: bytes) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Decode packed Anchor AccountingState. Reject HTML-sized / short buffers."""
    if not raw:
        return None, "empty accounting account"
    if raw[:8] != ACCOUNTING_DISCRIMINATOR:
        return None, "accounting discriminator mismatch"
    if len(raw) < ACCOUNTING_MIN_LEN:
        return None, f"accounting account too short ({len(raw)})"
    name = raw[8:40].split(b"\x00", 1)[0].decode("ascii", errors="replace")
    if name != STRATEGY_NAME:
        return None, f"unexpected strategy name {name!r}"
    total_assets = _u128(raw, 73)
    senior_assets = _u128(raw, 89)
    total_vesting = _u64(raw, 105)
    senior_vesting = _u64(raw, 113)
    vest_start = _u64(raw, 121)
    vest_end = _u64(raw, 129)
    junior_assets = max(total_assets - senior_assets, 0)
    junior_vesting = max(total_vesting - senior_vesting, 0)
    duration = vest_end - vest_start
    junior_ex = max(junior_assets - junior_vesting, 0)
    senior_ex = max(senior_assets - senior_vesting, 0)
    junior_apy = epoch_apy_fraction(junior_vesting, junior_ex, duration)
    senior_apy = epoch_apy_fraction(senior_vesting, senior_ex, duration)
    return (
        {
            "name": name,
            "bump": raw[40],
            "senior_shares": _u128(raw, 41),
            "junior_shares": _u128(raw, 57),
            "total_assets": total_assets,
            "senior_assets": senior_assets,
            "junior_assets": junior_assets,
            "total_vesting_assets": total_vesting,
            "senior_vesting_assets": senior_vesting,
            "junior_vesting_assets": junior_vesting,
            "vesting_start_time": vest_start,
            "vesting_end_time": vest_end,
            "vesting_duration_s": duration,
            "juniorApy": junior_apy,
            "seniorApy": senior_apy,
        },
        None,
    )


def parse_solstice_jr_apy(payload: Any) -> Tuple[Optional[float], Optional[str], Dict[str, Any]]:
    """Extract a verified JR APY field from JSON. Reject HTML. Never invent.

    Returns (fraction or None, error or None, row). Does not substitute
    the ~20% docs_target. On-chain fractions may exceed 1.0 (epoch > 100%).
    """
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        stripped = payload.lstrip().lower()
        if stripped.startswith("<!doctype") or stripped.startswith("<html"):
            return None, "rejected HTML scrape — public/docs JSON only", {}
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as e:
            return None, f"invalid JSON: {e}", {}
    if not isinstance(payload, dict):
        return None, "payload is not an object", {}

    for path in _PARSE_PATHS:
        raw = _dig(payload, path)
        if raw is None or raw == "":
            continue
        try:
            n = float(raw)
        except (TypeError, ValueError):
            continue
        if n < 0 or n > MAX_APY_FRACTION:
            continue
        # JSON helpers may still send 18.4 meaning 18.4%. On-chain juniorApy
        # is a fraction. Accept (0, 1] as fraction; (1, 100] as percent only
        # when the field is not juniorApy (app field is already a fraction).
        field = ".".join(path)
        if field.endswith("juniorApy") or field.endswith("junior_apy"):
            apy = n
        else:
            apy = normalize_apy_fraction(raw)
            if apy is None:
                continue
        return apy, None, {"field": field, "raw": raw}
    return None, "no verified JR APY field in payload", {}


def fetch_solstice_jr_apy(
    *,
    payload: Any = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Populate from a verified JSON payload. None payload is not an invent.

    Live RPC is ``fetch_solstice_jr_onchain``. Passing ``payload`` is for
    tests / a documented JSON source. HTML attestation is not parsed.
    """
    if payload is None:
        return None, SOURCE_BLOCKER
    apy, err, meta = parse_solstice_jr_apy(payload)
    if apy is None:
        return None, err or SOURCE_BLOCKER
    return (
        {
            "source": "solstice_docs_json",
            "product": PRODUCT_NAME,
            "apy": apy,
            "apy_est": apy,
            APY_FIELD: apy,
            "solstice_apy": apy,
            "strcusx_apy": apy,
            "field": meta.get("field") or APY_FIELD,
            "as_of": _now(),
        },
        None,
    )


def fetch_solstice_jr_onchain(
    *,
    rpc_url: str = DEFAULT_RPC,
    accounting: str = ACCOUNTING_PUBKEY,
    timeout: float = 15.0,
    opener=None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Public getAccountInfo → juniorApy. Soft-fail: (None, error). No HTML."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [accounting, {"encoding": "base64"}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        rpc_url,
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
        return None, f"solana rpc: {e}"[:240]
    if "html" in content_type:
        return None, "rejected HTML response — Solana JSON-RPC only"
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception as e:
        return None, f"decode failed: {e}"
    stripped = text.lstrip().lower()
    if stripped.startswith("<!doctype") or stripped.startswith("<html"):
        return None, "rejected HTML scrape — Solana JSON-RPC only"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"invalid RPC JSON: {e}"
    if not isinstance(payload, dict):
        return None, "RPC payload is not an object"
    if payload.get("error"):
        return None, f"RPC error: {payload.get('error')!r}"[:240]
    result = payload.get("result")
    value = result.get("value") if isinstance(result, dict) else None
    if value is None:
        return None, "accounting account missing"
    owner = value.get("owner") if isinstance(value, dict) else None
    if owner and owner != STRC_PROGRAM_ID:
        return None, f"unexpected owner {owner}"
    data = value.get("data") if isinstance(value, dict) else None
    b64 = data[0] if isinstance(data, (list, tuple)) and data else None
    if not isinstance(b64, str) or not b64:
        return None, "accounting data missing"
    try:
        buf = base64.b64decode(b64)
    except Exception as e:
        return None, f"accounting b64: {e}"
    row, err = decode_accounting_state(buf)
    if row is None:
        return None, err or "accounting decode failed"
    apy = row.get("juniorApy")
    if apy is None:
        return None, "juniorApy missing (empty epoch or zero junior book)"
    return (
        {
            "source": "solstice_onchain",
            "product": PRODUCT_NAME,
            "program_id": STRC_PROGRAM_ID,
            "accounting": accounting,
            "strategy": STRATEGY_NAME,
            "app": APP_STRCUSX_URL,
            "apy": float(apy),
            "apy_est": float(apy),
            APY_FIELD: float(apy),
            "solstice_apy": float(apy),
            "strcusx_apy": float(apy),
            "senior_apy": row.get("seniorApy"),
            "field": FIELD_NAME,
            "vesting_duration_s": row.get("vesting_duration_s"),
            "as_of": _now(),
        },
        None,
    )


def _prior_apy(row: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(row, dict):
        return None
    source = str(row.get("jr_strcusx_apy_source") or row.get("source") or "")
    if source in ("docs_target", "docs", "empty", "blocked"):
        return None
    for key in APY_ALIASES + ("apy", "apy_est"):
        n = row.get(key)
        if n is None or n == "":
            continue
        try:
            apy = float(n)
        except (TypeError, ValueError):
            continue
        if 0 <= apy <= MAX_APY_FRACTION:
            # Stored live quotes are fractions. A prior 0.184 must not become
            # 18.4% via normalize_apy_fraction's percent branch.
            if apy > 1.0 and key not in ("juniorApy",):
                # Sidecar may hold fraction > 1 (epoch > 100%). Keep it.
                return apy
            if apy <= 1.0:
                return apy
            return apy
    return None


def empty_solstice_jr_fields(*, err: Optional[str] = None) -> Dict[str, Any]:
    """Snapshot APY keys present, values None. Never the ~20% target."""
    return {
        APY_FIELD: None,
        "solstice_apy": None,
        "strcusx_apy": None,
        "jr_strcusx_apy_source": None,
        "jr_strcusx_apy_field": None,
        "jr_strcusx_apy_error": err or SOURCE_BLOCKER,
    }


def write_solstice_jr_fields(
    book: Dict[str, Any],
    *,
    apy: Optional[float],
    source: Optional[str] = None,
    field: Optional[str] = None,
    err: Optional[str] = None,
) -> Dict[str, Any]:
    """Write (or clear) solana-snapshot JR APY fields. Never invent 20%."""
    out = dict(book)
    if apy is None:
        out.update(empty_solstice_jr_fields(err=err))
        return out
    out[APY_FIELD] = apy
    out["solstice_apy"] = apy
    out["strcusx_apy"] = apy
    out["jr_strcusx_apy_source"] = source or "solstice_onchain"
    out["jr_strcusx_apy_field"] = field or FIELD_NAME
    if err:
        out["jr_strcusx_apy_error"] = err
    else:
        out.pop("jr_strcusx_apy_error", None)
    return out


def attach_solstice_jr_apy(
    book: Dict[str, Any],
    *,
    prior: Optional[Dict[str, Any]] = None,
    payload: Any = None,
    prefer_live: bool = True,
    opener=None,
    rpc_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Attach live JR APY from JSON payload or on-chain; else prior; else None.

    Soft-fail never writes DOCS_TARGET_APY. Wallet balances are untouched.
    """
    if not isinstance(book, dict):
        book = {}
    live: Optional[Dict[str, Any]] = None
    fetch_err: Optional[str] = None
    if payload is not None:
        live, fetch_err = fetch_solstice_jr_apy(payload=payload)
    elif prefer_live:
        live, fetch_err = fetch_solstice_jr_onchain(
            rpc_url=rpc_url or DEFAULT_RPC, opener=opener
        )
    else:
        fetch_err = SOURCE_BLOCKER
    if live is not None and live.get(APY_FIELD) is not None:
        return write_solstice_jr_fields(
            book,
            apy=float(live[APY_FIELD]),
            source=str(live.get("source") or "solstice_onchain"),
            field=str(live.get("field") or FIELD_NAME),
        )
    prior_apy = _prior_apy(prior)
    if prior_apy is not None:
        return write_solstice_jr_fields(
            book,
            apy=prior_apy,
            source=str((prior or {}).get("jr_strcusx_apy_source") or (prior or {}).get("source") or "prior"),
            field=str((prior or {}).get("jr_strcusx_apy_field") or FIELD_NAME),
            err=fetch_err or SOURCE_BLOCKER,
        )
    return write_solstice_jr_fields(book, apy=None, err=fetch_err or SOURCE_BLOCKER)


def fetch_solstice_jr(
    *,
    prefer_live: bool = True,
    prior: Optional[Dict[str, Any]] = None,
    snapshot: Optional[Path] = None,
    opener=None,
    rpc_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Live on-chain when asked; soft-fail to prior; never the ~20% target.

    Writes the sidecar only when a real APY is present (live or preserved prior).
    """
    path = snapshot_path(snapshot)
    if prior is None:
        prior = load_json(path)
    prior_apy = _prior_apy(prior)

    empty = {
        "source": "empty",
        "product": PRODUCT_NAME,
        "program_id": STRC_PROGRAM_ID,
        "accounting": ACCOUNTING_PUBKEY,
        "strategy": STRATEGY_NAME,
        "app": APP_STRCUSX_URL,
        "apy": None,
        "apy_est": None,
        APY_FIELD: None,
        "solstice_apy": None,
        "strcusx_apy": None,
        "as_of": _now(),
        "field": FIELD_NAME,
    }

    if prefer_live:
        live, err = fetch_solstice_jr_onchain(
            rpc_url=rpc_url or DEFAULT_RPC, opener=opener
        )
        if live is not None and live.get("apy") is not None:
            save_json(path, live)
            return live
        if prior_apy is not None and isinstance(prior, dict):
            kept = dict(prior)
            kept["apy"] = prior_apy
            kept["apy_est"] = prior_apy
            kept[APY_FIELD] = prior_apy
            kept["solstice_apy"] = prior_apy
            kept["strcusx_apy"] = prior_apy
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
        out[APY_FIELD] = prior_apy
        out["solstice_apy"] = prior_apy
        out["strcusx_apy"] = prior_apy
        out.setdefault("source", "snapshot")
        return out
    return empty


def main(argv: Optional[list] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Poll STRC-USX AccountingState juniorApy (app.solstice.finance/strcusx)"
    )
    p.add_argument("--offline", action="store_true", help="Read sidecar only; no RPC")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)
    row = fetch_solstice_jr(prefer_live=not args.offline, snapshot=args.out)
    print(
        json.dumps(
            {
                "ok": row.get("apy") is not None,
                "source": row.get("source"),
                "apy": row.get("apy"),
                "jr_strcusx_apy": row.get(APY_FIELD),
                "field": row.get("field"),
                "product": row.get("product"),
                "accounting": row.get("accounting"),
                "app": row.get("app"),
                "as_of": row.get("as_of"),
                "live_error": row.get("live_error"),
            },
            indent=2,
        )
    )
    return 0 if row.get("apy") is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
