"""Cut-rate coach. Bands come from coach_rules.json.

The fast-yellow edge is the cut band's ``hi``. The strength-trip edge
stays the rules value (0.5). A staged phase calendar changes targets
only when ``enabled`` is true and ``approved_by`` is set.
"""

from __future__ import annotations

import copy
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

from .coach_steps import (
    adherence_kcal_over,
    cut_band,
    cut_target_band,
    days_between,
    kcal_bump_size,
    kcal_cut_size,
    kcal_floor_per_lb,
    kcal_tolerance,
    load_rules,
    maintenance_band,
    parse_day,
    steps_cfg,
    strength_trip_pct,
    week_monday,
)
from .phase_calendar import (
    _advisory_offset_days,
    calendar_active,
    effective,
    kcal_track,
    last_calendar_step,
    load_calendar,
    phase_bounds,
    pre_draw_reminder,
    resolve_draw_date,
    staged_panel,
    steps_resolution,
    track_active,
)

FAST_LOSS_NOTE = "losing faster than 0.5%/wk, flagged to coach"

# Filled by ``refresh_bands``. Tests read ``BANDS["cut"]``.
BANDS: Dict[str, tuple] = {}


def refresh_bands(rules: Optional[dict] = None) -> Dict[str, tuple]:
    """Rebuild ``BANDS`` from rules. Missing keys use the 0.25–0.5 default."""
    rules = load_rules() if rules is None else rules
    built = {
        "cut": cut_band(rules),
        "cut_target": cut_target_band(rules),
        "maintenance": maintenance_band(rules),
    }
    BANDS.clear()
    BANDS.update(built)
    return BANDS


refresh_bands()


def fast_yellow(rate_pct: Any, hi: Any, *, strength_yellow: bool = False) -> bool:
    """Strength-yellow fires when the loss rate is above the band's hi."""
    if not strength_yellow:
        return False
    try:
        rate = float(rate_pct)
        edge = float(hi)
    except (TypeError, ValueError):
        return False
    return rate > edge


def _counted(weeks: Sequence[Any]) -> List[dict]:
    out = []
    for raw in weeks or []:
        if isinstance(raw, dict) and raw.get("counted", True) is not False:
            out.append(raw)
    return out


def _rate(week: dict) -> Optional[float]:
    try:
        return float(week.get("rate_pct"))
    except (TypeError, ValueError):
        return None


def _two(weeks: Sequence[Any]) -> List[dict]:
    return _counted(weeks)[-2:]


def _too_fast(weeks: Sequence[Any], hi: float) -> bool:
    rows = _two(weeks)
    if len(rows) < 2:
        return False
    rates = [_rate(w) for w in rows]
    return all(r is not None and r > hi for r in rates)


def _too_slow(weeks: Sequence[Any], lo: float) -> bool:
    rows = _two(weeks)
    if len(rows) < 2:
        return False
    rates = [_rate(w) for w in rows]
    return all(r is not None and r < lo for r in rates)


def _near_zero(weeks: Sequence[Any], n: int = 3, eps: float = 0.1) -> bool:
    rows = _counted(weeks)[-n:]
    if len(rows) < n:
        return False
    rates = [_rate(w) for w in rows]
    return all(r is not None and abs(r) < eps for r in rates)


def _fmt_pct(n: float) -> str:
    return f"{float(n):.2f}".rstrip("0").rstrip(".")


def _md(iso: Any) -> str:
    day = parse_day(iso)
    if not day:
        return str(iso or "")
    return f"{day.month}/{day.day}"


def _phase_label(phase_id: str, phase: str) -> str:
    if phase_id == "P1" or phase == "cut":
        name = "Gentle cut" if phase_id == "P1" else "Cut"
    else:
        name = "Maintenance"
    return name


def _floor_kcal(state: dict, rules: dict, phase_floor: Optional[int]) -> int:
    per_lb = kcal_floor_per_lb(rules)
    try:
        weight = float(state.get("weight_lbs") or 0)
    except (TypeError, ValueError):
        weight = 0.0
    from_weight = int(round(per_lb * weight)) if weight > 0 else 0
    if phase_floor is not None:
        return max(int(phase_floor), from_weight)
    if state.get("kcal_floor") not in (None, ""):
        try:
            return max(int(state.get("kcal_floor")), from_weight)
        except (TypeError, ValueError):
            pass
    return from_weight


def _position(rate: Optional[float], lo: float, hi: float) -> str:
    if rate is None:
        return "unknown"
    if rate < lo:
        return "below"
    if rate > hi:
        return "above"
    return "inside"


def _week_rates(weeks: Sequence[Any]) -> tuple:
    rows = _counted(weeks)
    if not rows:
        return None, None
    this = _rate(rows[-1])
    last_two = [_rate(w) for w in rows[-2:]]
    last_two = [r for r in last_two if r is not None]
    two = sum(last_two) / len(last_two) if last_two else None
    return this, two


def _adherence(weeks: Sequence[Any], step_target: int) -> Optional[float]:
    rows = _counted(weeks)
    if not rows or step_target <= 0:
        return None
    try:
        avg = float(rows[-1].get("steps_avg"))
    except (TypeError, ValueError):
        return None
    return avg / float(step_target)


def _kcal_avg(weeks: Sequence[Any]) -> Optional[float]:
    rows = _counted(weeks)
    if not rows:
        return None
    try:
        return float(rows[-1].get("kcal_avg"))
    except (TypeError, ValueError):
        return None


def _has_event(events: Sequence[Any], day: str, kind: str) -> bool:
    for raw in events or []:
        if isinstance(raw, dict) and str(raw.get("date") or "")[:10] == day and raw.get("kind") == kind:
            return True
    return False


def _deload_active(deload: Any) -> bool:
    if deload is True:
        return True
    if isinstance(deload, dict):
        return bool(deload.get("active", True))
    return False


def weekly_kcal_mean(
    as_of: Any,
    live_kcal: int,
    *,
    rules: dict,
    booking_date: Any = None,
    labs: Any = None,
) -> int:
    day = parse_day(as_of) or date.today()
    start = week_monday(day)
    active = calendar_active(rules)
    total = 0
    for i in range(7):
        current = start + timedelta(days=i)
        phase = (
            effective(current, rules=rules, booking_date=booking_date, labs=labs)
            if active
            else None
        )
        if phase and phase.get("kcal") is not None:
            total += int(phase["kcal"])
        else:
            total += int(live_kcal)
    return int(round(total / 7.0))


def kcal_indicator_for(
    as_of: Any,
    live_kcal: int,
    *,
    rules: dict,
    phase: Optional[dict] = None,
    booking_date: Any = None,
    labs: Any = None,
) -> dict:
    tol = kcal_tolerance(rules)
    kcal_on = track_active(kcal_track(rules))
    if not kcal_on:
        mean = int(live_kcal)
        phase = None
    else:
        mean = weekly_kcal_mean(
            as_of, live_kcal, rules=rules, booking_date=booking_date, labs=labs
        )
    if phase and phase.get("phase") == "maintenance":
        source = "maintenance"
    elif phase and phase.get("phase_id"):
        source = f"calendar {phase['phase_id']}"
    else:
        source = "cut"
    low = int(mean) - tol
    high = int(mean) + tol
    label = "cut" if source == "cut" else source
    return {
        "target": int(mean),
        "daily_target": int(phase["kcal"]) if phase and phase.get("kcal") is not None else int(live_kcal),
        "tolerance": tol,
        "low": low,
        "high": high,
        "weekly_mean": int(mean),
        "source": source,
        "phase_label": "cut" if source == "cut" else str((phase or {}).get("phase") or ""),
        "text": (
            f"Target {int(mean):,} ± {tol} ({low:,}–{high:,}) · {label}"
        ),
    }


def _live_band(state: dict, rules: dict) -> tuple:
    phase = str(state.get("phase") or "cut")
    if phase in ("maintain", "maintenance", "recomp"):
        return maintenance_band(rules)
    return cut_band(rules)


def decide(
    *,
    as_of: Any,
    state: Optional[dict] = None,
    rules: Optional[dict] = None,
    weeks: Optional[Sequence[dict]] = None,
    deload: Any = None,
    booking_date: Any = None,
    labs: Any = None,
    mini_cut: bool = False,
) -> dict:
    """Recommendation for one date. Does not mutate ``state``."""
    rules = copy.deepcopy(load_rules() if rules is None else rules)
    refresh_bands(rules)
    incoming = copy.deepcopy(state or {})
    out_state = copy.deepcopy(incoming)
    day = parse_day(as_of)
    day_s = day.isoformat() if day else str(as_of or "")[:10]
    weeks = list(weeks or incoming.get("weeks") or [])
    cfg = steps_cfg(rules)
    phase = effective(
        day_s, rules=rules, booking_date=booking_date, labs=labs
    )
    active = phase is not None and bool(phase.get("kcal"))
    steps_info = steps_resolution(day_s, rules=rules)
    live_kcal = int(incoming.get("kcal_target") or 0)
    live_steps = int(incoming.get("step_target") or cfg["default_target"])
    events = list(out_state.get("step_events") or [])
    labels: List[str] = []
    notes: List[str] = []
    action = "none"
    detail = ""
    steps_applied = bool(steps_info.get("applied"))
    scheduled = steps_info.get("step_target")
    past_end = bool(steps_info.get("past_end"))

    if active:
        kcal = int(phase["kcal"]) if phase.get("kcal") is not None else live_kcal
        base_steps = int(phase["step_target"]) if phase.get("step_target") is not None else live_steps
        band = tuple(phase["rate_band"]) if phase.get("rate_band") else _live_band(incoming, rules)
        target_band = tuple(phase["rate_target"]) if phase.get("rate_target") else cut_target_band(rules)
        floor = _floor_kcal(incoming, rules, phase.get("floor"))
        auto_steps = False
    else:
        kcal = live_kcal
        base_steps = live_steps
        band = _live_band(incoming, rules)
        target_band = cut_target_band(rules) if str(incoming.get("phase") or "cut") == "cut" else band
        floor = _floor_kcal(incoming, rules, None)
        auto_steps = True
    if steps_applied and scheduled is not None:
        base_steps = int(scheduled)
        if base_steps != live_steps and not _has_event(events, day_s, "calendar"):
            events.append(
                {"date": day_s, "kind": "calendar", "step_target": base_steps}
            )
        if base_steps != live_steps:
            auto_steps = False
    elif past_end and steps_info.get("carry_step") is not None:
        base_steps = int(steps_info["carry_step"])
        last_up = steps_info.get("carry_last_up")
        if not _has_event(events, day_s, "carry"):
            events.append(
                {
                    "date": day_s,
                    "kind": "carry",
                    "step_target": base_steps,
                    "last_step_up_date": last_up,
                }
            )
        if last_up:
            out_state["last_step_up_date"] = last_up
        out_state["auto_steps"] = True
        auto_steps = True

    lo, hi = float(band[0]), float(band[1])
    this_week, two_week = _week_rates(weeks)
    position = _position(this_week, lo, hi)
    trip = strength_trip_pct(rules)
    latest = _counted(weeks)[-1] if _counted(weeks) else {}
    strength_yellow = fast_yellow(
        _rate(latest) if latest else None,
        hi,
        strength_yellow=bool(latest.get("strength_yellow")),
    )
    strength_trip = bool(latest.get("strength_trip")) and (
        _rate(latest) is not None and _rate(latest) > trip
    )

    displayed_steps = base_steps
    if _deload_active(deload):
        displayed_steps = max(cfg["floor"], base_steps - cfg["deload_drop"])
        labels.append("Restore deload")

    adherence_basis = live_steps if not active else base_steps
    adherence = _adherence(weeks, adherence_basis)
    if adherence is not None and adherence < cfg["adherence_gate"]:
        labels.append("Hit the step target")

    cut_size = kcal_cut_size(rules)
    over = adherence_kcal_over(rules)
    avg = _kcal_avg(weeks)
    p1_hold = active and phase and phase.get("phase_id") == "P1" and phase.get("flags", {}).get("too_slow")

    fast_loss = (
        (this_week is not None and this_week > hi)
        or (two_week is not None and two_week > hi)
    )
    if mini_cut and active and phase and phase.get("flags", {}).get("mini_cut_allowed") is False:
        action = "mini_cut_blocked"
        detail = "Mini-cut blocked while the phase calendar is on."
    elif fast_loss or strength_yellow or strength_trip:
        # Fast loss is a flag only. It does not change kcal advice.
        if fast_loss or (this_week is not None and this_week > hi):
            labels.append(FAST_LOSS_NOTE)
            notes.append(FAST_LOSS_NOTE)
        detail = FAST_LOSS_NOTE if fast_loss else ""
    elif lo > 0 and _too_slow(weeks, lo):
        slow_lo = lo
        if p1_hold:
            if _near_zero(weeks):
                action = "accept_recomp"
                detail = "Accept as recomp"
            else:
                action = "hold_targets"
                detail = (
                    f"Below {_fmt_pct(slow_lo)}%/wk: hold {kcal:,} kcal; "
                    "hit the kcal and step targets"
                )
        elif avg is not None and avg > kcal + over:
            action = "hit_target"
            detail = f"Hit current target (avg {avg:.0f} vs {kcal:,} kcal)"
        elif kcal - cut_size < floor:
            action = "steps_plus_1500"
            detail = "Floor hit — step-up path"
            if auto_steps and _step_up_allowed(
                out_state, weeks, day, cfg, adherence, kcal_cut_same_week=False
            ):
                displayed_steps = base_steps + cfg["step_up"]
                if not _has_event(events, day_s, "auto"):
                    events.append(
                        {"date": day_s, "kind": "auto", "step_target": displayed_steps}
                    )
            else:
                action = "steps_plus_1500"
                detail = "Floor hit — step-up path"
                if not auto_steps:
                    action = "hold_targets"
                    detail = (
                        f"Below {_fmt_pct(slow_lo)}%/wk: hold {kcal:,} kcal; "
                        "hit the kcal and step targets"
                    )
        else:
            action = "kcal_minus_150"
            kcal = kcal - cut_size
            detail = f"Cut {cut_size} kcal"

    if action == "steps_plus_1500" and not auto_steps:
        action = "hold_targets"
        displayed_steps = base_steps if not _deload_active(deload) else displayed_steps

    # Calendar off must not rewrite the stored targets. The recommendation
    # can still name a change; the stored step and kcal stay put.
    steps_tracked = steps_applied or past_end
    if not steps_tracked and not active:
        out_state["kcal_target"] = live_kcal
        out_state["step_target"] = live_steps
        out_state["step_events"] = list(incoming.get("step_events") or [])
        events_out: List[dict] = []
    else:
        out_state["kcal_target"] = live_kcal
        out_state["step_target"] = int(base_steps)
        out_state["phase"] = incoming.get("phase") or "cut"
        out_state["step_events"] = events
        events_out = [e for e in events if e not in (incoming.get("step_events") or [])]

    reminder = None
    cal_file = load_calendar(rules)
    if isinstance(cal_file, dict):
        draw_info = resolve_draw_date(
            cal_file,
            rules=rules,
            booking_date=booking_date,
            labs=labs,
            use_store=False,
        )
        reminder = pre_draw_reminder(
            draw_info.get("date"), offset_days=_advisory_offset_days(cal_file)
        )

    pill = _pill(incoming, phase)
    chip = _chip(band, this_week, two_week, position, phase)
    indicator = kcal_indicator_for(
        day_s,
        live_kcal if not active else int(phase["kcal"]) if phase and phase.get("kcal") is not None else live_kcal,
        rules=rules,
        phase=phase,
        booking_date=booking_date,
        labs=labs,
    )
    # Indicator uses the weekly mean of the targets in effect, not a
    # one-day bump from a too-fast recommendation.
    card = {
        "as_of": day_s,
        "phase_pill": pill,
        "phase": (phase or {}).get("phase") or incoming.get("phase") or "cut",
        "phase_id": (phase or {}).get("phase_id"),
        "calendar_applied": bool(active),
        "auto_steps": bool(auto_steps) and not active,
        "rate": {
            "band_pct": [lo, hi],
            "target_pct": [float(target_band[0]), float(target_band[1])],
            "this_week": None if this_week is None else round(this_week, 3),
            "two_week": None if two_week is None else round(two_week, 3),
            "position": position,
        },
        "rate_chip": chip,
        "targets": {
            "kcal_target": int(kcal) if active or action in ("kcal_plus_150", "kcal_minus_150") else live_kcal,
            "step_target": int(displayed_steps if active or action == "steps_plus_1500" else live_steps),
            "kcal_floor": floor,
            "base_step_target": int(base_steps if active else live_steps),
        },
        "kcal_indicator": indicator,
        "recommendation": {"action": action, "detail": detail},
        "labels": labels,
        "notes": notes,
        "step_events": events_out,
        "macros": (phase or {}).get("macros"),
        "flags": (phase or {}).get("flags") or {},
        "mini_cut_allowed": (phase or {}).get("flags", {}).get("mini_cut_allowed", True) if active else True,
        "next_step_up_earliest": _next_step_up(out_state, cfg),
        "staged": staged_panel(rules=rules, booking_date=booking_date, labs=labs),
        "pre_draw_reminder": reminder,
        "state": {
            "kcal_target": out_state.get("kcal_target"),
            "step_target": out_state.get("step_target"),
            "last_step_up_date": out_state.get("last_step_up_date"),
            "step_events": out_state.get("step_events") or [],
            "phase": out_state.get("phase") or incoming.get("phase"),
        },
    }
    # Displayed recommendation kcal must not leak into stored state while off,
    # and the card's live targets stay the stored numbers when the calendar
    # is off even if the recommendation names a cut or a bump.
    card["targets"]["kcal_target"] = live_kcal if not active else int(
        phase.get("kcal") if phase and phase.get("kcal") is not None else live_kcal
    )
    if steps_tracked or _deload_active(deload):
        card["targets"]["step_target"] = int(displayed_steps)
        card["targets"]["base_step_target"] = int(base_steps)
    elif action == "steps_plus_1500" and auto_steps:
        card["targets"]["step_target"] = live_steps
        card["targets"]["base_step_target"] = live_steps
    else:
        card["targets"]["step_target"] = live_steps
        card["targets"]["base_step_target"] = live_steps
    card["steps_applied"] = bool(steps_applied or past_end)
    card["kcal_applied"] = bool(active)
    card["recommendation"]["proposed_kcal"] = int(live_kcal if fast_loss else kcal)
    card["recommendation"]["proposed_steps"] = int(displayed_steps)
    return card


def _step_up_allowed(
    state: dict,
    weeks: Sequence[Any],
    day: Optional[date],
    cfg: dict,
    adherence: Optional[float],
    *,
    kcal_cut_same_week: bool,
) -> bool:
    if kcal_cut_same_week:
        return False
    if len(_counted(weeks)) < int(cfg["counted_weeks"]):
        return False
    if adherence is None or adherence < float(cfg["adherence_gate"]):
        return False
    gap = days_between(state.get("last_step_up_date"), day)
    if state.get("last_step_up_date") and (gap is None or gap < int(cfg["min_spacing_days"])):
        return False
    return True


def _next_step_up(state: dict, cfg: dict) -> Optional[str]:
    last = parse_day(state.get("last_step_up_date"))
    if not last:
        return None
    return (last + timedelta(days=int(cfg["min_spacing_days"]))).isoformat()


def _pill(state: dict, phase: Optional[dict]) -> str:
    if phase and phase.get("phase_id"):
        name = _phase_label(str(phase.get("phase_id")), str(phase.get("phase") or ""))
        return (
            f"{phase['phase_id']} {name} · {_md(phase.get('start'))}–{_md(phase.get('end'))} (calendar)"
        )
    raw = str(state.get("phase") or "cut")
    label = "Cut" if raw == "cut" else "Maintenance" if raw in ("maintain", "maintenance") else raw.replace("_", " ").title()
    return f"{label} (live)"


def _chip(band: tuple, this_week: Optional[float], two_week: Optional[float], position: str, phase: Optional[dict]) -> str:
    lo, hi = float(band[0]), float(band[1])
    kind = "loss" if hi > 0 and lo >= 0 else "change"
    this_s = f"{this_week:.2f}" if this_week is not None else "—"
    two_s = f"{two_week:.2f}" if two_week is not None else "—"
    return (
        f"Band {_fmt_pct(lo)}–{_fmt_pct(hi)}%/wk {kind} · "
        f"this wk {this_s} · 2-wk {two_s} · {position}"
    )


def decision_view(card: dict) -> dict:
    """Applied coach output. The staged panel and the advisory reminder are display-only."""
    skip = {"staged", "pre_draw_reminder"}
    return {k: v for k, v in card.items() if k not in skip}


def build_card(**kwargs) -> dict:
    return decide(**kwargs)


def today_targets(card: dict) -> dict:
    """Targets a Today payload would expose. Calendar-off leaves stored values."""
    targets = card.get("targets") if isinstance(card.get("targets"), dict) else {}
    state = card.get("state") if isinstance(card.get("state"), dict) else {}
    if card.get("calendar_applied"):
        kcal = targets.get("kcal_target")
        steps = targets.get("step_target")
    else:
        kcal = state.get("kcal_target", targets.get("kcal_target"))
        steps = state.get("step_target", targets.get("step_target"))
    return {
        "kcal_target": kcal,
        "step_target": steps,
        "calendar_applied": bool(card.get("calendar_applied")),
    }


def coach_card_for_payload(payload: Optional[dict], *, as_of: Any = None) -> dict:
    """Read-only card from a dashboard payload. Never persists state."""
    data = payload if isinstance(payload, dict) else {}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    day = as_of or meta.get("local_today") or ""
    nut = data.get("nutrition_store") if isinstance(data.get("nutrition_store"), dict) else {}
    targets = nut.get("targets") if isinstance(nut.get("targets"), dict) else {}
    coach = data.get("coach") if isinstance(data.get("coach"), dict) else {}
    nt = coach.get("nutrition_targets") if isinstance(coach.get("nutrition_targets"), dict) else {}
    applied = nt.get("applied") if isinstance(nt.get("applied"), dict) else {}
    kcal = applied.get("calories", targets.get("calories"))
    try:
        kcal_i = int(round(float(kcal))) if kcal not in (None, "") else None
    except (TypeError, ValueError):
        kcal_i = None
    phase = str(applied.get("phase") or targets.get("phase") or "cut")
    rules = load_rules()
    cfg = steps_cfg(rules)
    state = {
        "kcal_target": kcal_i if kcal_i is not None else 2000,
        "step_target": cfg["default_target"],
        "phase": "cut" if phase == "cut" else phase,
        "step_events": [],
    }
    if kcal_i is None:
        state["kcal_target"] = 2000
    day_s = str(day or "2026-10-28")[:10]
    from . import coach_weekly as cw

    store = cw.load_store()
    try:
        weeks = cw.build_weekly_rows(data, as_of=day_s, store=store)
    except Exception:
        weeks = []
    card = decide(as_of=day_s, state=state, rules=rules, weeks=cw.decision_weeks(weeks))
    apply_weekly(card, weeks, rules=rules, store=store, live_kcal=int(state["kcal_target"]))
    health = data.get("health") if isinstance(data.get("health"), dict) else {}
    logs = health.get("food_logs") or []
    try:
        from .nutrition_micros import build_sat_fat_chip

        card["sat_fat_chip"] = build_sat_fat_chip(logs, as_of=card.get("as_of"), rules=rules)
    except Exception:
        card["sat_fat_chip"] = None
    return card


def apply_weekly(
    card: dict, weeks: Sequence[dict], *, rules: dict, store: dict, live_kcal: int
) -> dict:
    """Attach the weekly section and enforce the counted-week gate and steps-first rule.

    Fewer than 2 counted weeks: the card says "weekly data building" and makes
    no recommendation. Kcal track off: the only lever is steps, and the
    recommendation never names a kcal or macro change.
    """
    from . import coach_weekly as cw

    band = (card.get("rate") or {}).get("band_pct") or list(cut_band(rules))
    step = today_targets(card).get("step_target") or steps_cfg(rules)["default_target"]
    weekly = cw.weekly_section(
        weeks, as_of=card.get("as_of") or "", band=band, step_target=int(step), rules=rules, store=store
    )
    card["weekly"] = weekly
    kcal_on = track_active(kcal_track(rules))
    if weekly["status"] != "ready" or not kcal_on:
        adv = weekly["advisory"]
        card["recommendation"] = {
            "action": adv["action"],
            "detail": adv["detail"],
            "lever": adv.get("lever"),
            "proposed_kcal": int(live_kcal),
            "proposed_steps": int(adv["proposed_steps"]),
            "applies_automatically": False,
        }
        if not kcal_on:
            card["macros"] = None
    return card


def persist_weekly(card: Optional[dict]) -> bool:
    """Keep TDEE history (one row per card week). Best effort; read-only hosts skip."""
    try:
        from . import coach_weekly as cw

        weekly = (card or {}).get("weekly") or {}
        week = (weekly.get("week") or {}).get("start")
        store = cw.load_store()
        if cw.record_tdee(store, week, weekly.get("tdee")):
            cw.save_store(store)
            return True
    except Exception:
        return False
    return False


def attach_coach_card(payload: dict, *, as_of: Any = None) -> dict:
    try:
        payload["coach_card"] = coach_card_for_payload(payload, as_of=as_of)
    except Exception:
        payload["coach_card"] = None
    if payload.get("coach_card"):
        persist_weekly(payload["coach_card"])
    return payload
