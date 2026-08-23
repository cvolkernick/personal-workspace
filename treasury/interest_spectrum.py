"""APR/APY interest spectrum for FCC (Nakatoshi strip AC).

One shared 0% → ~30% axis. Debt chips above; yield chips below.
Honest rates only: locked debt seeds, locked yield seeds (Chris 2026-08-23),
and APR/APY already on books. Never invent yields. Equity/BTC
assumed-return stays off-axis. Wells/20 Tesla stays off FCC.
JR-strcUSX is a spectrum chip only (not HY/LTV). Coach threshold X
is not wired.

Chris / Nakatoshi lock (2026-08-23) — Interest Spectrum Morpho HY USDC:
  Preferred: live apy_est when a trustworthy source exists
  Fallback: seed 7% (Coinbase One Morpho HY)
  Override: FCC settings manual beats seed and beats live when set
  Do not invent rates.

Morpho HY precedence (settings > live books > seed):
  1. FCC settings manual ``config.coinbase_manual.vault_apy``
     (or dedicated ``morpho_hy_apy_est``) when set — human override
  2. Live books ``evaluation.inputs.vault_apy`` / ``hy_vault_apy`` /
     ``morpho_hy_apy_est`` (and snapshot paths already listed) from a
     trusted feed (Morpho GraphQL vaultV2 ``avgNetApy``)
  3. Seed 7%

Soft-fail live poller never writes a seed. No Coinbase HTML scrape.
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
SOLANA_SNAPSHOT = ROOT / "treasury" / "snapshots" / "solana_latest.json"
MORPHO_HY_SNAPSHOT = ROOT / "treasury" / "snapshots" / "morpho_hy_latest.json"
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

# Chris 2026-08-23 locked yield seeds. Always show; books override when apy_est
# / vault_apy is already present. Pattern matches Morpho borrow ~5% / One Card ~29%.
LOCKED_YIELD_SEEDS: tuple[dict[str, Any], ...] = (
    {
        "id": "x_money",
        "venue": "X Money",
        "label": "X Money",
        "kind": "yield",
        "rate_pct": 6.0,
        "approx": True,
        "rate_kind": "APY",
        "unit": "fraction",
        "notes": "locked seed 6% APY · books override when apy_est present",
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
        "id": "morpho_hy",
        "venue": "Morpho HY",
        "label": "Morpho HY",
        "kind": "yield",
        "rate_pct": 7.0,
        "approx": True,
        "rate_kind": "APY",
        "unit": "fraction",
        "notes": (
            "locked seed 7% APY · Coinbase One Morpho HY · variable · "
            "precedence: FCC settings vault_apy / morpho_hy_apy_est > "
            "live books (Morpho GraphQL vaultV2 avgNetApy) > seed · "
            "do not invent rates"
        ),
        "deep_link": "index.html#hy",
        # settings_paths are checked first so a human override beats live books.
        "settings_paths": (
            ("config", "coinbase_manual", "vault_apy"),
            ("config", "coinbase_manual", "morpho_hy_apy_est"),
        ),
        "paths": (
            ("evaluation", "inputs", "vault_apy"),
            ("evaluation", "inputs", "hy_vault_apy"),
            ("evaluation", "inputs", "morpho_hy_apy_est"),
            ("snapshot", "coinbase_manual", "vault_apy"),
            ("snapshot", "coinbase_manual", "morpho_hy_apy_est"),
            ("snapshot", "morpho_hy", "apy_est"),
            ("snapshot", "morpho_hy", "apy"),
            ("morpho_hy", "apy_est"),
            ("morpho_hy", "apy"),
        ),
        "notional_paths": (
            ("evaluation", "inputs", "vault_usdc"),
            ("snapshot", "coinbase_manual", "vault_usdc"),
            ("config", "coinbase_manual", "vault_usdc"),
        ),
    },
    {
        "id": "usdg_earn",
        "venue": "RH USDG Earn",
        "label": "RH USDG Earn",
        "kind": "yield",
        "rate_pct": 7.0,
        "approx": True,
        "rate_kind": "APY",
        "unit": "fraction",
        "notes": (
            "locked seed 7% APY · may end when RH Gold cancels — "
            "do not invent a post-Gold rate · books override when usdg_earn_apy_est present"
        ),
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

# Back-compat alias: yield venues are now always-on seeds (books still win).
YIELD_VENUES = LOCKED_YIELD_SEEDS

USDG_GOLD_CAVEAT = "may end when RH Gold cancels — do not invent a post-Gold rate"

# JR-strcUSX is not a locked seed. Live Solstice quote only if already on the
# FCC/solana snapshot — no scrape. Else ~20% target (docs), spectrum chip only.
JR_STRCUSX_ID = "jr_strcusx"
JR_TARGET_PCT = 20.0
JR_TARGET_LABEL = "~20% target"
JR_DOCS_NOTES = (
    "approx target · not a locked seed · docs.solstice.finance strcUSX · "
    "solstice.finance/vaults/strcusx · does not count toward HY/LTV floors · "
    "spectrum chip only"
)
JR_LIVE_APY_PATHS = (
    ("evaluation", "inputs", "jr_strcusx_apy"),
    ("evaluation", "inputs", "solstice_apy"),
    ("evaluation", "inputs", "strcusx_apy"),
    ("snapshot", "solana", "jr_strcusx_apy"),
    ("snapshot", "solana", "solstice_apy"),
    ("snapshot", "solana", "strcusx_apy"),
    ("snapshot", "solana", "vault_apy"),
    ("solana", "jr_strcusx_apy"),
    ("solana", "solstice_apy"),
    ("solana", "strcusx_apy"),
    ("solana", "vault_apy"),
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
ALLOWED_SOURCES = frozenset({"locked_financing", "locked_seed", "books", "docs_target"})
LOCKED_RATE_BY_ID = {row["id"]: float(row["rate_pct"]) for row in LOCKED_FLEET}
LOCKED_SEED_RATE_BY_ID = {
    **{row["id"]: float(row["rate_pct"]) for row in LOCKED_SEEDS},
    **{row["id"]: float(row["rate_pct"]) for row in LOCKED_YIELD_SEEDS},
}

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
    solana: Optional[Dict[str, Any]] = None,
    morpho_hy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    snap = treasury.get("snapshot") if isinstance(treasury.get("snapshot"), dict) else {}
    snap = dict(snap)
    sol = solana if isinstance(solana, dict) else {}
    if not sol and isinstance(snap.get("solana"), dict):
        sol = snap["solana"]
    mh = morpho_hy if isinstance(morpho_hy, dict) else {}
    if not mh and isinstance(snap.get("morpho_hy"), dict):
        mh = snap["morpho_hy"]
    if mh and not isinstance(snap.get("morpho_hy"), dict):
        snap["morpho_hy"] = mh
    return {
        "evaluation": treasury.get("evaluation") if isinstance(treasury.get("evaluation"), dict) else {},
        "snapshot": snap,
        "config": config if isinstance(config, dict) else {},
        "x_money": x_money if isinstance(x_money, dict) else {},
        "solana": sol,
        "morpho_hy": mh,
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


def _first_apy_hit(
    ctx: Dict[str, Any],
    paths: Iterable[Iterable[str]],
    *,
    unit: str = "fraction",
) -> tuple[Optional[float], Optional[str]]:
    for path in paths:
        raw = _dig(ctx, path)
        if raw is None or raw == "":
            continue
        rate = _fraction_field_to_pct(raw) if unit == "fraction" else _as_float(raw)
        if rate is None:
            continue
        return rate, ".".join(path)
    return None, None


def _jr_strcusx_chip(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Spectrum-only JR chip. Live Solstice APY if already on books; else ~20% target.

    Never attaches wallet balances. Never counts toward HY / LTV floors.
    """
    chip: Dict[str, Any] = {
        "id": JR_STRCUSX_ID,
        "venue": "JR-strcUSX",
        "label": "JR-strcUSX",
        "kind": "yield",
        "lane": "below",
        "rate_kind": "APY",
        "fcc_liability": False,
        "counts_toward_hy": False,
        "counts_toward_ltv_defense": False,
        "deep_link": "index.html#panel-solana",
        "placed": True,
    }
    live, hit = _first_apy_hit(ctx, JR_LIVE_APY_PATHS)
    if live is not None:
        chip["rate_pct"] = live
        chip["approx"] = False
        chip["source"] = "books"
        chip["notes"] = (
            f"from {hit} · does not count toward HY/LTV floors · spectrum chip only"
            if hit
            else "live Solstice on books · does not count toward HY/LTV floors · spectrum chip only"
        )
        return chip
    chip["rate_pct"] = JR_TARGET_PCT
    chip["rate_label"] = JR_TARGET_LABEL
    chip["approx"] = True
    chip["source"] = "docs_target"
    chip["notes"] = JR_DOCS_NOTES
    return chip


def _yield_chips(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """X Money / Morpho HY / USDG always appear (locked seeds); books override APY.

    Morpho HY walks ``settings_paths`` before live ``paths`` so FCC settings
    beat live books when Chris sets a manual vault_apy / morpho_hy_apy_est.
    """
    chips: List[Dict[str, Any]] = []
    for spec in LOCKED_YIELD_SEEDS:
        chip: Dict[str, Any] = {
            "id": spec["id"],
            "venue": spec["venue"],
            "label": spec["label"],
            "kind": "yield",
            "lane": "below",
            "rate_kind": "APY",
            "approx": True,
            "source": "locked_seed",
            "notes": spec.get("notes"),
            "fcc_liability": False,
            "deep_link": spec.get("deep_link"),
            "placed": True,
            "rate_pct": float(spec["rate_pct"]),
        }
        unit = str(spec.get("unit") or "fraction")
        rate, hit = _first_apy_hit(ctx, spec.get("settings_paths") or (), unit=unit)
        from_settings = rate is not None
        if rate is None:
            rate, hit = _first_apy_hit(ctx, spec.get("paths") or (), unit=unit)
        if rate is not None:
            chip["rate_pct"] = rate
            chip["approx"] = False
            chip["source"] = "books"
            notes = f"from {hit}" if hit else None
            if spec["id"] == "morpho_hy" and notes:
                if from_settings:
                    notes = f"{notes} · FCC settings override"
                else:
                    notes = f"{notes} · live books"
            if spec["id"] == "usdg_earn":
                notes = f"{notes} · {USDG_GOLD_CAVEAT}" if notes else USDG_GOLD_CAVEAT
            chip["notes"] = notes
        notional = _first_number(ctx, spec.get("notional_paths") or ())
        if notional is not None:
            chip["notional"] = notional
            chip["notional_kind"] = "balance"
        chips.append(chip)
    chips.append(_jr_strcusx_chip(ctx))
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
    solana: Optional[Dict[str, Any]] = None,
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
    solana = solana if isinstance(solana, dict) else _load_json(SOLANA_SNAPSHOT)
    if not solana:
        snap_sol = (treasury.get("snapshot") or {}).get("solana")
        if isinstance(snap_sol, dict):
            solana = snap_sol
    snap_mh = (treasury.get("snapshot") or {}).get("morpho_hy")
    morpho_hy = snap_mh if isinstance(snap_mh, dict) else {}
    # stub is retained as a blank file only — coach is not wired this ship.
    _ = stub if stub is not None else _load_json(FCC_STUB)

    ctx = _books_ctx(treasury, config, x_money, solana, morpho_hy)
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
    """True when every placed rate is locked-fleet, locked-seed, books, or JR docs target."""
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
        if source not in ALLOWED_SOURCES:
            return False
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
        if source == "docs_target":
            if str(chip.get("id")) != JR_STRCUSX_ID:
                return False
            if abs(float(rate) - JR_TARGET_PCT) > 1e-9:
                return False
            if chip.get("rate_label") != JR_TARGET_LABEL:
                return False
            continue
        if source != "books":
            return False
    return True
