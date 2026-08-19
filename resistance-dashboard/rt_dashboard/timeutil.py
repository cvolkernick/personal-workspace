"""Local-calendar helpers for the machine running the dashboard."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_DASHBOARD_TZ = "America/New_York"


def ensure_dashboard_tz() -> str:
    """Civil-day timezone. Ignore host/Vercel TZ=UTC unless DASHBOARD_TZ is set."""
    name = (os.environ.get("DASHBOARD_TZ") or "").strip()
    if not name:
        name = DEFAULT_DASHBOARD_TZ
        os.environ["DASHBOARD_TZ"] = name
    return name


def local_tz_name() -> str:
    return ensure_dashboard_tz()


def local_tz():
    name = ensure_dashboard_tz()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_DASHBOARD_TZ)


def local_today_iso() -> str:
    """Civil date in DASHBOARD_TZ (America/New_York by default), not UTC."""
    return datetime.now(local_tz()).strftime("%Y-%m-%d")


def local_now_iso() -> str:
    return datetime.now(local_tz()).isoformat(timespec="seconds")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
