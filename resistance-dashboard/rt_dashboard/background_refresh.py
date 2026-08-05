"""Background / incremental remote refresh so page loads stay cache-fast."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from .dashboard_cache import (
    load_github_sessions_cache,
    load_health_cache,
    merge_health_snapshots,
    save_github_sessions_cache,
    save_health_cache,
)
from .github_client import GitHubLiftClient
from .google_health import GoogleHealthClient
from .health_metrics_store import resolve_health_snapshot
from .hidrate_client import overlay_hidrate_hydration

log = logging.getLogger("resistance-dashboard.refresh")

_schedule_lock = threading.Lock()
_refresh_lock = threading.Lock()
_last_scheduled = 0.0


def _bg_min_interval() -> float:
    try:
        return max(60.0, float(os.environ.get("DASHBOARD_BG_REFRESH_MIN_SEC", "900")))
    except ValueError:
        return 900.0


def _incremental_days() -> int:
    try:
        return max(3, int(os.environ.get("HEALTH_INCREMENTAL_DAYS", "14")))
    except ValueError:
        return 14


def maybe_schedule_background_refresh(
    *,
    force: bool = False,
    local_dir: str = "",
    token: str = "",
    health_age_sec: Optional[float] = None,
) -> bool:
    """
    Kick a daemon refresh if enough time has passed since the last schedule.

    When ``force`` is False, only schedules if the cache age is at least half of
    the background interval (default 15 min). Safe to call on every load.
    """
    global _last_scheduled
    now = time.time()
    min_iv = _bg_min_interval()
    age = float(health_age_sec or 0.0)
    if not force and age < min_iv * 0.5:
        return False
    with _schedule_lock:
        if not force and (now - _last_scheduled) < min_iv:
            return False
        _last_scheduled = now

    def runner() -> None:
        if not _refresh_lock.acquire(blocking=False):
            return
        try:
            _refresh_health(local_dir=local_dir, token=token, incremental=not force)
            _refresh_github(token=token)
        except Exception as e:  # noqa: BLE001
            log.warning("background refresh failed: %s", e)
        finally:
            _refresh_lock.release()

    threading.Thread(target=runner, name="rdash-bg-refresh", daemon=True).start()
    return True


def _refresh_health(*, local_dir: str, token: str, incremental: bool) -> None:
    client = GoogleHealthClient()
    days = _incremental_days() if incremental else 90
    cached, _, _ = load_health_cache()
    if client.credentials_present():
        fresh = client.fetch_health(days=days)
        resolved = resolve_health_snapshot(
            fresh,
            workspace_dir=local_dir,
            github_token=token,
        )
    elif cached is not None:
        # Still refresh Hidrate water onto the last good GH snapshot.
        resolved = cached
    else:
        from .models import HealthSnapshot

        resolved = HealthSnapshot()
    # Hidrate Day totals win over GH hydration when credentials are set.
    resolved, _hidrate_meta = overlay_hidrate_hydration(resolved, days=days)
    if incremental and cached is not None:
        merged = merge_health_snapshots(cached, resolved)
    else:
        merged = resolved
    if (
        merged.weight
        or merged.sleep
        or merged.nutrition
        or merged.hydration
        or merged.calories_burned
    ):
        save_health_cache(merged, error=merged.error)
    else:
        save_health_cache(cached, error=resolved.error or "empty health refresh")


def _refresh_github(*, token: str) -> None:
    if not token:
        return
    try:
        remote = GitHubLiftClient(
            prefer_local=False,
            token=token,
            local_fallback_dir="",
        )
        sessions = remote.pull_sessions()
        if sessions:
            save_github_sessions_cache(sessions)
    except Exception as e:  # noqa: BLE001
        log.warning("github background refresh failed: %s", e)
