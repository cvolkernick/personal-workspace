"""Local-calendar helpers. Viewer civil day is a per-request IANA zone."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

FALLBACK_TZ = "America/New_York"


def resolve_tz_name(preferred: Optional[str] = None) -> str:
    """IANA zone: preferred, else DASHBOARD_TZ, else America/New_York.

    Ignores process TZ (Vercel sets TZ=UTC). Invalid names are skipped.
    America/New_York is fallback only, not a hardcoded product timezone.
    """
    candidates = (
        (preferred or "").strip(),
        (os.environ.get("DASHBOARD_TZ") or "").strip(),
        FALLBACK_TZ,
    )
    for name in candidates:
        if not name:
            continue
        try:
            ZoneInfo(name)
            return name
        except Exception:
            continue
    return FALLBACK_TZ


def local_tz_name(preferred: Optional[str] = None) -> str:
    return resolve_tz_name(preferred)


def local_tz(preferred: Optional[str] = None):
    return ZoneInfo(resolve_tz_name(preferred))


def local_now(preferred: Optional[str] = None, *, now: Optional[datetime] = None) -> datetime:
    """Current instant in the viewer (or fallback) zone, not process TZ.

    Vercel sets TZ=UTC; this always returns an aware datetime in
    ``resolve_tz_name(preferred)`` (request / DASHBOARD_TZ / America/New_York).
    """
    clock = now if now is not None else datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return clock.astimezone(local_tz(preferred))


def local_today_iso(preferred: Optional[str] = None, *, now: Optional[datetime] = None) -> str:
    """Civil date in the viewer (or fallback) zone, not process TZ."""
    return local_now(preferred, now=now).strftime("%Y-%m-%d")


def local_now_iso(preferred: Optional[str] = None, *, now: Optional[datetime] = None) -> str:
    return local_now(preferred, now=now).isoformat(timespec="seconds")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
