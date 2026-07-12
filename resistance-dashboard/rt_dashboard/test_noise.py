"""Filter synthetic / canary exercises from planning and trends."""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence

from .models import Session

# Names from e2e canaries / probes that should not drive plans or charts.
_TEST_NAME_RE = re.compile(
    r"(skeptic|persist\s*probe|canary|e2e\s*test|probe\s*press)",
    re.IGNORECASE,
)


def is_test_exercise_name(name: str) -> bool:
    return bool(_TEST_NAME_RE.search(name or ""))


def filter_sessions(sessions: Sequence[Session]) -> List[Session]:
    """Drop sessions that only contain test/canary exercises; strip test lifts otherwise."""
    out: List[Session] = []
    for s in sessions:
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
                )
            )
    return out
