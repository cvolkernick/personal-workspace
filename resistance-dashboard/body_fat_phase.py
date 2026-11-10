"""Propose a bulk/cut flip from body-fat streaks. Display only.

For each America/New_York civil day D, the trailing average is the arithmetic
mean of valid body-fat readings in [D-6, D]. That is the same window as
``static/body-fat-series.js`` (default 7 calendar days): missing days are left
out, they are not stored as 0, and two readings on one day both count.

Merge twins are collapsed first. Readings on the same civil date, with the
same percentage, whose ``recorded_at`` values differ only by a fractional
second, count once.

A day is thin when that window has fewer than 4 deduped readings or fewer
than 3 distinct days. A thin day, or a day whose average is not past the
active threshold, resets the streak to 0. In a cut, the threshold is
<= 10.0%. In a bulk (stored ``bulk`` / ``slow_bulk`` / ``gain``), it is
>= 15.0%. Fourteen consecutive qualifying days propose a flip. The proposal
is never written: this module does not switch phase or persist targets.

Phase comes from the live nutrition-targets payload (``phase``). A missing
phase is cut, which is the stored starting state.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

WINDOW_DAYS = 7
STREAK_NEEDED = 14
MIN_READINGS = 4
MIN_DAYS = 3
CUT_AT = 10.0
BULK_AT = 15.0
PROTEIN_FLOOR_G = 170
FAT_CAP_G = 65
GAIN_TARGET_PCT_WK = 0.25
GAIN_BAND_PCT_WK = (0.1, 0.35)

_FRAC_SECONDS = re.compile(r"\.(\d+)(?=(Z|[+-]\d{2}:?\d{2})?$)")


def add_days(iso: str, delta: int) -> str:
    """Civil-date shift. Calendar arithmetic, so a DST boundary cannot skip a day."""
    day = datetime.strptime(str(iso)[:10], "%Y-%m-%d")
    return (day + timedelta(days=int(delta))).strftime("%Y-%m-%d")


def _civil_day(value: Any) -> str:
    day = str(value or "")[:10]
    if len(day) == 10 and day[4] == "-" and day[7] == "-":
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            return ""
        return day
    return ""


def _round_pct(pct: float) -> float:
    """Hundredths, half away from zero for positive percentages (matches JS Math.round)."""
    return math.floor(float(pct) * 100.0 + 0.5) / 100.0


def percentage_of(row: Any) -> Optional[float]:
    """Valid logged percentage. Matches body-fat-series.js: finite and inside [0, 100]."""
    if not isinstance(row, dict):
        return None
    raw = row.get("percentage")
    if raw is None:
        raw = row.get("body_fat_pct")
    if isinstance(raw, bool) or raw is None or raw == "":
        return None
    try:
        pct = float(raw)
    except (TypeError, ValueError):
        return None
    if pct != pct or pct < 0 or pct > 100:  # NaN or outside the logged range
        return None
    return _round_pct(pct)


def _second_stamp(recorded_at: Any) -> str:
    text = str(recorded_at or "").strip()
    if not text:
        return ""
    return _FRAC_SECONDS.sub("", text)


def measurements(samples: Sequence[Any]) -> List[dict]:
    """Every valid reading. Same-day rows stay separate. Invalid rows are omitted."""
    out: List[dict] = []
    for row in samples or []:
        if not isinstance(row, dict):
            continue
        day = _civil_day(row.get("date"))
        pct = percentage_of(row)
        if not day or pct is None:
            continue
        out.append(
            {
                "date": day,
                "percentage": pct,
                "recorded_at": str(row.get("recorded_at") or ""),
            }
        )
    out.sort(key=lambda item: (item["date"], item["recorded_at"]))
    return out


def dedupe_twins(rows: Sequence[dict]) -> List[dict]:
    """Collapse same-day, same-value readings whose timestamps differ only by fractional seconds.

    A reading with no timestamp is kept. It is not treated as a twin of another
    untimestamped row, because nothing shows the stamps differ only by a fraction.
    """
    seen = set()
    out: List[dict] = []
    for row in rows or []:
        stamp = _second_stamp(row.get("recorded_at"))
        if not stamp:
            out.append(row)
            continue
        key = (row["date"], row["percentage"], stamp)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _in_window(rows: Sequence[dict], day: str, window_days: int) -> List[dict]:
    start = add_days(day, -(int(window_days) - 1))
    return [row for row in rows if start <= row["date"] <= day]


def window_stats(samples: Sequence[Any], day: str, window_days: int = WINDOW_DAYS) -> dict:
    """Mean, counts, and the thin flag for the window ending on ``day``.

    An empty window has a null average, not 0. Thin means fewer than 4 deduped
    readings or fewer than 3 distinct civil days.
    """
    civil = _civil_day(day)
    empty = {
        "avg": None,
        "n_readings": 0,
        "n_days": 0,
        "thin": True,
        "window_start": "",
        "window_end": civil,
    }
    if not civil:
        return empty
    rows = _in_window(dedupe_twins(measurements(samples)), civil, window_days)
    empty["window_start"] = add_days(civil, -(int(window_days) - 1))
    if not rows:
        return empty
    days = {row["date"] for row in rows}
    total = sum(row["percentage"] for row in rows)
    return {
        "avg": total / len(rows),
        "n_readings": len(rows),
        "n_days": len(days),
        "thin": len(rows) < MIN_READINGS or len(days) < MIN_DAYS,
        "window_start": empty["window_start"],
        "window_end": civil,
    }


def phase_kind(phase: Any) -> str:
    """``cut`` (including unset), ``bulk`` (including slow_bulk and gain), or the raw token."""
    if phase is None:
        return "cut"
    text = str(phase).strip().lower().replace("-", "_").replace(" ", "_")
    if text in ("", "none", "null"):
        return "cut"
    if text in ("bulk", "slow_bulk", "gain"):
        return "bulk"
    if text == "cut":
        return "cut"
    return text


def _meets(kind: str, avg: Optional[float]) -> bool:
    if avg is None:
        return False
    if kind == "cut":
        return avg <= CUT_AT
    if kind == "bulk":
        return avg >= BULK_AT
    return False


def streak_days(samples: Sequence[Any], as_of: str, kind: str, *, lookback: int = 120) -> int:
    """Consecutive days ending today whose 7-day window is not thin and is past the threshold.

    The walk stops at the first thin day or the first day that misses the
    threshold. That day resets the streak; older qualifying days do not carry
    through it.
    """
    day = _civil_day(as_of)
    if not day or kind not in ("cut", "bulk"):
        return 0
    count = 0
    for _ in range(max(1, int(lookback))):
        stats = window_stats(samples, day, WINDOW_DAYS)
        if stats["thin"] or not _meets(kind, stats["avg"]):
            break
        count += 1
        day = add_days(day, -1)
    return count


def _round_nearest_50(kcal: float) -> int:
    return int(round(float(kcal) / 50.0) * 50)


def _number(value: Any) -> Optional[float]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num:
        return None
    return num


def _bulk_macros(calories: int) -> dict:
    protein = PROTEIN_FLOOR_G
    fat = FAT_CAP_G
    remain = int(calories) - protein * 4 - fat * 9
    carbs = int(round(remain / 4.0))
    if carbs < 0:
        carbs = 0
    return {
        "calories": int(calories),
        "protein_g": protein,
        "fat_g": fat,
        "carbs_g": carbs,
    }


def _bulk_targets(tdee: Any) -> dict:
    """Maintenance on flip day, then TDEE+200 rounded to the nearest 50. Display only."""
    base = {
        "phase": "bulk",
        "applies_automatically": False,
        "tdee_kcal": None,
        "flip_day": None,
        "after_14d": None,
        "protein_g_floor": PROTEIN_FLOOR_G,
        "fat_g_cap": FAT_CAP_G,
        "gain_band_pct_wk": [GAIN_BAND_PCT_WK[0], GAIN_BAND_PCT_WK[1]],
        "gain_target_pct_wk": GAIN_TARGET_PCT_WK,
    }
    live = _number(tdee)
    if live is None:
        base["note"] = "TDEE estimate unavailable; calories not invented"
        return base
    tdee_i = int(round(live))
    steady = _round_nearest_50(tdee_i + 200)
    flip = _bulk_macros(tdee_i)
    flip["label"] = "maintenance (TDEE) on flip day"
    later = _bulk_macros(steady)
    later["label"] = "TDEE+200 rounded to nearest 50"
    base["tdee_kcal"] = tdee_i
    base["flip_day"] = flip
    base["after_14d"] = later
    return base


def _cut_targets(applied: Optional[dict]) -> dict:
    """Live applied cut targets. Missing fields stay missing; nothing is filled in."""
    src = applied if isinstance(applied, dict) else {}
    out: Dict[str, Any] = {
        "phase": "cut",
        "applies_automatically": False,
        "source": "nutrition_targets",
    }
    for key in ("calories", "protein_g", "carbs_g", "fat_g"):
        num = _number(src.get(key))
        if num is None:
            continue
        out[key] = int(round(num)) if key == "calories" else num
    return out


def threshold_label(kind: str) -> str:
    if kind == "cut":
        return "Bulk at <=10.0%"
    if kind == "bulk":
        return "Cut at >=15.0%"
    return "Hold"


def evaluate(
    samples: Sequence[Any],
    *,
    as_of: Any,
    phase: Any = None,
    tdee: Any = None,
    applied: Optional[dict] = None,
) -> dict:
    """Read-only status for one civil day. Does not write phase or calories."""
    kind = phase_kind(phase)
    day = _civil_day(as_of)
    today = window_stats(samples, day, WINDOW_DAYS) if day else window_stats([], "", WINDOW_DAYS)
    streak = streak_days(samples, day, kind) if day else 0
    if today["thin"]:
        status = "data_thin"
        proposed = None
    elif kind == "cut" and streak >= STREAK_NEEDED:
        status = "propose_bulk"
        proposed = _bulk_targets(tdee)
    elif kind == "bulk" and streak >= STREAK_NEEDED:
        status = "propose_cut"
        proposed = _cut_targets(applied)
    else:
        status = "hold"
        proposed = None
    return {
        "phase": kind,
        "avg_7d": today["avg"],
        "n_readings_7d": today["n_readings"],
        "n_days_7d": today["n_days"],
        "threshold_label": threshold_label(kind),
        "streak_days": streak,
        "streak_needed": STREAK_NEEDED,
        "status": status,
        "proposed_targets": proposed,
    }


def bf_phase_for_payload(payload: Optional[dict], *, as_of: Any, tdee: Any = None) -> dict:
    """Status from a dashboard payload. Phase is the nutrition-targets payload phase."""
    data = payload if isinstance(payload, dict) else {}
    nut = data.get("nutrition_store") if isinstance(data.get("nutrition_store"), dict) else {}
    targets = nut.get("targets") if isinstance(nut.get("targets"), dict) else {}
    health = data.get("health") if isinstance(data.get("health"), dict) else {}
    samples = health.get("body_fat") or []
    if tdee is None:
        card = data.get("coach_card") if isinstance(data.get("coach_card"), dict) else {}
        weekly = card.get("weekly") if isinstance(card.get("weekly"), dict) else {}
        block = weekly.get("tdee") if isinstance(weekly.get("tdee"), dict) else {}
        tdee = block.get("tdee")
    coach = data.get("coach") if isinstance(data.get("coach"), dict) else {}
    nt = coach.get("nutrition_targets") if isinstance(coach.get("nutrition_targets"), dict) else {}
    applied = nt.get("applied") if isinstance(nt.get("applied"), dict) else {}
    merged = dict(applied)
    for key, value in targets.items():
        if value is not None:
            merged[key] = value
    return evaluate(
        samples,
        as_of=as_of,
        phase=targets.get("phase"),
        tdee=tdee,
        applied=merged,
    )
