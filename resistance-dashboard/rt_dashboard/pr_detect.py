"""Automatic PR tagging from historical lift logs."""

from __future__ import annotations

import re
from typing import Dict, Optional, Sequence, Tuple

from .models import ExerciseEntry, Session
from .test_noise import is_test_exercise_name


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def historical_bests(
    sessions: Sequence[Session],
    *,
    before_date: Optional[str] = None,
) -> Dict[str, Tuple[float, float]]:
    """
    Map normalized exercise name -> (best_working_weight, best_e1rm)
    for sessions strictly before ``before_date`` (ISO YYYY-MM-DD) when set.
    """
    best: Dict[str, Tuple[float, float]] = {}
    for s in sessions:
        if before_date and s.date >= before_date:
            continue
        for ex in s.exercises:
            if is_test_exercise_name(ex.name):
                continue
            key = _norm(ex.name)
            if not key:
                continue
            w = float(ex.best_working_weight or 0.0)
            e = float(ex.best_e1rm or 0.0)
            prev = best.get(key)
            if prev is None:
                best[key] = (w, e)
            else:
                best[key] = (max(prev[0], w), max(prev[1], e))
    return best


def is_exercise_pr(
    entry: ExerciseEntry,
    history_bests: Dict[str, Tuple[float, float]],
    *,
    weight_eps: float = 0.25,
    e1rm_eps: float = 0.5,
) -> bool:
    """
    True if this entry is a personal best vs history.

    - First time logging the exercise name → PR
    - Best working weight higher than any prior → PR
    - Best estimated 1RM higher than any prior (same weight, more reps) → PR
    """
    if is_test_exercise_name(entry.name):
        return False
    key = _norm(entry.name)
    if not key or not entry.sets:
        return False
    w = float(entry.best_working_weight or 0.0)
    e = float(entry.best_e1rm or 0.0)
    prior = history_bests.get(key)
    if prior is None:
        return True  # first logged instance of this lift
    prior_w, prior_e = prior
    if w > prior_w + weight_eps:
        return True
    if e > prior_e + e1rm_eps:
        return True
    return False


def apply_auto_prs(session: Session, history: Sequence[Session]) -> Session:
    """Return session with ``is_pr`` set from history (prior dates only)."""
    bests = historical_bests(history, before_date=session.date)
    for ex in session.exercises:
        ex.is_pr = is_exercise_pr(ex, bests)
    return session
