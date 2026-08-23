"""APR/APY interest spectrum for FCC.

Honest rates only: locked fleet financing + APR/APY fields already present
in treasury books/config/snapshots. Never invent yields. Never plot equity
or BTC assumed-return / appreciation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "treasury" / "config.json"
FCC_STUB = ROOT / "financial-command" / "interest-spectrum.json"
TREASURY_FCC = ROOT / "financial-command" / "treasury_latest.json"
TREASURY_SNAP = ROOT / "treasury" / "snapshots" / "treasury_latest.json"
XM_SNAPSHOT = ROOT / "treasury" / "snapshots" / "x_money_latest.json"
FLEET_NOTES = ROOT / "auto-fleet" / "data" / "notes.json"
FLEET_ROSTER = ROOT / "auto-fleet" / "data" / "roster.json"

# Issue #277 locked fleet APRs — cost-of-debt chips, not balances.
LOCKED_FLEET: tuple[dict[str, Any], ...] = (
    {
        "id": "corolla-2024",
        "label": "24 Corolla",
        "instrument": "Santander",
        "kind": "debt",
        "rate_pct": 10.18,
        "rate_kind": "APR",
        "notes": "cost-of-debt chip",
        "fcc_liability": True,
    },
    {
        "id": "corolla-2022",
        "label": "22 Corolla",
        "instrument": "Capital One",
        "kind": "debt",
        "rate_pct": 11.14,
        "rate_kind": "APR",
        "notes": "cost-of-debt chip",
        "fcc_liability": True,
    },
    {
        "id": "m3-2022",
        "label": "22 Tesla",
        "instrument": "GM Financial",
        "kind": "debt",
        "rate_pct": 18.15,
        "rate_kind": "APR",
        "notes": "cost-of-debt chip",
        "fcc_liability": True,
    },
    {
        "id": "m3-2020",
        "label": "20 Tesla",
        "instrument": "Wells Fargo",
        "kind": "debt",
        "rate_pct": 5.65,
        "rate_kind": "APR",
        "notes": "lender/APR metadata · not a FCC liability",
        "fcc_liability": False,
        "role": "metadata",
    },
    {
        "id": "r1s-2023",
        "label": "23 Rivian",
        "instrument": "Vivek",
        "kind": "debt",
        "rate_pct": 0.0,
        "rate_kind": "APR",
        "notes": "$1350/mo · 0% APR",
        "fcc_liability": True,
        "monthly_payment": 1350,
    },
)

# Book venues we know exist in FCC. Rate stays unknown unless an allowlisted
# APR/APY field is actually present. Do not invent 7% / 29% / vault APY.
BOOK_VENUES: tuple[dict[str, Any], ...] = (
    {
        "id": "morpho_borrow",
        "label": "Morpho borrow",
        "instrument": "Morpho",
        "kind": "debt",
        "rate_kind": "APR",
        "unit": "fraction",
        "paths": (
            ("evaluation", "inputs", "variable_apr"),
            ("snapshot", "coinbase_manual", "variable_apr"),
            ("config", "coinbase_manual", "variable_apr"),
        ),
    },
    {
        "id": "morpho_hy",
        "label": "Morpho High Yield",
        "instrument": "Morpho",
        "kind": "yield",
        "rate_kind": "APY",
        "unit": "fraction",
        "paths": (
            ("evaluation", "inputs", "vault_apy"),
            ("evaluation", "inputs", "hy_vault_apy"),
            ("snapshot", "coinbase_manual", "vault_apy"),
            ("config", "coinbase_manual", "vault_apy"),
        ),
    },
    {
        "id": "x_money",
        "label": "X Money",
        "instrument": "X Money",
        "kind": "yield",
        "rate_kind": "APY",
        "unit": "fraction",
        "paths": (
            ("evaluation", "inputs", "x_money_apy_est"),
            ("snapshot", "x_money", "apy_est"),
            ("x_money", "apy_est"),
        ),
    },
    {
        "id": "usdg_earn",
        "label": "RH USDG Earn",
        "instrument": "Robinhood",
        "kind": "yield",
        "rate_kind": "APY",
        "unit": "fraction",
        "paths": (
            ("evaluation", "inputs", "rh_usdg_earn_apy_est"),
            ("snapshot", "robinhood", "usdg_earn_apy_est"),
            ("config", "robinhood", "usdg_earn_apy_est"),
        ),
    },
)

# Never promote these keys into spectrum chips (assumed performance stays off-axis).
FORBIDDEN_RATE_KEYS = frozenset(
    {
        "expected_return",
        "expected_return_pct",
        "assumed_return",
        "assumed_return_pct",
        "equity_return",
        "equity_expected_return",
        "btc_return",
        "btc_expected_return",
        "btc_assumed_return",
        "appreciation",
        "appreciation_pct",
        "asset_return",
    }
)

ALLOWED_CHIP_KINDS = frozenset({"debt", "yield"})
LOCKED_RATE_BY_ID = {row["id"]: float(row["rate_pct"]) for row in LOCKED_FLEET}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _dig(root: Any, path: Iterable[str]) -> Any:
    cur = root
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fraction_field_to_pct(value: Any) -> Optional[float]:
    """Convert a books field documented as 0–1 into percent.

    Values already stored as percent (> 1) pass through. Missing stays missing.
    0 is a real rate (e.g. 0% APR), not unknown.
    """
    n = _as_float(value)
    if n is None:
        return None
    if n > 1.0:
        return n
    return n * 100.0


def _coach_threshold_pct(stub: Dict[str, Any], config: Dict[str, Any]) -> Optional[float]:
    """Blank unless Chris locked a number. Do not default to 5."""
    for blob in (stub, config.get("interest_spectrum") or {}):
        if not isinstance(blob, dict):
            continue
        raw = blob.get("coach_threshold_pct")
        if raw is None or raw == "":
            continue
        n = _as_float(raw)
        if n is None:
            continue
        return n
    return None


def _fleet_chips() -> List[Dict[str, Any]]:
    """Locked financing table. notes.json may confirm labels; rates stay locked."""
    notes = (_load_json(FLEET_NOTES).get("units") or {}) if FLEET_NOTES.is_file() else {}
    roster_by_id = {}
    roster = _load_json(FLEET_ROSTER)
    for unit in roster.get("units") or []:
        if isinstance(unit, dict) and unit.get("id"):
            roster_by_id[str(unit["id"])] = unit

    chips: List[Dict[str, Any]] = []
    for row in LOCKED_FLEET:
        unit_id = str(row["id"])
        portal = notes.get(unit_id) if isinstance(notes, dict) else None
        roster_u = roster_by_id.get(unit_id) or {}
        chip = {
            "id": unit_id,
            "label": row["label"],
            "instrument": row["instrument"],
            "kind": "debt",
            "rate_pct": float(row["rate_pct"]),
            "rate_kind": "APR",
            "source": "locked_financing",
            "notes": row.get("notes"),
            "fcc_liability": bool(row.get("fcc_liability")),
            "deep_link": "fleet",
            "placed": True,
        }
        if row.get("role"):
            chip["role"] = row["role"]
        if row.get("monthly_payment") is not None:
            chip["monthly_payment"] = row["monthly_payment"]
        if isinstance(portal, dict) and portal.get("lender"):
            chip["instrument"] = str(portal.get("lender") or chip["instrument"])
        if roster_u.get("year") and roster_u.get("model"):
            chip["vehicle"] = f"{roster_u.get('year')} {roster_u.get('make') or ''} {roster_u.get('model')}".replace(
                "  ", " "
            ).strip()
        chips.append(chip)
    return chips


def _book_chips(
    treasury: Dict[str, Any],
    config: Dict[str, Any],
    x_money: Dict[str, Any],
) -> List[Dict[str, Any]]:
    ctx = {
        "evaluation": treasury.get("evaluation") if isinstance(treasury.get("evaluation"), dict) else {},
        "snapshot": treasury.get("snapshot") if isinstance(treasury.get("snapshot"), dict) else {},
        "config": config if isinstance(config, dict) else {},
        "x_money": x_money if isinstance(x_money, dict) else {},
    }
    chips: List[Dict[str, Any]] = []
    for spec in BOOK_VENUES:
        raw = None
        hit_path = None
        for path in spec["paths"]:
            raw = _dig(ctx, path)
            if raw is not None and raw != "":
                hit_path = ".".join(path)
                break
        rate = _fraction_field_to_pct(raw) if spec.get("unit") == "fraction" else _as_float(raw)
        chip: Dict[str, Any] = {
            "id": spec["id"],
            "label": spec["label"],
            "instrument": spec["instrument"],
            "kind": spec["kind"],
            "rate_kind": spec["rate_kind"],
            "source": "books" if rate is not None else "unknown",
            "fcc_liability": spec["kind"] == "debt",
            "deep_link": None,
            "placed": rate is not None,
            "rate_pct": rate,
        }
        if rate is None:
            chip["notes"] = "rate unknown"
        elif hit_path:
            chip["notes"] = f"from {hit_path}"
        chips.append(chip)
    return chips


def _reject_forbidden(obj: Any) -> None:
    """Safety: caller may pass rich treasury blobs; we never read forbidden keys."""
    # Intentionally a no-op walk for documentation — extraction is allowlist-only.
    _ = obj


def _axis_max(placed: List[Dict[str, Any]]) -> float:
    rates = [abs(float(c["rate_pct"])) for c in placed if c.get("rate_pct") is not None]
    span = max(rates) if rates else 0.0
    if span <= 20:
        return 20.0
    return float((int(span) // 5) * 5 + 5)


def build_interest_spectrum(
    *,
    treasury: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    x_money: Optional[Dict[str, Any]] = None,
    stub: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble APR/APY chips. Missing book rates stay unknown (not invented)."""
    treasury = treasury if isinstance(treasury, dict) else _load_json(
        TREASURY_FCC if TREASURY_FCC.is_file() else TREASURY_SNAP
    )
    config = config if isinstance(config, dict) else _load_json(CONFIG_PATH)
    x_money = x_money if isinstance(x_money, dict) else _load_json(XM_SNAPSHOT)
    if not x_money:
        snap_xm = (treasury.get("snapshot") or {}).get("x_money")
        if isinstance(snap_xm, dict):
            x_money = snap_xm
    stub = stub if isinstance(stub, dict) else _load_json(FCC_STUB)
    _reject_forbidden(treasury)

    fleet = _fleet_chips()
    books = _book_chips(treasury, config, x_money)
    chips = fleet + books
    for chip in chips:
        if chip.get("kind") not in ALLOWED_CHIP_KINDS:
            chip["kind"] = "debt" if chip.get("rate_kind") == "APR" else "yield"

    placed = [c for c in chips if c.get("rate_pct") is not None]
    unknown = [c for c in chips if c.get("rate_pct") is None]
    threshold = _coach_threshold_pct(stub, config)

    return {
        "ok": True,
        "title": "Interest Spectrum",
        "brand": "FCC",
        "as_of": _now(),
        "axis": {
            "left": "Cost of debt (APR)",
            "center": "0%",
            "right": "Yield (APY)",
            "max_pct": _axis_max(placed),
        },
        "chips": chips,
        "placed": placed,
        "unknown": unknown,
        "coach_threshold_pct": threshold,
        "coach_threshold_locked": threshold is not None,
        "policy": {
            "apr_apy_only": True,
            "equity_btc_assumed_return": False,
            "invented_rates": False,
            "wells_is_fcc_liability": False,
        },
        "sources": {
            "locked_financing": True,
            "books": any(c.get("source") == "books" for c in books),
            "fleet_notes": FLEET_NOTES.is_file(),
        },
    }


def rates_are_honest(payload: Dict[str, Any]) -> bool:
    """True when every placed rate is locked-fleet or an extracted books value."""
    if not isinstance(payload, dict):
        return False
    for chip in payload.get("chips") or []:
        if not isinstance(chip, dict):
            return False
        if chip.get("kind") not in ALLOWED_CHIP_KINDS:
            return False
        if chip.get("rate_kind") not in ("APR", "APY"):
            return False
        rate = chip.get("rate_pct")
        if rate is None:
            continue
        if chip.get("source") == "locked_financing":
            locked = LOCKED_RATE_BY_ID.get(str(chip.get("id")))
            if locked is None or abs(float(rate) - locked) > 1e-9:
                return False
            continue
        if chip.get("source") != "books":
            return False
    return True
