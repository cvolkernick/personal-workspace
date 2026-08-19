"""Load/save exercise catalog + training goals (local workspace / GitHub)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .github_client import GitHubLiftClient
from .nutrition_store import read_nutrition_file, write_nutrition_file
from .workout_planner import (
    CATALOG_PATH,
    DEFAULT_CATALOG,
    GOALS_PATH,
    default_catalog,
    default_goals,
    load_json_file,
    normalize_goals,
)


def _workspace_file_candidates(rel: str) -> list:
    """Repo-root SoT first, then the Vercel-bundled copy under resistance-dashboard/."""
    here = Path(__file__).resolve()
    rel_path = Path(rel)
    ordered = []
    # rt_dashboard/workout_store.py → parents[2] = repo root
    if len(here.parents) >= 3:
        ordered.append(here.parents[2] / rel_path)
    # parents[1] = resistance-dashboard (Vercel project root)
    if len(here.parents) >= 2:
        ordered.append(here.parents[1] / rel_path)
    cwd = Path.cwd().resolve()
    ordered.append(cwd / rel_path)
    for parent in cwd.parents:
        ordered.append(parent / rel_path)
    seen = set()
    out = []
    for cand in ordered:
        try:
            resolved = cand.resolve()
        except OSError:
            continue
        if resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
    return out


def load_workspace_goals() -> Tuple[dict, str]:
    """Read fitness/exercises/goals.json (same file Pi uses).

    Vercel Root Directory is resistance-dashboard/, so a byte-identical copy
    ships at resistance-dashboard/fitness/exercises/goals.json (includeFiles).
    Source is GOALS_PATH when the file is found, else "default".
    """
    for path in _workspace_file_candidates(GOALS_PATH):
        if not path.is_file():
            continue
        raw = load_json_file(path, {})
        if not raw:
            continue
        return normalize_goals(raw), GOALS_PATH
    return normalize_goals(default_goals()), "default"


def load_workspace_catalog() -> Tuple[dict, str]:
    """Read fitness/exercises/catalog.json (full file; ~7KB / 19 exercises).

    Same SoT-then-bundle walk as targets.json / goals.json.
    Source is CATALOG_PATH when the file is found, else "default".
    """
    for path in _workspace_file_candidates(CATALOG_PATH):
        if not path.is_file():
            continue
        raw = load_json_file(path, {})
        if not isinstance(raw, dict) or not raw:
            continue
        if not isinstance(raw.get("exercises"), list):
            raw = {**raw, "exercises": []}
        return raw, CATALOG_PATH
    return dict(DEFAULT_CATALOG), "default"



def apply_goals_volume_caps(catalog: dict, goals: dict) -> dict:
    """Catalog supplies names/movements. Set caps come from goals, never default_sets=3.

    Blind-wiring catalog default_sets=3 is junk volume (DeanT 4–8 / default_hard_sets).
    """
    from .workout_planner import normalize_goals

    goals = normalize_goals(goals if isinstance(goals, dict) else None)
    hard = max(1, int(goals.get("default_hard_sets") or 2))
    out = deepcopy(catalog) if isinstance(catalog, dict) else {"exercises": []}
    for ex in out.get("exercises") or []:
        if not isinstance(ex, dict):
            continue
        ex["default_sets"] = hard
        ex["volume_from"] = "goals"
    return out


def catalog_names(catalog: Optional[dict]) -> List[str]:
    names: List[str] = []
    for ex in ((catalog or {}).get("exercises") or []):
        if isinstance(ex, dict) and ex.get("name"):
            names.append(str(ex["name"]))
    return names


def flatten_logged_exercise(ex: dict) -> dict:
    """Ask/planner pack: name + weight/sets/reps (nested Turso sets flattened)."""
    raw_sets = ex.get("sets")
    slim = []
    if isinstance(raw_sets, list):
        for st in raw_sets[:8]:
            if not isinstance(st, dict):
                continue
            slim.append(
                {
                    "weight_lbs": st.get("weight_lbs"),
                    "sets": st.get("sets"),
                    "reps": st.get("reps"),
                }
            )
    weight = ex.get("weight_lbs") or ex.get("best_working_weight")
    reps = ex.get("reps")
    nsets = raw_sets if not isinstance(raw_sets, list) else None
    if slim:
        if weight is None:
            weight = slim[0].get("weight_lbs")
        if reps is None:
            reps = slim[0].get("reps")
        if nsets is None:
            try:
                nsets = sum(int(st.get("sets") or 1) for st in slim)
            except (TypeError, ValueError):
                nsets = len(slim)
    return {
        "name": ex.get("name"),
        "weight_lbs": weight,
        "sets": slim or nsets,
        "reps": reps,
        "volume": ex.get("volume"),
    }


def brief_sessions(sessions: Sequence[Any], limit: int = 5) -> List[dict]:
    out: List[dict] = []
    for s in list(sessions or [])[: max(1, min(5, int(limit)))]:
        d = s.to_dict() if hasattr(s, "to_dict") else (s if isinstance(s, dict) else None)
        if not isinstance(d, dict):
            continue
        exercises = [
            flatten_logged_exercise(ex)
            for ex in (d.get("exercises") or [])[:12]
            if isinstance(ex, dict)
        ]
        out.append(
            {
                "date": d.get("date"),
                "session_type": d.get("session_type") or d.get("type"),
                "exercises": exercises,
                "total_volume": d.get("volume") or d.get("total_volume"),
            }
        )
    return out


def next_session_brief(sessions: Sequence[Any], goals: dict) -> Dict[str, Any]:
    """One-line next PPL slot from rotation + last logged session. Not a plan."""
    from .workout_planner import last_session_type, next_session_type, normalize_goals

    goals = normalize_goals(goals if isinstance(goals, dict) else None)
    next_st = next_session_type(sessions, goals)
    last_st = last_session_type(sessions)
    last_date = None
    ppl = [
        s
        for s in (sessions or [])
        if getattr(s, "session_type", None) in ("push", "pull", "legs")
    ]
    if ppl:
        last_date = max(ppl, key=lambda s: s.date).date
    if last_st and last_date:
        line = f"Next session: {next_st.upper()} (PPL after last {last_st} on {last_date})"
    elif last_st:
        line = f"Next session: {next_st.upper()} (PPL after last {last_st})"
    else:
        line = f"Next session: {next_st.upper()} (PPL rotation)"
    return {
        "next_session_type": next_st,
        "last_session_type": last_st,
        "last_session_date": last_date,
        "line": line,
    }


def build_training_pack(
    goals: dict,
    catalog: dict,
    sessions: Sequence[Any],
    *,
    next_brief: Optional[dict] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Ask/dashboard pack: goals + last 3–5 sessions + catalog names + next slot."""
    brief = next_brief or next_session_brief(sessions, goals)
    return {
        "goals": goals,
        "sessions": brief_sessions(sessions, limit=limit),
        "catalog_names": catalog_names(catalog),
        "next_session_type": brief.get("next_session_type"),
        "next_session_line": brief.get("line"),
    }


def rest_gate(
    goals: dict,
    recovery: Optional[dict],
    *,
    sparse: Optional[bool] = None,
) -> Dict[str, Any]:
    """Force rest when score < goals.rest_if_recovery_below AND data is not sparse.

    Caution 30–39 still rests (threshold is 40, not the Needs Rest label < 30).
    Sparse sleep (missing logs looking like low recovery) must not trigger rest.
    """
    goals = normalize_goals(goals if isinstance(goals, dict) else None)
    threshold = int(goals.get("rest_if_recovery_below") or 40)
    rec = recovery if isinstance(recovery, dict) else {}
    if sparse is None:
        sparse = bool(rec.get("sparse"))
    score = rec.get("score")
    try:
        score_f = float(score) if score is not None else None
    except (TypeError, ValueError):
        score_f = None
    force = score_f is not None and score_f < threshold and not bool(sparse)
    reason = None
    if force:
        reason = (
            f"Recovery score {score_f:.0f} is below threshold "
            f"({threshold}). Suggested rest or light walk/mobility only."
        )
    return {
        "force_rest": force,
        "threshold": threshold,
        "score": score_f,
        "sparse": bool(sparse),
        "reason": reason,
    }


def apply_rest_gate(
    workout_plan: Optional[dict],
    goals: dict,
    recovery: Optional[dict],
    *,
    sparse: Optional[bool] = None,
) -> dict:
    """Stamp rest-gate as planner INPUT. Never omit the slot or next PPL.

    Grok may emit a rest day as the plan. That is a plan, not an empty hole.
    """
    gate = rest_gate(goals, recovery, sparse=sparse)
    plan = dict(workout_plan) if isinstance(workout_plan, dict) else {}
    stamp = {
        "force_rest": gate["force_rest"],
        "threshold": gate["threshold"],
        "sparse": gate["sparse"],
        "score": gate["score"],
        "reason": gate.get("reason"),
    }
    plan["rest_gate"] = stamp
    ctx = dict(plan.get("context") or {})
    ctx["rest_gate"] = stamp
    ctx["rest_if_recovery_below"] = gate["threshold"]
    plan["context"] = ctx
    return plan


def load_catalog_and_goals(client: GitHubLiftClient) -> Dict[str, Any]:
    catalog, cat_src = read_nutrition_file(client, CATALOG_PATH, default_catalog())
    goals, goals_src = read_nutrition_file(
        client, GOALS_PATH, normalize_goals(default_goals())
    )
    # Seed local files on first run
    if client.local_fallback_dir:
        base = Path(client.local_fallback_dir)
        cat_path = base / CATALOG_PATH
        goals_path = base / GOALS_PATH
        if not cat_path.is_file() and (catalog.get("exercises") or []):
            from .workout_planner import save_json_file

            save_json_file(cat_path, catalog)
        if not goals_path.is_file():
            from .workout_planner import save_json_file

            save_json_file(goals_path, normalize_goals(goals))
    return {
        "catalog": catalog if isinstance(catalog, dict) else default_catalog(),
        "goals": normalize_goals(goals if isinstance(goals, dict) else None),
        "sources": {"catalog": cat_src, "goals": goals_src},
    }


def write_catalog(client: GitHubLiftClient, catalog: dict, message: str) -> dict:
    return write_nutrition_file(client, CATALOG_PATH, catalog, message=message)


def write_goals(client: GitHubLiftClient, goals: dict, message: str) -> dict:
    return write_nutrition_file(
        client, GOALS_PATH, normalize_goals(goals), message=message
    )
