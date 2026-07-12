"""Merge lift sessions from multiple sources (remote GitHub + local workspace)."""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

from .models import Session


def session_key(s: Session) -> Tuple[str, str, str]:
    """Identity key: date + type + exercise fingerprint."""
    names = "|".join(sorted(e.name.lower() for e in s.exercises))
    return (s.date, s.session_type.lower(), names)


def merge_sessions(
    *sources: Sequence[Session],
    prefer_first: bool = True,
) -> List[Session]:
    """
    Union sessions from multiple sources.

    Sources are ordered by priority when prefer_first=True (earlier wins on key collision).
    When prefer_first=False, later sources overwrite.
    """
    by_key = {}
    order: List[Tuple[str, str, str]] = []
    source_list = sources if prefer_first else tuple(reversed(sources))
    for group in source_list:
        for s in group:
            k = session_key(s)
            if k not in by_key:
                by_key[k] = s
                order.append(k)
            elif not prefer_first:
                by_key[k] = s
    # If prefer_first, first source wins — already handled by only inserting missing keys.
    # Rebuild order: all keys, sort by date desc then type
    merged = list(by_key.values())
    merged.sort(key=lambda s: (s.date, s.session_type), reverse=True)
    return merged


def local_only_sessions(
    local: Sequence[Session], remote: Sequence[Session]
) -> List[Session]:
    remote_keys = {session_key(s) for s in remote}
    return [s for s in local if session_key(s) not in remote_keys]
