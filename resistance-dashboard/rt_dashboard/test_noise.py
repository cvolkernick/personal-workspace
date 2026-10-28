"""Filter synthetic / canary exercises from planning and trends."""

from __future__ import annotations

import re
from typing import Any, Iterable, List, Sequence

from .models import Session

# Names from e2e canaries / probes that should not drive plans or charts.
_TEST_NAME_RE = re.compile(
    r"(skeptic|persist\s*probe|canary|e2e\s*test|probe\s*press)",
    re.IGNORECASE,
)


def is_test_exercise_name(name: str) -> bool:
    return bool(_TEST_NAME_RE.search(name or ""))


def coerce_sessions(sessions: Iterable[Any] | None) -> List[Session]:
    """Return ``Session`` objects for a mixed list of objects and ``to_dict()`` payloads.

    ``load_dashboard_data`` carries ``sessions`` as dicts (``sessions_to_dicts``),
    while the planners read ``.exercises``. Objects pass through untouched;
    dicts are rebuilt with ``sessions_from_dicts``; anything else is dropped.
    """
    from .dashboard_cache import sessions_from_dicts

    out: List[Session] = []
    for item in sessions or []:
        if isinstance(item, Session):
            out.append(item)
        elif isinstance(item, dict):
            out.extend(sessions_from_dicts([item]))
        elif hasattr(item, "exercises"):
            out.append(item)  # duck-typed session object
    return out


def filter_sessions(sessions: Sequence[Session]) -> List[Session]:
    """Drop sessions that only contain test/canary exercises; strip test lifts otherwise.

    Accepts ``Session`` objects or their ``to_dict()`` payloads (#1242).
    """
    out: List[Session] = []
    for s in coerce_sessions(sessions):
        real = [e for e in s.exercises if not is_test_exercise_name(e.name)]
        if not real:
            continue
        if len(real) == len(s.exercises):
            out.append(s)
        else:
            out.append(
                Session(
                    date=s.date,
                    session_type=s.session_type,
                    exercises=real,
                    notes=s.notes,
                    source_file=s.source_file,
                    closed_at=s.closed_at,
                    status=s.status,
                    submission_id=s.submission_id,
                    plan_objective=s.plan_objective,
                    skipped=s.skipped,
                    substitutions=s.substitutions,
                )
            )
    return out
