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
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

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
        "fitbench",
    }
)

# Double progression (#954). Class band beats the catalog rep_range.
# Hitting the top of the band adds one step and resets to the bottom.
COMPOUND_REP_BAND = (5, 9)
ISOLATION_REP_BAND = (8, 15)

# Used when an inventory row for that tag has no max_weight_lbs.
LIBRARY_LOAD_CAPS = {
    "dumbbells": 50.0,
    "fitbench": 30.0,
}

LOWER_BODY_MUSCLES = frozenset(
    {"quads", "hamstrings", "glutes", "calves", "adductors"}
)
PIN_LOAD_TAGS = frozenset(
    {"cable", "machine", "lat_pulldown", "leg_press", "assisted_pullup"}
)
BAR_LOAD_TAGS = frozenset({"barbell", "smith_machine"})
DB_LOAD_TAGS = frozenset({"dumbbells", "fitbench"})

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
    "seated leg curl": "seated-leg-curls",
    "lying leg curl": "lying-leg-curls",
    "lying leg curls": "lying-leg-curls",
    "laying leg curl": "lying-leg-curls",
    "laying leg curls": "lying-leg-curls",
    "prone leg curl": "lying-leg-curls",
    "prone leg curls": "lying-leg-curls",
    "calf raises": "calf-raises",
    "calf raise": "calf-raises",
    "calf extensions": "calf-raises",
    "calf extension": "calf-raises",
    "machine calf raise": "calf-raises",
    "machine calf raises": "calf-raises",
    "seated calf raise": "calf-raises",
    "seated calf raises": "calf-raises",
    "db calf raises": "db-calf-raises",
    "db calf raise": "db-calf-raises",
    "dumbbell calf raises": "db-calf-raises",
    "dumbbell calf raise": "db-calf-raises",
    "standing calf raises": "db-calf-raises",
    "standing calf raise": "db-calf-raises",
    "back extension machine": "back-extension",
    "smith bench": "smith-flat-bench",
    "smith flat bench": "smith-flat-bench",
    "smith incline bench": "smith-incline-bench",
    "smith shrugs": "smith-shrugs",
    "db floor press": "db-floor-press",
    "dumbbell floor press": "db-floor-press",
    "floor press": "db-floor-press",
    "db row": "db-row",
    "dumbbell row": "db-row",
    "one arm row": "db-row",
    "goblet squat": "goblet-squat",
    "db goblet squat": "goblet-squat",
}

# Same motion, different implement = one slot per session.
# Incline is the angle change (allowed with one horizontal). OHP is vertical.
HORIZONTAL_PRESS_FAMILY = "horizontal_press"
INCLINE_PRESS_FAMILY = "incline_press"
VERTICAL_PRESS_FAMILY = "vertical_press"
VERTICAL_PULL_FAMILY = "vertical_pull"
HORIZONTAL_ROW_FAMILY = "horizontal_row"
KNEE_DOMINANT_FAMILY = "knee_dominant"
HAMSTRING_CURL_FAMILY = "hamstring_curl"
CALF_FAMILY = "calf"

PATTERN_FAMILY_BY_ID: Dict[str, str] = {
    "db-flat-press": HORIZONTAL_PRESS_FAMILY,
    "smith-flat-bench": HORIZONTAL_PRESS_FAMILY,
    "db-floor-press": HORIZONTAL_PRESS_FAMILY,
    "db-incline-press": INCLINE_PRESS_FAMILY,
    "smith-incline-bench": INCLINE_PRESS_FAMILY,
    "db-shoulder-press": VERTICAL_PRESS_FAMILY,
    "pulldowns": VERTICAL_PULL_FAMILY,
    "assisted-pullups": VERTICAL_PULL_FAMILY,
    "seated-cable-row": HORIZONTAL_ROW_FAMILY,
    "machine-row": HORIZONTAL_ROW_FAMILY,
    "db-row": HORIZONTAL_ROW_FAMILY,
    "leg-press": KNEE_DOMINANT_FAMILY,
    "goblet-squat": KNEE_DOMINANT_FAMILY,
    "seated-leg-curls": HAMSTRING_CURL_FAMILY,
    "lying-leg-curls": HAMSTRING_CURL_FAMILY,
    "calf-raises": CALF_FAMILY,
    "db-calf-raises": CALF_FAMILY,
}

PATTERN_FAMILY_SESSION_CAP = 1


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


def pattern_family(ex: Optional[dict]) -> Optional[str]:
    """Movement-pattern slot. None = no uniqueness cap (isolations, unique compounds)."""
    if not isinstance(ex, dict):
        return None
    explicit = str(ex.get("pattern_family") or "").strip().lower()
    if explicit:
        return explicit
    eid = str(ex.get("id") or "").strip()
    if eid in PATTERN_FAMILY_BY_ID:
        return PATTERN_FAMILY_BY_ID[eid]
    if str(ex.get("movement") or "").lower() != "compound":
        return None
    name = _norm_name(str(ex.get("name") or ""))
    prim = {normalize_muscle(m) for m in (ex.get("primary_muscles") or [])}
    if "chest" in prim:
        if "incline" in name:
            return INCLINE_PRESS_FAMILY
        if any(tok in name for tok in ("press", "bench")):
            if "shoulder" in name or "overhead" in name:
                return VERTICAL_PRESS_FAMILY
            return HORIZONTAL_PRESS_FAMILY
    if prim & {"delts"} and any(tok in name for tok in ("press", "ohp", "overhead")):
        return VERTICAL_PRESS_FAMILY
    # Pull: one vertical pull, one horizontal row. Face pulls are isolation.
    if "row" in name and "face" not in name:
        return HORIZONTAL_ROW_FAMILY
    if any(tok in name for tok in ("pulldown", "pullup", "pull-up", "chin-up", "chinup")):
        return VERTICAL_PULL_FAMILY
    # Legs: one knee-dominant compound. RDL and back extension stay uncapped.
    if "quads" in prim and any(tok in name for tok in ("squat", "leg press", "lunge")):
        return KNEE_DOMINANT_FAMILY
    return None


def last_pattern_family_ids(
    sessions: Sequence[Any],
    catalog_by_id: Optional[Dict[str, dict]] = None,
) -> Dict[str, str]:
    """Most recently logged catalog id for each pattern family."""
    catalog_by_id = catalog_by_id or {}
    found: Dict[str, str] = {}
    ordered = sorted(list(sessions), key=session_date_of, reverse=True)
    for s in ordered:
        for name in _session_exercise_names(s):
            cid = _canonical_exercise_id(name, catalog_by_id)
            if not cid:
                cid = NAME_ALIASES.get(_norm_name(name)) or _slug(name)
            cat = catalog_by_id.get(cid) if cid else None
            fam = pattern_family(cat) if cat else PATTERN_FAMILY_BY_ID.get(cid or "")
            if fam and fam not in found and cid:
                found[fam] = cid
    return found


def _session_exercise_names(session: Any) -> List[str]:
    exercises = getattr(session, "exercises", None)
    if exercises is None and isinstance(session, dict):
        exercises = session.get("exercises") or []
    names: List[str] = []
    for raw in exercises or []:
        if isinstance(raw, dict):
            name = str(raw.get("name") or "")
        else:
            name = str(getattr(raw, "name", "") or "")
        if name:
            names.append(name)
    return names


def _logged_catalog_ids(
    sessions: Sequence[Any],
    catalog_by_id: Optional[Dict[str, dict]] = None,
) -> Set[str]:
    catalog_by_id = catalog_by_id or {}
    found: Set[str] = set()
    for s in sessions or []:
        for name in _session_exercise_names(s):
            cid = _canonical_exercise_id(name, catalog_by_id)
            if not cid:
                cid = NAME_ALIASES.get(_norm_name(name)) or _slug(name)
            if cid:
                found.add(cid)
    return found


def _family_slot_taken(ex: dict, chosen: Sequence[dict]) -> bool:
    fam = pattern_family(ex)
    if not fam:
        return False
    n = sum(1 for c in chosen if pattern_family(c) == fam)
    return n >= PATTERN_FAMILY_SESSION_CAP


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
    """Allow a library movement only when every required tag is accessible.

    ``equipment_any`` is OR (barbell *or* dumbbells). Missing gear → skip.
    Safety net — not how movements enter the programmed library.
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
    """Library (available=true) minus movements current access cannot load."""
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
            note = f"Load capped at {capped:g} lb (inventory max)."
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
        if "invent" not in msg.lower() and "equipment" not in msg.lower():
            workout["message"] = (
                f"No accessible equipment can load a {st} lift from the library. "
                "Fix the equipment inventory — the planner will not invent lifts."
            )
    return workout


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def _canonical_exercise_id(
    exercise_name: str,
    catalog_by_id: Optional[Dict[str, dict]] = None,
) -> Optional[str]:
    """Catalog id from exact name or alias. Never substring (Calf Raises ⊄ DB Calf Raises)."""
    key = _norm_name(exercise_name)
    if not key:
        return None
    alias = NAME_ALIASES.get(key)
    if alias and (not catalog_by_id or alias in catalog_by_id):
        return alias
    if catalog_by_id:
        slug = _slug(exercise_name)
        if slug in catalog_by_id:
            return slug
        for eid, ex in catalog_by_id.items():
            if _norm_name(ex.get("name") or "") == key:
                return eid
    return None


def match_catalog_id(exercise_name: str, catalog_by_id: Dict[str, dict]) -> Optional[str]:
    return _canonical_exercise_id(exercise_name, catalog_by_id)


def last_performance(
    sessions: Sequence[Session],
    exercise_name: str,
    catalog_by_id: Optional[Dict[str, dict]] = None,
) -> Optional[dict]:
    """Most recent logged sets for an exercise (catalog id, exact name, or alias)."""
    target = _norm_name(exercise_name)
    target_id = _canonical_exercise_id(exercise_name, catalog_by_id)
    ordered = sorted(sessions, key=lambda s: s.date, reverse=True)
    for s in ordered:
        for ex in s.exercises:
            logged = _norm_name(ex.name)
            if logged != target:
                logged_id = _canonical_exercise_id(ex.name, catalog_by_id)
                if not target_id or not logged_id or logged_id != target_id:
                    continue
            if not ex.sets:
                continue
            best_w = max(st.weight_lbs for st in ex.sets)
            # Judge progression on the first working set. Later sets that
            # fall off do not change the load (#954).
            first = ex.sets[0]
            top = max(ex.sets, key=lambda st: (st.weight_lbs, st.reps, st.sets))
            total_sets = sum(st.sets for st in ex.sets)
            return {
                "date": s.date,
                "session_type": s.session_type,
                "weight_lbs": float(first.weight_lbs),
                "sets": int(total_sets) if total_sets else int(first.sets),
                "reps": int(first.reps),
                "first_weight_lbs": float(first.weight_lbs),
                "first_reps": int(first.reps),
                "best_working_weight": float(best_w),
                "top_weight_lbs": float(top.weight_lbs),
                "top_reps": int(top.reps),
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

    Charts and history still bucket by civil date. Planning / letter
    advancement uses ``training_day.ppl_logged_for_planning`` (wake window)
    when last_wake is known.
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


def next_letter_after(letter: Optional[str], goals: dict) -> str:
    """PPL letter after ``letter``. Missing/unknown letter → rotation[0]."""
    rotation = goals.get("rotation") or ["push", "pull", "legs"]
    rotation = [str(r).lower() for r in rotation]
    last = str(letter or "").lower()
    if not last or last not in rotation:
        return rotation[0]
    idx = rotation.index(last)
    return rotation[(idx + 1) % len(rotation)]


def next_session_type(sessions: Sequence[Any], goals: dict) -> str:
    return next_letter_after(last_session_type(sessions), goals)


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


def progression_band(catalog_ex: Optional[dict]) -> Tuple[int, int]:
    """Compound 5–9, isolation 8–15. Missing movement is a compound."""
    movement = str((catalog_ex or {}).get("movement") or "compound").strip().lower()
    if movement == "isolation":
        return ISOLATION_REP_BAND
    return COMPOUND_REP_BAND


def rep_range_label(band: Tuple[int, int]) -> str:
    return f"{int(band[0])}-{int(band[1])}"


def _is_lower_body(catalog_ex: dict) -> bool:
    prim = {
        normalize_muscle(m) for m in (catalog_ex.get("primary_muscles") or [])
    }
    return bool(prim & LOWER_BODY_MUSCLES)


def _db_step_lbs(last_weight: Optional[float]) -> float:
    """+5 lb, or +2.5 lb when the logged load sits on a 2.5 lb grid."""
    if last_weight is None:
        return 5.0
    rem = round(float(last_weight) % 5.0, 1)
    if abs(rem - 2.5) < 0.05:
        return 2.5
    return 5.0


def _implement_kind(catalog_ex: dict, equipment: Optional[dict]) -> str:
    """db | pin | bar | unknown. Optional machine beats optional smith."""
    required = set(movement_required_tags(catalog_ex))
    any_tags = set(movement_any_tags(catalog_ex))
    if isinstance(equipment, dict):
        from .equipment_store import owned_equipment_tags

        owned = owned_equipment_tags(equipment)
        if owned:
            any_tags = {t for t in any_tags if t in owned}
    if required & DB_LOAD_TAGS:
        return "db"
    if required & PIN_LOAD_TAGS:
        return "pin"
    if required & BAR_LOAD_TAGS:
        return "bar"
    if any_tags & PIN_LOAD_TAGS:
        return "pin"
    if any_tags & BAR_LOAD_TAGS:
        return "bar"
    if any_tags & DB_LOAD_TAGS:
        return "db"
    return "unknown"


def load_step_lbs(
    catalog_ex: dict,
    last_weight: Optional[float],
    equipment: Optional[dict] = None,
) -> float:
    """One load step for this implement.

    DB +5 lb (2.5 lb when the rack's last log shows it). Cable or machine
    next pin is +5 lb. Barbell or Smith is +5 lb upper body, +10 lb lower.
    """
    kind = _implement_kind(catalog_ex, equipment)
    if kind == "db":
        return _db_step_lbs(last_weight)
    if kind == "pin":
        return 5.0
    if kind == "bar":
        return 10.0 if _is_lower_body(catalog_ex) else 5.0
    return 5.0


def _shift_load(weight: float, step: float, *, up: bool) -> float:
    weight = float(weight)
    step = float(step)
    if up:
        return round(weight + step, 1)
    dropped = round(weight - step, 1)
    if dropped <= 0:
        if weight >= step:
            return round(step, 1)
        return round(weight, 1)
    return dropped


def _target_held_at_cap(judged_reps: int, hi: int) -> int:
    """+1 rep until the top of the band, then stay there."""
    if int(judged_reps) + 1 <= int(hi):
        return int(judged_reps) + 1
    return int(hi)


def resolve_load_cap(
    catalog_ex: dict,
    equipment: Optional[dict],
    explicit: Optional[float] = None,
) -> Optional[float]:
    """Equipment max for this lift. Library fallback: DBs 50, FITBENCH 30."""
    if explicit is not None:
        try:
            return float(explicit)
        except (TypeError, ValueError):
            return None
    if not isinstance(equipment, dict):
        return None
    cap = available_load_lbs(catalog_ex, equipment)
    from .equipment_store import owned_equipment_items

    items = owned_equipment_items(equipment)
    by_tag = {str(i.get("tag") or ""): i for i in items}
    tags = set(movement_required_tags(catalog_ex)) | set(movement_any_tags(catalog_ex))
    for item in items:
        blob = " ".join(
            str(item.get(k) or "") for k in ("tag", "name", "id")
        ).lower()
        if "fitbench" in blob:
            tags.add("fitbench")
    for tag, known in LIBRARY_LOAD_CAPS.items():
        if tag not in tags:
            continue
        item = by_tag.get(tag)
        item_max = None if not isinstance(item, dict) else item.get("max_weight_lbs")
        if item_max is None:
            cap = known if cap is None else min(float(cap), float(known))
    return cap


def _judged_set(last: dict) -> Tuple[float, int]:
    """First working set. Later sets do not pick the load."""
    weight = last.get("first_weight_lbs")
    reps = last.get("first_reps")
    if weight is None:
        weight = last.get("weight_lbs")
    if reps is None:
        reps = last.get("reps")
    return float(weight), int(reps)


def prescribe(
    catalog_ex: dict,
    last: Optional[dict],
    *,
    recovery_score: Optional[float] = None,
    continuity: Optional[Dict[str, Any]] = None,
    default_hard_sets: Optional[int] = None,
    rhr_under_recovered: bool = False,
    equipment: Optional[dict] = None,
    load_cap: Optional[float] = None,
    deload: bool = False,
) -> dict:
    """Double progression from the first working set of the last log.

    Compound band is 5–9. Isolation band is 8–15. One implement step on a
    miss of either end. A deload flag, RHR under-recovery, or recovery
    below 50 blocks a load increase and the 10% deload cut wins. Continuity
    phases that are not normal keep their ramp and do not progress.

    Set volume is seeded from goals.default_hard_sets, never catalog default_sets=3.
    """
    lo, hi = progression_band(catalog_ex)
    label = rep_range_label((lo, hi))
    if load_cap is None:
        load_cap = resolve_load_cap(catalog_ex, equipment)
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
    rationale = "No history for this lift — seeded from the generator."
    reason = "seed"
    target_reps = reps
    cont = continuity or training_continuity(0)
    allow_prog = bool(cont.get("allow_load_progression", True))
    load_m = float(cont.get("load_multiplier") or 1.0)
    continuity_cut = False
    anchor: Optional[float] = None
    judged_reps: Optional[int] = None

    if last:
        anchor, judged_reps = _judged_set(last)
        weight = anchor
        sets = int(last.get("sets") or sets)
        sets = max(1, min(4, sets))
        if not allow_prog:
            continuity_cut = True
            weight = round(anchor * load_m, 1)
            target_reps = lo
            reps = lo
            reason = "deload_override"
            if cont.get("phase") in ("reentry", "restart"):
                sets = max(1, min(sets, 2))
            elif cont.get("phase") == "return":
                sets = max(1, min(sets, 3))
            cut = int(cont.get("load_cut_pct") or round((1.0 - load_m) * 100))
            days = cont.get("days_since")
            days_txt = f"{days}d since last log" if days is not None else "no recent logs"
            rationale = (
                f"{cont.get('label') or 'Return'} ({days_txt}): "
                f"{anchor:g} lb last on {last['date']} → {weight:g} lb "
                f"(−{cut}%), {sets}×{reps} to re-establish before progressing."
            )
        elif judged_reps < lo:
            step = load_step_lbs(catalog_ex, anchor, equipment)
            weight = _shift_load(anchor, step, up=False)
            target_reps = lo
            reason = "drop_load"
        elif judged_reps >= hi:
            step = load_step_lbs(catalog_ex, anchor, equipment)
            nxt = _shift_load(anchor, step, up=True)
            if load_cap is not None and nxt > float(load_cap) + 1e-9:
                weight = round(float(load_cap), 1)
                target_reps = _target_held_at_cap(judged_reps, hi)
                reason = "hold_plus1"
            else:
                weight = nxt
                target_reps = lo
                reason = "add_load"
        else:
            weight = anchor
            target_reps = judged_reps + 1
            reason = "hold_plus1"
        reps = target_reps

    # Recovery under 50, an RHR flag, or an explicit deload blocks add_load.
    # Below 40 is inside that window. The 10% cut is the deload prescription.
    wants_cut = bool(deload) or bool(rhr_under_recovered) or (
        recovery_score is not None and float(recovery_score) < 50
    )
    if wants_cut and last and not continuity_cut:
        if (
            weight is not None
            and anchor is not None
            and weight > anchor + 1e-9
        ):
            weight = anchor
            target_reps = lo
            reps = lo
        reason = "deload_override"
    if wants_cut and weight is not None:
        before = weight
        weight = round(float(weight) * 0.9, 1)
        # Continuity keeps the ramp sentence written above. Other paths
        # rebuild the rationale after this cut.
        if continuity_cut and weight < before - 1e-9:
            if rhr_under_recovered:
                rationale += " RHR under-recovered → ~10% load deload."
            elif deload and (
                recovery_score is None or float(recovery_score) >= 50
            ):
                rationale += " Deload flag → ~10% load deload."
            else:
                rationale += " Recovery moderate/low → ~10% load deload."

    capped_to_max = False
    if load_cap is not None and weight is not None and weight > float(load_cap) + 1e-9:
        weight = round(float(load_cap), 1)
        capped_to_max = True
        if reason == "add_load":
            reason = "hold_plus1"
            judged = int(judged_reps if judged_reps is not None else hi)
            target_reps = _target_held_at_cap(judged, hi)
            reps = target_reps

    if last and not continuity_cut:
        when = last.get("date") or "last session"
        shown = f"{anchor:g}" if anchor is not None else "?"
        got = f"{weight:g}" if weight is not None else "—"
        if reason == "add_load":
            rationale = (
                f"First working set {judged_reps} at the top of {label} "
                f"on {when} @ {shown} lb → {got} lb, target {target_reps}."
            )
        elif reason == "drop_load":
            rationale = (
                f"First working set {judged_reps} under {label} "
                f"on {when} @ {shown} lb → {got} lb, target {target_reps}."
            )
        elif reason == "hold_plus1":
            if judged_reps is not None and judged_reps >= hi:
                rationale = (
                    f"First working set {judged_reps} at the top of {label} "
                    f"on {when} @ {shown} lb stays {got} lb, target {target_reps}."
                )
            else:
                rationale = (
                    f"First working set {judged_reps} inside {label} "
                    f"on {when} @ {shown} lb → {got} lb, target {target_reps}."
                )
        elif reason == "deload_override":
            rationale = (
                f"Deload override on {when} @ {shown} lb → {got} lb, "
                f"target {target_reps}. No load increase."
            )
            if rhr_under_recovered:
                rationale += " RHR under-recovered → ~10% load deload."
            elif deload and (
                recovery_score is None or float(recovery_score) >= 50
            ):
                rationale += " Deload flag → ~10% load deload."
            elif recovery_score is not None and float(recovery_score) < 50:
                rationale += " Recovery moderate/low → ~10% load deload."

    if capped_to_max and weight is not None:
        rationale += f" Load held at {weight:g} lb (equipment max)."

    return {
        "weight_lbs": weight,
        "load": weight,
        "sets": sets,
        "reps": int(target_reps),
        "target_reps": int(target_reps),
        "rep_range": [lo, hi],
        "rep_range_label": label,
        "progression_reason": reason,
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
    recovery_rhr_under: bool = False,
    deload: bool = False,
    session_type: Optional[str] = None,
    as_of: Optional[str] = None,
    equipment: Optional[dict] = None,
    train_parent_completed: bool = False,
    last_wake_at: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict:
    """
    Build today's workout from catalog + history + recovery.

    Similar role to generate_meal_plan for nutrition.

    When ``recovery_sparse`` is True (e.g. no real sleep logs from Health yet),
    a low recovery score does not force a rest day — zero-filled sleep debt
    would otherwise score ~30 Caution and blank the plan on cold cache.

    If a PPL session already closed in the current wake window (or on
    civil ``as_of`` when last_wake is unknown), ``session_type`` stays
    that letter and the exercise list is the next rotation letter (#951).
    A partial log (no close stamp) still pins the same letter. Pass an
    explicit ``session_type`` to force that letter.

    Day-complete (``already_trained_today``) is ``train_parent_completed``
    on the civil fallback. When last_wake is current it also requires a
    PPL session in that wake — parent complete alone must not pin the
    next letter. A partial session row is a pin, not a finished day.
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
    from .training_day import (
        closed_ppl_for_planning,
        day_complete_for_planning,
        ppl_logged_for_planning,
    )

    logged_today = ppl_logged_for_planning(
        sessions,
        as_of=day,
        last_wake_at=last_wake_at,
        now=now,
    )
    closed_letter = closed_ppl_for_planning(
        sessions,
        as_of=day,
        last_wake_at=last_wake_at,
        now=now,
    )
    force_letter = explicit in ("push", "pull", "legs")
    day_complete = day_complete_for_planning(
        train_parent_completed,
        ppl_logged_today=logged_today,
        last_wake_at=last_wake_at,
        as_of=day,
        now=now,
    )
    # A persisted close rolls the plan onto the next letter (#951).
    # Parent-complete without a close stamp still returns the empty
    # "already trained" shell. An explicit session_type still generates
    # that letter.
    plan_next = bool(closed_letter) and not force_letter
    # Parent complete pins only when this wake actually trained (or civil fallback).
    if day_complete and not force_letter and not plan_next:
        pin = logged_today or next_session_type(sessions, goals)
        nxt = next_letter_after(pin, goals) if pin in ("push", "pull", "legs") else pin
        balance = volume_balance_report(tally, goals)
        balance["suggested_focus"] = focus_res.get("suggested") or suggest_focus_muscles(
            tally, goals
        )
        balance["focus"] = {
            "muscles": goals.get("focus_muscles") or [],
            "source": focus_res.get("source"),
            "reason": focus_res.get("reason"),
        }
        st_done = pin if pin in ("push", "pull", "legs") else logged_today or "session"
        return {
            "date": day,
            "session_type": st_done,
            "is_rest_day": False,
            "already_trained_today": True,
            "ppl_logged_today": logged_today,
            "exercises": [],
            "next_session_type": nxt,
            "message": (
                f"Already trained today ({str(st_done).upper()}). "
                f"Next session: {str(nxt).upper()} tomorrow."
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
                "ppl_logged_today": logged_today,
            },
        }

    pin_open_session = bool(logged_today) and not force_letter and not plan_next
    if (
        not pin_open_session
        and not plan_next
        and recovery_score is not None
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

    if plan_next:
        st = next_letter_after(closed_letter, goals)
    else:
        st = (
            session_type
            or (logged_today if pin_open_session else None)
            or next_session_type(sessions, goals)
        ).lower()
    pool = [ex for ex in available if st in ex["session_types"]]
    # Do not steal lifts from another PPL slot or invent unequipped gear.
    if not pool and not equipment_on:
        pool = list(available)

    bands = scale_muscle_targets_for_continuity(muscle_targets(goals), continuity)
    focus = {normalize_muscle(m) for m in (goals.get("focus_muscles") or [])}
    done: Dict[str, float] = dict(tally.get("by_muscle") or {})
    last_family_ids = last_pattern_family_ids(sessions, by_id)
    logged_ids = _logged_catalog_ids(sessions, by_id)
    if plan_next:
        # Next letter is a full session. Do not shrink it by today's close.
        today_sessions = []
    elif last_wake_at:
        from .training_day import session_in_wake as _in_wake
        from .training_day import wake_covers_as_of as _wake_ok

        if _wake_ok(last_wake_at, day, now=now):
            today_sessions = [
                s
                for s in sessions
                if _in_wake(s, last_wake_at=last_wake_at, now=now)
            ]
        else:
            today_sessions = [s for s in sessions if session_date_of(s) == day]
    else:
        today_sessions = [s for s in sessions if session_date_of(s) == day]
    today_logged_ids = _logged_catalog_ids(today_sessions, by_id)
    today_logged_names = {
        _norm_name(n)
        for s in today_sessions
        for n in _session_exercise_names(s)
        if _norm_name(n)
    }

    def _repeat_penalty(e: dict) -> int:
        # Rotate implements inside a family (DB flat ↔ Smith), don't restack last.
        fam = pattern_family(e)
        if not fam:
            return 0
        last_id = last_family_ids.get(fam)
        if not last_id or e.get("id") != last_id:
            return 0
        # New calf row stays off Today until Chris has actually logged it.
        # Cap still prevents stacking both in one session.
        if fam == CALF_FAMILY and "db-calf-raises" not in logged_ids:
            return 0
        return 1

    # Rank pool by volume need (under-target muscles) + compound efficiency
    pool_scored = sorted(
        pool,
        key=lambda e: (
            _repeat_penalty(e),
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
    n_logged_today = max(len(today_logged_ids), len(today_logged_names))
    if n_logged_today:
        n = max(0, n - n_logged_today)
    base_cap = max(6, int(goals.get("session_working_set_cap") or 14))
    session_cap = max(4, int(round(base_cap * float(continuity.get("session_cap_scale") or 1.0))))
    default_hard = max(1, min(4, int(goals.get("default_hard_sets") or 2)))
    if continuity.get("phase") in ("reentry", "restart"):
        default_hard = min(default_hard, 2)
    elif continuity.get("phase") == "return":
        default_hard = min(default_hard, 2)

    def _already_logged_today(e: dict) -> bool:
        eid = str(e.get("id") or "")
        if eid and eid in today_logged_ids:
            return True
        return _norm_name(str(e.get("name") or "")) in today_logged_names

    chosen: List[dict] = []
    # Seed with top compound if compounds preferred
    if n and goals.get("prefer_compounds_first", True):
        for e in pool_scored:
            if _already_logged_today(e):
                continue
            if e.get("movement") == "compound" and not _family_slot_taken(e, chosen):
                chosen.append(e)
                break
    for e in pool_scored:
        if len(chosen) >= n:
            break
        if _already_logged_today(e):
            continue
        if e["id"] in {c["id"] for c in chosen}:
            continue
        if _family_slot_taken(e, chosen):
            continue
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
        last = last_performance(sessions, ex["name"], by_id)
        if not last:
            for alias, aid in NAME_ALIASES.items():
                if aid == ex["id"]:
                    last = last_performance(sessions, alias, by_id)
                    if last:
                        break
        # Volume from goals.default_hard_sets — never catalog default_sets=3
        ex_rx = dict(ex)
        ex_rx["default_sets"] = default_hard
        equip_for_rx = equipment if equipment_on else None
        load_cap = resolve_load_cap(ex, equip_for_rx)
        rx = prescribe(
            ex_rx,
            last,
            recovery_score=recovery_score,
            continuity=continuity,
            default_hard_sets=default_hard,
            rhr_under_recovered=bool(recovery_rhr_under),
            equipment=equip_for_rx,
            load_cap=load_cap,
            deload=bool(deload),
        )
        capped_w, _inv_cap, was_capped = cap_weight_to_inventory(
            rx.get("weight_lbs"), ex, equip_for_rx
        )
        if was_capped:
            rx["weight_lbs"] = capped_w
            rx["load"] = capped_w
            if rx.get("progression_reason") == "add_load":
                _lo, _hi = rx.get("rep_range") or progression_band(ex)
                judged = int((rx.get("last") or {}).get("reps") or _hi)
                rx["target_reps"] = _target_held_at_cap(judged, int(_hi))
                rx["reps"] = rx["target_reps"]
                rx["progression_reason"] = "hold_plus1"
            rx["rationale"] = (
                f"{rx['rationale']} Load capped at {capped_w:g} lb "
                f"(owned max)."
            )
        elif capped_w is not None:
            rx["weight_lbs"] = capped_w
            rx["load"] = capped_w
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
                "load": rx.get("load"),
                "rep_range": rx.get("rep_range_label"),
                "target_reps": rx.get("target_reps"),
                "progression_reason": rx.get("progression_reason"),
                "prescription": {
                    "weight_lbs": rx["weight_lbs"],
                    "load": rx.get("load"),
                    "sets": rx["sets"],
                    "reps": rx["reps"],
                    "rep_range": rx["rep_range"],
                    "rep_range_label": rx.get("rep_range_label"),
                    "target_reps": rx.get("target_reps"),
                    "progression_reason": rx.get("progression_reason"),
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
    empty_plan_error = None
    if not plan_ex:
        if equipment_on:
            empty_plan_error = (
                f"No accessible equipment can load a {st.upper()} lift from the library. "
                "Fix the equipment inventory — the planner will not invent lifts."
            )
        else:
            empty_plan_error = (
                f"No {st.upper()} plan could be built from the library."
            )
        msg_parts = [empty_plan_error]
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

    if plan_next:
        display_session = closed_letter
        nxt_open = st
        trained = True
        session_closed = True
        logged_out = closed_letter
    else:
        display_session = st
        nxt_open = logged_today if pin_open_session else next_session_type(sessions, goals)
        trained = False
        session_closed = False
        logged_out = logged_today
    if plan_next and plan_ex:
        message = (
            f"Already trained today ({str(display_session).upper()}). "
            f"Next session: {str(nxt_open).upper()}."
        )
    else:
        message = " · ".join(msg_parts)
    out = {
        "date": day,
        "session_type": display_session,
        "is_rest_day": False,
        "already_trained_today": trained,
        "session_closed_today": session_closed,
        "ppl_logged_today": logged_out,
        "next_session_type": nxt_open,
        "exercises": plan_ex,
        "message": message,
        "goals": goals,
        "volume": balance,
        "context": {
            "recovery_label": recovery_label,
            "recovery_score": recovery_score,
            "last_session_type": last_st,
            "next_session_type": nxt_open,
            "already_trained_today": trained,
            "session_closed_today": session_closed,
            "ppl_logged_today": logged_out,
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
    if empty_plan_error:
        out["generate_error"] = empty_plan_error
        out["message"] = empty_plan_error
        out["context"]["generate_error"] = empty_plan_error
    return out


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
