"""Advisory signals on monthly snapshots. No orders. No accounts.

Signal 1 uses the trailing window of prior eligible months, sample standard
deviation (ddof 1), and an inclusive percentile rank: 100 * count(v <= x) / n.
Signal 2 uses 100 * ln(M_t / M_t-12). A method_version change keeps only the
contiguous suffix that matches the current version.
"""

from __future__ import annotations

import json
import math
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from research.global_allocation.model import CLASS_IDS, add_months

PACKAGE_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PACKAGE_ROOT / "signals_config.json"

MONEY_CLASSES = ("broad_money", "gold", "bitcoin")


def D(value: Any) -> Decimal:
    return Decimal(str(value))


def q(value: Decimal, places: str) -> str:
    return format(value.quantize(Decimal(places), rounding=ROUND_HALF_UP), "f")


def load_config(path: Path | None = None) -> dict[str, Any]:
    raw = json.loads((path or CONFIG_PATH).read_text(encoding="utf-8"))
    values: dict[str, Decimal] = {}
    for key, item in raw.items():
        if not str(item.get("rationale") or "").strip():
            raise ValueError(f"missing rationale for {key}")
        values[key] = D(item["value"])
    return values


def percentile_rank(x: Decimal, window: list[Decimal]) -> Decimal:
    if not window:
        raise ValueError("empty window")
    count = sum(1 for value in window if value <= x)
    return Decimal(count) / Decimal(len(window)) * Decimal(100)


def sample_std(window: list[Decimal]) -> Decimal:
    n = len(window)
    mean = sum(window, Decimal(0)) / Decimal(n)
    var = sum((value - mean) ** 2 for value in window) / Decimal(n - 1)
    return var.sqrt()


def _ln(value: Decimal) -> Decimal:
    return Decimal(str(math.log(float(value))))


def eligible_monthly(docs: list[dict[str, Any]], superseded: set[str] | None = None) -> list[dict[str, Any]]:
    """Monthly snapshots with stats_eligible true. Seed and weekly never qualify."""
    skipped = superseded or set()
    chosen = []
    for doc in docs:
        if doc.get("snapshot_id") in skipped:
            continue
        if doc.get("kind") != "monthly":
            continue
        if doc.get("stats_eligible") is not True:
            continue
        chosen.append(doc)
    chosen.sort(key=lambda doc: (doc.get("run_date_et") or "", doc.get("snapshot_id") or ""))
    return chosen


def _class_map(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in doc.get("classes") or []}


def _version_suffix(points: list[dict[str, Any]], version: str) -> list[dict[str, Any]]:
    suffix: list[dict[str, Any]] = []
    for point in reversed(points):
        if point.get("method_version") != version:
            break
        suffix.append(point)
    suffix.reverse()
    return suffix


def _break_note(prior: list[dict[str, Any]], suffix: list[dict[str, Any]], current: dict[str, Any]) -> str | None:
    if len(suffix) == len(prior):
        return None
    older = prior[-(len(suffix) + 1)]
    note = f"method_version break: {older.get('method_version')} -> {current.get('method_version')}"
    text = current.get("methodology_break")
    if text:
        note = f"{note}. {text}"
    return note


def signal_1_class(
    class_id: str,
    series: list[dict[str, Any]],
    config: dict[str, Decimal],
) -> dict[str, Any]:
    """``series`` is chronological and already filtered to one class. The last point is t."""
    result: dict[str, Any] = {
        "break_note": None,
        "class_id": class_id,
        "fired_high": False,
        "fired_low": False,
        "n": 0,
        "percentile": None,
        "status": "insufficient",
        "watch": False,
        "z": None,
    }
    if not series:
        return result
    current = series[-1]
    prior = series[:-1]
    version = current.get("method_version")
    suffix = _version_suffix(prior, version)
    result["break_note"] = _break_note(prior, suffix, current)
    result["n"] = len(suffix)
    if len(suffix) < int(config["min_history"]):
        result["status"] = "insufficient"
        return result
    window_n = int(config["window"])
    window = [D(point["share_pct"]) for point in suffix[-window_n:]]
    x = D(current["share_pct"])
    std = sample_std(window)
    if std < config["flat_std_pp"]:
        result["status"] = "flat"
        return result
    mean = sum(window, Decimal(0)) / Decimal(len(window))
    z = (x - mean) / std
    pct = percentile_rank(x, window)
    result["z"] = z
    result["percentile"] = pct
    result["fired_high"] = z >= config["z_high"] or pct >= config["percentile_high"]
    result["fired_low"] = z <= -config["z_high"] or pct <= config["percentile_low"]
    watch_band = config["watch_z"] <= abs(z) < config["z_high"]
    result["watch"] = watch_band and not result["fired_high"] and not result["fired_low"]
    if result["fired_high"] or result["fired_low"]:
        result["status"] = "fired"
    elif result["watch"]:
        result["status"] = "watch"
    else:
        result["status"] = "none"
    return result


def _class_series(docs: list[dict[str, Any]], class_id: str) -> list[dict[str, Any]]:
    series = []
    for doc in docs:
        item = _class_map(doc).get(class_id)
        if item is None or item.get("share_pct") is None:
            continue
        series.append(
            {
                "method_version": item.get("method_version"),
                "methodology_break": item.get("methodology_break"),
                "run_date_et": doc.get("run_date_et"),
                "share_pct": item.get("share_pct"),
                "snapshot_id": doc.get("snapshot_id"),
                "value_usd_tn": item.get("value_usd_tn"),
            }
        )
    return series


def _stable_tail(docs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Contiguous suffix whose money, gold, and bitcoin versions match the latest point."""
    if not docs:
        return [], []
    current = _class_map(docs[-1])
    versions = {class_id: (current.get(class_id) or {}).get("method_version") for class_id in MONEY_CLASSES}
    tail: list[dict[str, Any]] = []
    for doc in reversed(docs):
        mapping = _class_map(doc)
        if any((mapping.get(class_id) or {}).get("method_version") != versions[class_id] for class_id in MONEY_CLASSES):
            break
        if any(mapping.get(class_id) is None for class_id in MONEY_CLASSES):
            break
        tail.append(doc)
    tail.reverse()
    notes = []
    if len(tail) < len(docs):
        older_docs = docs[: len(docs) - len(tail)]
        for class_id in MONEY_CLASSES:
            latest = (current.get(class_id) or {}).get("method_version")
            previous = (_class_map(older_docs[-1]).get(class_id) or {}).get("method_version") if older_docs else None
            if previous == latest:
                continue
            note = f"method_version break: {class_id} {previous} -> {latest}"
            text = (current.get(class_id) or {}).get("methodology_break")
            if text:
                note = f"{note}. {text}"
            notes.append(note)
    return tail, notes


def signal_2(
    docs: list[dict[str, Any]],
    config: dict[str, Decimal],
    m2_points: list[tuple[str, Decimal]] | None = None,
) -> dict[str, Any]:
    tail, notes = _stable_tail(docs)
    needed = int(config["signal2_points"])
    proxy = m2_yoy(m2_points or [])
    result: dict[str, Any] = {
        "break_notes": notes,
        "fired_hard": False,
        "fired_money": False,
        "g": None,
        "gap": None,
        "h": None,
        "n": len(tail),
        "proxy": proxy,
        "share_change_pp": None,
        "status": "short",
    }
    if len(tail) < needed:
        return result
    latest = _class_map(tail[-1])
    back = _class_map(tail[-needed])
    money_now = D(latest["broad_money"]["value_usd_tn"])
    money_then = D(back["broad_money"]["value_usd_tn"])
    hard_now = D(latest["gold"]["value_usd_tn"]) + D(latest["bitcoin"]["value_usd_tn"])
    hard_then = D(back["gold"]["value_usd_tn"]) + D(back["bitcoin"]["value_usd_tn"])
    if money_then <= 0 or hard_then <= 0 or money_now <= 0 or hard_now <= 0:
        result["status"] = "bad_base"
        return result
    g = _ln(money_now / money_then) * Decimal(100)
    h = _ln(hard_now / hard_then) * Decimal(100)
    gap = g - h
    share_now = D(latest["gold"]["share_pct"]) + D(latest["bitcoin"]["share_pct"])
    share_then = D(back["gold"]["share_pct"]) + D(back["bitcoin"]["share_pct"])
    result.update(
        {
            "fired_hard": -gap >= config["money_gap_low_pp"],
            "fired_money": gap >= config["money_gap_high_pp"] and g >= config["money_growth_min_pct"],
            "g": g,
            "gap": gap,
            "h": h,
            "share_change_pp": share_now - share_then,
            "status": "ok",
        }
    )
    return result


def m2_yoy(points: list[tuple[str, Decimal]]) -> dict[str, Any] | None:
    """Simple percent change versus the same month a year earlier. No interpolation."""
    if not points:
        return None
    ordered = sorted(points, key=lambda item: item[0])
    last_date, last_value = ordered[-1]
    prior_key = _shift_year(last_date[:7])
    found = [value for stamp, value in ordered if stamp[:7] == prior_key]
    if not found or last_value <= 0 or found[-1] <= 0:
        return None
    return {
        "as_of": last_date,
        "percent": (last_value / found[-1] - 1) * Decimal(100),
    }


def _shift_year(year_month: str) -> str:
    year, month = year_month.split("-")
    return f"{int(year) - 1:04d}-{month}"


def signal_3(
    equity: dict[str, Any] | None,
    yields: list[tuple[str, Decimal]],
    run_date: str,
    config: dict[str, Decimal],
    snapshot_yield: tuple[Decimal, str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "as_of": None,
        "fired_high": False,
        "fired_low": False,
        "percentile": None,
        "r": None,
        "status": "no_yield",
        "z": None if equity is None else equity.get("z"),
    }
    chosen = _yield_on(yields, run_date)
    if chosen is None and snapshot_yield is not None:
        result["r"] = snapshot_yield[0]
        result["as_of"] = snapshot_yield[1]
        result["status"] = "no_percentile"
        return result
    if chosen is None:
        return result
    as_of, value = chosen
    window = _yield_window(yields, as_of, int(config["yield_window_months"]))
    pct = percentile_rank(value, window) if window else None
    result.update({"as_of": as_of, "percentile": pct, "r": value, "status": "ok"})
    z = None if equity is None else equity.get("z")
    result["z"] = z
    if z is None or pct is None:
        return result
    if z >= config["equity_z"] and pct >= config["real_yield_percentile_high"]:
        result["fired_high"] = True
    if z <= -config["equity_z"] and pct <= config["real_yield_percentile_low"]:
        result["fired_low"] = True
    return result


def _yield_on(yields: list[tuple[str, Decimal]], run_date: str) -> tuple[str, Decimal] | None:
    chosen = None
    for stamp, value in sorted(yields, key=lambda item: item[0]):
        if stamp <= run_date:
            chosen = (stamp, value)
    return chosen


def _yield_window(yields: list[tuple[str, Decimal]], as_of: str, months: int) -> list[Decimal]:
    start = add_months(date.fromisoformat(as_of), -months).isoformat()
    return [value for stamp, value in yields if start <= stamp <= as_of]


def evaluate(
    docs: list[dict[str, Any]],
    *,
    superseded: set[str] | None = None,
    config: dict[str, Decimal] | None = None,
    yields: list[tuple[str, Decimal]] | None = None,
    m2_points: list[tuple[str, Decimal]] | None = None,
    focus: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    monthly = eligible_monthly(docs, superseded)
    by_class = {
        class_id: signal_1_class(class_id, _class_series(monthly, class_id), cfg) for class_id in CLASS_IDS
    }
    subject = focus if focus is not None else (monthly[-1] if monthly else None)
    run_date = (subject or {}).get("run_date_et") or ""
    snapshot_yield = None
    inputs = (subject or {}).get("inputs") or {}
    if inputs.get("real_yield_10y") is not None and inputs.get("real_yield_as_of"):
        snapshot_yield = (D(inputs["real_yield_10y"]), str(inputs["real_yield_as_of"]))
    if not run_date and snapshot_yield is not None:
        run_date = snapshot_yield[1]
    return {
        "by_class": by_class,
        "config": cfg,
        "monthly": monthly,
        "signal_2": signal_2(monthly, cfg, m2_points),
        "signal_3": signal_3(by_class.get("listed_equities"), yields or [], run_date, cfg, snapshot_yield),
    }
