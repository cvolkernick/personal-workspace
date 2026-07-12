"""Local-calendar helpers for the machine running the dashboard."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo


def local_tz_name() -> str:
    """Best-effort IANA / system timezone name."""
    explicit = (os.environ.get("DASHBOARD_TZ") or os.environ.get("TZ") or "").strip()
    if explicit:
        return explicit
    try:
        # Python 3.9+ on macOS often exposes the key via tzlocal-less approach
        name = datetime.now().astimezone().tzinfo
        if name is not None:
            key = getattr(name, "key", None)
            if key:
                return str(key)
    except Exception:
        pass
    # Fallback: system offset label (still correct "today" via astimezone())
    return str(datetime.now().astimezone().tzinfo or "local")


def local_tz():
    name = (os.environ.get("DASHBOARD_TZ") or os.environ.get("TZ") or "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def local_today_iso() -> str:
    """Civil date on the host running the server (not UTC)."""
    return datetime.now(local_tz()).strftime("%Y-%m-%d")


def local_now_iso() -> str:
    return datetime.now(local_tz()).isoformat(timespec="seconds")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
