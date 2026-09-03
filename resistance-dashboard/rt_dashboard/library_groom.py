"""Coach suggestions: add/remove from the programmed exercise library.

Equipment is the constraint. Catalog available=true is the library. Suggest
≠ apply. Home-loadable movements score up (transition off the gym). Mentzer:
do not dump junk-volume duplicates onto the library.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .workout_planner import (
    movement_feasible,
    normalize_exercise,
)


HOME_TAGS = frozenset({"dumbbells"})


def _sessions_names(sessions: Sequence[Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for sess in sessions or []:
        exercises = getattr(sess, "exercises", None)
        if exercises is None and isinstance(sess, dict):
            exercises = sess.get("exercises") or []
        for ex in exercises or []:
            name = ""
            if hasattr(ex, "name"):
                name = str(ex.name or "")
            elif isinstance(ex, dict):
                name = str(ex.get("name") or "")
            key = name.strip().lower()
            if key:
                counts[key] = counts.get(key, 0) + 1
    return counts


def _is_home_only(ex: dict) -> bool:
    req = [str(t).lower() for t in (ex.get("equipment") or []) if t]
    any_tags = [str(t).lower() for t in (ex.get("equipment_any") or []) if t]
    tags = set(req) | set(any_tags)
    if not tags:
        return False
    return tags <= HOME_TAGS


def _library_primary_counts(library: List[dict]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for ex in library:
        if ex.get("movement") != "compound":
            continue
        for m in ex.get("primary_muscles") or []:
            key = str(m).lower()
            counts[key] = counts.get(key, 0) + 1
    return counts


def suggest_library_additions(
    catalog: dict,
    equipment: Optional[dict],
    sessions: Optional[Sequence[Any]] = None,
    max_suggestions: int = 6,
) -> dict:
    """Feasible catalog rows that are not in the library, ranked for add."""
    rows: List[dict] = []
    library: List[dict] = []
    for raw in (catalog or {}).get("exercises") or []:
        if not isinstance(raw, dict):
            continue
        try:
            ex = normalize_exercise(raw)
        except ValueError:
            continue
        if ex["available"]:
            library.append(ex)
        else:
            rows.append(ex)
    log_counts = _sessions_names(sessions or [])
    primary_counts = _library_primary_counts(library)
    suggestions: List[dict] = []
    for ex in rows:
        if not movement_feasible(ex, equipment):
            continue
        logs = log_counts.get(ex["name"].lower(), 0)
        home = _is_home_only(ex)
        primaries = [str(m).lower() for m in (ex.get("primary_muscles") or [])]
        covered = sum(primary_counts.get(m, 0) for m in primaries)
        # Mentzer: skip a compound add when that muscle already has 2+ library compounds
        # unless it is a home-transition movement or it shows up in logs.
        if (
            ex.get("movement") == "compound"
            and covered >= 2
            and not home
            and logs == 0
        ):
            continue
        score = 40.0
        reason_bits = []
        if home:
            score += 25
            reason_bits.append("home-loadable (DBs)")
        if logs:
            score += 20 + min(logs, 5) * 2
            reason_bits.append(f"logged {logs}×")
        if covered == 0 and primaries:
            score += 15
            reason_bits.append("fills a library gap")
        elif home and covered:
            reason_bits.append("gym already covers this; home option for mixed days")
        if not reason_bits:
            reason_bits.append("feasible with current access, not in the library")
        suggestions.append(
            {
                "id": ex["id"],
                "name": ex["name"],
                "action": "add",
                "session_types": ex.get("session_types") or [],
                "primary_muscles": ex.get("primary_muscles") or [],
                "equipment": ex.get("equipment") or [],
                "movement": ex.get("movement"),
                "reason": "; ".join(reason_bits) + ".",
                "score": round(score, 1),
                "home": home,
            }
        )
    suggestions.sort(key=lambda s: (-float(s["score"]), str(s["name"])))
    top = suggestions[: max(0, int(max_suggestions))]
    if not top:
        summary = "No off-library movements are feasible with current access."
    else:
        summary = (
            "Feasible movements not in the library. Apply is explicit — "
            "adding gear does not dump them onto Today."
        )
    return {"suggestions": top, "summary": summary, "count": len(top)}


def suggest_library_removals(
    catalog: dict,
    equipment: Optional[dict],
    sessions: Optional[Sequence[Any]] = None,
    max_suggestions: int = 6,
) -> dict:
    """Library rows we cannot load, or unused junk-volume isolations."""
    log_counts = _sessions_names(sessions or [])
    library: List[dict] = []
    for raw in (catalog or {}).get("exercises") or []:
        if not isinstance(raw, dict):
            continue
        try:
            ex = normalize_exercise(raw)
        except ValueError:
            continue
        if ex["available"]:
            library.append(ex)
    suggestions: List[dict] = []
    for ex in library:
        if not movement_feasible(ex, equipment):
            suggestions.append(
                {
                    "id": ex["id"],
                    "name": ex["name"],
                    "action": "remove",
                    "session_types": ex.get("session_types") or [],
                    "primary_muscles": ex.get("primary_muscles") or [],
                    "equipment": ex.get("equipment") or [],
                    "reason": "In the library but current access cannot load it.",
                    "score": 90.0,
                }
            )
            continue
        logs = log_counts.get(ex["name"].lower(), 0)
        if (
            ex.get("movement") == "isolation"
            and logs == 0
            and int(ex.get("priority") or 0) <= 4
        ):
            suggestions.append(
                {
                    "id": ex["id"],
                    "name": ex["name"],
                    "action": "remove",
                    "session_types": ex.get("session_types") or [],
                    "primary_muscles": ex.get("primary_muscles") or [],
                    "equipment": ex.get("equipment") or [],
                    "reason": "Low-priority isolation with no logs — junk volume candidate.",
                    "score": 35.0,
                }
            )
    suggestions.sort(key=lambda s: (-float(s["score"]), str(s["name"])))
    top = suggestions[: max(0, int(max_suggestions))]
    if not top:
        summary = "Library rows match current access."
    else:
        summary = "Apply is explicit. Removing drops the row from Today, not from the catalog."
    return {"suggestions": top, "summary": summary, "count": len(top)}
