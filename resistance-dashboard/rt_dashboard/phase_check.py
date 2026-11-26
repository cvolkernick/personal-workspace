"""Phase check: a read-only view of the live body-fat auto-switch (FF-4).

Display only. Every rule number comes from ``body_fat_phase``: the counter
is the ``evaluate()`` decision the coach card already built (``bf_phase``),
and the switch calories come from ``maintenance_targets()`` /
``ramp_targets()``. Nothing here writes phase, kcal, macros or steps, and it
never calls ``commit_targets``, ``update_targets``, ``persist_targets`` or
``applied_targets_write``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

CAVEAT = (
    "Home-scale body fat is an estimate (±3–5 pts). "
    "The trend matters more than any one reading."
)
APPROACH_POINTS = 2.0
AVG_DAYS = 14
STATE_LABELS = {
    "not_enough_data": "Not enough data",
    "switched": "Switched",
    "holding": "Holding",
    "approaching": "Approaching",
    "far": "Far",
}


def _bfp():
    try:
        import body_fat_phase as bfp
    except ImportError:
        import sys
        from pathlib import Path

        parent = str(Path(__file__).resolve().parents[1])
        if parent not in sys.path:
            sys.path.insert(0, parent)
        import body_fat_phase as bfp
    return bfp


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _num(value: Any) -> Optional[float]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return None if num != num else num


def bf_avg_14d(samples: Sequence[Any], as_of: Any) -> Dict[str, Any]:
    """Mean of deduped readings in [D-13, D]. Port of ``trailingAverage(..., 14)``.

    D is the latest valid reading on or before ``as_of`` (the day the Trends
    "body fat 14-day avg" text is computed for). When no reading falls in the
    14 days ending ``as_of`` the value is null. Display only.
    """
    bfp = _bfp()
    day = bfp._civil_day(as_of)
    if not day:
        return {"value": None, "date": None}
    rows = bfp.dedupe_twins(bfp.measurements(samples))
    floor = bfp.add_days(day, -(AVG_DAYS - 1))
    recent = [row for row in rows if floor <= row["date"] <= day]
    if not recent:
        return {"value": None, "date": None}
    end = max(row["date"] for row in recent)
    start = bfp.add_days(end, -(AVG_DAYS - 1))
    vals = [row["percentage"] for row in rows if start <= row["date"] <= end]
    if not vals:
        return {"value": None, "date": None}
    return {"value": sum(vals) / len(vals), "date": end}


def earliest_switch_date(bf: dict) -> Optional[str]:
    """First day D >= as_of on which ``hits`` reaches ``hits_needed``,
    assuming every new counted day is past the bound.

    Uses the counted window ``evaluate()`` already returned. Each new counted
    day enters at the newest end and the oldest counted day drops out, the
    same way ``counted_window`` walks. Null when it cannot be reached by that
    assumption (phase is neither cut nor bulk, or there is no as_of).
    """
    bfp = _bfp()
    kind = bf.get("phase")
    day = bfp._civil_day(bf.get("as_of"))
    if kind not in ("cut", "bulk") or not day:
        return None
    need_hits = int(bf.get("hits_needed") or bfp.HITS_NEEDED)
    need_counted = int(bf.get("counted_needed") or bfp.COUNTED_NEEDED)
    counted = [row for row in (bf.get("counted_days") or []) if isinstance(row, dict)]
    flags: List[bool] = [bool(row.get("past")) for row in counted]  # chronological
    if len(flags) >= need_counted and sum(flags[-need_counted:]) >= need_hits:
        return day
    today_counted = bool(counted) and bfp._civil_day(counted[-1].get("date")) == day
    candidate = bfp.add_days(day, 1) if today_counted else day
    for _ in range(need_counted + 1):
        flags.append(True)
        window = flags[-need_counted:]
        if len(window) >= need_counted and sum(window) >= need_hits:
            return candidate
        candidate = bfp.add_days(candidate, 1)
    return None


def _weekday_md(iso: Optional[str]) -> Optional[str]:
    if not iso:
        return None
    try:
        dt = datetime.strptime(str(iso)[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return f"{dt.strftime('%a')} {dt.month}/{dt.day}"


def _switch_block(targets: Optional[dict]) -> Optional[dict]:
    if not isinstance(targets, dict) or _num(targets.get("calories")) is None:
        return None
    return {
        "phase": targets.get("phase"),
        "kcal": int(round(_num(targets.get("calories")))),
        "protein_g": targets.get("protein_g"),
        "fat_g": targets.get("fat_g"),
        "carbs_g": targets.get("carbs_g"),
    }


def _stored_cut(uid: str) -> Optional[dict]:
    """Cut targets stored on the way into the bulk (read only)."""
    bfp = _bfp()
    try:
        user = bfp._uid(uid)
        return bfp._stored_cut(user, bfp.load_events(user))
    except Exception:
        return None


def _weight_rate(card: dict) -> dict:
    rate = _as_dict(card.get("rate"))
    band = rate.get("band_pct") if isinstance(rate.get("band_pct"), (list, tuple)) else None
    two = _num(rate.get("two_week"))
    return {
        "two_week_pct": None if two is None else round(two, 2),
        "band": list(band) if band else None,
        "position": rate.get("position"),
    }


def _lifts(data: dict, day: str) -> dict:
    guard = _as_dict(data.get("guardrails"))
    tiles = guard.get("tiles") if isinstance(guard.get("tiles"), list) else None
    if tiles is None or (guard.get("as_of") and guard.get("as_of") != day):
        try:
            from .guardrails import build_guardrails

            tiles = build_guardrails(data, as_of=day or None).get("tiles") or []
        except Exception:
            tiles = []
    tile = next((t for t in tiles if isinstance(t, dict) and t.get("id") == "lifts"), None)
    if not tile:
        return {"status": "insufficient", "reason": "Not enough data."}
    return {"status": tile.get("status") or "insufficient", "reason": tile.get("reason")}


def _state(bf: dict, kind: str, endpoint: Optional[float]) -> str:
    bfp = _bfp()
    counted = int(bf.get("counted") or 0)
    needed = int(bf.get("counted_needed") or bfp.COUNTED_NEEDED)
    if counted < needed or bool(bf.get("thin")):
        return "not_enough_data"
    last = _as_dict(bf.get("last_flip"))
    flip_day = bfp._civil_day(last.get("date"))
    gap = bfp._days_between(flip_day, bf.get("as_of")) if flip_day else None
    if bf.get("applied") or (gap is not None and 0 <= gap <= bfp.BANNER_DAYS):
        return "switched"
    median = _num(bf.get("median_7d"))
    if int(bf.get("hits") or 0) >= 1 and bfp._meets(kind, median):
        return "holding"
    if median is not None and endpoint is not None and abs(median - endpoint) <= APPROACH_POINTS:
        return "approaching"
    return "far"


def build_phase_check(data: Optional[dict], card: Optional[dict], bf: Optional[dict]) -> Optional[dict]:
    """``phase_check`` for the coach card. Pure reads of ``bf`` (the evaluate decision)."""
    if not isinstance(bf, dict):
        return None
    bfp = _bfp()
    payload = _as_dict(data)
    card = _as_dict(card)
    kind = bf.get("phase")
    if kind not in ("cut", "bulk"):
        kind = bfp.phase_kind(kind)
    endpoint = bfp.CUT_AT if kind == "cut" else bfp.BULK_AT if kind == "bulk" else None
    direction = "<=" if kind == "cut" else ">=" if kind == "bulk" else None
    day = bfp._civil_day(bf.get("as_of") or card.get("as_of"))
    samples = _as_dict(payload.get("health")).get("body_fat") or []
    avg = bf_avg_14d(samples, day)
    weekly = _as_dict(card.get("weekly"))
    tdee = _as_dict(weekly.get("tdee")).get("tdee")
    if kind == "cut":
        on_switch = _switch_block(bfp.maintenance_targets(tdee))
        ramp_t = bfp.ramp_targets(tdee)
        bump = bfp.ramp_targets(0)
        ramp = {
            "after_days": bfp.RAMP_AFTER_DAYS,
            "kcal": None if not ramp_t else int(ramp_t["calories"]),
            "plus_kcal": None if not bump else int(bump["calories"]),
        }
        if on_switch is None:
            ramp["kcal"] = None
    elif kind == "bulk":
        meta = _as_dict(payload.get("meta"))
        on_switch = _switch_block(_stored_cut(str(meta.get("user_id") or "")))
        ramp = None
    else:
        on_switch = None
        ramp = None
    auto = bool(bfp.auto_flip_enabled())
    staged = _as_dict(card.get("staged"))
    state = _state(bf, kind, endpoint)
    earliest = earliest_switch_date(bf)
    report = _as_dict(bf.get("phase_report"))
    return {
        "phase": kind,
        "phase_label": bfp.phase_label(kind),
        "as_of": day or None,
        "state": state,
        "state_label": STATE_LABELS.get(state, state),
        "endpoint": endpoint,
        "direction": direction,
        "switch_to": "bulk" if kind == "cut" else "cut" if kind == "bulk" else None,
        "median_7d": bf.get("median_7d"),
        "median_14d": bf.get("median_14d"),
        "bf_avg_14d": None if avg["value"] is None else round(float(avg["value"]), 2),
        "bf_avg_14d_date": avg["date"],
        "today_past": bool(not bf.get("thin") and bfp._meets(kind, _num(bf.get("median_7d")))),
        "hits": int(bf.get("hits") or 0),
        "hits_needed": int(bf.get("hits_needed") or bfp.HITS_NEEDED),
        "counted": int(bf.get("counted") or 0),
        "counted_needed": int(bf.get("counted_needed") or bfp.COUNTED_NEEDED),
        "thin": bool(bf.get("thin")),
        "min_readings": bfp.MIN_READINGS,
        "window_days": bfp.WINDOW_DAYS,
        "earliest_switch_date": earliest,
        "earliest_switch_label": _weekday_md(earliest),
        "on_switch": on_switch,
        "ramp": ramp,
        "last_switch": {
            "date": _as_dict(bf.get("last_flip")).get("date"),
            "phase": _as_dict(bf.get("last_flip")).get("phase"),
            "targets": report.get("targets"),
        }
        if state == "switched"
        else None,
        "weight_rate": _weight_rate(card),
        "lifts": _lifts(payload, day),
        "labs": {"date": staged.get("draw")},
        "auto_switch": auto,
        "applies_automatically": auto,
        "caveat": CAVEAT,
    }
