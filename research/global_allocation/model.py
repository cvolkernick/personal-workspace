"""Class math for one snapshot: anchors, prices, shares, overlap, staleness.

BTC supply is the sum of the first ``height`` subsidy blocks (heights
``0 .. height-1``). Height 971907 is 20,099,709.375 BTC, which is the figure
#1250 requires. The subsidy of the block at ``height`` itself is not included.
Lost coins are not deducted.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from research.global_allocation.sources.base import SourceRecord

CLASS_IDS = [
    "real_estate",
    "debt_securities",
    "listed_equities",
    "broad_money",
    "gold",
    "private_markets",
    "art_collectibles",
    "bitcoin",
    "other_crypto",
]

LABELS = {
    "real_estate": "Real estate",
    "debt_securities": "Debt securities",
    "listed_equities": "Listed equities",
    "broad_money": "Broad money",
    "gold": "Gold",
    "private_markets": "Private markets",
    "art_collectibles": "Art and collectibles",
    "bitcoin": "Bitcoin",
    "other_crypto": "Other crypto",
}

# Named overlap partners. Gold, art, and bitcoin stay false. Bitcoin ETF
# holdings are a small overlap with listed equities and stay in method_note.
OVERLAP: dict[str, list[str]] = {
    "real_estate": ["debt_securities", "listed_equities", "private_markets"],
    "debt_securities": ["real_estate", "broad_money"],
    "listed_equities": ["real_estate", "private_markets", "bitcoin", "other_crypto"],
    "broad_money": ["debt_securities", "other_crypto"],
    "gold": [],
    "private_markets": ["real_estate", "listed_equities"],
    "art_collectibles": [],
    "bitcoin": [],
    "other_crypto": ["broad_money", "listed_equities"],
}

OZ_PER_TONNE = Decimal("32150.7466")
ONE_TN = Decimal("1000000000000")
CAVEAT = "gross, overlapping, mixed dates"


def issued_supply(height: int) -> Decimal:
    if height < 0:
        raise ValueError("height must be >= 0")
    reward = 50 * 100_000_000
    left = height
    total = 0
    while left > 0 and reward > 0:
        take = min(left, 210_000)
        total += take * reward
        left -= take
        reward //= 2
    return Decimal(total) / Decimal(100_000_000)


def q1(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def add_months(start: date, months: int) -> date:
    index = start.month - 1 + months
    year = start.year + index // 12
    month = index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def window_label(review: dict[str, Any]) -> str:
    amount = int(review["amount"])
    unit = review["unit"]
    if unit == "months":
        return f"{amount} months"
    if unit == "days":
        return f"{amount} days"
    raise ValueError(unit)


def stale_days(as_of: str, run_date: str, review: dict[str, Any] | None) -> tuple[bool, int]:
    if not review:
        return False, 0
    start = date.fromisoformat(as_of)
    run = date.fromisoformat(run_date)
    if review["unit"] == "months":
        due = add_months(start, int(review["amount"]))
    else:
        due = start + timedelta(days=int(review["amount"]))
    if run > due:
        return True, (run - due).days
    return False, 0


def share_map(values: dict[str, Decimal], gross: Decimal) -> dict[str, Decimal]:
    raw: dict[str, Decimal] = {}
    for key in CLASS_IDS:
        raw[key] = (values[key] / gross * Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    drift = (Decimal(100) - sum(raw.values(), Decimal(0))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if drift != 0:
        top = max(CLASS_IDS, key=lambda key: values[key])
        raw[top] = (raw[top] + drift).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return raw


def class_record(
    class_id: str,
    value: Decimal,
    share: Decimal,
    as_of: str,
    value_kind: str,
    stale: bool,
    stale_days_n: int,
    source_name: str,
    source_url: str,
    method_note: str,
    method_version: str,
    methodology_break: str | None,
    confidence: str,
    verified: bool,
) -> dict[str, Any]:
    partners = list(OVERLAP[class_id])
    return {
        "as_of": as_of,
        "confidence": confidence,
        "id": class_id,
        "label": LABELS[class_id],
        "method_note": method_note,
        "method_version": method_version,
        "methodology_break": methodology_break,
        "overlap_flag": bool(partners),
        "overlap_with": partners,
        "share_pct": share,
        "source_name": source_name,
        "source_url": source_url,
        "stale": stale,
        "stale_days": stale_days_n,
        "value_kind": value_kind,
        "value_usd_tn": value,
        "verified": verified,
    }


def carry_forward(prior: dict[str, Any], reason: str) -> dict[str, Any]:
    carried = dict(prior)
    carried["value_kind"] = "carried_forward"
    carried["stale"] = True
    carried["stale_days"] = max(int(prior.get("stale_days") or 0), 1)
    carried["method_note"] = prior["method_note"].rstrip() + f" Carried forward: {reason}."
    carried["verified"] = False
    if "value_usd_tn" in prior:
        carried["value"] = Decimal(str(prior["value_usd_tn"]))
    return carried


def anchor_review(anchors: dict[str, dict[str, Any]], quantities: dict[str, dict[str, Any]], run_date: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for class_id in CLASS_IDS:
        entry = anchors.get(class_id)
        if not entry or not entry.get("review_after"):
            continue
        due, days = stale_days(entry["as_of"], run_date, entry["review_after"])
        if due:
            items.append(
                {
                    "as_of": entry["as_of"],
                    "class_id": class_id,
                    "due": True,
                    "stale_days": days,
                    "window": window_label(entry["review_after"]),
                }
            )
    tonnes = quantities.get("gold_above_ground_tonnes")
    if tonnes and tonnes.get("review_after"):
        due, days = stale_days(tonnes["as_of"], run_date, tonnes["review_after"])
        if due:
            items.append(
                {
                    "as_of": tonnes["as_of"],
                    "class_id": "gold",
                    "due": True,
                    "stale_days": days,
                    "window": window_label(tonnes["review_after"]) + " (tonnes)",
                }
            )
    return items


def _prior_class(prior: dict[str, Any] | None, class_id: str) -> dict[str, Any] | None:
    if not prior:
        return None
    for item in prior.get("classes") or []:
        if item.get("id") == class_id:
            return item
    return None


def _from_anchor(entry: dict[str, Any], run_date: str) -> dict[str, Any]:
    due, days = stale_days(entry["as_of"], run_date, entry.get("review_after"))
    return {
        "as_of": entry["as_of_display"],
        "confidence": entry["confidence"],
        "method_note": entry["method_note"],
        "method_version": entry["method_version"],
        "methodology_break": entry.get("methodology_break"),
        "source_name": entry["source_name"],
        "source_url": entry["source_url"],
        "stale": due,
        "stale_days": days,
        "value": Decimal(str(entry["value_usd_tn"])),
        "value_kind": entry["value_kind"],
        "verified": bool(entry.get("verified", False)),
    }


def listed_equities_stats_eligible(
    equities: dict[str, Any] | None,
    equity_anchor: dict[str, Any] | None,
) -> bool:
    """A statistical point needs a scaled listed-equity stock.

    Carrying the SIFMA level forward leaves an unscaled reported stock.
    That snapshot is ineligible, the same rule as snapshot 0. A later run
    or ``--revise`` is eligible only after ``inputs/anchors.json`` holds a
    VT close for the anchor date and this run's class is ``scaled_anchor``.
    The price stays null until that close is recorded.
    """
    if not equities or equities.get("value_kind") != "scaled_anchor":
        return False
    price = (equity_anchor or {}).get("proxy_price_at_anchor")
    return price is not None


def assemble(
    *,
    run_date: str,
    run_at_utc: str,
    kind: str,
    snapshot_id: str,
    supersedes: str | None,
    revision_reason: str | None,
    anchors: dict[str, dict[str, Any]],
    quantities: dict[str, dict[str, Any]],
    sources: dict[str, SourceRecord],
    prior: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """Return ``(doc, unfilled_class_ids, caveats_extra)``.

    ``doc`` is None when any class could not be filled. The caller exits 2
    when every class is unfilled and 1 when only some are.
    ``stats_eligible`` is derived from the listed-equity class. A caller
    cannot mark an unscaled carry eligible.
    """
    built: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    extra_caveats: list[str] = []

    def take(class_id: str, fresh: dict[str, Any] | None, reason: str) -> None:
        if fresh is not None:
            built[class_id] = fresh
            return
        previous = _prior_class(prior, class_id)
        if previous is None:
            failures.append(class_id)
            return
        built[class_id] = carry_forward(previous, reason)
        extra_caveats.append(f"{reason}; {LABELS[class_id]} carried forward")

    for class_id in ("real_estate", "debt_securities", "private_markets", "art_collectibles", "other_crypto"):
        entry = anchors.get(class_id)
        take(class_id, _from_anchor(entry, run_date) if entry else None, f"{class_id} anchor missing")

    other = built.get("other_crypto")
    gecko = sources.get("coingecko")
    if other and gecko and gecko.unavailable:
        other["method_note"] = other["method_note"].rstrip() + f" Live CoinGecko pull is off: {gecko.unavailable}."

    _equities(built, failures, extra_caveats, anchors, sources, prior, run_date)
    _broad_money(built, failures, extra_caveats, sources, prior)
    gold_inputs = _gold(built, failures, extra_caveats, quantities, sources, prior, run_date)
    btc_inputs = _bitcoin(built, failures, extra_caveats, sources, prior, run_date)

    if len(built) != len(CLASS_IDS):
        return None, failures, extra_caveats

    values = {key: built[key]["value"] for key in CLASS_IDS}
    gross = sum(values.values(), Decimal(0))
    shares = share_map(values, gross)
    classes = []
    for key in CLASS_IDS:
        item = built[key]
        classes.append(
            class_record(
                key,
                item["value"],
                shares[key],
                item["as_of"],
                item["value_kind"],
                item["stale"],
                item["stale_days"],
                item["source_name"],
                item["source_url"],
                item["method_note"],
                item["method_version"],
                item["methodology_break"],
                item["confidence"],
                item["verified"],
            )
        )

    fx_basis, fx_caveat = _fx(sources.get("frankfurter"), prior)
    if fx_caveat:
        extra_caveats.append(fx_caveat)
    real_yield, real_as_of, yield_caveat = _yield(sources.get("treasury_real_yield"))
    if yield_caveat:
        extra_caveats.append(yield_caveat)

    equity = sources.get("equity_proxy")
    equity_anchor = anchors.get("listed_equities") or {}
    stats_eligible = listed_equities_stats_eligible(built.get("listed_equities"), equity_anchor)
    doc = {
        "anchor_review": anchor_review(anchors, quantities, run_date),
        "caveats": [CAVEAT, "Not net wealth. Advisory only.", *extra_caveats],
        "classes": classes,
        "fx_basis": fx_basis,
        "inputs": {
            "btc_height": btc_inputs["height"],
            "btc_supply": btc_inputs["supply"],
            "equity_proxy_price_at_anchor": equity_anchor.get("proxy_price_at_anchor"),
            "equity_proxy_price_now": equity.payload["price"] if equity and equity.ok else None,
            "equity_proxy_symbol": equity_anchor.get("proxy_symbol") or "VT",
            "gold_tonnes": gold_inputs["tonnes"],
            "gold_tonnes_as_of": gold_inputs["as_of"],
            "real_yield_10y": real_yield,
            "real_yield_as_of": real_as_of,
        },
        "kind": kind,
        "revision_reason": revision_reason,
        "run_at_utc": run_at_utc,
        "run_date_et": run_date,
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "stats_eligible": stats_eligible,
        "supersedes": supersedes,
        "totals": {"gross_usd_tn": gross, "shares_pct": shares},
        "unit": "usd_tn",
    }
    return doc, [], extra_caveats


def _equities(built, failures, caveats, anchors, sources, prior, run_date) -> None:
    entry = anchors.get("listed_equities")
    proxy = sources.get("equity_proxy")
    if entry is None:
        _fail_or_carry(built, failures, caveats, "listed_equities", prior, "listed equities anchor missing")
        return
    if proxy is None or not proxy.ok:
        reason = proxy.unavailable if proxy else "equity proxy missing"
        _fail_or_carry(built, failures, caveats, "listed_equities", prior, f"Alpha Vantage: {reason}")
        return
    price_at = entry.get("proxy_price_at_anchor")
    if price_at is None:
        _fail_or_carry(
            built,
            failures,
            caveats,
            "listed_equities",
            prior,
            "listed equities not scaled: proxy_price_at_anchor is unset",
        )
        return
    price_now = Decimal(str(proxy.payload["price"]))
    value = q1(Decimal(str(entry["value_usd_tn"])) * price_now / Decimal(str(price_at)))
    due, days = stale_days(entry["as_of"], run_date, entry.get("review_after"))
    symbol = entry.get("proxy_symbol") or "VT"
    built["listed_equities"] = {
        "as_of": entry["as_of_display"],
        "confidence": "medium",
        "method_note": (
            f"Anchor {entry['value_usd_tn']} as of {entry['as_of']} scaled by "
            f"{symbol} {price_now} / {price_at} on {proxy.as_of}. "
            "Share-count change is ignored (about 1% a year). "
            f"{entry['method_note']}"
        ),
        "method_version": "scaled-anchor-v1",
        "methodology_break": (
            "Scaled from the reported SIFMA anchor. Snapshot 0 is the unscaled reported figure."
        ),
        "source_name": entry["source_name"],
        "source_url": entry["source_url"],
        "stale": due,
        "stale_days": days,
        "value": value,
        "value_kind": "scaled_anchor",
        "verified": False,
    }


def _broad_money(built, failures, caveats, sources, prior) -> None:
    rec = sources.get("m2index")
    if rec is None or not rec.ok or not (rec.payload or {}).get("as_of"):
        reason = rec.unavailable if rec and rec.unavailable else "missing as_of"
        _fail_or_carry(built, failures, caveats, "broad_money", prior, f"M2 Index: {reason}")
        return
    value = q1(Decimal(rec.payload["value_usd"]) / ONE_TN)
    fed = sources.get("fed_h6")
    ecb = sources.get("ecb_m3")
    cross = []
    if fed and fed.ok:
        cross.append(f"Fed H.6 M2 {fed.payload['m2_billions']} billion USD as of {fed.payload['as_of']}")
    else:
        cross.append(f"Fed H.6 unavailable ({fed.unavailable if fed else 'missing'})")
    if ecb and ecb.ok:
        cross.append(
            f"ECB M3 {ecb.payload['obs_value']} UNIT_MULT {ecb.payload['unit_mult']} "
            f"{ecb.payload['unit']} as of {ecb.payload['as_of']}"
        )
    else:
        cross.append(f"ECB M3 unavailable ({ecb.unavailable if ecb else 'missing'})")
    wb = sources.get("world_bank")
    if wb and wb.ok:
        cross.append(f"World Bank equity cap cross-check {wb.payload['value_usd']} USD as of {wb.payload['as_of']}")
    built["broad_money"] = {
        "as_of": rec.payload.get("as_of") or rec.as_of or "",
        "confidence": "medium",
        "method_note": (
            "Sum of m2index value_usd across economies with a USD conversion. "
            "Attribute M2 Index, m2index.com, with the observation date. "
            f"Rows used {rec.payload['rows_used']}, blank USD skipped {rec.payload['rows_skipped_blank_usd']}, "
            f"oldest period {rec.payload.get('oldest_period')}. "
            + " ".join(cross)
        ),
        "method_version": "m2index-sum-v1",
        "methodology_break": "Headline is the summed CSV, not the hand-compiled 144.4 figure in snapshot 0.",
        "source_name": rec.source_name,
        "source_url": rec.source_url,
        "stale": False,
        "stale_days": 0,
        "value": value,
        "value_kind": "reported",
        "verified": True,
    }


def _gold(built, failures, caveats, quantities, sources, prior, run_date) -> dict[str, Any]:
    tonnes_entry = quantities.get("gold_above_ground_tonnes")
    spot = sources.get("gold_spot")
    empty = {"tonnes": None, "as_of": None}
    if tonnes_entry is None or spot is None or not spot.ok:
        reason = "gold tonnes anchor missing" if tonnes_entry is None else f"gold-api: {spot.unavailable if spot else 'missing'}"
        _fail_or_carry(built, failures, caveats, "gold", prior, reason)
        if tonnes_entry:
            return {"tonnes": Decimal(str(tonnes_entry["value"])), "as_of": tonnes_entry["as_of"]}
        return empty
    tonnes = Decimal(str(tonnes_entry["value"]))
    price = Decimal(str(spot.payload["price"]))
    value = q1(tonnes * OZ_PER_TONNE * price / ONE_TN)
    paxg = sources.get("coinbase_paxg")
    if paxg and paxg.ok:
        other = Decimal(str(paxg.payload["price"]))
        gap = abs(price - other) / price
        check = "diverged" if gap > Decimal("0.02") else "ok"
        check_txt = f"PAXG-USD {other} price_check {check}. Primary spot is used."
    else:
        check_txt = f"PAXG-USD unavailable ({paxg.unavailable if paxg else 'missing'}); price_check unchecked. Primary spot is used."
    due, days = stale_days(tonnes_entry["as_of"], run_date, tonnes_entry.get("review_after"))
    built["gold"] = {
        "as_of": spot.as_of or run_date,
        "confidence": "medium",
        "method_note": (
            f"tonnes {tonnes} as of {tonnes_entry['as_of']} × 32150.7466 oz/t × spot {price}. "
            f"{check_txt} {tonnes_entry['method_note']}"
        ),
        "method_version": "tonnes-x-spot-v1",
        "methodology_break": "Price source changed from the London fix used in snapshot 0 to the spot feed.",
        "source_name": spot.source_name,
        "source_url": spot.source_url,
        "stale": due,
        "stale_days": days,
        "value": value,
        "value_kind": "computed_price_x_qty",
        "verified": True,
    }
    return {"tonnes": tonnes, "as_of": tonnes_entry["as_of"]}


def _bitcoin(built, failures, caveats, sources, prior, run_date) -> dict[str, Any]:
    height_rec = sources.get("mempool_height")
    price_rec = sources.get("coinbase_btc")
    problems = []
    if height_rec is None or not height_rec.ok:
        problems.append(f"mempool.space: {height_rec.unavailable if height_rec else 'missing'}")
    if price_rec is None or not price_rec.ok:
        problems.append(f"Coinbase: {price_rec.unavailable if price_rec else 'missing'}")
    if problems:
        _fail_or_carry(built, failures, caveats, "bitcoin", prior, "; ".join(problems))
        return {"height": None, "supply": None}
    height = int(height_rec.payload["height"])
    supply = issued_supply(height)
    price = Decimal(str(price_rec.payload["price"]))
    value = q1(price * supply / ONE_TN)
    built["bitcoin"] = {
        "as_of": run_date,
        "confidence": "high",
        "method_note": (
            f"spot {price} × issued supply {supply} from tip height {height}. "
            "Supply sums the first `height` block subsidies (heights 0 through height-1) "
            "and does not include the subsidy of the block at `height`. "
            "Lost coins are not deducted. "
            "Bitcoin ETF holdings are a small overlap with listed equities and are not removed."
        ),
        "method_version": "height-supply-v1",
        "methodology_break": "Snapshot 0 used a CoinMarketCap price and a rounded 20.10M supply, not this height rule.",
        "source_name": "Coinbase",
        "source_url": price_rec.source_url,
        "stale": False,
        "stale_days": 0,
        "value": value,
        "value_kind": "computed_price_x_qty",
        "verified": True,
    }
    return {"height": height, "supply": supply}


def _fx(rec: SourceRecord | None, prior: dict[str, Any] | None) -> tuple[dict[str, Any], str | None]:
    if rec and rec.ok:
        rates = {str(k): Decimal(str(v)) for k, v in rec.payload["rates"].items()}
        return (
            {
                "as_of": rec.payload.get("as_of"),
                "rates": rates,
                "source_name": rec.source_name,
                "source_url": rec.source_url,
                "unavailable": None,
            },
            None,
        )
    reason = rec.unavailable if rec else "frankfurter missing"
    if prior and isinstance(prior.get("fx_basis"), dict):
        carried = dict(prior["fx_basis"])
        carried["unavailable"] = reason
        return carried, f"Frankfurter: {reason}; fx basis carried forward"
    return (
        {
            "as_of": None,
            "rates": {},
            "source_name": "Frankfurter (ECB reference)",
            "source_url": "https://api.frankfurter.dev/v1/latest",
            "unavailable": reason,
        },
        f"Frankfurter: {reason}",
    )


def _yield(rec: SourceRecord | None) -> tuple[Decimal | None, str | None, str | None]:
    if rec and rec.ok:
        return Decimal(str(rec.payload["real_yield_10y"])), rec.payload["as_of"], None
    reason = rec.unavailable if rec else "treasury yield missing"
    return None, None, f"US Treasury real yield: {reason}"


def _fail_or_carry(built, failures, caveats, class_id, prior, reason: str) -> None:
    previous = _prior_class(prior, class_id)
    if previous is None:
        failures.append(class_id)
        return
    # Drop share fields from the prior class; share_map recomputes them.
    fresh = carry_forward(previous, reason)
    fresh["value"] = Decimal(str(previous["value_usd_tn"]))
    built[class_id] = fresh
    caveats.append(f"{reason}; {LABELS[class_id]} carried forward")
