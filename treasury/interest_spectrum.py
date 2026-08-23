"""APR/APY interest spectrum for FCC (Nakatoshi strip AC).

One shared 0% → ~30% axis. Debt chips above; yield chips below.
Honest rates only: locked seeds + APR/APY already on books. Never invent
yields. Equity/BTC assumed-return stays off-axis. Wells/20 Tesla stays off FCC.
Coach threshold X is not wired.
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

# Locked fleet APRs on the FCC spectrum (cost-of-debt chips, not balances).
# Wells / 20 Tesla is Auto Fleet metadata only — off FCC.
LOCKED_FLEET: tuple[dict[str, Any], ...] = (
    {
        "id": "corolla-2024",
        "venue": "Santander",
        "label": "Santander",
        "detail": "24 Corolla",
        "kind": "debt",
        "rate_pct": 10.18,
        "rate_kind": "APR",
        "notes": "cost-of-debt chip",
        "fcc_liability": True,
        "deep_link": "fleet",
    },
    {
        "id": "corolla-2022",
        "venue": "Capital One",
        "label": "Capital One",
        "detail": "22 Corolla",
        "kind": "debt",
        "rate_pct": 11.14,
        "rate_kind": "APR",
        "notes": "cost-of-debt chip",
        "fcc_liability": True,
        "deep_link": "fleet",
    },
    {
        "id": "m3-2022",
        "venue": "GM Financial",
        "label": "GM Financial",
        "detail": "22 Tesla",
        "kind": "debt",
        "rate_pct": 18.15,
        "rate_kind": "APR",
        "notes": "cost-of-debt chip",
        "fcc_liability": True,
        "deep_link": "fleet",
    },
    {
        "id": "r1s-2023",
        "venue": "Rivian",
        "label": "Rivian",
        "detail": "23 Rivian · Vivek",
        "kind": "debt",
        "rate_pct": 0.0,
        "rate_kind": "APR",
        "notes": "$1350/mo · 0% APR",
        "fcc_liability": True,
        "monthly_payment": 1350,
        "deep_link": "fleet",
    },
)

# Nakatoshi locked seeds (approximate). Books override when a real field exists.
LOCKED_SEEDS: tuple[dict[str, Any], ...] = (
    {
        "id": "morpho_borrow",
        "venue": "Morpho borrow",
        "label": "Morpho borrow",
        "kind": "debt",
        "rate_pct": 5.0,
        "approx": True,
        "rate_kind": "APR",
        "notes": "locked seed ~5% · books override when variable_apr present",
        "deep_link": "index.html#morpho",
        "fcc_liability": True,
    },
    {
        "id": "one_card",
        "venue": "One Card",
        "label": "One Card",
        "kind": "debt",
        "rate_pct": 29.0,
        "approx": True,
        "rate_kind": "APR",
        "notes": "locked seed ~29% contractual",
        "deep_link": "index.html#one-card",
        "fcc_liability": True,
    },
)

# Yield venues: plot only when an allowlisted apy_est (or vault APY) is present.
YIELD_VENUES: tuple[dict[str, Any], ...] = (
    {
        "id": "morpho_hy",
        "venue": "Morpho HY",
        "label": "Morpho HY",
        "kind": "yield",
        "rate_kind": "APY",
        "unit": "fraction",
        "deep_link": "index.html#hy",
        "paths": (
            ("evaluation", "inputs", "vault_apy"),
            ("evaluation", "inputs", "hy_vault_apy"),
            ("snapshot", "coinbase_manual", "vault_apy"),
            ("config", "coinbase_manual", "vault_apy"),
        ),
        "notional_paths": (
            ("evaluation", "inputs", "vault_usdc"),
            ("snapshot", "coinbase_manual", "vault_usdc"),
            ("config", "coinbase_manual", "vault_usdc"),
        ),
    },
    {
        "id": "x_money",
        "venue": "X Money",
        "label": "X Money",
        "kind": "yield",
        "rate_kind": "APY",
        "unit": "fraction",
        "deep_link": "index.html#x-money",
        "paths": (
            ("evaluation", "inputs", "x_money_apy_est"),
            ("snapshot", "x_money", "apy_est"),
            ("x_money", "apy_est"),
        ),
        "notional_paths": (
            ("evaluation", "inputs", "x_money_cash"),
            ("snapshot", "x_money", "cash"),
            ("x_money", "cash"),
        ),
    },
    {
        "id": "usdg_earn",
        "venue": "RH USDG Earn",
        "label": "RH USDG Earn",
        "kind": "yield",
        "rate_kind": "APY",
        "unit": "fraction",
        "deep_link": "index.html#panel-brokerage",
        "paths": (
            ("evaluation", "inputs", "rh_usdg_earn_apy_est"),
            ("snapshot", "robinhood", "usdg_earn_apy_est"),
            ("config", "robinhood", "usdg_earn_apy_est"),
        ),
        "notional_paths": (
            ("evaluation", "inputs", "rh_usdg_earn_usdg"),
            ("snapshot", "robinhood", "usdg_earn_usdg"),
        ),
    },
)

MORPHO_BOOK_PATHS = (
    ("evaluation", "inputs", "variable_apr"),
    ("snapshot", "coinbase_manual", "variable_apr"),
    ("config", "coinbase_manual", "variable_apr"),
)
MORPHO_NOTIONAL_PATHS = (
    ("evaluation", "inputs", "loan_principal_usdc"),
    ("snapshot", "coinbase_manual", "loan_principal_usdc"),
    ("config", "coinbase_manual", "loan_principal_usdc"),
)
ONE_CARD_NOTIONAL_PATHS = (
    ("evaluation", "inputs", "card_balance"),
    ("snapshot", "one_card", "balance"),
    ("snapshot", "one_card", "cleared_balance"),
    ("config", "coinbase_manual", "card_balance"),
)

# Wells / 20 Tesla — Auto Fleet metadata only; never a FCC spectrum chip.
WELLS_OFF_FCC_ID = "m3-2020"

ALLOWED_CHIP_KINDS = frozenset({"debt", "yield"})
LOCKED_RATE_BY_ID = {row["id"]: float(row["rate_pct"]) for row in LOCKED_FLEET}
LOCKED_SEED_RATE_BY_ID = {row["id"]: float(row["rate_pct"]) for row in LOCKED_SEEDS}

# Axis tick marks from locked seeds (percent).
SEED_TICKS_PCT: tuple[float, ...] = (0.0, 5.0, 10.18, 11.14, 18.15, 29.0)
DEFAULT_AXIS_MAX_PCT = 30.0


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


def _first_number(ctx: Dict[str, Any], paths: Iterable[Iterable[str]]) -> Optional[float]:
    for path in paths:
        n = _as_float(_dig(ctx, path))
        if n is not None:
            return n
    return None


def _books_ctx(
    treasury: Dict[str, Any],
    config: Dict[str, Any],
    x_money: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "evaluation": treasury.get("evaluation") if isinstance(treasury.get("evaluation"), dict) else {},
        "snapshot": treasury.get("snapshot") if isinstance(treasury.get("snapshot"), dict) else {},
        "config": config if isinstance(config, dict) else {},
        "x_money": x_money if isinstance(x_money, dict) else {},
    }


def _fleet_chips() -> List[Dict[str, Any]]:
    """Locked financing table. Rates stay locked; roster only supplies vehicle labels."""
    roster_by_id: Dict[str, Any] = {}
    roster = _load_json(FLEET_ROSTER)
    for unit in roster.get("units") or []:
        if isinstance(unit, dict) and unit.get("id"):
            roster_by_id[str(unit["id"])] = unit

    chips: List[Dict[str, Any]] = []
    for row in LOCKED_FLEET:
        unit_id = str(row["id"])
        if unit_id == WELLS_OFF_FCC_ID:
            continue
        roster_u = roster_by_id.get(unit_id) or {}
        venue = str(row["venue"])
        chip: Dict[str, Any] = {
            "id": unit_id,
            "venue": venue,
            "label": venue,
            "detail": row.get("detail"),
            "kind": "debt",
            "lane": "above",
            "rate_pct": float(row["rate_pct"]),
            "rate_kind": "APR",
            "approx": False,
            "source": "locked_financing",
            "notes": row.get("notes"),
            "fcc_liability": True,
            "deep_link": "fleet",
            "fleet_unit": unit_id,
            "placed": True,
        }
        if row.get("monthly_payment") is not None:
            chip["monthly_payment"] = row["monthly_payment"]
            chip["notional"] = row["monthly_payment"]
            chip["notional_kind"] = "monthly"
        if roster_u.get("year") and roster_u.get("model"):
            chip["vehicle"] = (
                f"{roster_u.get('year')} {roster_u.get('make') or ''} {roster_u.get('model')}"
                .replace("  ", " ")
                .strip()
            )
        chips.append(chip)
    return chips


def _seed_debt_chips(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    chips: List[Dict[str, Any]] = []
    for row in LOCKED_SEEDS:
        chip: Dict[str, Any] = {
            "id": row["id"],
            "venue": row["venue"],
            "label": row["label"],
            "kind": "debt",
            "lane": "above",
            "rate_kind": "APR",
            "approx": True,
            "source": "locked_seed",
            "notes": row.get("notes"),
            "fcc_liability": True,
            "deep_link": row.get("deep_link"),
            "placed": True,
            "rate_pct": float(row["rate_pct"]),
        }
        if row["id"] == "morpho_borrow":
            books = _fraction_field_to_pct(_first_number(ctx, MORPHO_BOOK_PATHS))
            if books is not None:
                chip["rate_pct"] = books
                chip["approx"] = False
                chip["source"] = "books"
                chip["notes"] = "from books variable_apr"
            notional = _first_number(ctx, MORPHO_NOTIONAL_PATHS)
            if notional is not None:
                chip["notional"] = notional
                chip["notional_kind"] = "principal"
        if row["id"] == "one_card":
            notional = _first_number(ctx, ONE_CARD_NOTIONAL_PATHS)
            if notional is not None:
                chip["notional"] = notional
                chip["notional_kind"] = "balance"
        chips.append(chip)
    return chips


def _yield_chips(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """X Money / HY / USDG — only when an allowlisted APY is actually present."""
    chips: List[Dict[str, Any]] = []
    for spec in YIELD_VENUES:
        raw = None
        hit = None
        for path in spec["paths"]:
            raw = _dig(ctx, path)
            if raw is not None and raw != "":
                hit = ".".join(path)
                break
        rate = (
            _fraction_field_to_pct(raw) if spec.get("unit") == "fraction" else _as_float(raw)
        )
        if rate is None:
            continue
        chip: Dict[str, Any] = {
            "id": spec["id"],
            "venue": spec["venue"],
            "label": spec["label"],
            "kind": "yield",
            "lane": "below",
            "rate_kind": "APY",
            "approx": False,
            "source": "books",
            "notes": f"from {hit}" if hit else None,
            "fcc_liability": False,
            "deep_link": spec.get("deep_link"),
            "placed": True,
            "rate_pct": rate,
        }
        notional = _first_number(ctx, spec.get("notional_paths") or ())
        if notional is not None:
            chip["notional"] = notional
            chip["notional_kind"] = "balance"
        chips.append(chip)
    return chips


def _axis_max(placed: List[Dict[str, Any]]) -> float:
    rates = [abs(float(c["rate_pct"])) for c in placed if c.get("rate_pct") is not None]
    rates.extend(SEED_TICKS_PCT)
    span = max(rates) if rates else 0.0
    if span <= DEFAULT_AXIS_MAX_PCT:
        return DEFAULT_AXIS_MAX_PCT
    return float(((int(span) + 4) // 5) * 5)


def build_interest_spectrum(
    *,
    treasury: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    x_money: Optional[Dict[str, Any]] = None,
    stub: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble APR/APY chips on a shared 0→~30% two-lane axis."""
    treasury = treasury if isinstance(treasury, dict) else _load_json(
        TREASURY_FCC if TREASURY_FCC.is_file() else TREASURY_SNAP
    )
    config = config if isinstance(config, dict) else _load_json(CONFIG_PATH)
    x_money = x_money if isinstance(x_money, dict) else _load_json(XM_SNAPSHOT)
    if not x_money:
        snap_xm = (treasury.get("snapshot") or {}).get("x_money")
        if isinstance(snap_xm, dict):
            x_money = snap_xm
    # stub is retained as a blank file only — coach is not wired this ship.
    _ = stub if stub is not None else _load_json(FCC_STUB)

    ctx = _books_ctx(treasury, config, x_money)
    chips = _fleet_chips() + _seed_debt_chips(ctx) + _yield_chips(ctx)
    for chip in chips:
        if chip.get("kind") not in ALLOWED_CHIP_KINDS:
            chip["kind"] = "debt" if chip.get("rate_kind") == "APR" else "yield"
        chip["lane"] = "above" if chip["kind"] == "debt" else "below"
        if chip.get("id") == WELLS_OFF_FCC_ID:
            raise AssertionError("Wells/20 Tesla must stay off the FCC spectrum")

    placed = [c for c in chips if c.get("rate_pct") is not None]
    unknown: List[Dict[str, Any]] = []
    books_used = any(c.get("source") == "books" for c in chips)

    return {
        "ok": True,
        "title": "Interest Spectrum",
        "brand": "FCC",
        "as_of": _now(),
        "axis": {
            "layout": "two_lane",
            "left": "0%",
            "right": "~30%",
            "min_pct": 0.0,
            "max_pct": _axis_max(placed),
            "debt_lane": "above",
            "yield_lane": "below",
            "ticks": list(SEED_TICKS_PCT),
        },
        "chips": chips,
        "placed": placed,
        "unknown": unknown,
        "coach_wired": False,
        "policy": {
            "apr_apy_only": True,
            "equity_btc_assumed_return": False,
            "invented_rates": False,
            "wells_is_fcc_liability": False,
            "wells_on_fcc_spectrum": False,
            "chip_size_is_notional": False,
            "coach_wired": False,
        },
        "sources": {
            "locked_financing": True,
            "locked_seed": True,
            "books": books_used,
            "fleet_notes": FLEET_NOTES.is_file(),
        },
    }


def rates_are_honest(payload: Dict[str, Any]) -> bool:
    """True when every placed rate is locked-fleet, locked-seed, or books."""
    if not isinstance(payload, dict):
        return False
    if payload.get("coach_wired"):
        return False
    for chip in payload.get("chips") or []:
        if not isinstance(chip, dict):
            return False
        if str(chip.get("id")) == WELLS_OFF_FCC_ID:
            return False
        if chip.get("kind") not in ALLOWED_CHIP_KINDS:
            return False
        if chip.get("rate_kind") not in ("APR", "APY"):
            return False
        rate = chip.get("rate_pct")
        if rate is None:
            return False
        source = chip.get("source")
        if source == "locked_financing":
            locked = LOCKED_RATE_BY_ID.get(str(chip.get("id")))
            if locked is None or abs(float(rate) - locked) > 1e-9:
                return False
            continue
        if source == "locked_seed":
            locked = LOCKED_SEED_RATE_BY_ID.get(str(chip.get("id")))
            if locked is None or abs(float(rate) - locked) > 1e-9:
                return False
            continue
        if source != "books":
            return False
    return True
