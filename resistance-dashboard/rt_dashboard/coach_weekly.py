"""Weekly coach rows for the Goal 3b card (port of the v1 weekly card).

Rows are built on read from the dashboard payload: Mon-Sun weeks, means over
days with data, counted when >= 5/7 weigh-ins AND >= 5/7 food logs. Rates are
week-over-week % change in average weight. Noise weeks are shown but cannot
trigger a decision. Travel and illness tags are MANUAL only (store), never
inferred.

The one-lever rule is STEPS FIRST (Frankenfit addendum 2026-10-28): while the
kcal track is off the advisory never names a kcal or macro change. Advisory
only; nothing here applies a target.

Small JSON store (``coach_weekly.json`` under ``$FITDASH_COACH_STATE_DIR``)
keeps TDEE history, ``last_adjustment`` and manual week tags. Back it up with
every deploy.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

MIN_DAYS = 5
PROTEIN_FLOOR_G = 170.0
PROTEIN_GAP_DAYS = 3
TDEE_BAND = 150
COUNTED_WEEKS_GATE = 2
STEP_PULL_MAX = 1000  # max +steps/day per counted week when pulling a step earlier
MANUAL_TAGS = ("travel", "illness")
BUILDING_TEXT = "Weekly data building"
FAST_TEXT = "Losing faster than the band: flag to Grok. No change."

STORE_DEFAULT: Dict[str, Any] = {
    "schema_version": 1,
    "tdee_history": [],  # [{week_start, tdee, low, high, weeks_used}]
    "last_adjustment": None,
    "week_tags": {},  # {week_start: ["travel" | "illness"]}, manual only
}


# ---------------------------------------------------------------- store
def store_path() -> Path:
    base = os.environ.get("FITDASH_COACH_STATE_DIR") or os.path.expanduser("~/.local/state/fitdash-coach")
    return Path(base) / "coach_weekly.json"


def load_store(path: Optional[Path] = None) -> Dict[str, Any]:
    out = json.loads(json.dumps(STORE_DEFAULT))
    try:
        raw = json.loads((path or store_path()).read_text())
        if isinstance(raw, dict):
            out.update(raw)
    except (OSError, ValueError):
        pass
    return out


def save_store(store: Dict[str, Any], path: Optional[Path] = None) -> Path:
    p = path or store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".coach_weekly.")
    with os.fdopen(fd, "w") as fh:
        json.dump(store, fh, indent=1, sort_keys=True)
    os.replace(tmp, p)
    return p


def record_tdee(store: Dict[str, Any], week_start: str, tdee: Optional[Dict[str, Any]]) -> bool:
    """Append one TDEE estimate per card week. Returns True when the store changed."""
    if not tdee or not week_start:
        return False
    hist = store.setdefault("tdee_history", [])
    if any(isinstance(h, dict) and h.get("week_start") == week_start for h in hist):
        return False
    hist.append(dict(tdee, week_start=week_start))
    return True


def week_tags(store: Dict[str, Any], week_start: str) -> List[str]:
    raw = (store.get("week_tags") or {}).get(week_start) or []
    return [t for t in raw if t in MANUAL_TAGS]


# ---------------------------------------------------------------- helpers
def _d(s: str) -> datetime:
    return datetime.strptime(str(s)[:10], "%Y-%m-%d")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def monday_of(day: str) -> str:
    dt = _d(day)
    return _iso(dt - timedelta(days=dt.weekday()))


def last_completed_week_start(as_of: str) -> str:
    """The Monday card covers the previous Mon-Sun."""
    return _iso(_d(monday_of(as_of)) - timedelta(days=7))


def _mean(vals: Sequence[Any]) -> Optional[float]:
    vals = [float(v) for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _r(v: Optional[float], n: int = 1) -> Optional[float]:
    return None if v is None else round(v, n)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _health(data: Dict[str, Any]) -> Dict[str, Any]:
    h = data.get("health") if isinstance(data, dict) else None
    if hasattr(h, "to_dict"):
        try:
            h = h.to_dict()
        except Exception:  # noqa: BLE001
            h = {}
    return h if isinstance(h, dict) else {}


# ---------------------------------------------------------------- daily data
def daily_bodyweight(health: Any) -> Dict[str, Dict[str, Any]]:
    """{date: {weight_lbs}}. Morning weigh-in = first sample listed that day."""
    rows = health.get("weight") or [] if isinstance(health, dict) else getattr(health, "weight", None) or []
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        raw = row.to_dict() if hasattr(row, "to_dict") else row
        if not isinstance(raw, dict):
            continue
        day = str(raw.get("date") or "")[:10]
        lbs = _f(raw.get("weight_lbs") if raw.get("weight_lbs") is not None else raw.get("lbs"))
        if len(day) != 10 or lbs is None or lbs <= 0 or day in out:
            continue
        out[day] = {"date": day, "weight_lbs": round(lbs, 2)}
    return out


def daily_intake(data: Dict[str, Any], days: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    from .agent_today import _consumed_for_day

    out: Dict[str, Dict[str, Any]] = {}
    for day in days:
        try:
            row = _consumed_for_day(data, day)
        except Exception:  # noqa: BLE001
            row = None
        if row and _f(row.get("calories")) and float(row["calories"]) > 0:
            out[day] = row
    return out


# ---------------------------------------------------------------- weeks
def protein_low_days(intake: Dict[str, Dict[str, Any]], days: Sequence[str]) -> int:
    n = 0
    for d in days:
        p = _f((intake.get(d) or {}).get("protein_g"))
        if p is not None and p < PROTEIN_FLOOR_G:
            n += 1
    return n


def summarize_week(
    week_start: str,
    weights: Dict[str, Dict[str, Any]],
    steps: Dict[str, Dict[str, Any]],
    intake: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    days = [_iso(_d(week_start) + timedelta(days=i)) for i in range(7)]
    w = [weights[d]["weight_lbs"] for d in days if d in weights]
    s = [steps[d]["steps"] for d in days if d in steps]
    k = [intake[d]["calories"] for d in days if d in intake]
    return {
        "week_start": week_start,
        "week_end": days[-1],
        "weigh_ins": len(w),
        "food_logs": len(k),
        "step_days": len(s),
        "avg_weight": _r(_mean(w), 2),
        "avg_kcal": _r(_mean(k), 0),
        "avg_steps": _r(_mean(s), 0),
        "steps_used": len(s) >= MIN_DAYS,
        "counted": len(w) >= MIN_DAYS and len(k) >= MIN_DAYS,
        "protein_low_days": protein_low_days(intake, days),
    }


def attach_rates(weeks: List[Dict[str, Any]]) -> None:
    """Oldest->newest. ``rate_pct`` = WoW % change (negative = loss); ``loss_pct`` = -rate_pct."""
    prev = None
    for wk in weeks:
        for key in ("rate_pct", "loss_pct", "lb_change", "kcal_delta", "steps_delta", "weight_delta"):
            wk[key] = None
        if prev is not None:
            if wk["avg_weight"] is not None and prev["avg_weight"]:
                wk["lb_change"] = round(wk["avg_weight"] - prev["avg_weight"], 2)
                wk["weight_delta"] = wk["lb_change"]
                wk["rate_pct"] = round(100.0 * wk["lb_change"] / prev["avg_weight"], 3)
                wk["loss_pct"] = round(-wk["rate_pct"], 3) + 0.0
            if wk["avg_kcal"] is not None and prev["avg_kcal"] is not None:
                wk["kcal_delta"] = round(wk["avg_kcal"] - prev["avg_kcal"], 0)
            if wk["avg_steps"] is not None and prev["avg_steps"] is not None:
                wk["steps_delta"] = round(wk["avg_steps"] - prev["avg_steps"], 0)
        prev = wk


def noise_reasons(
    wk: Dict[str, Any], *, phase_week: Optional[int] = None, explicit_start: bool = False, tags: Sequence[str] = ()
) -> List[str]:
    """Noise weeks are shown, never decide. Tags come only from the manual store."""
    out = []
    rate = wk.get("rate_pct")
    kd = wk.get("kcal_delta")
    if rate is not None and rate > 1.0 and (kd is None or kd < 300):
        out.append("weight_up_gt_1pct_kcal_up_lt_300")
    if explicit_start and phase_week == 1:
        out.append("week_1_of_phase")
    for t in tags or []:
        if t in MANUAL_TAGS:
            out.append(t)
    return out


def estimate_tdee(weeks: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """TDEE = avg kcal - (weekly lb change x 3500 / 7) over the last 2-4 counted weeks, ±150."""
    use = [
        w for w in weeks
        if w.get("counted") and not w.get("noise") and w.get("lb_change") is not None and w.get("avg_kcal") is not None
    ][-4:]
    if len(use) < 2:
        return None
    kcal = _mean([w["avg_kcal"] for w in use])
    lb = _mean([w["lb_change"] for w in use])
    tdee = kcal - lb * 3500.0 / 7.0
    return {
        "tdee": int(round(tdee)),
        "low": int(round(tdee - TDEE_BAND)),
        "high": int(round(tdee + TDEE_BAND)),
        "weeks_used": len(use),
    }


def decision_weeks(weeks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rows in the shape ``cut_coach.decide`` reads. Noise weeks are not counted."""
    out = []
    for w in weeks:
        out.append(
            {
                "week_start": w["week_start"],
                "counted": bool(w.get("counted")) and not w.get("noise") and w.get("loss_pct") is not None,
                "rate_pct": w.get("loss_pct"),
                "steps_avg": w.get("avg_steps") if w.get("steps_used") else None,
                "kcal_avg": w.get("avg_kcal"),
            }
        )
    return out


def build_weekly_rows(
    data: Dict[str, Any], *, as_of: str, store: Optional[Dict[str, Any]] = None, weeks_back: int = 8,
    phase_start_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Completed Mon-Sun rows, oldest->newest, ending with the week before ``as_of``'s week."""
    from .steps_history import daily_steps

    store = store if store is not None else {}
    card_week = last_completed_week_start(as_of)
    starts = [_iso(_d(card_week) - timedelta(days=7 * i)) for i in range(weeks_back, -1, -1)]
    all_days = [_iso(_d(starts[0]) + timedelta(days=i)) for i in range(7 * len(starts))]
    health = _health(data)
    weights = daily_bodyweight(health)
    steps = daily_steps(health)
    intake = daily_intake(data, all_days)
    weeks = [summarize_week(s, weights, steps, intake) for s in starts]
    attach_rates(weeks)
    anchor = monday_of(phase_start_date) if phase_start_date else None
    for wk in weeks:
        pw = None
        if anchor:
            n = (_d(wk["week_start"]) - _d(anchor)).days // 7 + 1
            pw = n if n >= 1 else None
        wk["phase_week"] = pw
        wk["noise"] = noise_reasons(
            wk, phase_week=pw, explicit_start=bool(anchor), tags=week_tags(store, wk["week_start"])
        )
    return weeks


# ---------------------------------------------------------------- advisory
def _schedule(rules: Optional[dict]) -> List[Dict[str, Any]]:
    try:
        from .phase_calendar import load_calendar, steps_track

        track = steps_track(rules, load_calendar(rules))
    except Exception:  # noqa: BLE001
        return []
    rows = []
    for row in (track or {}).get("schedule") or []:
        if isinstance(row, dict) and row.get("from") and row.get("step_target") is not None:
            rows.append({"from": str(row["from"])[:10], "step_target": int(row["step_target"])})
    return sorted(rows, key=lambda r: r["from"])


def next_calendar_step(as_of: str, rules: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    for row in _schedule(rules):
        if row["from"] > str(as_of)[:10]:
            return row
    return None


def _md(iso: str) -> str:
    dt = _d(iso)
    return f"{dt.month}/{dt.day}"


def steps_first_advisory(
    *, decision_rate: Optional[float], band: Sequence[float], counted: int, step_target: int,
    as_of: str, rules: Optional[dict] = None,
) -> Dict[str, Any]:
    """One lever, steps first. Never names kcal or macros. Never auto-applied."""
    base = {"lever": None, "proposed_steps": int(step_target), "applies_automatically": False}
    if counted < COUNTED_WEEKS_GATE or decision_rate is None:
        return dict(base, action="building", detail=f"{BUILDING_TEXT} ({counted}/{COUNTED_WEEKS_GATE} counted weeks)")
    lo, hi = float(band[0]), float(band[1])
    if decision_rate > hi:
        return dict(base, action="flag_to_grok", detail=FAST_TEXT)
    if decision_rate >= lo:
        return dict(base, action="change_nothing", detail="Inside the band: change nothing.")
    nxt = next_calendar_step(as_of, rules)
    if nxt is None:
        return dict(base, action="hold", detail="Below the band, no further approved step. Flag to Grok.")
    proposed = min(int(nxt["step_target"]), int(step_target) + STEP_PULL_MAX)
    detail = (
        f"Below the band: steps are the lever. Next approved step {nxt['step_target']:,}/day from {_md(nxt['from'])}; "
        f"can pull it earlier, max +{STEP_PULL_MAX:,}/day per counted week ({proposed:,}/day)."
    )
    return dict(
        base, action="steps_pull_earlier", lever="steps", proposed_steps=proposed,
        next_step={"from": nxt["from"], "step_target": nxt["step_target"]}, detail=detail,
    )


def weekly_section(
    weeks: Sequence[Dict[str, Any]], *, as_of: str, band: Sequence[float], step_target: int,
    rules: Optional[dict] = None, store: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    store = store if store is not None else {}
    cur = weeks[-1] if weeks else {}
    eligible = [w for w in weeks if w.get("counted") and not w.get("noise") and w.get("loss_pct") is not None]
    counted = len(eligible)
    ready = counted >= COUNTED_WEEKS_GATE
    last2 = eligible[-2:]
    dec_rate = round(sum(w["loss_pct"] for w in last2) / 2.0, 3) if ready else None
    tdee = estimate_tdee(weeks)
    low = int(cur.get("protein_low_days") or 0)
    advisory = steps_first_advisory(
        decision_rate=dec_rate, band=band, counted=counted, step_target=step_target, as_of=as_of, rules=rules
    )
    return {
        "status": "ready" if ready else "building",
        "text": None if ready else f"{BUILDING_TEXT} ({counted}/{COUNTED_WEEKS_GATE} counted weeks)",
        "week": {"start": cur.get("week_start"), "end": cur.get("week_end")},
        "counted": bool(cur.get("counted")),
        "counted_weeks": counted,
        "noise": list(cur.get("noise") or []),
        "days_logged": {
            "weigh_ins": f"{cur.get('weigh_ins', 0)}/7",
            "food": f"{cur.get('food_logs', 0)}/7",
            "steps": f"{cur.get('step_days', 0)}/7",
        },
        "averages": {
            "calories": {"avg": cur.get("avg_kcal"), "wow": cur.get("kcal_delta")},
            "steps": {"avg": cur.get("avg_steps"), "wow": cur.get("steps_delta"), "used": bool(cur.get("steps_used"))},
            "bodyweight": {"avg": cur.get("avg_weight"), "wow": cur.get("weight_delta")},
        },
        "rate": {
            "week_loss_pct": cur.get("loss_pct"),
            "decision_loss_pct_2wk": dec_rate,
            "band_pct": [float(band[0]), float(band[1])],
        },
        "trend_4wk": [
            {"week_start": w["week_start"], "avg_weight": w.get("avg_weight"), "loss_pct": w.get("loss_pct"),
             "counted": w.get("counted"), "noise": w.get("noise")}
            for w in list(weeks)[-4:]
        ],
        "tdee": tdee,
        "tdee_history": list(store.get("tdee_history") or [])[-8:],
        "protein_chip": {
            "show": low >= PROTEIN_GAP_DAYS,
            "days_under_170g": low,
            "label": "Protein gap" if low >= PROTEIN_GAP_DAYS else None,
        },
        "last_adjustment": store.get("last_adjustment"),
        "advisory": advisory,
        "applies_automatically": False,
    }
