"""Exercise catalog + daily workout plan generation (mirror of meal planner).

Volume framework (Dean Turner / DeanTTraining — balanced hypertrophy):
  - You do **not** need 10–20 working sets per muscle per week.
  - Aim roughly **4–8 hard sets per major muscle group per week**, counting
    compound **overlap** (e.g. RDL credits hams + glutes).
  - Heavy priority on 1–2 muscles is fine; others drop toward a maintenance dose.
  - Productive work is capped per session and per microcycle — high per-muscle
    volume crowds out the rest of the body.
  Source framing: https://x.com/DeanTTraining/status/2081501543510028437
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import Session

CATALOG_PATH = "fitness/exercises/catalog.json"
GOALS_PATH = "fitness/exercises/goals.json"
EQUIPMENT_PATH = "fitness/exercises/equipment.json"

# Implements that carry a load (DB max/hand, bar + plates, cable/machine stack).
LOAD_EQUIPMENT_TAGS = frozenset(
    {
        "dumbbells",
        "barbell",
        "cable",
        "machine",
        "smith_machine",
        "leg_press",
        "lat_pulldown",
        "assisted_pullup",
    }
)

# Canonical major groups (DeanT list; aliases map into these).
MAJOR_MUSCLES: Tuple[str, ...] = (
    "chest",
    "mid_upper_back",
    "lats",
    "delts",
    "biceps",
    "triceps",
    "quads",
    "hamstrings",
    "calves",
    "glutes",
    "adductors",
    "abs",
    "traps",
)

# Catalog / log muscle tags → major group
MUSCLE_ALIASES: Dict[str, str] = {
    "chest": "chest",
    "pecs": "chest",
    "pectorals": "chest",
    "back": "mid_upper_back",
    "mid_upper_back": "mid_upper_back",
    "upper_back": "mid_upper_back",
    "mid_back": "mid_upper_back",
    "rhomboids": "mid_upper_back",
    "lats": "lats",
    "lat": "lats",
    "latissimus": "lats",
    "delts": "delts",
    "delt": "delts",
    "shoulders": "delts",
    "shoulder": "delts",
    "rear_delts": "delts",
    "side_delts": "delts",
    "front_delts": "delts",
    "biceps": "biceps",
    "bis": "biceps",
    "bicep": "biceps",
    "triceps": "triceps",
    "tris": "triceps",
    "tricep": "triceps",
    "quads": "quads",
    "quad": "quads",
    "quadriceps": "quads",
    "hamstrings": "hamstrings",
    "hams": "hamstrings",
    "ham": "hamstrings",
    "calves": "calves",
    "calf": "calves",
    "glutes": "glutes",
    "glute": "glutes",
    "adductors": "adductors",
    "adductor": "adductors",
    "abs": "abs",
    "core": "abs",
    "traps": "traps",
    "trapezius": "traps",
    "lower_back": "mid_upper_back",  # erectors — credit upper/mid back bucket lightly
    "forearms": "biceps",  # small carry; not a major DeanT group
}

VOLUME_FRAMEWORK = {
    "id": "dean_t_balanced_4_8",
    "label": "Balanced volume (≈4–8 sets/muscle/week)",
    "source": "https://x.com/DeanTTraining/status/2081501543510028437",
    "summary": (
        "Hard sets ~4–8 per major muscle per week with compound overlap counted; "
        "10–20+/muscle is usually unnecessary and exceeds productive weekly capacity. "
        "Prioritize 1–2 muscles only by putting others at maintenance."
    ),
}

# Primary majors shown for a PPL session day (UI filter; weekly credits still
# accumulate for the full body). Catalog session_types map into these buckets.
SESSION_MUSCLES: Dict[str, Tuple[str, ...]] = {
    "push": ("chest", "delts", "triceps", "traps"),
    "pull": ("mid_upper_back", "lats", "biceps", "traps"),
    "legs": ("quads", "hamstrings", "glutes", "calves", "adductors"),
}

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
    # When true (default), each plan gen picks lagging muscles from logs and
    # applies them as focus for volume bands + exercise selection — no Ask needed.
    # Set false + explicit focus_muscles for a manual pin.
    "auto_focus_muscles": True,
    "rest_if_recovery_below": 40,
    # DeanT volume framework
    "volume_framework": VOLUME_FRAMEWORK["id"],
    "sets_per_muscle_week_min": 4,
    "sets_per_muscle_week_max": 8,
    "sets_per_muscle_week_priority_max": 12,
    "maintenance_sets_per_muscle_week": 3,
    "session_working_set_cap": 14,
    "secondary_set_fraction": 0.5,
    "default_hard_sets": 2,  # preferred hard sets when history is thin
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
    for k in (
        "sessions_per_week_target",
        "exercises_per_session",
        "rest_if_recovery_below",
        "sets_per_muscle_week_min",
        "sets_per_muscle_week_max",
        "sets_per_muscle_week_priority_max",
        "maintenance_sets_per_muscle_week",
        "session_working_set_cap",
        "default_hard_sets",
    ):
        if raw.get(k) is not None:
            try:
                g[k] = int(raw[k])
            except (TypeError, ValueError):
                pass
    if raw.get("secondary_set_fraction") is not None:
        try:
            g["secondary_set_fraction"] = float(raw["secondary_set_fraction"])
        except (TypeError, ValueError):
            pass
    if raw.get("volume_framework"):
        g["volume_framework"] = str(raw["volume_framework"])
    if "prefer_compounds_first" in raw:
        g["prefer_compounds_first"] = bool(raw["prefer_compounds_first"])
    if raw.get("progression"):
        g["progression"] = str(raw["progression"])
    if raw.get("notes") is not None:
        g["notes"] = str(raw["notes"])
    if isinstance(raw.get("focus_muscles"), list):
        g["focus_muscles"] = [normalize_muscle(str(x)) for x in raw["focus_muscles"]]
    if "auto_focus_muscles" in raw:
        g["auto_focus_muscles"] = bool(raw["auto_focus_muscles"])
    if raw.get("updated_at"):
        g["updated_at"] = str(raw["updated_at"])
    return g


def normalize_muscle(name: str) -> str:
    key = re.sub(r"[\s\-]+", "_", str(name or "").strip().lower())
    return MUSCLE_ALIASES.get(key, key)


def muscle_targets(goals: dict) -> Dict[str, Dict[str, float]]:
    """Per-muscle weekly set min/max, elevating focus muscles."""
    goals = normalize_goals(goals)
    lo = float(goals.get("sets_per_muscle_week_min") or 4)
    hi = float(goals.get("sets_per_muscle_week_max") or 8)
    pri_hi = float(goals.get("sets_per_muscle_week_priority_max") or 12)
    maint = float(goals.get("maintenance_sets_per_muscle_week") or 3)
    focus = {normalize_muscle(m) for m in (goals.get("focus_muscles") or [])}
    out: Dict[str, Dict[str, float]] = {}
    for m in MAJOR_MUSCLES:
        if m in focus:
            out[m] = {"min": lo, "max": pri_hi, "priority": True}
        elif focus:
            # Non-focus while prioritizing others → maintenance band
            out[m] = {"min": max(2.0, maint - 1), "max": maint, "priority": False}
        else:
            out[m] = {"min": lo, "max": hi, "priority": False}
    return out


def _working_sets_from_entry(ex: Any) -> int:
    """Hard/working sets from a logged ExerciseEntry or dict."""
    if hasattr(ex, "sets"):
        rows = ex.sets or []
        total = 0
        for st in rows:
            total += int(getattr(st, "sets", 0) or 0)
        return max(0, total)
    if isinstance(ex, dict):
        sets_field = ex.get("sets")
        if isinstance(sets_field, list):
            total = 0
            for st in sets_field:
                if isinstance(st, dict):
                    total += int(st.get("sets") or 0)
                else:
                    total += int(getattr(st, "sets", 0) or 0)
            return max(0, total)
        if sets_field is not None:
            try:
                return max(0, int(sets_field))
            except (TypeError, ValueError):
                return 0
    return 0


def credit_sets_for_exercise(
    primary: Sequence[str],
    secondary: Sequence[str],
    hard_sets: float,
    *,
    secondary_fraction: float = 0.5,
) -> Dict[str, float]:
    """Distribute hard sets across major muscles (primary full, secondary fractional)."""
    credits: Dict[str, float] = {}
    prim = [normalize_muscle(m) for m in primary if m]
    sec = [normalize_muscle(m) for m in secondary if m]
    # Avoid double-counting same major group
    prim_u = list(dict.fromkeys(prim))
    sec_u = [m for m in dict.fromkeys(sec) if m not in prim_u]
    if prim_u:
        share = float(hard_sets) / len(prim_u)
        for m in prim_u:
            credits[m] = credits.get(m, 0.0) + share
    if sec_u and secondary_fraction > 0:
        share = float(hard_sets) * float(secondary_fraction) / len(sec_u)
        for m in sec_u:
            credits[m] = credits.get(m, 0.0) + share
    return credits


def weekly_set_tally(
    sessions: Sequence[Session],
    catalog: dict,
    *,
    as_of: Optional[str] = None,
    window_days: int = 7,
    secondary_fraction: float = 0.5,
) -> Dict[str, Any]:
    """Trailing-week hard-set credits by major muscle (with compound overlap)."""
    if as_of is None:
        from .timeutil import local_today_iso

        day = local_today_iso()
    else:
        day = as_of
    try:
        end = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=max(1, window_days) - 1)

    available = available_exercises(catalog) if catalog else []
    by_id = {ex["id"]: ex for ex in available}
    by_name = {_norm_name(ex["name"]): ex for ex in available}

    totals: Dict[str, float] = {m: 0.0 for m in MAJOR_MUSCLES}
    logged_exercises = 0

    for s in sessions or []:
        try:
            sd = datetime.strptime(str(s.date)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if sd < start or sd > end:
            continue
        for ex in s.exercises or []:
            hard = _working_sets_from_entry(ex)
            if hard <= 0:
                continue
            name = getattr(ex, "name", None) or (ex.get("name") if isinstance(ex, dict) else "")
            cat = None
            cid = match_catalog_id(str(name), by_id)
            if cid:
                cat = by_id.get(cid)
            if not cat:
                cat = by_name.get(_norm_name(str(name)))
            if cat:
                prim = cat.get("primary_muscles") or []
                sec = cat.get("secondary_muscles") or []
            else:
                prim, sec = [], []
            credits = credit_sets_for_exercise(
                prim, sec, hard, secondary_fraction=secondary_fraction
            )
            if not credits:
                # Unknown lift — skip rather than invent a muscle
                continue
            logged_exercises += 1
            for m, c in credits.items():
                if m in totals:
                    totals[m] += c
                else:
                    totals[m] = c

    rounded = {m: round(v, 2) for m, v in sorted(totals.items(), key=lambda x: x[0])}
    return {
        "window_days": window_days,
        "as_of": day,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "by_muscle": rounded,
        "total_set_credits": round(sum(rounded.values()), 2),
        "logged_exercise_entries": logged_exercises,
    }


def classify_volume_status(
    done: float, band: Dict[str, float]
) -> str:
    """under | ok | high | over relative to weekly band."""
    lo = float(band.get("min") or 4)
    hi = float(band.get("max") or 8)
    if done < lo * 0.75:
        return "under"
    if done < lo:
        return "low"
    if done <= hi:
        return "ok"
    if done <= hi * 1.25:
        return "high"
    return "over"


def suggest_focus_muscles(
    tally: Dict[str, Any],
    goals: Optional[dict] = None,
    *,
    max_focus: int = 2,
) -> Dict[str, Any]:
    """Pick 1–2 lagging major muscles for priority volume (DeanT style).

    Uses trailing-week hard-set credits vs the balanced 4–8 band. Prefers muscles
    that are furthest under the weekly min among core program groups.
    """
    goals = normalize_goals(goals or {})
    bands = muscle_targets({**goals, "focus_muscles": []})  # balanced bands
    by = dict(tally.get("by_muscle") or {})
    # Prefer big drivers when gaps are equal (DeanT: prioritize 1–2 groups, not calves/arms first)
    candidates = [
        # rank 0 = highest priority for focus selection
        ("chest", 0),
        ("lats", 0),
        ("mid_upper_back", 0),
        ("quads", 0),
        ("hamstrings", 0),
        ("glutes", 0),
        ("delts", 1),
        ("triceps", 2),
        ("biceps", 2),
        ("traps", 3),
        ("calves", 3),
    ]
    scored: List[Tuple[float, int, str, float, float]] = []
    for m, rank in candidates:
        done = float(by.get(m) or 0)
        lo = float((bands.get(m) or {}).get("min") or 4)
        gap = lo - done
        if gap <= 0:
            continue
        scored.append((gap, rank, m, done, lo))
    # Largest gap first, then more important muscle groups
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))
    picks = [m for _, _, m, _, _ in scored[: max(1, min(3, max_focus))]]
    reason_bits = []
    for gap, _rank, m, done, lo in scored[: len(picks)]:
        reason_bits.append(f"{m.replace('_', ' ')} {done:g}/{lo:g} sets")
    return {
        "muscles": picks,
        "reason": (
            "Lagging vs ≈4–8/week band: " + "; ".join(reason_bits)
            if reason_bits
            else "No clear lagging muscles in the last 7 days — balanced volume is fine."
        ),
        "candidates": [
            {"muscle": m, "done": d, "min": lo, "gap": round(g, 2)}
            for g, _r, m, d, lo in scored[:6]
        ],
    }


def resolve_focus_for_plan(
    goals: dict,
    tally: Dict[str, Any],
    *,
    max_focus: int = 2,
) -> Dict[str, Any]:
    """Decide effective focus muscles for this plan generation.

    Default: autonomous — derive lagging groups from weekly logs.
    Manual pin: ``auto_focus_muscles=false`` and non-empty ``focus_muscles``.
    """
    goals = normalize_goals(goals)
    suggested = suggest_focus_muscles(tally, goals, max_focus=max_focus)
    manual = [normalize_muscle(m) for m in (goals.get("focus_muscles") or [])]
    manual = [m for m in manual if m in MAJOR_MUSCLES]
    auto = bool(goals.get("auto_focus_muscles", True))

    if not auto and manual:
        return {
            "muscles": manual,
            "source": "manual",
            "auto": False,
            "suggested": suggested,
            "reason": "Pinned focus (auto focus off).",
        }
    if suggested.get("muscles"):
        return {
            "muscles": list(suggested["muscles"]),
            "source": "auto",
            "auto": True,
            "suggested": suggested,
            "reason": suggested.get("reason") or "Auto from weekly volume gaps.",
        }
    # Nothing lagging — keep empty (balanced) or fall back to manual if any
    if manual:
        return {
            "muscles": manual,
            "source": "manual_fallback",
            "auto": auto,
            "suggested": suggested,
            "reason": "No lagging gaps; keeping stored focus.",
        }
    return {
        "muscles": [],
        "source": "balanced",
        "auto": auto,
        "suggested": suggested,
        "reason": suggested.get("reason")
        or "Balanced volume — no priority muscles this week.",
    }


def volume_balance_report(
    tally: Dict[str, Any],
    goals: dict,
    *,
    planned_credits: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Compare weekly tally (+ optional planned session) to DeanT bands."""
    goals = normalize_goals(goals)
    bands = muscle_targets(goals)
    by = dict(tally.get("by_muscle") or {})
    planned_credits = planned_credits or {}
    rows = []
    under, ok, high = [], [], []
    for m in MAJOR_MUSCLES:
        done = float(by.get(m) or 0)
        add = float(planned_credits.get(m) or 0)
        projected = done + add
        band = bands.get(m) or {"min": 4, "max": 8, "priority": False}
        status = classify_volume_status(projected if add else done, band)
        row = {
            "muscle": m,
            "done": round(done, 2),
            "planned": round(add, 2),
            "projected": round(projected, 2),
            "min": band["min"],
            "max": band["max"],
            "priority": bool(band.get("priority")),
            "status": status,
        }
        rows.append(row)
        if status in ("under", "low"):
            under.append(m)
        elif status == "ok":
            ok.append(m)
        else:
            high.append(m)
    return {
        "framework": VOLUME_FRAMEWORK,
        "bands": {
            m: {"min": bands[m]["min"], "max": bands[m]["max"], "priority": bands[m]["priority"]}
            for m in MAJOR_MUSCLES
        },
        "muscles": rows,
        "under_target": under,
        "in_range": ok,
        "high_or_over": high,
        "window": {
            "days": tally.get("window_days"),
            "start": tally.get("start"),
            "end": tally.get("end"),
            "as_of": tally.get("as_of"),
        },
        "total_set_credits": tally.get("total_set_credits"),
    }


def _score_exercise_for_volume(
    ex: dict,
    done: Dict[str, float],
    bands: Dict[str, Dict[str, float]],
    *,
    focus: set,
) -> float:
    """Higher = more useful for filling under-target muscles without overshooting."""
    score = 0.0
    prim = [normalize_muscle(m) for m in (ex.get("primary_muscles") or [])]
    sec = [normalize_muscle(m) for m in (ex.get("secondary_muscles") or [])]
    for m in prim:
        band = bands.get(m) or {"min": 4, "max": 8}
        d = float(done.get(m) or 0)
        if d < band["min"]:
            score += (band["min"] - d) * 3.0
        elif d > band["max"]:
            score -= (d - band["max"]) * 4.0
        else:
            score += 0.5
        if m in focus:
            score += 2.0
    for m in sec:
        band = bands.get(m) or {"min": 4, "max": 8}
        d = float(done.get(m) or 0)
        if d < band["min"]:
            score += (band["min"] - d) * 1.0
        elif d > band["max"]:
            score -= (d - band["max"]) * 1.5
    if ex.get("movement") == "compound":
        score += 1.5  # efficiency / multi-muscle stimulus
    score += float(ex.get("priority") or 0) * 0.05
    return score


def _cap_sets_for_muscles(
    hard_sets: int,
    primary: Sequence[str],
    secondary: Sequence[str],
    done: Dict[str, float],
    bands: Dict[str, Dict[str, float]],
    *,
    secondary_fraction: float,
) -> int:
    """Shrink hard sets so primaries stay near weekly max after this lift."""
    sets = max(1, int(hard_sets))
    while sets > 1:
        credits = credit_sets_for_exercise(
            primary, secondary, sets, secondary_fraction=secondary_fraction
        )
        over = False
        for m, c in credits.items():
            band = bands.get(m)
            if not band:
                continue
            # Only hard-cap on primary muscles
            if normalize_muscle(m) not in [normalize_muscle(x) for x in primary]:
                continue
            if float(done.get(m) or 0) + c > float(band["max"]) + 0.51:
                over = True
                break
        if not over:
            break
        sets -= 1
    return sets


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
        "equipment": [str(t).lower() for t in (raw.get("equipment") or [])],
        "equipment_any": [str(t).lower() for t in (raw.get("equipment_any") or [])],
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


def _norm_equipment_tag(tag: str) -> str:
    from .equipment_store import normalize_equipment_tag

    return normalize_equipment_tag(tag)


def movement_required_tags(ex: dict) -> List[str]:
    return [_norm_equipment_tag(t) for t in (ex.get("equipment") or []) if t]


def movement_any_tags(ex: dict) -> List[str]:
    return [_norm_equipment_tag(t) for t in (ex.get("equipment_any") or []) if t]


def movement_feasible(ex: dict, equipment: Optional[dict]) -> bool:
    """Allow a catalog movement only when every required tag is owned.

    ``equipment_any`` is OR (barbell *or* dumbbells). Missing gear → skip;
    never invent cable/smith/assisted-pullup.
    """
    required = movement_required_tags(ex)
    any_tags = movement_any_tags(ex)
    if not required and not any_tags:
        return True
    from .equipment_store import owned_equipment_tags

    owned = owned_equipment_tags(equipment)
    if any(t not in owned for t in required):
        return False
    if any_tags and not any(t in owned for t in any_tags):
        return False
    return True


def available_load_lbs(ex: dict, equipment: Optional[dict]) -> Optional[float]:
    """Max load this movement can actually load from owned implements."""
    from .equipment_store import owned_equipment_items

    by_tag = {i["tag"]: i for i in owned_equipment_items(equipment)}
    required = [t for t in movement_required_tags(ex) if t in LOAD_EQUIPMENT_TAGS]
    any_tags = [t for t in movement_any_tags(ex) if t in LOAD_EQUIPMENT_TAGS]
    required_caps: List[float] = []
    for t in required:
        item = by_tag.get(t)
        if item and item.get("max_weight_lbs") is not None:
            required_caps.append(float(item["max_weight_lbs"]))
    cap: Optional[float] = min(required_caps) if required_caps else None
    any_caps = [
        float(by_tag[t]["max_weight_lbs"])
        for t in any_tags
        if t in by_tag and by_tag[t].get("max_weight_lbs") is not None
    ]
    if any_caps:
        any_cap = max(any_caps)
        cap = min(cap, any_cap) if cap is not None else any_cap
    return cap


def filter_catalog_by_equipment(catalog: dict, equipment: Optional[dict]) -> dict:
    """Catalog minus movements the owned gear cannot load. Does not invent lifts."""
    out = deepcopy(catalog) if isinstance(catalog, dict) else {"exercises": []}
    kept = []
    for raw in out.get("exercises") or []:
        if not isinstance(raw, dict):
            continue
        try:
            ex = normalize_exercise(raw)
        except ValueError:
            continue
        if ex["available"] and movement_feasible(ex, equipment):
            kept.append(raw)
    out["exercises"] = kept
    return out


def cap_weight_to_inventory(
    weight: Optional[float],
    catalog_ex: dict,
    equipment: Optional[dict],
) -> Tuple[Optional[float], Optional[float], bool]:
    """Hold double-progression at the load he can actually load."""
    cap = available_load_lbs(catalog_ex, equipment)
    if weight is None or cap is None:
        return weight, cap, False
    if float(weight) > float(cap):
        return float(cap), cap, True
    return float(weight), cap, False


def clamp_workout_to_equipment(
    workout: dict,
    catalog: dict,
    equipment: Optional[dict],
) -> dict:
    """Drop invented / unequipped SuperGrok lifts; cap prescribed loads."""
    workout = dict(workout or {})
    available = available_exercises(filter_catalog_by_equipment(catalog, equipment))
    by_id = {ex["id"]: ex for ex in available}
    by_name = {_norm_name(ex["name"]): ex for ex in available}
    kept: List[dict] = []
    for raw in workout.get("exercises") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "")
        cid = match_catalog_id(name, by_id) if name else None
        cat = by_id.get(cid) if cid else None
        if not cat:
            cat = by_name.get(_norm_name(name))
        if not cat:
            continue
        if not movement_feasible(cat, equipment):
            continue
        row = dict(raw)
        row["name"] = cat["name"]
        row["id"] = cat.get("id") or row.get("id")
        row["equipment"] = cat.get("equipment") or []
        rx = dict(row.get("prescription") or {})
        w = rx.get("weight_lbs")
        try:
            w_f = float(w) if w is not None and w != "" else None
        except (TypeError, ValueError):
            w_f = None
        capped, _cap, was_capped = cap_weight_to_inventory(w_f, cat, equipment)
        if was_capped:
            rx["weight_lbs"] = capped
            note = f"Load capped at {capped:g} lb (owned max)."
            rationale = str(row.get("rationale") or "")
            row["rationale"] = f"{rationale} {note}".strip()
        elif capped is not None:
            rx["weight_lbs"] = capped
        row["prescription"] = rx
        kept.append(row)
    workout["exercises"] = kept
    if (
        not kept
        and not workout.get("is_rest_day")
        and isinstance(equipment, dict)
    ):
        st = str(workout.get("session_type") or "").upper() or "this"
        workout["empty"] = True
        msg = str(workout.get("message") or "")
        if "invent" not in msg.lower() and "owned equipment" not in msg.lower():
            workout["message"] = (
                f"No owned equipment can load a {st} lift. "
                "Add gear and max weight — the planner will not invent "
                "cable, smith, or assisted-pullup."
            )
    return workout


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


def session_type_of(session: Any) -> str:
    """Session.session_type or brief dict session_type/type."""
    if isinstance(session, dict):
        return str(session.get("session_type") or session.get("type") or "").lower()
    return str(getattr(session, "session_type", "") or "").lower()


def session_date_of(session: Any) -> str:
    """Session.date or brief dict date (YYYY-MM-DD)."""
    if isinstance(session, dict):
        return str(session.get("date") or "")[:10]
    return str(getattr(session, "date", "") or "")[:10]


def last_session_type(sessions: Sequence[Any]) -> Optional[str]:
    ordered = sorted(
        [s for s in sessions if session_type_of(s) in ("push", "pull", "legs")],
        key=session_date_of,
        reverse=True,
    )
    if not ordered:
        return None
    return session_type_of(ordered[0])


def ppl_logged_on_day(sessions: Sequence[Any], day: Optional[str]) -> Optional[str]:
    """PPL letter already logged on civil ``day``, if any.

    One slot per day: the first push/pull/legs session on that date.
    Does not invent a second type. ``next_session_type`` remains the
    following rotation letter (tomorrow).
    """
    target = str(day or "")[:10]
    if not target:
        return None
    for s in sessions or []:
        if session_date_of(s) != target:
            continue
        st = session_type_of(s)
        if st in ("push", "pull", "legs"):
            return st
    return None


def session_types_for_lift_name(
    name: str,
    catalog: Optional[dict] = None,
) -> Tuple[str, ...]:
    """Catalog PPL slots for a lift title. Empty if unknown — callers must not guess."""
    key = re.sub(r"\s+", " ", (name or "").strip().lower())
    if not key:
        return ()
    data = catalog if isinstance(catalog, dict) else default_catalog()
    alias_id = NAME_ALIASES.get(key)
    for ex in data.get("exercises") or []:
        if not isinstance(ex, dict):
            continue
        n = re.sub(r"\s+", " ", str(ex.get("name") or "").strip().lower())
        eid = str(ex.get("id") or "").strip().lower()
        if key == n or key == eid or (alias_id and eid == alias_id):
            types = [str(t).lower() for t in (ex.get("session_types") or [])]
            return tuple(t for t in types if t in ("push", "pull", "legs"))
    return ()


def next_session_type(sessions: Sequence[Any], goals: dict) -> str:
    rotation = goals.get("rotation") or ["push", "pull", "legs"]
    rotation = [str(r).lower() for r in rotation]
    last = last_session_type(sessions)
    if not last or last not in rotation:
        return rotation[0]
    idx = rotation.index(last)
    return rotation[(idx + 1) % len(rotation)]


def days_since_last_session(sessions: Sequence[Any], as_of: Optional[str] = None) -> Optional[int]:
    dated = [s for s in (sessions or []) if session_date_of(s)]
    if not dated:
        return None
    if as_of is None:
        from .timeutil import local_today_iso

        day = local_today_iso()
    else:
        day = as_of
    ordered = sorted(dated, key=session_date_of, reverse=True)
    try:
        last = datetime.strptime(session_date_of(ordered[0]), "%Y-%m-%d")
        today = datetime.strptime(str(day)[:10], "%Y-%m-%d")
        return max(0, (today - last).days)
    except ValueError:
        return None


# Continuity phases after training silence (not the same as in-cycle weekly volume gaps).
# Long-term target stays ≈4–8 hard sets/muscle/week; these scales only control
# how fast we approach that band after a layoff (load + volume ramp).
_CONTINUITY_PHASES: Tuple[Tuple[Optional[int], str, str, float, float, float, bool], ...] = (
    # max_days (inclusive), id, label, load_mult, volume_band_scale, session_cap_scale, allow_progression
    (6, "normal", "Normal", 1.0, 1.0, 1.0, True),
    (13, "rusty", "Rusty", 0.925, 1.0, 0.95, False),
    (27, "return", "Return", 0.85, 0.78, 0.85, False),
    (59, "reentry", "Re-entry", 0.775, 0.60, 0.70, False),
    (None, "restart", "Restart", 0.70, 0.50, 0.65, False),
)


def training_continuity(
    days_since: Optional[int],
) -> Dict[str, Any]:
    """Map days since last real session → load/volume ramp for prescriptions.

    ``days_since is None`` (no logs) is treated as restart — starter-friendly
    volume, not a full 4–8 chase on day one.
    """
    if days_since is None:
        days_key: Optional[int] = None
        phase = _CONTINUITY_PHASES[-1]
    else:
        days_key = max(0, int(days_since))
        phase = _CONTINUITY_PHASES[-1]
        for max_d, pid, label, load_m, vol_s, cap_s, allow_prog in _CONTINUITY_PHASES:
            if max_d is None or days_key <= max_d:
                phase = (max_d, pid, label, load_m, vol_s, cap_s, allow_prog)
                break

    _max_d, pid, label, load_m, vol_s, cap_s, allow_prog = phase
    load_cut_pct = int(round((1.0 - float(load_m)) * 100))
    if pid == "normal":
        summary = "Continuity normal — full progression and volume band."
    elif days_key is None:
        summary = (
            "No recent lift logs — restart: conservative volume, "
            "build continuity before chasing prior loads."
        )
    else:
        summary = (
            f"{label} · {days_key}d since last log · loads −{load_cut_pct}% vs last "
            f"working weight · volume ramping (don’t chase old PRs this week)."
        )
    return {
        "phase": pid,
        "label": label,
        "days_since": days_key,
        "load_multiplier": float(load_m),
        "volume_band_scale": float(vol_s),
        "session_cap_scale": float(cap_s),
        "allow_load_progression": bool(allow_prog),
        "load_cut_pct": load_cut_pct,
        "summary": summary,
    }


def scale_muscle_targets_for_continuity(
    bands: Dict[str, Dict[str, float]],
    continuity: Dict[str, Any],
) -> Dict[str, Dict[str, float]]:
    """Shrink weekly min/max bands during return phases (don’t fill multi-week debt)."""
    scale = float(continuity.get("volume_band_scale") or 1.0)
    if scale >= 0.999:
        return bands
    out: Dict[str, Dict[str, float]] = {}
    for m, band in bands.items():
        lo = float(band.get("min") or 4)
        hi = float(band.get("max") or 8)
        # Keep a usable band; floor so zeros don’t collapse the model
        new_lo = max(1.0, round(lo * scale, 2))
        new_hi = max(new_lo + 1.0, round(hi * scale, 2))
        out[m] = {
            "min": new_lo,
            "max": new_hi,
            "priority": bool(band.get("priority")),
        }
    return out


def prescribe(
    catalog_ex: dict,
    last: Optional[dict],
    *,
    recovery_score: Optional[float] = None,
    continuity: Optional[Dict[str, Any]] = None,
    default_hard_sets: Optional[int] = None,
) -> dict:
    """Double-progression style prescription from last logged set.

    When ``continuity`` is not normal, hold or cut load vs last log instead of
    progressing — re-establish pattern/work capacity after silence.

    Set volume is seeded from goals.default_hard_sets, never catalog default_sets=3.
    """
    lo, hi = catalog_ex["rep_range"]
    if default_hard_sets is not None:
        sets = max(1, int(default_hard_sets))
    else:
        try:
            raw_i = int(catalog_ex.get("default_sets") or 0)
        except (TypeError, ValueError):
            raw_i = 0
        # Blind catalog default_sets=3 is junk volume (DeanT / default_hard_sets=2).
        sets = 2 if raw_i in (0, 3) else raw_i
    reps = int(catalog_ex.get("default_reps") or 10)
    weight: Optional[float] = None
    rationale = "Default starter prescription (no history for this lift)."
    cont = continuity or training_continuity(0)
    allow_prog = bool(cont.get("allow_load_progression", True))
    load_m = float(cont.get("load_multiplier") or 1.0)

    if last:
        base_w = float(last["weight_lbs"])
        weight = base_w
        sets = int(last.get("sets") or sets)
        reps = int(last.get("reps") or reps)
        # Cap sets to productive hard-set range (DeanT: more is rarely better)
        sets = max(1, min(4, sets))
        if not allow_prog:
            # Re-entry: technique loads, bottom of rep range, thinner sets
            weight = round(base_w * load_m, 1)
            reps = lo
            if cont.get("phase") in ("reentry", "restart"):
                sets = max(1, min(sets, 2))
            elif cont.get("phase") == "return":
                sets = max(1, min(sets, 3))
            cut = int(cont.get("load_cut_pct") or round((1.0 - load_m) * 100))
            days = cont.get("days_since")
            days_txt = f"{days}d since last log" if days is not None else "no recent logs"
            rationale = (
                f"{cont.get('label') or 'Return'} ({days_txt}): "
                f"{base_w:g} lb last on {last['date']} → {weight:g} lb "
                f"(−{cut}%), {sets}×{reps} to re-establish before progressing."
            )
        elif reps >= hi:
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
        before = weight
        weight = round(weight * 0.9, 1)
        if weight < before:
            rationale += " Recovery moderate/low → ~10% load deload."

    return {
        "weight_lbs": weight,
        "sets": sets,
        "reps": reps,
        "rep_range": [lo, hi],
        "rationale": rationale,
        "last": last,
        "continuity_phase": cont.get("phase"),
    }


def generate_workout_plan(
    catalog: dict,
    goals: dict,
    sessions: Sequence[Session],
    *,
    recovery_label: Optional[str] = None,
    recovery_score: Optional[float] = None,
    recovery_sparse: bool = False,
    session_type: Optional[str] = None,
    as_of: Optional[str] = None,
    equipment: Optional[dict] = None,
) -> dict:
    """
    Build today's workout from catalog + history + recovery.

    Similar role to generate_meal_plan for nutrition.

    When ``recovery_sparse`` is True (e.g. no real sleep logs from Health yet),
    a low recovery score does not force a rest day — zero-filled sleep debt
    would otherwise score ~30 Caution and blank the plan on cold cache.

    If a PPL session is already logged on ``as_of``, pin to that letter and
    do not generate the next rotation for the same civil day. Pass an
    explicit ``session_type`` (force Push/Pull/Legs) to override.
    """
    goals = normalize_goals(goals)
    if as_of is None:
        from .timeutil import local_today_iso

        day = local_today_iso()
    else:
        day = as_of
    # Ignore canary / probe lifts when choosing rotation and loads
    from .test_noise import filter_sessions

    sessions = filter_sessions(list(sessions))
    equipment_on = isinstance(equipment, dict)
    available = available_exercises(catalog)
    if equipment_on:
        available = [ex for ex in available if movement_feasible(ex, equipment)]
    by_id = {ex["id"]: ex for ex in available}

    rest_threshold = int(goals.get("rest_if_recovery_below") or 40)
    sec_frac = float(goals.get("secondary_set_fraction") or 0.5)
    days = days_since_last_session(sessions, as_of=day)
    continuity = training_continuity(days)
    tally = weekly_set_tally(
        sessions,
        catalog,
        as_of=day,
        window_days=7,
        secondary_fraction=sec_frac,
    )
    # Autonomous coach: pick focus from logs before volume bands / selection.
    # During re-entry/restart, lagging-everything is noise — prefer balanced bands
    # so we don't invent a "priority blast" after a long layoff.
    focus_goals = goals
    if continuity.get("phase") in ("reentry", "restart", "return"):
        focus_goals = {
            **goals,
            "auto_focus_muscles": False,
            "focus_muscles": list(goals.get("focus_muscles") or []),
        }
        # Drop empty manual focus so bands stay balanced during ramp
        if not focus_goals.get("focus_muscles"):
            focus_goals = {**focus_goals, "focus_muscles": []}
    focus_res = resolve_focus_for_plan(focus_goals, tally, max_focus=2)
    if continuity.get("phase") in ("reentry", "restart") and focus_res.get("source") == "auto":
        # Suppress auto lagging picks in deep return phases
        focus_res = {
            **focus_res,
            "muscles": list(goals.get("focus_muscles") or []),
            "source": "continuity" if not goals.get("focus_muscles") else focus_res.get("source"),
            "reason": (
                f"{continuity.get('label')}: volume ramp — no auto-priority "
                "until continuity is normal (don’t fill multi-week debt)."
            ),
            "auto": False,
        }
    goals = {**goals, "focus_muscles": list(focus_res.get("muscles") or [])}
    goals["_focus_resolution"] = {
        "source": focus_res.get("source"),
        "auto": focus_res.get("auto"),
        "reason": focus_res.get("reason"),
    }

    explicit = str(session_type or "").strip().lower()
    logged_today = ppl_logged_on_day(sessions, day)
    if logged_today and explicit not in ("push", "pull", "legs"):
        nxt = next_session_type(sessions, goals)
        balance = volume_balance_report(tally, goals)
        balance["suggested_focus"] = focus_res.get("suggested") or suggest_focus_muscles(
            tally, goals
        )
        balance["focus"] = {
            "muscles": goals.get("focus_muscles") or [],
            "source": focus_res.get("source"),
            "reason": focus_res.get("reason"),
        }
        return {
            "date": day,
            "session_type": logged_today,
            "is_rest_day": False,
            "already_trained_today": True,
            "exercises": [],
            "next_session_type": nxt,
            "message": (
                f"Already trained today ({logged_today.upper()}). "
                f"Next session: {nxt.upper()} tomorrow."
            ),
            "goals": goals,
            "volume": balance,
            "context": {
                "recovery_label": recovery_label,
                "recovery_score": recovery_score,
                "last_session_type": last_session_type(sessions),
                "next_session_type": nxt,
                "days_since_last": days,
                "training_continuity": continuity,
                "volume_framework": VOLUME_FRAMEWORK,
                "weekly_sets": tally,
                "focus": balance["focus"],
                "already_trained_today": True,
            },
        }

    if (
        recovery_score is not None
        and recovery_score < rest_threshold
        and not recovery_sparse
    ):
        balance = volume_balance_report(tally, goals)
        balance["suggested_focus"] = focus_res.get("suggested") or suggest_focus_muscles(
            tally, goals
        )
        balance["focus"] = {
            "muscles": goals.get("focus_muscles") or [],
            "source": focus_res.get("source"),
            "reason": focus_res.get("reason"),
        }
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
            "volume": balance,
            "context": {
                "recovery_label": recovery_label,
                "recovery_score": recovery_score,
                "last_session_type": last_session_type(sessions),
                "days_since_last": days,
                "training_continuity": continuity,
                "volume_framework": VOLUME_FRAMEWORK,
                "weekly_sets": tally,
                "focus": balance["focus"],
            },
        }

    st = (session_type or next_session_type(sessions, goals)).lower()
    pool = [ex for ex in available if st in ex["session_types"]]
    # Do not steal lifts from another PPL slot or invent unequipped gear.
    if not pool and not equipment_on:
        pool = list(available)

    bands = scale_muscle_targets_for_continuity(muscle_targets(goals), continuity)
    focus = {normalize_muscle(m) for m in (goals.get("focus_muscles") or [])}
    done: Dict[str, float] = dict(tally.get("by_muscle") or {})

    # Rank pool by volume need (under-target muscles) + compound efficiency
    pool_scored = sorted(
        pool,
        key=lambda e: (
            -_score_exercise_for_volume(e, done, bands, focus=focus),
            0 if e.get("movement") == "compound" else 1,
            -int(e.get("priority") or 0),
            e.get("name") or "",
        ),
    )

    n = max(3, min(8, int(goals.get("exercises_per_session") or 5)))
    # Fewer movements during deep re-entry — finish the session, don’t pile debt
    if continuity.get("phase") in ("reentry", "restart"):
        n = max(3, min(n, 4))
    elif continuity.get("phase") == "return":
        n = max(3, min(n, 5))
    base_cap = max(6, int(goals.get("session_working_set_cap") or 14))
    session_cap = max(4, int(round(base_cap * float(continuity.get("session_cap_scale") or 1.0))))
    default_hard = max(1, min(4, int(goals.get("default_hard_sets") or 2)))
    if continuity.get("phase") in ("reentry", "restart"):
        default_hard = min(default_hard, 2)
    elif continuity.get("phase") == "return":
        default_hard = min(default_hard, 2)

    chosen: List[dict] = []
    # Seed with top compound if compounds preferred
    if goals.get("prefer_compounds_first", True):
        for e in pool_scored:
            if e.get("movement") == "compound":
                chosen.append(e)
                break
    for e in pool_scored:
        if len(chosen) >= n:
            break
        if e["id"] not in {c["id"] for c in chosen}:
            # Skip isolations whose primaries are already over weekly max
            prim = [normalize_muscle(m) for m in (e.get("primary_muscles") or [])]
            if e.get("movement") != "compound" and prim:
                if all(float(done.get(m) or 0) >= float((bands.get(m) or {}).get("max") or 8) for m in prim):
                    continue
            chosen.append(e)

    plan_ex: List[dict] = []
    planned_credits: Dict[str, float] = {}
    session_sets = 0

    for ex in chosen:
        if session_sets >= session_cap:
            break
        last = last_performance(sessions, ex["name"])
        if not last:
            for alias, aid in NAME_ALIASES.items():
                if aid == ex["id"]:
                    last = last_performance(sessions, alias)
                    if last:
                        break
        # Volume from goals.default_hard_sets — never catalog default_sets=3
        ex_rx = dict(ex)
        ex_rx["default_sets"] = default_hard
        rx = prescribe(
            ex_rx,
            last,
            recovery_score=recovery_score,
            continuity=continuity,
            default_hard_sets=default_hard,
        )
        capped_w, load_cap, was_capped = cap_weight_to_inventory(
            rx.get("weight_lbs"), ex, equipment if equipment_on else None
        )
        if was_capped:
            rx["weight_lbs"] = capped_w
            rx["rationale"] = (
                f"{rx['rationale']} Load capped at {capped_w:g} lb "
                f"(owned max {load_cap:g} lb)."
            )
        elif capped_w is not None:
            rx["weight_lbs"] = capped_w
        hard = int(rx["sets"] or default_hard)
        hard = _cap_sets_for_muscles(
            hard,
            ex.get("primary_muscles") or [],
            ex.get("secondary_muscles") or [],
            {**done, **{k: done.get(k, 0) + planned_credits.get(k, 0) for k in set(done) | set(planned_credits)}},
            bands,
            secondary_fraction=sec_frac,
        )
        # Also respect remaining session budget
        hard = max(1, min(hard, session_cap - session_sets))
        rx["sets"] = hard
        prior_sets = (last or {}).get("sets")
        if prior_sets is None:
            prior_sets = default_hard
        if hard < int(prior_sets or hard):
            framework_note = (
                f"Volume cap: {hard} hard sets "
                f"(ramped band · continuity {continuity.get('label')})."
                if continuity.get("phase") != "normal"
                else f"Volume cap: {hard} hard sets (≈4–8/muscle/week framework)."
            )
            rx["rationale"] = f"{rx['rationale']} {framework_note}".strip()

        credits = credit_sets_for_exercise(
            ex.get("primary_muscles") or [],
            ex.get("secondary_muscles") or [],
            hard,
            secondary_fraction=sec_frac,
        )
        for m, c in credits.items():
            planned_credits[m] = planned_credits.get(m, 0.0) + c
        session_sets += hard

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
                "set_credits": {k: round(v, 2) for k, v in credits.items()},
                "rationale": rx["rationale"],
                "last": rx["last"],
            }
        )

    # Report volume against long-term bands, but annotate ramped planning bands
    balance = volume_balance_report(tally, goals, planned_credits=planned_credits)
    balance["planning_bands"] = {
        m: {"min": bands[m]["min"], "max": bands[m]["max"], "priority": bands[m]["priority"]}
        for m in bands
    }
    balance["continuity"] = {
        "phase": continuity.get("phase"),
        "volume_band_scale": continuity.get("volume_band_scale"),
        "note": (
            "Weekly under-target fill uses ramped planning bands during return — "
            "not multi-week catch-up."
            if continuity.get("phase") != "normal"
            else "Full weekly band."
        ),
    }
    balance["suggested_focus"] = focus_res.get("suggested") or suggest_focus_muscles(
        tally, goals
    )
    balance["focus"] = {
        "muscles": list(goals.get("focus_muscles") or []),
        "source": focus_res.get("source"),
        "reason": focus_res.get("reason"),
    }

    last_st = last_session_type(sessions)
    if not plan_ex and equipment_on:
        msg_parts = [
            f"No owned equipment can load a {st.upper()} lift. "
            "Add gear and max weight in Equipment inventory — "
            "the planner will not invent cable, smith, or assisted-pullup."
        ]
    else:
        msg_parts = [
            f"Suggested {st.upper()} session ({len(plan_ex)} exercises, {session_sets} hard sets)."
        ]
    if continuity.get("phase") != "normal":
        msg_parts.insert(0, continuity.get("summary") or continuity.get("label") or "Return phase")
    if last_st:
        msg_parts.append(f"Last trained: {last_st}")
    if days is not None:
        msg_parts.append(f"{days}d since last log")
    if recovery_label:
        msg_parts.append(f"Recovery: {recovery_label}")
    focus_list = list(goals.get("focus_muscles") or [])
    if focus_list:
        src = focus_res.get("source") or "auto"
        pretty = ", ".join(m.replace("_", " ") for m in focus_list)
        label = "Auto focus" if src == "auto" else "Focus"
        msg_parts.append(f"{label}: {pretty}")
    under = balance.get("under_target") or []
    if under and continuity.get("phase") == "normal":
        msg_parts.append(
            f"Volume fill: {', '.join(under[:4])}"
            + ("…" if len(under) > 4 else "")
        )
    elif under and continuity.get("phase") != "normal":
        msg_parts.append(
            f"Ramp targets (not catch-up): {', '.join(under[:3])}"
            + ("…" if len(under) > 3 else "")
        )
    if continuity.get("phase") == "normal":
        msg_parts.append("Framework: ≈4–8 sets/muscle/week (w/ overlap)")
    else:
        scale_pct = int(round(float(continuity.get("volume_band_scale") or 1) * 100))
        msg_parts.append(
            f"Framework: ≈4–8 long-term · this week planning band ~{scale_pct}% ramp"
        )

    return {
        "date": day,
        "session_type": st,
        "is_rest_day": False,
        "exercises": plan_ex,
        "message": " · ".join(msg_parts),
        "goals": goals,
        "volume": balance,
        "context": {
            "recovery_label": recovery_label,
            "recovery_score": recovery_score,
            "last_session_type": last_st,
            "days_since_last": days,
            "training_continuity": continuity,
            "catalog_available": len(available),
            "pool_for_session": len(pool),
            "equipment_filtered": equipment_on,
            "equipment_owned": (
                sorted(
                    {
                        str(i.get("tag"))
                        for i in ((equipment or {}).get("items") or [])
                        if isinstance(i, dict) and i.get("tag")
                    }
                )
                if equipment_on
                else []
            ),
            "session_hard_sets": session_sets,
            "session_working_set_cap": session_cap,
            "volume_framework": VOLUME_FRAMEWORK,
            "focus": balance["focus"],
            "weekly_sets": tally,
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
