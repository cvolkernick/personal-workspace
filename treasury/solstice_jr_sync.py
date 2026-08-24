"""Solstice JR-strcUSX APY on the Solana snapshot — public/docs only.

Issue #309. Interest Spectrum JR-strcUSX is not a locked seed. Live quote
only if already on the FCC/solana snapshot; else ~20% docs_target.

SOURCE BLOCKER (verified 2026-08-24) — do not invent a live print:
  Docs target (not a live field):
    https://docs.solstice.finance/solstice-for-users/yieldvault/strcusx
    https://docs.solstice.finance/solstice-for-users/yieldvault/strcusx/yield-and-apy
    JR leftover ~20% when coverage ~200%. Target, not a locked seed.

  Live rates publish in the app and Proof of Solvency dashboard
    https://attestation.solstice.finance/ — HTML (text/html), not a JSON
    APY field. HTML scrape is rejected as source of truth.

  Documented REST API https://api.solstice.finance/v1/ requires
    Authorization: Bearer from partners@solstice.finance
    (https://docs.solstice.finance/solstice-for-builders/apis) and exposes
    mint/redeem/lock *instruction* endpoints only — no public APY field.

Soft-fail: leave snapshot APY fields None. Never persist the ~20%
docs_target as a live print. No wallet invent. No secrets. No mint.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Tuple

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

# Canonical solana-snapshot fields. Spectrum already walks these paths.
APY_FIELD = "jr_strcusx_apy"
APY_ALIASES = ("jr_strcusx_apy", "solstice_apy", "strcusx_apy")

SOURCE_BLOCKER = (
    "source blocked: no public/docs JSON APY field for JR-strcUSX. "
    f"docs target ~20% at {DOCS_YIELD_APY_URL} (not a live print). "
    f"REST {SOLSTICE_API_URL} is partner Bearer + instruction endpoints "
    f"only ({DOCS_API_URL}). "
    f"Live rates on {ATTESTATION_URL} are HTML — scrape rejected."
)

# Explicit JR APY keys only. Generic "apy" is rejected (eUSX / senior / mix).
_PARSE_PATHS: tuple[tuple[str, ...], ...] = (
    ("jr_strcusx_apy",),
    ("solstice_apy",),
    ("strcusx_apy",),
    ("jr_apy",),
    ("junior_apy",),
    ("data", "jr_strcusx_apy"),
    ("data", "solstice_apy"),
    ("data", "strcusx_apy"),
    ("data", "jr_apy"),
    ("data", "junior_apy"),
    ("vault", "jr_strcusx_apy"),
    ("strcusx", "jr_strcusx_apy"),
    ("strcusx", "junior_apy"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dig(root: Any, path: Iterable[str]) -> Any:
    cur = root
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def parse_solstice_jr_apy(payload: Any) -> Tuple[Optional[float], Optional[str], Dict[str, Any]]:
    """Extract a verified JR APY field from JSON. Reject HTML. Never invent.

    Returns (fraction or None, error or None, row). Does not substitute
    the ~20% docs_target.
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
        apy = normalize_apy_fraction(raw)
        if apy is None:
            continue
        return apy, None, {"field": ".".join(path), "raw": raw}
    return None, "no verified JR APY field in payload", {}


def fetch_solstice_jr_apy(
    *,
    payload: Any = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Populate only from a verified public/docs JSON payload.

    Live network fetch is not wired: no public unauthenticated APY field
    exists. Passing ``payload`` is for tests / a future documented JSON
    source. Partners API and HTML attestation are not called.
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


def _prior_apy(row: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(row, dict):
        return None
    source = str(row.get("jr_strcusx_apy_source") or row.get("source") or "")
    if source in ("docs_target", "docs", "empty", "blocked"):
        return None
    for key in APY_ALIASES:
        n = normalize_apy_fraction(row.get(key))
        if n is not None:
            return n
    return None


def empty_solstice_jr_fields(*, err: Optional[str] = None) -> Dict[str, Any]:
    """Snapshot APY keys present, values None. Never the ~20% target."""
    out: Dict[str, Any] = {
        APY_FIELD: None,
        "solstice_apy": None,
        "strcusx_apy": None,
        "jr_strcusx_apy_source": None,
        "jr_strcusx_apy_field": None,
        "jr_strcusx_apy_error": err or SOURCE_BLOCKER,
    }
    return out


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
    out["jr_strcusx_apy_source"] = source or "solstice_docs_json"
    out["jr_strcusx_apy_field"] = field or APY_FIELD
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
) -> Dict[str, Any]:
    """Attach live JR APY when a verified payload exists; else keep prior; else None.

    Soft-fail never writes DOCS_TARGET_APY. Wallet balances are untouched.
    """
    if not isinstance(book, dict):
        book = {}
    live, fetch_err = fetch_solstice_jr_apy(payload=payload)
    if live is not None and live.get(APY_FIELD) is not None:
        return write_solstice_jr_fields(
            book,
            apy=float(live[APY_FIELD]),
            source=str(live.get("source") or "solstice_docs_json"),
            field=str(live.get("field") or APY_FIELD),
        )
    prior_apy = _prior_apy(prior)
    if prior_apy is not None:
        return write_solstice_jr_fields(
            book,
            apy=prior_apy,
            source=str((prior or {}).get("jr_strcusx_apy_source") or "prior"),
            field=str((prior or {}).get("jr_strcusx_apy_field") or APY_FIELD),
            err=fetch_err or SOURCE_BLOCKER,
        )
    return write_solstice_jr_fields(book, apy=None, err=fetch_err or SOURCE_BLOCKER)
