"""Trends guardrail monitor (#1026).

Derives four tiles from sessions, scale logs, and the existing phase.
No new table and no new phase store. Thresholds live on
``phase_barometer.GUARDRAIL_THRESHOLDS``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .analytics import canonical_lift_name, lift_alias_table, strength_trend
from .phase_barometer import GUARDRAIL_THRESHOLDS, canonical_phase, phase_label

_ROLE_ORDER = ("hinge", "squat", "push", "pull")
_ROLE_HINTS = {
    "hinge": ("rdl", "romanian", "deadlift", "hinge"),
    "squat": ("leg press", "squat"),
    "push": ("press", "bench"),
    "pull": ("pulldown", "pull down", "row"),
}
_ROLE_DEFAULTS = {
    "hinge": "RDL",
    "squat": "Leg Press",
    "push": "DB Flat Press",
    "pull": "Seated Cable Row",
}
_STATUS_RANK = {"red": 3, "yellow": 2, "green": 1, "insufficient": 0}


def resolve_phase(payload: Optional[dict]) -> str:
    """Canonical phase from the barometer, then nutrition targets, else cut."""
    data = payload if isinstance(payload, dict) else {}
    baro = data.get("phase_barometer") if isinstance(data.get("phase_barometer"), dict) else {}
    if baro.get("phase"):
        return canonical_phase(baro.get("phase"))
    coach = data.get("coach") if isinstance(data.get("coach"), dict) else {}
    targets = coach.get("nutrition_targets") if isinstance(coach.get("nutrition_targets"), dict) else {}
    if targets.get("phase"):
        return canonical_phase(targets.get("phase"))
    nut = data.get("nutrition_store") if isinstance(data.get("nutrition_store"), dict) else {}
    stored = nut.get("targets") if isinstance(nut.get("targets"), dict) else {}
    if stored.get("phase"):
        return canonical_phase(stored.get("phase"))
    return "cut"


def build_guardrails(payload: Optional[dict], *, as_of: Optional[str] = None) -> dict:
    """``{phase, as_of, tiles, flag}``. Insufficient history never invents a number."""
    data = payload if isinstance(payload, dict) else {}
    phase = resolve_phase(data)
    day = _as_of(data, as_of)
    end = _parse_day(day)
    sessions = data.get("sessions") if isinstance(data.get("sessions"), list) else []
    daily = _daily_tonnage(sessions)
    weights = _weight_samples(data.get("health"))
    goals = _goals(data)
    lifts = _lift_tile(sessions, goals, end)
    tonnage = _tonnage_tile(data, daily, end, phase)
    scale = _scale_tile(weights, end, phase, lifts_sliding=lifts["status"] in ("yellow", "red"))
    flag = _flag(tonnage, lifts, scale, end, daily, sessions, goals, weights, phase)
    tiles = [tonnage, lifts, scale, _flag_tile(flag)]
    return {
        "phase": phase,
        "phase_label": phase_label(phase),
        "as_of": day,
        "tiles": tiles,
        "flag": flag,
    }


def attach_guardrails(payload: dict, *, as_of: Optional[str] = None) -> dict:
    payload["guardrails"] = build_guardrails(payload, as_of=as_of)
    return payload


def guardrail_export(payload: Optional[dict], *, as_of: Optional[str] = None) -> dict:
    """Statuses and key values for the 90-day export and the agent feed."""
    full = build_guardrails(payload, as_of=as_of)
    tiles = []
    for tile in full["tiles"]:
        tiles.append(
            {
                "id": tile.get("id"),
                "status": tile.get("status"),
                "value": tile.get("value"),
                "reason": tile.get("reason"),
            }
        )
    flag = full.get("flag") if isinstance(full.get("flag"), dict) else {}
    return {
        "phase": full.get("phase"),
        "phase_label": full.get("phase_label"),
        "as_of": full.get("as_of"),
        "tiles": tiles,
        "flag": {"status": flag.get("status"), "sentence": flag.get("sentence")},
    }


def _flag_tile(flag: dict) -> dict:
    return {
        "id": "flag",
        "status": flag.get("status"),
        "value": flag.get("sentence"),
        "reason": flag.get("sentence"),
        "rule": "Green if any metric is moving the right way. Yellow if all three are flat. Red if all three stay flat for 3 weeks.",
        "series": [],
        "chips": list(flag.get("chips") or []),
        "weeks_flat": flag.get("weeks_flat"),
    }


def _tonnage_tile(payload: dict, daily: Dict[str, float], end: datetime, phase: str) -> dict:
    spec = GUARDRAIL_THRESHOLDS["tonnage"]
    bands = spec["phases"].get(phase) or spec["phases"]["cut"]
    sums = _week_sums(daily, end, 4)
    training_weeks = sum(1 for total in sums if total > 0)
    baseline = sum(sums) / 4.0 if sums else 0.0
    series = _daily_series(daily, end, 14)
    rule = _tonnage_rule(phase, bands)
    base = {
        "id": "tonnage",
        "rule": rule,
        "series": series,
        "baseline_week_lb": None,
        "baseline_daily_lb": None,
        "delta_pct": None,
        "arrow": None,
        "arrow_tone": "neutral",
        "positive": False,
        "deload_suppressed": False,
        "weeks_below_red": 0,
    }
    if training_weeks < int(spec["min_training_weeks"]) or baseline <= 0:
        return {
            **base,
            "status": "insufficient",
            "value": None,
            "reason": "Not enough data.",
        }
    pct_below = (baseline - sums[0]) / baseline * 100.0
    flat = float(spec["flat_band_pct"])
    if pct_below > flat:
        arrow = "down"
    elif pct_below < -flat:
        arrow = "up"
    else:
        arrow = "flat"
    red_above = float(bands["red_above_pct"])
    streak = 0
    for total in sums:
        below = (baseline - total) / baseline * 100.0
        if below > red_above:
            streak += 1
        else:
            break
    deload = streak >= int(spec["red_weeks"]) and _deload_in_weeks(payload, end, streak)
    status = "green"
    if streak >= int(spec["red_weeks"]):
        status = "yellow" if deload else "red"
    elif float(bands["yellow_low_pct"]) <= pct_below <= float(bands["yellow_high_pct"]) or pct_below > red_above:
        status = "yellow"
    tone = "good" if arrow == "up" else "bad" if arrow == "down" else "neutral"
    reason = _tonnage_reason(status, pct_below, bands, deload, streak)
    latest = _latest_training_day(daily, end)
    return {
        **base,
        "status": status,
        "value": None if latest is None else latest["lb"],
        "latest_training_day_lb": latest,
        "compare_lb": round(sums[0], 1),
        "reason": reason,
        "baseline_week_lb": round(baseline, 1),
        "baseline_daily_lb": round(baseline / 7.0, 2),
        "delta_pct": round(-pct_below, 2),
        "arrow": arrow,
        "arrow_tone": tone,
        "positive": arrow == "up",
        "deload_suppressed": bool(deload),
        "weeks_below_red": streak,
        "week_lb": [round(v, 1) for v in sums],
    }


def _tonnage_rule(phase: str, bands: dict) -> str:
    low = _num(bands["yellow_low_pct"])
    high = _num(bands["yellow_high_pct"])
    red = _num(bands["red_above_pct"])
    name = phase_label(phase)
    return (
        f"{name}: yellow when the week is {low}–{high}% under the 4-week baseline. "
        f"Red when it stays more than {red}% under for 2 weeks. A logged deload keeps red off."
    )


def _tonnage_reason(status: str, pct_below: float, bands: dict, deload: bool, streak: int) -> str:
    shown = _num(abs(pct_below))
    direction = "under" if pct_below >= 0 else "over"
    if status == "red":
        return (
            f"Trailing week is {shown}% {direction} the 4-week baseline "
            f"for {streak} weeks."
        )
    if deload and streak >= 2:
        return (
            f"Trailing week is {shown}% under the 4-week baseline. "
            "A logged deload is keeping this off red."
        )
    if status == "yellow" and pct_below > float(bands["red_above_pct"]):
        return (
            f"Trailing week is {shown}% under the 4-week baseline. "
            "Red needs 2 weeks under that line."
        )
    if status == "yellow":
        return (
            f"Trailing week is {shown}% {direction} the 4-week baseline. "
            f"Yellow is {_num(bands['yellow_low_pct'])}–{_num(bands['yellow_high_pct'])}% under."
        )
    if pct_below < -float(GUARDRAIL_THRESHOLDS["tonnage"]["flat_band_pct"]):
        return f"Trailing week is {shown}% over the 4-week baseline."
    return "Trailing week is in line with the 4-week baseline."


def _lift_tile(sessions: Sequence[Any], goals: dict, end: datetime) -> dict:
    spec = GUARDRAIL_THRESHOLDS["lifts"]
    aliases = goals.get("lift_aliases") if isinstance(goals.get("lift_aliases"), dict) else {}
    names = goals.get("main_lifts") if isinstance(goals.get("main_lifts"), list) else []
    lines = []
    for role, name in _role_names(names, aliases):
        series = strength_trend(sessions, name, aliases=aliases)
        lines.append(_lift_line(role, name, series, end, spec))
    known = [line for line in lines if line["status"] != "insufficient"]
    if not known:
        status = "insufficient"
        reason = "Not enough data."
    else:
        status = max(known, key=lambda line: _STATUS_RANK.get(line["status"], 0))["status"]
        if status == "red":
            reason = "A main lift has been down for 3 weeks."
        elif status == "yellow":
            reason = "A main lift has been down for 2 weeks."
        elif any(line.get("positive") for line in lines):
            reason = "A main lift moved up."
        else:
            reason = "Main-lift first sets are holding."
    return {
        "id": "lifts",
        "status": status,
        "value": None if status == "insufficient" else _lift_value(lines),
        "reason": reason,
        "rule": "Yellow after 2 weeks down. Red after 3. More reps at the same weight is not down.",
        "series": [
            {"date": point["date"], "value": point["weight_lbs"], "name": line["name"]}
            for line in lines
            for point in line.get("series") or []
        ],
        "lines": lines,
        "positive": any(bool(line.get("positive")) for line in lines),
        "arrow": "up" if any(line.get("move") == "up" for line in lines) else (
            "down" if status in ("yellow", "red") else "flat" if known else None
        ),
        "arrow_tone": "good" if any(line.get("move") == "up" for line in lines) else (
            "bad" if status in ("yellow", "red") else "neutral"
        ),
    }


def _lift_value(lines: Sequence[dict]) -> str:
    parts = []
    for line in lines:
        if line.get("label"):
            parts.append(f"{line['role']} {line['label']}")
    return " · ".join(parts)


def _lift_line(role: str, name: str, series: Sequence[dict], end: datetime, spec: dict) -> dict:
    weekly: Dict[int, dict] = {}
    spark = []
    for point in series:
        first = point.get("first_set") if isinstance(point.get("first_set"), dict) else {}
        weight = _as_float(first.get("weight_lbs"))
        reps = first.get("reps")
        day = str(point.get("date") or "")[:10]
        if weight is None or reps is None or len(day) != 10:
            continue
        try:
            reps_i = int(reps)
        except (TypeError, ValueError):
            continue
        row = {"date": day, "weight_lbs": weight, "reps": reps_i}
        spark.append(row)
        idx = _week_index(day, end)
        if idx is None or idx > 8:
            continue
        prev = weekly.get(idx)
        if prev is None or day >= prev["date"]:
            weekly[idx] = row
    latest = weekly.get(0)
    label = _set_label(latest["weight_lbs"], latest["reps"]) if latest else None
    compared = 0 in weekly and 1 in weekly
    if not compared:
        return {
            "role": role,
            "name": name,
            "status": "insufficient",
            "label": label,
            "weight_lbs": latest["weight_lbs"] if latest else None,
            "reps": latest["reps"] if latest else None,
            "weeks_down": 0,
            "move": None,
            "positive": False,
            "reason": "Not enough data.",
            "series": spark[-8:],
        }
    streak = 0
    move = _load_move(weekly[1], weekly[0])
    k = 0
    while k in weekly and (k + 1) in weekly:
        step = _load_move(weekly[k + 1], weekly[k])
        if step != "down":
            break
        streak += 1
        k += 1
    yellow_at = int(spec["yellow_weeks_down"])
    red_at = int(spec["red_weeks_down"])
    if streak >= red_at:
        status = "red"
        reason = f"{name} first set has been down for {streak} weeks."
    elif streak >= yellow_at:
        status = "yellow"
        reason = f"{name} first set has been down for {streak} weeks."
    else:
        status = "green"
        reason = f"{name} first set is {label}."
    return {
        "role": role,
        "name": name,
        "status": status,
        "label": label,
        "weight_lbs": latest["weight_lbs"],
        "reps": latest["reps"],
        "weeks_down": streak,
        "move": move,
        "positive": move == "up",
        "reason": reason,
        "series": spark[-8:],
    }


def _load_move(older: dict, newer: dict) -> str:
    """Down is a lighter first set, or fewer reps at the same weight.

    More reps at the same weight is not down and is not a load increase.
    """
    if newer["weight_lbs"] > older["weight_lbs"] + 1e-6:
        return "up"
    if newer["weight_lbs"] < older["weight_lbs"] - 1e-6:
        return "down"
    if int(newer["reps"]) < int(older["reps"]):
        return "down"
    return "flat"


def _scale_tile(weights: Sequence[Tuple[str, float]], end: datetime, phase: str, *, lifts_sliding: bool) -> dict:
    spec = GUARDRAIL_THRESHOLDS["scale"]
    bands = spec["phases"].get(phase) or spec["phases"]["maintain"]
    avgs = _week_weight_avgs(weights, end, 6)
    pcts = [_wow_pct(avgs, k) for k in range(5)]
    pct = pcts[0]
    series = _weight_series(weights, end, 14)
    rule = _scale_rule(phase, bands)
    value = round(avgs[0], 1) if avgs and avgs[0] is not None else None
    base = {
        "id": "scale",
        "rule": rule,
        "series": series,
        "weekly_pct": None,
        "arrow": None,
        "arrow_tone": "neutral",
        "positive": False,
        "band": None,
        "lifts_sliding": bool(lifts_sliding),
    }
    if pct is None:
        return {**base, "status": "insufficient", "value": value, "reason": "Not enough data."}
    arrow = "up" if pct > 0.05 else "down" if pct < -0.05 else "flat"
    tone = _scale_tone(phase, pct)
    status, reason = _scale_status(phase, pcts, bands, lifts_sliding)
    positive = _scale_positive(phase, pct, pcts[1] if len(pcts) > 1 else None)
    return {
        **base,
        "status": status,
        "value": value,
        "reason": reason,
        "weekly_pct": round(pct, 2),
        "arrow": arrow,
        "arrow_tone": tone,
        "positive": positive and status != "insufficient",
        "band": {
            "low": bands.get("target_low_pct"),
            "high": bands.get("target_high_pct"),
            "marker": round(pct, 2),
        },
    }


def _scale_rule(phase: str, bands: dict) -> str:
    name = phase_label(phase)
    if phase == "cut":
        return (
            f"{name}: target loss {_num(abs(bands['target_high_pct']))}–{_num(abs(bands['target_low_pct']))}% "
            "of body weight a week. Yellow under 0.25% or over 1.5% for 2 weeks. "
            "Red when weight stalls for 3 weeks and a main lift is sliding."
        )
    if phase == "slow_bulk":
        return (
            f"{name}: target gain {_num(bands['target_low_pct'])}–{_num(bands['target_high_pct'])}% a week. "
            "Yellow when the gain is over 0.5% or there is no gain, for 2 weeks. "
            "Red when that lasts 3 weeks."
        )
    return (
        f"{name}: hold the week inside ±{_num(bands['target_high_pct'])}%. "
        f"Yellow when the change is past ±{_num(bands['yellow_abs_pct'])}% for 2 weeks."
    )


def _scale_status(phase: str, pcts: Sequence[Optional[float]], bands: dict, lifts_sliding: bool) -> Tuple[str, str]:
    if phase == "cut":
        stall = _streak(pcts, lambda pct: pct is not None and (-pct) <= 0)
        slow_or_fast = _streak(
            pcts,
            lambda pct: pct is not None
            and ((-pct) < float(bands["yellow_loss_under_pct"]) or (-pct) > float(bands["yellow_loss_over_pct"])),
        )
        if stall >= int(bands["red_stall_weeks"]) and lifts_sliding:
            return "red", "Weight has not come down for 3 weeks and a main lift is sliding."
        if slow_or_fast >= int(bands["yellow_weeks"]):
            return "yellow", "Weekly scale change has been outside the cut band for 2 weeks."
        return "green", "Weekly scale change is inside the cut rule."
    if phase == "slow_bulk":
        bad = _streak(
            pcts,
            lambda pct: pct is not None and (pct > float(bands["yellow_gain_over_pct"]) or pct <= 0),
        )
        if bad >= int(bands["red_weeks"]):
            return "red", "Bulk gain has been too fast, or missing, for 3 weeks."
        if bad >= int(bands["yellow_weeks"]):
            return "yellow", "Bulk gain has been too fast, or missing, for 2 weeks."
        return "green", "Weekly scale change is inside the bulk rule."
    yellow_at = int(bands["yellow_weeks"])
    outside = _streak(pcts, lambda pct: pct is not None and abs(pct) > float(bands["yellow_abs_pct"]))
    if outside >= yellow_at:
        return "yellow", "Scale has moved more than 0.5% a week for 2 weeks."
    return "green", "Weekly scale change is inside the maintenance band."


def _scale_positive(phase: str, pct: Optional[float], prev: Optional[float]) -> bool:
    if pct is None:
        return False
    if phase == "cut":
        return (-pct) > float(GUARDRAIL_THRESHOLDS["scale"]["flat_band_pct"])
    if phase == "slow_bulk":
        return pct > float(GUARDRAIL_THRESHOLDS["scale"]["flat_band_pct"])
    if abs(pct) <= float(GUARDRAIL_THRESHOLDS["scale"]["flat_band_pct"]):
        return False
    if prev is None:
        return False
    return abs(pct) < abs(prev)


def _scale_tone(phase: str, pct: float) -> str:
    if phase == "cut":
        if pct < -0.05:
            return "good"
        if pct > 0.05:
            return "bad"
        return "neutral"
    if phase == "slow_bulk":
        if pct > 0.05:
            return "good"
        if pct < -0.05:
            return "bad"
        return "neutral"
    if abs(pct) <= 0.25:
        return "good"
    if abs(pct) > 0.5:
        return "bad"
    return "neutral"


def _flag(
    tonnage: dict,
    lifts: dict,
    scale: dict,
    end: datetime,
    daily: Dict[str, float],
    sessions: Sequence[Any],
    goals: dict,
    weights: Sequence[Tuple[str, float]],
    phase: str,
) -> dict:
    chips = [
        {"id": "tonnage", "label": "Tonnage", "status": tonnage.get("status")},
        {"id": "lifts", "label": "Lifts", "status": lifts.get("status")},
        {"id": "scale", "label": "Scale", "status": scale.get("status")},
    ]
    positives = []
    if tonnage.get("positive"):
        positives.append("Tonnage is up versus the 4-week baseline.")
    if lifts.get("positive"):
        positives.append("A main lift moved up.")
    if scale.get("positive"):
        positives.append("Scale is moving toward the phase target.")
    weeks_flat = _flat_weeks(tonnage, end, daily, sessions, goals, weights, phase)
    known = [chip["status"] for chip in chips if chip["status"] in ("green", "yellow", "red")]
    if positives:
        status = "green"
        sentence = " ".join(positives)
    elif len(known) < 3:
        status = "insufficient"
        sentence = "Not enough data."
    elif weeks_flat >= int(GUARDRAIL_THRESHOLDS["flag"]["flat_weeks"]):
        status = "red"
        sentence = f"All three metrics flat for {weeks_flat} weeks."
    elif weeks_flat >= 1:
        status = "yellow"
        unit = "week" if weeks_flat == 1 else "weeks"
        sentence = f"All three metrics flat for {weeks_flat} {unit}."
    else:
        status = "yellow"
        sentence = _mixed_sentence(tonnage, lifts, scale)
    return {
        "status": status,
        "sentence": sentence,
        "chips": chips,
        "weeks_flat": weeks_flat,
    }


def _mixed_sentence(tonnage: dict, lifts: dict, scale: dict) -> str:
    for tile in (tonnage, lifts, scale):
        if tile.get("status") in ("yellow", "red") and tile.get("reason"):
            return str(tile["reason"])
    return "Not progressing."


def _flat_weeks(
    tonnage: dict,
    end: datetime,
    daily: Dict[str, float],
    sessions: Sequence[Any],
    goals: dict,
    weights: Sequence[Tuple[str, float]],
    phase: str,
) -> int:
    del phase
    baseline = tonnage.get("baseline_week_lb")
    sums = _week_sums(daily, end, 6)
    aliases = goals.get("lift_aliases") if isinstance(goals.get("lift_aliases"), dict) else {}
    names = goals.get("main_lifts") if isinstance(goals.get("main_lifts"), list) else []
    role_weekly = []
    for _role, name in _role_names(names, aliases):
        series = strength_trend(sessions, name, aliases=aliases)
        weekly: Dict[int, dict] = {}
        for point in series:
            first = point.get("first_set") if isinstance(point.get("first_set"), dict) else {}
            weight = _as_float(first.get("weight_lbs"))
            reps = first.get("reps")
            day = str(point.get("date") or "")[:10]
            if weight is None or reps is None or _week_index(day, end) is None:
                continue
            try:
                reps_i = int(reps)
            except (TypeError, ValueError):
                continue
            idx = _week_index(day, end)
            if idx is None:
                continue
            prev = weekly.get(idx)
            if prev is None or day >= prev["date"]:
                weekly[idx] = {"weight_lbs": weight, "reps": reps_i, "date": day}
        role_weekly.append(weekly)
    avgs = _week_weight_avgs(weights, end, 6)
    flat_pct = float(GUARDRAIL_THRESHOLDS["tonnage"]["flat_band_pct"])
    scale_flat = float(GUARDRAIL_THRESHOLDS["scale"]["flat_band_pct"])
    count = 0
    for k in range(5):
        if not _tonnage_flat(baseline, sums, k, flat_pct):
            break
        if not _lifts_flat(role_weekly, k):
            break
        pct = _wow_pct(avgs, k)
        if pct is None or abs(pct) > scale_flat:
            break
        count += 1
    return count


def _tonnage_flat(baseline: Any, sums: Sequence[float], week: int, band: float) -> bool:
    try:
        base = float(baseline)
    except (TypeError, ValueError):
        return False
    if base <= 0 or week >= len(sums):
        return False
    below = (base - sums[week]) / base * 100.0
    return abs(below) <= band


def _lifts_flat(role_weekly: Sequence[Dict[int, dict]], week: int) -> bool:
    compared = 0
    for weekly in role_weekly:
        if week not in weekly or (week + 1) not in weekly:
            continue
        compared += 1
        if _load_move(weekly[week + 1], weekly[week]) != "flat":
            return False
    return compared > 0


def _role_names(main_lifts: Sequence[Any], aliases: dict) -> List[Tuple[str, str]]:
    table = lift_alias_table(aliases)
    chosen: Dict[str, str] = {}
    for raw in main_lifts or []:
        name = canonical_lift_name(str(raw), table)
        key = name.lower()
        if not key:
            continue
        for role in _ROLE_ORDER:
            if role in chosen:
                continue
            if any(hint in key for hint in _ROLE_HINTS[role]):
                chosen[role] = name
                break
    for role in _ROLE_ORDER:
        chosen.setdefault(role, _ROLE_DEFAULTS[role])
    return [(role, chosen[role]) for role in _ROLE_ORDER]


def _goals(payload: dict) -> dict:
    store = payload.get("workout_store") if isinstance(payload.get("workout_store"), dict) else {}
    goals = dict(store.get("goals") or {}) if isinstance(store.get("goals"), dict) else {}
    if not isinstance(goals.get("main_lifts"), list) and isinstance(payload.get("main_lifts"), list):
        goals["main_lifts"] = payload.get("main_lifts")
    if not isinstance(goals.get("lift_aliases"), dict) and isinstance(payload.get("lift_aliases"), dict):
        goals["lift_aliases"] = payload.get("lift_aliases")
    if isinstance(goals.get("main_lifts"), list) and goals.get("main_lifts"):
        return goals
    try:
        from .workout_store import load_workspace_goals

        loaded, _src = load_workspace_goals()
    except Exception:  # noqa: BLE001
        loaded = {}
    if isinstance(loaded, dict):
        merged = dict(loaded)
        merged.update({k: v for k, v in goals.items() if v not in (None, [], {})})
        return merged
    return goals


def _deload_in_weeks(payload: dict, end: datetime, weeks: int) -> bool:
    if weeks <= 0:
        return False
    window = {
        (end - timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(weeks * 7)
    }
    return bool(_deload_dates(payload, end) & window)


def _deload_dates(payload: dict, end: datetime) -> set:
    """Restore / goals deload, deload_override, continuity phase deload, or a logged deload note."""
    dates = set()
    today = end.strftime("%Y-%m-%d")
    for session in payload.get("sessions") or []:
        day = _session_day(session)
        if len(day) != 10:
            continue
        notes = _session_text(session, "notes")
        kind = _session_text(session, "session_type")
        flagged = False
        if isinstance(session, dict):
            flagged = bool(session.get("deload"))
        else:
            flagged = bool(getattr(session, "deload", False))
        if flagged or kind.strip().lower() == "deload" or "deload" in notes.lower():
            dates.add(day)
    store = payload.get("workout_store") if isinstance(payload.get("workout_store"), dict) else {}
    goals = store.get("goals") if isinstance(store.get("goals"), dict) else {}
    if goals.get("deload") or goals.get("deload_week"):
        dates.add(today)
    plan = store.get("plan") if isinstance(store.get("plan"), dict) else {}
    if plan.get("deload"):
        dates.add(today)
    cont = plan.get("training_continuity") if isinstance(plan.get("training_continuity"), dict) else {}
    if str(cont.get("phase") or "").strip().lower() == "deload":
        dates.add(today)
    for ex in plan.get("exercises") or []:
        if isinstance(ex, dict) and str(ex.get("progression_reason") or "") == "deload_override":
            dates.add(today)
            break
    return dates


def _session_day(session: Any) -> str:
    if isinstance(session, dict):
        return str(session.get("date") or "")[:10]
    return str(getattr(session, "date", "") or "")[:10]


def _session_text(session: Any, key: str) -> str:
    if isinstance(session, dict):
        return str(session.get(key) or "")
    return str(getattr(session, key, "") or "")


def _session_volume(session: Any) -> float:
    if isinstance(session, dict):
        if session.get("volume") is not None:
            try:
                return float(session["volume"])
            except (TypeError, ValueError):
                return 0.0
        total = 0.0
        for ex in session.get("exercises") or []:
            if not isinstance(ex, dict):
                continue
            if ex.get("volume") is not None:
                try:
                    total += float(ex["volume"])
                    continue
                except (TypeError, ValueError):
                    pass
            for st in ex.get("sets") or []:
                if not isinstance(st, dict):
                    continue
                try:
                    total += float(st.get("weight_lbs") or 0) * int(st.get("sets") or 0) * int(st.get("reps") or 0)
                except (TypeError, ValueError):
                    continue
        return total
    try:
        return float(getattr(session, "volume", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _daily_tonnage(sessions: Sequence[Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for session in sessions or []:
        day = _session_day(session)
        if len(day) != 10:
            continue
        out[day] = out.get(day, 0.0) + _session_volume(session)
    return out


def _weight_samples(health: Any) -> List[Tuple[str, float]]:
    if health is None:
        return []
    if isinstance(health, dict):
        rows = health.get("weight") or []
    else:
        rows = getattr(health, "weight", None) or []
    out = []
    for row in rows:
        if isinstance(row, dict):
            day = str(row.get("date") or "")[:10]
            lbs = _as_float(row.get("weight_lbs") if row.get("weight_lbs") is not None else row.get("lbs"))
        else:
            day = str(getattr(row, "date", "") or "")[:10]
            lbs = _as_float(getattr(row, "weight_lbs", None))
        if lbs is None or len(day) != 10 or lbs <= 0:
            continue
        out.append((day, lbs))
    return out


def _week_sums(daily: Dict[str, float], end: datetime, weeks: int) -> List[float]:
    sums = [0.0] * weeks
    for offset in range(weeks * 7):
        day = (end - timedelta(days=offset)).strftime("%Y-%m-%d")
        sums[offset // 7] += float(daily.get(day, 0.0))
    return sums


def _week_weight_avgs(samples: Sequence[Tuple[str, float]], end: datetime, weeks: int) -> List[Optional[float]]:
    buckets: List[List[float]] = [[] for _ in range(weeks)]
    for day, lbs in samples:
        idx = _week_index(day, end)
        if idx is None or idx >= weeks:
            continue
        buckets[idx].append(lbs)
    return [(sum(bucket) / len(bucket)) if bucket else None for bucket in buckets]


def _wow_pct(avgs: Sequence[Optional[float]], week: int) -> Optional[float]:
    if week + 1 >= len(avgs):
        return None
    cur = avgs[week]
    prev = avgs[week + 1]
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / prev * 100.0


def _daily_series(daily: Dict[str, float], end: datetime, days: int) -> List[dict]:
    rows = []
    for offset in range(days - 1, -1, -1):
        day = (end - timedelta(days=offset)).strftime("%Y-%m-%d")
        rows.append({"date": day, "value": round(float(daily.get(day, 0.0)), 1)})
    return rows


def _weight_series(samples: Sequence[Tuple[str, float]], end: datetime, days: int) -> List[dict]:
    by_day = {day: lbs for day, lbs in samples}
    rows = []
    for offset in range(days - 1, -1, -1):
        day = (end - timedelta(days=offset)).strftime("%Y-%m-%d")
        if day not in by_day:
            continue
        rows.append({"date": day, "value": round(by_day[day], 2)})
    return rows


def _latest_training_day(daily: Dict[str, float], end: datetime) -> Optional[dict]:
    best = None
    for day, total in daily.items():
        if total <= 0 or _parse_day(day) is None or _parse_day(day) > end:
            continue
        if best is None or day > best["date"]:
            best = {"date": day, "lb": round(total, 1)}
    return best


def _week_index(day: str, end: datetime) -> Optional[int]:
    parsed = _parse_day(day)
    if parsed is None or parsed > end:
        return None
    return (end - parsed).days // 7


def _streak(pcts: Sequence[Optional[float]], pred) -> int:
    count = 0
    for pct in pcts:
        if pred(pct):
            count += 1
        else:
            break
    return count


def _parse_day(value: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _as_of(payload: dict, as_of: Optional[str]) -> str:
    if as_of and _parse_day(as_of):
        return str(as_of)[:10]
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    coach = payload.get("coach") if isinstance(payload.get("coach"), dict) else {}
    today = coach.get("today") if isinstance(coach.get("today"), dict) else {}
    raw = meta.get("local_today") or today.get("date") or ""
    if _parse_day(raw):
        return str(raw)[:10]
    from .timeutil import local_today_iso

    return local_today_iso()


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num:
        return None
    return num


def _set_label(weight: float, reps: int) -> str:
    shown = int(weight) if abs(weight - round(weight)) < 1e-9 else round(weight, 1)
    return f"{shown}×{int(reps)}"


def _num(value: float) -> str:
    num = float(value)
    if abs(num - round(num)) < 1e-9:
        return str(int(round(num)))
    return f"{num:.2f}".rstrip("0").rstrip(".")
