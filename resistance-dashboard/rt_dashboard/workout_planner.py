"""Exercise catalog + daily workout plan generation (mirror of meal planner)."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import Session

CATALOG_PATH = "fitness/exercises/catalog.json"
GOALS_PATH = "fitness/exercises/goals.json"

DEFAULT_GOALS = {
    "split": "ppl",
    "rotation": ["push", "pull", "legs"],
    "goal": "strength_hypertrophy",
    "sessions_per_week_target": 5,
    "exercises_per_session": 5,
    "prefer_compounds_first": True,
    "progression": "double_progression",
    "notes": "",
    "focus_muscles": [],
    "rest_if_recovery_below": 40,
    "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
}

DEFAULT_CATALOG = {"exercises": [], "updated_at": "", "notes": ""}

# Map messy log names → catalog ids (lowercase keys)
NAME_ALIASES = {
    "db flat press": "db-flat-press",
    "db incline press": "db-incline-press",
    "db shoulder press": "db-shoulder-press",
    "lateral raises": "lateral-raises",
    "lateral raise": "lateral-raises",
    "tricep pushdowns": "tricep-pushdowns",
    "tricep pushdown": "tricep-pushdowns",
    "seated cable row": "seated-cable-row",
    "pulldowns": "pulldowns",
    "pull downs": "pulldowns",
    "assisted pullups": "assisted-pullups",
    "assisted pull-ups": "assisted-pullups",
    "machine row": "machine-row",
    "face pulls": "face-pulls",
    "db curls": "db-curls",
    "hammer curls": "hammer-curls",
    "leg press": "leg-press",
    "rdl": "rdl",
    "rdls": "rdl",
    "seated leg curls": "seated-leg-curls",
    "calf raises": "calf-raises",
    "back extension machine": "back-extension",
    "smith bench": "smith-bench",
    "smith shrugs": "smith-shrugs",
}


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "exercise"


def load_json_file(path: Path, default: dict) -> dict:
    if not path.is_file():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return deepcopy(default)


def save_json_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def default_catalog_path() -> Path:
    return Path(__file__).resolve().parents[2] / CATALOG_PATH


def default_goals_path() -> Path:
    return Path(__file__).resolve().parents[2] / GOALS_PATH


def default_catalog() -> dict:
    p = default_catalog_path()
    if p.is_file():
        return load_json_file(p, DEFAULT_CATALOG)
    return deepcopy(DEFAULT_CATALOG)


def default_goals() -> dict:
    p = default_goals_path()
    if p.is_file():
        return normalize_goals(load_json_file(p, DEFAULT_GOALS))
    return normalize_goals(DEFAULT_GOALS)


def normalize_goals(raw: Optional[dict]) -> dict:
    g = deepcopy(DEFAULT_GOALS)
    if not raw:
        return g
    if raw.get("split"):
        g["split"] = str(raw["split"])
    if isinstance(raw.get("rotation"), list) and raw["rotation"]:
        g["rotation"] = [str(x).lower() for x in raw["rotation"]]
    if raw.get("goal"):
        g["goal"] = str(raw["goal"])
    for k in ("sessions_per_week_target", "exercises_per_session", "rest_if_recovery_below"):
        if raw.get(k) is not None:
            try:
                g[k] = int(raw[k])
            except (TypeError, ValueError):
                pass
    if "prefer_compounds_first" in raw:
        g["prefer_compounds_first"] = bool(raw["prefer_compounds_first"])
    if raw.get("progression"):
        g["progression"] = str(raw["progression"])
    if raw.get("notes") is not None:
        g["notes"] = str(raw["notes"])
    if isinstance(raw.get("focus_muscles"), list):
        g["focus_muscles"] = [str(x) for x in raw["focus_muscles"]]
    if raw.get("updated_at"):
        g["updated_at"] = str(raw["updated_at"])
    return g


def normalize_exercise(raw: dict) -> dict:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("exercise name required")
    eid = str(raw.get("id") or _slug(name)).strip()
    session_types = raw.get("session_types") or []
    if isinstance(session_types, str):
        session_types = [session_types]
    primary = raw.get("primary_muscles") or []
    secondary = raw.get("secondary_muscles") or []
    if isinstance(primary, str):
        primary = [primary]
    if isinstance(secondary, str):
        secondary = [secondary]
    rep_range = raw.get("rep_range") or [8, 12]
    if not isinstance(rep_range, list) or len(rep_range) < 2:
        rep_range = [8, 12]
    return {
        "id": eid,
        "name": name,
        "session_types": [str(s).lower() for s in session_types] or ["other"],
        "primary_muscles": [str(m).lower() for m in primary],
        "secondary_muscles": [str(m).lower() for m in secondary],
        "movement": str(raw.get("movement") or "compound").lower(),
        "equipment": list(raw.get("equipment") or []),
        "default_sets": int(raw.get("default_sets") or 3),
        "default_reps": int(raw.get("default_reps") or 10),
        "rep_range": [int(rep_range[0]), int(rep_range[1])],
        "priority": int(raw.get("priority") or 5),
        "available": bool(raw.get("available", True)),
        "notes": str(raw.get("notes") or ""),
    }


def available_exercises(catalog: dict) -> List[dict]:
    out = []
    for raw in catalog.get("exercises") or []:
        if not isinstance(raw, dict):
            continue
        try:
            ex = normalize_exercise(raw)
        except ValueError:
            continue
        if ex["available"]:
            out.append(ex)
    return out


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def match_catalog_id(exercise_name: str, catalog_by_id: Dict[str, dict]) -> Optional[str]:
    key = _norm_name(exercise_name)
    if key in NAME_ALIASES and NAME_ALIASES[key] in catalog_by_id:
        return NAME_ALIASES[key]
    # direct id
    slug = _slug(exercise_name)
    if slug in catalog_by_id:
        return slug
    # name match
    for eid, ex in catalog_by_id.items():
        if _norm_name(ex["name"]) == key:
            return eid
    # fuzzy contains
    for eid, ex in catalog_by_id.items():
        n = _norm_name(ex["name"])
        if key in n or n in key:
            return eid
    return None


def last_performance(
    sessions: Sequence[Session], exercise_name: str
) -> Optional[dict]:
    """Most recent logged sets for an exercise (by name, case-insensitive)."""
    target = _norm_name(exercise_name)
    # also try alias reverse: if name maps to catalog, match any alias names
    ordered = sorted(sessions, key=lambda s: s.date, reverse=True)
    for s in ordered:
        for ex in s.exercises:
            if _norm_name(ex.name) == target or target in _norm_name(ex.name):
                if not ex.sets:
                    continue
                best_w = max(st.weight_lbs for st in ex.sets)
                # representative working set: highest weight, then its sets/reps
                top = max(ex.sets, key=lambda st: (st.weight_lbs, st.reps, st.sets))
                total_sets = sum(st.sets for st in ex.sets)
                return {
                    "date": s.date,
                    "session_type": s.session_type,
                    "weight_lbs": float(top.weight_lbs),
                    "sets": int(total_sets) if total_sets else int(top.sets),
                    "reps": int(top.reps),
                    "best_working_weight": float(best_w),
                    "volume": float(ex.volume),
                    "is_pr": bool(ex.is_pr),
                }
    return None


def last_session_type(sessions: Sequence[Session]) -> Optional[str]:
    ordered = sorted(
        [s for s in sessions if s.session_type in ("push", "pull", "legs")],
        key=lambda s: s.date,
        reverse=True,
    )
    if not ordered:
        return None
    return ordered[0].session_type.lower()


def next_session_type(sessions: Sequence[Session], goals: dict) -> str:
    rotation = goals.get("rotation") or ["push", "pull", "legs"]
    rotation = [str(r).lower() for r in rotation]
    last = last_session_type(sessions)
    if not last or last not in rotation:
        return rotation[0]
    idx = rotation.index(last)
    return rotation[(idx + 1) % len(rotation)]


def days_since_last_session(sessions: Sequence[Session], as_of: Optional[str] = None) -> Optional[int]:
    if not sessions:
        return None
    day = as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ordered = sorted(sessions, key=lambda s: s.date, reverse=True)
    try:
        last = datetime.strptime(ordered[0].date, "%Y-%m-%d")
        today = datetime.strptime(day, "%Y-%m-%d")
        return max(0, (today - last).days)
    except ValueError:
        return None


def prescribe(
    catalog_ex: dict,
    last: Optional[dict],
    *,
    recovery_score: Optional[float] = None,
) -> dict:
    """Double-progression style prescription from last logged set."""
    lo, hi = catalog_ex["rep_range"]
    sets = int(catalog_ex["default_sets"])
    reps = int(catalog_ex["default_reps"])
    weight: Optional[float] = None
    rationale = "Default starter prescription (no history for this lift)."

    if last:
        weight = float(last["weight_lbs"])
        sets = int(last.get("sets") or sets)
        reps = int(last.get("reps") or reps)
        # Cap sets to reasonable
        sets = max(2, min(5, sets))
        if reps >= hi:
            # progress load
            bump = 5.0 if weight >= 40 else 2.5
            weight = weight + bump
            reps = lo
            rationale = (
                f"Hit top of range ({hi}+) last time on {last['date']} @ "
                f"{last['weight_lbs']} lb → +{bump:g} lb, reset to {lo} reps."
            )
        elif reps < lo:
            rationale = (
                f"Below range last time ({reps} reps @ {last['weight_lbs']} lb on "
                f"{last['date']}) → hold weight, aim for {lo}–{hi}."
            )
            reps = lo
        else:
            # stay weight, nudge reps up
            target_reps = min(hi, reps + 1)
            rationale = (
                f"Last: {last['weight_lbs']} lb × {last.get('sets')} × {last['reps']} "
                f"on {last['date']} → same weight, push toward {target_reps} reps "
                f"(range {lo}–{hi})."
            )
            reps = target_reps

    if recovery_score is not None and recovery_score < 50 and weight is not None:
        weight = round(weight * 0.9, 1)
        rationale += " Recovery moderate/low → ~10% load deload."

    return {
        "weight_lbs": weight,
        "sets": sets,
        "reps": reps,
        "rep_range": [lo, hi],
        "rationale": rationale,
        "last": last,
    }


def generate_workout_plan(
    catalog: dict,
    goals: dict,
    sessions: Sequence[Session],
    *,
    recovery_label: Optional[str] = None,
    recovery_score: Optional[float] = None,
    session_type: Optional[str] = None,
    as_of: Optional[str] = None,
) -> dict:
    """
    Build today's workout from catalog + history + recovery.

    Similar role to generate_meal_plan for nutrition.
    """
    goals = normalize_goals(goals)
    day = as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    available = available_exercises(catalog)
    by_id = {ex["id"]: ex for ex in available}

    rest_threshold = int(goals.get("rest_if_recovery_below") or 40)
    if recovery_score is not None and recovery_score < rest_threshold:
        return {
            "date": day,
            "session_type": "rest",
            "is_rest_day": True,
            "exercises": [],
            "message": (
                f"Recovery score {recovery_score:.0f} is below threshold "
                f"({rest_threshold}). Suggested rest or light walk/mobility only."
            ),
            "goals": goals,
            "context": {
                "recovery_label": recovery_label,
                "recovery_score": recovery_score,
                "last_session_type": last_session_type(sessions),
                "days_since_last": days_since_last_session(sessions, as_of=day),
            },
        }

    st = (session_type or next_session_type(sessions, goals)).lower()
    pool = [ex for ex in available if st in ex["session_types"]]
    if not pool:
        pool = list(available)

    # Prefer compounds first, then priority
    if goals.get("prefer_compounds_first", True):
        pool.sort(
            key=lambda e: (
                0 if e["movement"] == "compound" else 1,
                -int(e["priority"]),
                e["name"],
            )
        )
    else:
        pool.sort(key=lambda e: (-int(e["priority"]), e["name"]))

    # Focus muscles boost
    focus = {m.lower() for m in (goals.get("focus_muscles") or [])}

    def focus_boost(ex: dict) -> int:
        if not focus:
            return 0
        muscles = set(ex["primary_muscles"]) | set(ex["secondary_muscles"])
        return len(muscles & focus)

    if focus:
        pool.sort(
            key=lambda e: (
                -focus_boost(e),
                0 if e["movement"] == "compound" else 1,
                -int(e["priority"]),
            )
        )

    n = max(3, min(8, int(goals.get("exercises_per_session") or 5)))
    # Ensure at least one compound if possible
    chosen: List[dict] = []
    compounds = [e for e in pool if e["movement"] == "compound"]
    isolations = [e for e in pool if e["movement"] != "compound"]
    for e in compounds[: max(2, n - 2)]:
        chosen.append(e)
    for e in isolations:
        if len(chosen) >= n:
            break
        if e["id"] not in {c["id"] for c in chosen}:
            chosen.append(e)
    # fill if short
    for e in pool:
        if len(chosen) >= n:
            break
        if e["id"] not in {c["id"] for c in chosen}:
            chosen.append(e)

    plan_ex: List[dict] = []
    for ex in chosen[:n]:
        # history: try catalog name + any alias that maps to this id
        last = last_performance(sessions, ex["name"])
        if not last:
            for alias, aid in NAME_ALIASES.items():
                if aid == ex["id"]:
                    last = last_performance(sessions, alias)
                    if last:
                        break
        rx = prescribe(ex, last, recovery_score=recovery_score)
        plan_ex.append(
            {
                "id": ex["id"],
                "name": ex["name"],
                "primary_muscles": ex["primary_muscles"],
                "secondary_muscles": ex["secondary_muscles"],
                "movement": ex["movement"],
                "equipment": ex["equipment"],
                "prescription": {
                    "weight_lbs": rx["weight_lbs"],
                    "sets": rx["sets"],
                    "reps": rx["reps"],
                    "rep_range": rx["rep_range"],
                },
                "rationale": rx["rationale"],
                "last": rx["last"],
            }
        )

    last_st = last_session_type(sessions)
    days = days_since_last_session(sessions, as_of=day)
    msg_parts = [f"Suggested {st.upper()} session ({len(plan_ex)} exercises)."]
    if last_st:
        msg_parts.append(f"Last trained: {last_st}")
    if days is not None:
        msg_parts.append(f"{days}d since last log")
    if recovery_label:
        msg_parts.append(f"Recovery: {recovery_label}")

    return {
        "date": day,
        "session_type": st,
        "is_rest_day": False,
        "exercises": plan_ex,
        "message": " · ".join(msg_parts),
        "goals": goals,
        "context": {
            "recovery_label": recovery_label,
            "recovery_score": recovery_score,
            "last_session_type": last_st,
            "days_since_last": days,
            "catalog_available": len(available),
            "pool_for_session": len(pool),
        },
    }


def update_goals(raw: dict) -> dict:
    g = normalize_goals(raw)
    g["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return g


def add_or_update_exercise(catalog: dict, raw: dict) -> dict:
    cat = deepcopy(catalog) if catalog else {"exercises": []}
    ex = normalize_exercise(raw)
    items = list(cat.get("exercises") or [])
    replaced = False
    for i, existing in enumerate(items):
        if not isinstance(existing, dict):
            continue
        if str(existing.get("id")) == ex["id"] or _norm_name(
            str(existing.get("name") or "")
        ) == _norm_name(ex["name"]):
            items[i] = ex
            replaced = True
            break
    if not replaced:
        items.append(ex)
    cat["exercises"] = items
    cat["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return cat


def set_exercise_available(catalog: dict, exercise_id: str, available: bool) -> dict:
    cat = deepcopy(catalog) if catalog else {"exercises": []}
    for ex in cat.get("exercises") or []:
        if isinstance(ex, dict) and str(ex.get("id")) == exercise_id:
            ex["available"] = bool(available)
    cat["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return cat
