"""Suggested additions for under-emphasized muscles (#956).

Flags a rolling window of logged hard sets, offers 1–3 lifts the current
inventory can load, and swaps one accessory into the matching session.
Equipment gaps are prompts only. Nothing is purchased.

Major-group floors live in fitness/exercises/suggestion_bands.json.
The planner's 4–8 band still builds the automatic session.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .equipment_store import _workspace_file_candidates, add_equipment_item
from .workout_planner import (
    classify_volume_status,
    credit_sets_for_exercise,
    days_since_last_session,
    last_performance,
    movement_feasible,
    normalize_exercise,
    prescribe,
    resolve_load_cap,
    training_continuity,
)

BANDS_PATH = "fitness/exercises/suggestion_bands.json"
GAPS_PATH = "fitness/exercises/equipment_gaps.json"
SUGGESTIONS_PER_GROUP = 3
PROMPTS_PER_GROUP = 4

_MEMORY: Dict[str, dict] = {}

ENSURE_SQL = """
CREATE TABLE IF NOT EXISTS muscle_suggestion_state (
  user_id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""


def _token(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", str(name or "").strip().lower())


def _empty_state() -> dict:
    return {
        "dismissed": {},
        "equipment_hides": {},
        "last_suggested": {},
        "pins": [],
    }


def _normalize_state(raw: Any) -> dict:
    base = _empty_state()
    if not isinstance(raw, dict):
        return base
    for key in ("dismissed", "equipment_hides", "last_suggested"):
        blob = raw.get(key)
        if isinstance(blob, dict):
            base[key] = {
                str(k): str(v)[:10]
                for k, v in blob.items()
                if str(k).strip() and str(v).strip()
            }
    pins = []
    for pin in raw.get("pins") or []:
        if not isinstance(pin, dict):
            continue
        eid = str(pin.get("exercise_id") or "").strip()
        session = str(pin.get("session_type") or "").strip().lower()
        until = str(pin.get("until") or "")[:10]
        if not eid or session not in ("push", "pull", "legs") or not until:
            continue
        pins.append(
            {
                "exercise_id": eid,
                "session_type": session,
                "until": until,
                "group_id": str(pin.get("group_id") or ""),
            }
        )
    base["pins"] = pins[:8]
    return base


def _uid(user_id: str) -> str:
    return (user_id or "").strip() or "default"


def _read_json(rel: str) -> dict:
    for path in _workspace_file_candidates(rel):
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                return data
    return {}


def load_bands() -> dict:
    return _read_json(BANDS_PATH)


def load_gaps() -> dict:
    return _read_json(GAPS_PATH)


def _parse_day(as_of: Optional[str]):
    if not as_of:
        from .timeutil import local_today_iso

        as_of = local_today_iso()
    return datetime.strptime(str(as_of)[:10], "%Y-%m-%d").date()


def _shift(day, weeks: int) -> str:
    return (day + timedelta(days=7 * int(weeks))).isoformat()


def _still_hidden(until: str, day) -> bool:
    try:
        end = datetime.strptime(str(until)[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return day < end


def _groups(bands: dict) -> List[dict]:
    out = []
    for raw in bands.get("groups") or []:
        if not isinstance(raw, dict):
            continue
        gid = str(raw.get("id") or "").strip()
        tags = [_token(t) for t in (raw.get("tags") or []) if str(t).strip()]
        if not gid or not tags:
            continue
        out.append(
            {
                "id": gid,
                "label": str(raw.get("label") or gid.replace("_", " ")),
                "size": str(raw.get("size") or "small"),
                "min": float(raw.get("min") or 0),
                "max": float(raw.get("max") or 0),
                "tags": tags,
            }
        )
    return out


def _all_exercises(catalog: dict) -> List[dict]:
    out = []
    for raw in (catalog or {}).get("exercises") or []:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(normalize_exercise(raw))
        except ValueError:
            continue
    return out


def _hit(ex: dict, group: dict) -> str:
    """primary | secondary | '' for this suggestion group.

    Side delts count an isolation tagged shoulders as direct work.
    Compound shoulder presses do not. Rear and side stay split.
    """
    prim = {_token(m) for m in (ex.get("primary_muscles") or [])}
    sec = {_token(m) for m in (ex.get("secondary_muscles") or [])}
    tags = set(group["tags"])
    if prim & tags:
        return "primary"
    if (
        group["id"] == "side_delts"
        and str(ex.get("movement") or "") == "isolation"
        and "shoulders" in prim
    ):
        return "primary"
    if sec & tags:
        return "secondary"
    if group["id"] == "side_delts" and "shoulders" in sec:
        return "secondary"
    return ""


def _lookup(exercises: Sequence[dict]) -> Tuple[Dict[str, dict], Dict[str, dict]]:
    by_id = {ex["id"]: ex for ex in exercises}
    by_name = {}
    for ex in exercises:
        by_name.setdefault(_norm_key(ex["name"]), ex)
    return by_id, by_name


def _norm_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def _match_logged(name: str, by_id: Dict[str, dict], by_name: Dict[str, dict]) -> Optional[dict]:
    from .workout_planner import match_catalog_id

    cid = match_catalog_id(str(name), by_id)
    if cid and cid in by_id:
        return by_id[cid]
    return by_name.get(_norm_key(name))


def tally_group_sets(
    sessions: Sequence[Any],
    catalog: dict,
    bands: dict,
    *,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """Weekly hard-set credits. Primary 1.0 each, secondary 0.5 each.

    A lift with two primary muscles gives each muscle the full set count.
    The same set is not split across those muscles.
    """
    day = _parse_day(as_of)
    window_days = max(7, int(bands.get("window_days") or 28))
    start = day - timedelta(days=window_days - 1)
    weeks = window_days / 7.0
    fraction = float(bands.get("secondary_fraction") if bands.get("secondary_fraction") is not None else 0.5)
    exercises = _all_exercises(catalog)
    by_id, by_name = _lookup(exercises)
    groups = _groups(bands)
    direct = {g["id"]: 0.0 for g in groups}
    extra = {g["id"]: 0.0 for g in groups}

    for sess in sessions or []:
        raw_day = getattr(sess, "date", None)
        if raw_day is None and isinstance(sess, dict):
            raw_day = sess.get("date")
        try:
            sd = datetime.strptime(str(raw_day)[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if sd < start or sd > day:
            continue
        logged = getattr(sess, "exercises", None)
        if logged is None and isinstance(sess, dict):
            logged = sess.get("exercises") or []
        for entry in logged or []:
            from .workout_planner import _working_sets_from_entry

            hard = _working_sets_from_entry(entry)
            if hard <= 0:
                continue
            name = getattr(entry, "name", None)
            if name is None and isinstance(entry, dict):
                name = entry.get("name")
            cat = _match_logged(str(name or ""), by_id, by_name)
            if not cat:
                continue
            seen_primary = set()
            seen_secondary = set()
            for group in groups:
                kind = _hit(cat, group)
                if kind == "primary" and group["id"] not in seen_primary:
                    direct[group["id"]] += float(hard)
                    seen_primary.add(group["id"])
                elif kind == "secondary" and group["id"] not in seen_secondary:
                    extra[group["id"]] += float(hard) * fraction
                    seen_secondary.add(group["id"])

    rows = []
    for group in groups:
        gid = group["id"]
        weekly = (direct[gid] + extra[gid]) / weeks
        direct_weekly = direct[gid] / weeks
        if direct[gid] <= 0:
            status = "neglected"
        elif weekly < group["min"]:
            status = "under"
        else:
            status = "ok"
        rows.append(
            {
                "id": gid,
                "label": group["label"],
                "size": group["size"],
                "min": group["min"],
                "max": group["max"],
                "sets_per_week": round(weekly, 2),
                "direct_sets_per_week": round(direct_weekly, 2),
                "status": status,
                "flagged": status != "ok",
            }
        )
    return {
        "window_days": window_days,
        "weeks": weeks,
        "as_of": day.isoformat(),
        "start": start.isoformat(),
        "end": day.isoformat(),
        "groups": rows,
    }


def additions_blocked(
    *,
    recovery_score: Optional[float] = None,
    recovery_sparse: bool = False,
    deload: bool = False,
) -> Tuple[bool, str]:
    """Deload week or recovery under 40: show the list, do not rotate."""
    if deload:
        return True, "Deload week — suggestions only. Nothing rotates in."
    if (
        recovery_score is not None
        and float(recovery_score) < 40
        and not recovery_sparse
    ):
        return True, "Recovery is under 40 — suggestions only. Nothing rotates in."
    return False, ""


def _plan_ids(plan: Optional[dict]) -> set:
    if not isinstance(plan, dict):
        return set()
    return {
        str(ex.get("id") or "")
        for ex in (plan.get("exercises") or [])
        if isinstance(ex, dict) and ex.get("id")
    }


def _suggest_for_group(
    group: dict,
    exercises: Sequence[dict],
    equipment: Optional[dict],
    state: dict,
    day,
    plan_ids: set,
) -> List[dict]:
    dismissed = state.get("dismissed") or {}
    last = str((state.get("last_suggested") or {}).get(group["id"]) or "")
    candidates = []
    for ex in exercises:
        if _hit(ex, group) != "primary":
            continue
        if not movement_feasible(ex, equipment):
            continue
        if ex["id"] in plan_ids:
            continue
        until = dismissed.get(ex["id"])
        if until and _still_hidden(until, day):
            continue
        candidates.append(ex)
    if len(candidates) > 1 and last:
        kept = [ex for ex in candidates if ex["id"] != last]
        if kept:
            candidates = kept
    candidates.sort(
        key=lambda ex: (
            0 if ex.get("available") else 1,
            0 if ex.get("movement") == "isolation" else 1,
            -int(ex.get("priority") or 0),
            str(ex.get("name") or ""),
        )
    )
    out = []
    for ex in candidates[:SUGGESTIONS_PER_GROUP]:
        out.append(
            {
                "id": ex["id"],
                "name": ex["name"],
                "session_types": list(ex.get("session_types") or []),
                "movement": ex.get("movement"),
                "primary_muscles": list(ex.get("primary_muscles") or []),
                "reason": _reason(ex, group),
            }
        )
    return out


def _reason(ex: dict, group: dict) -> str:
    gear = ", ".join(ex.get("equipment") or ex.get("equipment_any") or []) or "current access"
    kind = "Isolation" if ex.get("movement") == "isolation" else "Compound"
    return f"{kind} for {group['label'].lower()}. Loadable with {gear}."


def _prompts_for_group(
    group: dict,
    gaps: dict,
    equipment: Optional[dict],
    state: dict,
    day,
) -> List[dict]:
    from .equipment_store import owned_equipment_tags

    owned = owned_equipment_tags(equipment)
    hides = state.get("equipment_hides") or {}
    prompts = []
    for item in gaps.get("items") or []:
        if not isinstance(item, dict):
            continue
        muscles = {_token(m) for m in (item.get("muscles") or [])}
        if group["id"] not in muscles:
            continue
        tag = _token(item.get("tag") or "")
        iid = str(item.get("id") or tag)
        if not tag or tag in owned:
            continue
        until = hides.get(iid)
        if until and _still_hidden(until, day):
            continue
        unlocks = []
        for lift in item.get("unlocks") or []:
            if isinstance(lift, dict) and lift.get("name"):
                unlocks.append(
                    {"id": str(lift.get("id") or ""), "name": str(lift.get("name"))}
                )
        prompts.append(
            {
                "id": iid,
                "name": str(item.get("name") or iid),
                "tag": tag,
                "unlocks": unlocks,
            }
        )
        if len(prompts) >= PROMPTS_PER_GROUP:
            break
    return prompts


def build_report(
    sessions: Sequence[Any],
    catalog: dict,
    equipment: Optional[dict],
    *,
    state: Optional[dict] = None,
    plan: Optional[dict] = None,
    bands: Optional[dict] = None,
    gaps: Optional[dict] = None,
    as_of: Optional[str] = None,
    recovery_score: Optional[float] = None,
    recovery_sparse: bool = False,
    deload: bool = False,
) -> dict:
    bands = bands if isinstance(bands, dict) and bands.get("groups") else load_bands()
    gaps = gaps if isinstance(gaps, dict) and gaps.get("items") else load_gaps()
    state = _normalize_state(state)
    day = _parse_day(as_of)
    tally = tally_group_sets(sessions, catalog, bands, as_of=day.isoformat())
    exercises = _all_exercises(catalog)
    blocked, note = additions_blocked(
        recovery_score=recovery_score,
        recovery_sparse=recovery_sparse,
        deload=deload,
    )
    plan_ids = _plan_ids(plan)
    rows = []
    for group_row in tally["groups"]:
        group = next(g for g in _groups(bands) if g["id"] == group_row["id"])
        suggestions: List[dict] = []
        prompts: List[dict] = []
        if group_row["flagged"]:
            suggestions = _suggest_for_group(
                group, exercises, equipment, state, day, plan_ids
            )
            if not suggestions:
                prompts = _prompts_for_group(group, gaps, equipment, state, day)
        row = dict(group_row)
        row["suggestions"] = suggestions
        row["equipment_prompts"] = prompts
        rows.append(row)
    flagged = [r for r in rows if r["flagged"]]
    flagged.sort(
        key=lambda r: (
            0 if r["status"] == "neglected" else 1,
            -(float(r["min"]) - float(r["sets_per_week"])),
            r["label"],
        )
    )
    count = len(flagged)
    if count == 0:
        summary = "All muscle groups on target."
    elif count == 1:
        summary = "1 group flagged."
    else:
        summary = f"{count} groups flagged."
    return {
        "summary": summary,
        "flagged_count": count,
        "additions_allowed": not blocked,
        "additions_note": note,
        "window": {
            "days": tally["window_days"],
            "weeks": tally["weeks"],
            "start": tally["start"],
            "end": tally["end"],
            "as_of": tally["as_of"],
        },
        "groups": flagged,
    }


def _session_choice(ex: dict, requested: Optional[str]) -> str:
    types = [
        str(t).lower()
        for t in (ex.get("session_types") or [])
        if str(t).lower() in ("push", "pull", "legs")
    ]
    if not types:
        types = ["push"]
    want = str(requested or "").strip().lower()
    if want in types:
        return want
    return types[0]


def _find_gap(gaps: dict, item_id: str) -> Optional[dict]:
    want = str(item_id or "").strip()
    for item in gaps.get("items") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "") == want or _token(item.get("tag") or "") == _token(want):
            return item
    return None


def pick_swap_index(exercises: Sequence[dict], incoming: dict) -> int:
    """Replace a redundant accessory. Session length stays the same."""
    prim = {_token(m) for m in (incoming.get("primary_muscles") or [])}

    def _iso(ex: dict) -> bool:
        return str(ex.get("movement") or "") == "isolation"

    def _overlap(ex: dict) -> bool:
        theirs = {_token(m) for m in (ex.get("primary_muscles") or [])}
        return bool(prim & theirs)

    for i, ex in enumerate(exercises):
        if not isinstance(ex, dict):
            continue
        if ex.get("id") == incoming.get("id"):
            continue
        if _iso(ex) and _overlap(ex):
            return i
    for i in range(len(exercises) - 1, -1, -1):
        ex = exercises[i]
        if isinstance(ex, dict) and _iso(ex) and ex.get("id") != incoming.get("id"):
            return i
    return len(exercises) - 1


def prescribed_row(
    ex: dict,
    sessions: Sequence[Any],
    equipment: Optional[dict],
    goals: Optional[dict],
    *,
    as_of: Optional[str] = None,
    recovery_score: Optional[float] = None,
    rhr_under: bool = False,
    deload: bool = False,
) -> dict:
    """Same double-progression fields the planner writes onto a plan lift."""
    goals = goals or {}
    try:
        default_hard = max(1, min(4, int(goals.get("default_hard_sets") or 2)))
    except (TypeError, ValueError):
        default_hard = 2
    by_id = {ex["id"]: ex}
    last = last_performance(sessions or [], ex["name"], by_id)
    days = days_since_last_session(sessions or [], as_of=as_of)
    ex_rx = dict(ex)
    ex_rx["default_sets"] = default_hard
    load_cap = resolve_load_cap(ex, equipment)
    rx = prescribe(
        ex_rx,
        last,
        recovery_score=recovery_score,
        continuity=training_continuity(days if last else 0),
        default_hard_sets=default_hard,
        rhr_under_recovered=bool(rhr_under),
        equipment=equipment,
        load_cap=load_cap,
        deload=bool(deload),
    )
    hard = int(rx.get("sets") or default_hard)
    credits = credit_sets_for_exercise(
        ex.get("primary_muscles") or [],
        ex.get("secondary_muscles") or [],
        hard,
        secondary_fraction=float(goals.get("secondary_set_fraction") or 0.5),
    )
    return {
        "id": ex["id"],
        "name": ex["name"],
        "primary_muscles": ex.get("primary_muscles") or [],
        "secondary_muscles": ex.get("secondary_muscles") or [],
        "movement": ex.get("movement"),
        "equipment": ex.get("equipment") or [],
        "load": rx.get("load"),
        "rep_range": rx.get("rep_range_label"),
        "target_reps": rx.get("target_reps"),
        "progression_reason": rx.get("progression_reason"),
        "prescription": {
            "weight_lbs": rx.get("weight_lbs"),
            "load": rx.get("load"),
            "sets": rx.get("sets"),
            "reps": rx.get("reps"),
            "rep_range": rx.get("rep_range"),
            "rep_range_label": rx.get("rep_range_label"),
            "target_reps": rx.get("target_reps"),
            "progression_reason": rx.get("progression_reason"),
        },
        "set_credits": {k: round(v, 2) for k, v in credits.items()},
        "rationale": rx.get("rationale"),
        "last": rx.get("last"),
        "suggestion_add": True,
    }


def _can_swap(plan: Optional[dict], session_type: str) -> bool:
    if not isinstance(plan, dict):
        return False
    if plan.get("is_rest_day") or plan.get("already_trained_today"):
        return False
    if str(plan.get("session_type") or "").lower() != session_type:
        return False
    return bool(plan.get("exercises"))


def _shift_plan_volume(plan: dict, victim: dict, added: dict) -> None:
    """Keep Today's muscle chips aligned with the swapped lift."""
    volume = plan.get("volume") if isinstance(plan, dict) else None
    if not isinstance(volume, dict) or not isinstance(volume.get("muscles"), list):
        return
    old = {
        str(k): float(v or 0)
        for k, v in ((victim or {}).get("set_credits") or {}).items()
    }
    new = {
        str(k): float(v or 0)
        for k, v in ((added or {}).get("set_credits") or {}).items()
    }
    by = {row.get("muscle"): row for row in volume["muscles"] if isinstance(row, dict)}
    for muscle in set(old) | set(new):
        row = by.get(muscle)
        if not isinstance(row, dict):
            continue
        planned = float(row.get("planned") or 0) + new.get(muscle, 0.0) - old.get(muscle, 0.0)
        done = float(row.get("done") or 0)
        row["planned"] = round(max(0.0, planned), 2)
        row["projected"] = round(done + row["planned"], 2)
        measure = row["projected"] if row["planned"] else done
        row["status"] = classify_volume_status(
            measure, {"min": row.get("min") or 4, "max": row.get("max") or 8}
        )


def apply_pins(
    plan: Optional[dict],
    state: dict,
    catalog: dict,
    sessions: Sequence[Any],
    equipment: Optional[dict],
    goals: Optional[dict],
    *,
    as_of: Optional[str] = None,
    recovery_score: Optional[float] = None,
    rhr_under: bool = False,
) -> Tuple[dict, Optional[dict]]:
    """Swap pinned lifts into this session. Length stays the same."""
    if not isinstance(plan, dict):
        return plan or {}, None
    day = _parse_day(as_of)
    session_type = str(plan.get("session_type") or "").lower()
    if not _can_swap(plan, session_type):
        return plan, None
    exercises = _all_exercises(catalog)
    by_id = {ex["id"]: ex for ex in exercises}
    out = [dict(ex) if isinstance(ex, dict) else ex for ex in (plan.get("exercises") or [])]
    replaced = None
    changed = False
    for pin in state.get("pins") or []:
        if pin.get("session_type") != session_type:
            continue
        if not _still_hidden(pin.get("until") or "", day):
            continue
        eid = pin.get("exercise_id")
        if any(isinstance(ex, dict) and ex.get("id") == eid for ex in out):
            continue
        ex = by_id.get(eid)
        if not ex or not movement_feasible(ex, equipment):
            continue
        idx = pick_swap_index(out, ex)
        victim = out[idx] if isinstance(out[idx], dict) else {}
        added = prescribed_row(
            ex,
            sessions,
            equipment,
            goals,
            as_of=day.isoformat(),
            recovery_score=recovery_score,
            rhr_under=rhr_under,
            deload=False,
        )
        out[idx] = added
        _shift_plan_volume(plan, victim if isinstance(victim, dict) else {}, added)
        replaced = {"id": victim.get("id"), "name": victim.get("name")}
        changed = True
    if not changed:
        return plan, None
    nxt = dict(plan)
    nxt["exercises"] = out
    return nxt, replaced


def _active_pins(state: dict, day) -> List[dict]:
    kept = []
    for pin in state.get("pins") or []:
        if _still_hidden(pin.get("until") or "", day):
            kept.append(pin)
    return kept


def apply_action(
    action: str,
    body: Optional[dict],
    *,
    catalog: dict,
    equipment: Optional[dict],
    goals: Optional[dict],
    sessions: Sequence[Any],
    plan: Optional[dict],
    state: Optional[dict],
    as_of: Optional[str] = None,
    recovery_score: Optional[float] = None,
    recovery_sparse: bool = False,
    deload: bool = False,
    rhr_under: bool = False,
    bands: Optional[dict] = None,
    gaps: Optional[dict] = None,
) -> dict:
    """Dismiss, answer an equipment prompt, or pin a one-tap add."""
    body = body if isinstance(body, dict) else {}
    bands = bands if isinstance(bands, dict) and bands.get("groups") else load_bands()
    gaps = gaps if isinstance(gaps, dict) and gaps.get("items") else load_gaps()
    state = _normalize_state(state)
    day = _parse_day(as_of)
    kind = str(action or body.get("action") or "").strip().lower()
    exercises = _all_exercises(catalog)
    by_id = {ex["id"]: ex for ex in exercises}

    def _finish(message: str, *, ok: bool = True, error: str = "", plan_out=None, replaced=None, pending=False, equipment_out=None) -> dict:
        report = build_report(
            sessions,
            catalog,
            equipment_out if equipment_out is not None else equipment,
            state=state,
            plan=plan_out if plan_out is not None else plan,
            bands=bands,
            gaps=gaps,
            as_of=day.isoformat(),
            recovery_score=recovery_score,
            recovery_sparse=recovery_sparse,
            deload=deload,
        )
        return {
            "ok": ok,
            "error": error,
            "message": message,
            "muscle_suggestions": report,
            "plan": plan_out if plan_out is not None else plan,
            "state": state,
            "replaced": replaced,
            "pending": pending,
            "equipment": equipment_out,
        }

    if kind == "dismiss":
        eid = str(body.get("exercise_id") or body.get("id") or "").strip()
        if eid not in by_id:
            raise ValueError("exercise not found")
        weeks = int(bands.get("dismiss_weeks") or 4)
        state["dismissed"][eid] = _shift(day, weeks)
        group_id = str(body.get("group_id") or "")
        if group_id:
            state["last_suggested"][group_id] = eid
        state["pins"] = [p for p in state["pins"] if p.get("exercise_id") != eid]
        name = by_id[eid]["name"]
        return _finish(f"Dismissed {name} for {weeks} weeks.")

    if kind == "equipment":
        item = _find_gap(gaps, str(body.get("item_id") or body.get("id") or ""))
        if not item:
            raise ValueError("equipment prompt not found")
        choice = str(body.get("choice") or "").strip().lower()
        iid = str(item.get("id") or "")
        if choice in ("have", "yes", "owned"):
            updated = add_equipment_item(
                equipment or {"items": []},
                {
                    "id": _token(item.get("tag") or ""),
                    "name": item.get("name"),
                    "tag": item.get("tag"),
                    "source": "owned",
                    "notes": "Checked from suggested additions. Nothing was purchased.",
                },
            )
            state["equipment_hides"].pop(iid, None)
            equipment = updated
            return _finish(
                f"Added {item.get('name')} to equipment. Suggestions refreshed.",
                equipment_out=updated,
            )
        hides = bands.get("equipment_hide_weeks") or {}
        if choice in ("dont", "no", "missing"):
            weeks = int(hides.get("dont") or 8)
            state["equipment_hides"][iid] = _shift(day, weeks)
            return _finish(f"Hid {item.get('name')} for {weeks} weeks.")
        if choice in ("maybe", "later", "remind"):
            weeks = int(hides.get("maybe") or 1)
            state["equipment_hides"][iid] = _shift(day, weeks)
            return _finish(f"Will ask about {item.get('name')} again in {weeks} week.")
        raise ValueError("choice must be have, dont, or maybe")

    if kind != "add":
        raise ValueError("unknown suggestion action")

    eid = str(body.get("exercise_id") or body.get("id") or "").strip()
    ex = by_id.get(eid)
    if not ex:
        raise ValueError("exercise not found")
    if not movement_feasible(ex, equipment):
        raise ValueError("current equipment cannot load that exercise")
    blocked, note = additions_blocked(
        recovery_score=recovery_score,
        recovery_sparse=recovery_sparse,
        deload=deload,
    )
    if blocked:
        report_plan = plan
        return _finish(note, ok=False, error="additions_blocked", plan_out=report_plan)

    session_type = _session_choice(ex, body.get("session_type"))
    weeks = int(bands.get("pin_weeks") or bands.get("dismiss_weeks") or 4)
    until = _shift(day, weeks)
    group_id = str(body.get("group_id") or "")
    state["pins"] = [
        p
        for p in state["pins"]
        if not (p.get("exercise_id") == eid and p.get("session_type") == session_type)
    ]
    state["pins"].append(
        {
            "exercise_id": eid,
            "session_type": session_type,
            "until": until,
            "group_id": group_id,
        }
    )
    state["pins"] = state["pins"][-8:]
    if group_id:
        state["last_suggested"][group_id] = eid
    swapped_plan, replaced = apply_pins(
        plan,
        state,
        catalog,
        sessions,
        equipment,
        goals,
        as_of=day.isoformat(),
        recovery_score=recovery_score,
        rhr_under=rhr_under,
    )
    label = session_type.upper()
    if replaced is not None:
        victim = replaced.get("name") or "an accessory"
        message = f"Added {ex['name']} to this {label}. Swapped out {victim}."
        return _finish(message, plan_out=swapped_plan, replaced=replaced, pending=False)
    message = f"Queued {ex['name']} for the next {label}. It swaps in when that session is planned."
    return _finish(message, plan_out=plan, pending=True)


def load_state(user_id: str = "") -> dict:
    uid = _uid(user_id)
    try:
        from .turso_http import turso_enabled

        if turso_enabled():
            got = _turso_get(uid)
            if isinstance(got, dict):
                _MEMORY[uid] = _normalize_state(got)
                return deepcopy(_MEMORY[uid])
    except Exception:
        pass
    return deepcopy(_MEMORY.get(uid) or _empty_state())


def save_state(user_id: str, state: dict) -> dict:
    uid = _uid(user_id)
    norm = _normalize_state(state)
    _MEMORY[uid] = norm
    try:
        from .turso_http import turso_enabled

        if turso_enabled():
            _turso_put(uid, norm)
    except Exception:
        pass
    return deepcopy(norm)


def commit_action(user_id: str, action: str, body: Optional[dict], **kwargs) -> dict:
    """Load, apply, and store one suggestion action. Tests call apply_action directly."""
    state = load_state(user_id)
    result = apply_action(action, body, state=state, **kwargs)
    save_state(user_id, result.get("state") or state)
    return result


def commit_from_dashboard(user_id: str, body: Optional[dict], data: dict) -> dict:
    """Apply one action against a dashboard payload both servers already built."""
    from .dashboard_cache import sessions_from_dicts

    data = data if isinstance(data, dict) else {}
    store = data.get("workout_store") if isinstance(data.get("workout_store"), dict) else {}
    rec = data.get("recovery") if isinstance(data.get("recovery"), dict) else {}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    goals = store.get("goals") if isinstance(store.get("goals"), dict) else {}
    as_of = str(meta.get("local_today") or "") or None
    return commit_action(
        user_id,
        str((body or {}).get("action") or ""),
        body if isinstance(body, dict) else {},
        catalog=store.get("catalog") or {"exercises": []},
        equipment=store.get("equipment"),
        goals=goals,
        sessions=sessions_from_dicts(data.get("sessions") or []),
        plan=store.get("plan") if isinstance(store.get("plan"), dict) else {},
        as_of=as_of,
        recovery_score=rec.get("score"),
        recovery_sparse=bool(rec.get("sparse")),
        deload=bool(goals.get("deload") or goals.get("deload_week")),
        rhr_under=bool((rec.get("inputs") or {}).get("rhr_under_recovered")),
    )


def _turso_get(user_id: str) -> Optional[dict]:
    from .turso_http import connect, turso_enabled

    if not turso_enabled():
        return None
    with connect() as conn:
        conn.execute(ENSURE_SQL)
        row = conn.execute(
            "SELECT payload FROM muscle_suggestion_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    payload = row["payload"] if isinstance(row, dict) else row[0]
    try:
        data = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _turso_put(user_id: str, state: dict) -> None:
    from .turso_http import connect

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    blob = json.dumps(_normalize_state(state), separators=(",", ":"))
    with connect() as conn:
        conn.execute(ENSURE_SQL)
        conn.execute(
            """
            INSERT INTO muscle_suggestion_state(user_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              payload = excluded.payload,
              updated_at = excluded.updated_at
            """,
            (user_id, blob, now),
        )


def _deload_from_goals(goals: Optional[dict]) -> bool:
    if not isinstance(goals, dict):
        return False
    return bool(goals.get("deload") or goals.get("deload_week"))


def install_suggestions(
    workout_store: dict,
    sessions: Sequence[Any],
    *,
    user_id: str = "",
    as_of: Optional[str] = None,
    recovery_score: Optional[float] = None,
    recovery_sparse: bool = False,
    rhr_under: bool = False,
) -> dict:
    """Apply stored pins when allowed, then attach the More-tab report."""
    if not isinstance(workout_store, dict):
        return {}
    try:
        goals = workout_store.get("goals") or {}
        catalog = workout_store.get("catalog") or {"exercises": []}
        equipment = workout_store.get("equipment")
        plan = workout_store.get("plan") if isinstance(workout_store.get("plan"), dict) else {}
        state = load_state(user_id)
        day = _parse_day(as_of)
        state["pins"] = _active_pins(state, day)
        deload = _deload_from_goals(goals)
        blocked, _note = additions_blocked(
            recovery_score=recovery_score,
            recovery_sparse=recovery_sparse,
            deload=deload,
        )
        if not blocked:
            plan, _replaced = apply_pins(
                plan,
                state,
                catalog,
                sessions,
                equipment,
                goals,
                as_of=day.isoformat(),
                recovery_score=recovery_score,
                rhr_under=rhr_under,
            )
            workout_store["plan"] = plan
        report = build_report(
            sessions,
            catalog,
            equipment,
            state=state,
            plan=plan,
            as_of=day.isoformat(),
            recovery_score=recovery_score,
            recovery_sparse=recovery_sparse,
            deload=deload,
        )
        workout_store["muscle_suggestions"] = report
        return report
    except Exception as exc:  # noqa: BLE001
        workout_store["muscle_suggestions"] = {
            "summary": "Suggestions unavailable.",
            "flagged_count": 0,
            "groups": [],
            "additions_allowed": True,
            "additions_note": "",
            "error": type(exc).__name__,
        }
        return workout_store["muscle_suggestions"]


def load_program(user_id: str) -> Tuple[dict, dict, dict]:
    """Catalog universe, equipment, and goals the dashboards already merge."""
    from .custom_movements import load_custom_movements, merge_custom_universe
    from .equipment_store import load_preview_equipment
    from .library_store import apply_library_overlay, load_library_overlay
    from .workout_store import (
        apply_goals_volume_caps,
        load_workspace_catalog,
        load_workspace_goals,
    )

    goals, _src = load_workspace_goals()
    catalog, _src = load_workspace_catalog()
    custom, _src = load_custom_movements(user_id)
    catalog = merge_custom_universe(catalog, custom)
    overlay, _src = load_library_overlay(user_id)
    catalog = apply_library_overlay(catalog, overlay)
    catalog = apply_goals_volume_caps(catalog, goals)
    equipment, _src = load_preview_equipment(user_id)
    return catalog, equipment, goals
