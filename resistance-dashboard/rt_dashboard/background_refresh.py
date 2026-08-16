"""Background / incremental remote refresh so page loads stay cache-fast.

Two kickers:

- ``maybe_schedule_background_refresh`` — opportunistic, after a dashboard
  load. Honors ``DASHBOARD_BG_REFRESH_MIN_SEC`` (default 15 min). ``force=True``
  still does a full 90-day Health pull (Refresh data).
- ``schedule_incremental_warm`` / ``start_warm_loop`` — Pi warmer. Always
  incremental (14-day Health + Hidrate). Never ``?refresh=1``. Runs even when
  nobody has the browser open.
"""

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
_warm_loop_started = False


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


def warm_interval_sec() -> float:
    """Seconds between Pi incremental warms. ``0`` disables the in-process loop."""
    raw = (os.environ.get("DASHBOARD_WARM_INTERVAL_SEC") or "300").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 300.0


def maybe_schedule_background_refresh(
    *,
    force: bool = False,
    local_dir: str = "",
    token: str = "",
    health_age_sec: Optional[float] = None,
    health_refresh_token: str = "",
) -> bool:
    """
    Kick a daemon refresh if enough time has passed since the last schedule.

    When ``force`` is False, only schedules if the cache age is at least half of
    the background interval (default 15 min). Safe to call on every load.

    ``force=True`` is a full 90-day Health pull (Refresh data). Do not use it
    on a timer.
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

    _spawn_refresh(
        local_dir=local_dir,
        token=token,
        incremental=not force,
        health_refresh_token=health_refresh_token,
    )
    return True


def schedule_incremental_warm(
    *,
    local_dir: str = "",
    token: str = "",
    health_refresh_token: str = "",
    force_schedule: bool = False,
) -> bool:
    """Always incremental (14d Health + Hidrate). Never a 90-day Refresh-data pull.

    ``force_schedule`` bypasses the interval gate (explicit ``GET /api/warm``).
    """
    global _last_scheduled
    interval = warm_interval_sec()
    if interval <= 0 and not force_schedule:
        return False
    now = time.time()
    with _schedule_lock:
        gate = max(60.0, interval) if interval > 0 else 60.0
        if not force_schedule and _last_scheduled and (now - _last_scheduled) < gate:
            return False
        _last_scheduled = now

    _spawn_refresh(
        local_dir=local_dir,
        token=token,
        incremental=True,
        health_refresh_token=health_refresh_token,
    )
    return True


def start_warm_loop(*, local_dir: str = "", token: str = "") -> bool:
    """Daemon loop: incremental Health + Hidrate while the server process is up.

    Disable with ``DASHBOARD_WARM_INTERVAL_SEC=0``. Safe to call once from
    ``main()``; later calls are no-ops.
    """
    global _warm_loop_started
    interval = warm_interval_sec()
    if interval <= 0:
        return False
    with _schedule_lock:
        if _warm_loop_started:
            return False
        _warm_loop_started = True

    def loop() -> None:
        # Let the HTTP server bind before the first pull.
        time.sleep(min(20.0, interval))
        while True:
            try:
                health_rt = _resolve_warm_health_token()
                schedule_incremental_warm(
                    local_dir=local_dir,
                    token=token or os.environ.get("GITHUB_TOKEN", ""),
                    health_refresh_token=health_rt,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("warm loop tick failed: %s", e)
            time.sleep(max(60.0, interval))

    threading.Thread(target=loop, name="rdash-warm-loop", daemon=True).start()
    log.info("warm loop started (every %.0fs, incremental Health + Hidrate)", interval)
    return True


def _resolve_warm_health_token() -> str:
    """Prefer the most recently logged-in user's sealed token; else env."""
    try:
        from .user_store import UserStore

        users = UserStore().list_users_with_health_token()
        if users:
            tok = UserStore().get_health_refresh_token(users[0]["id"])
            if tok:
                return tok
    except Exception as e:  # noqa: BLE001
        log.warning("warm loop could not read user Health token: %s", e)
    return (os.environ.get("GOOGLE_REFRESH_TOKEN") or "").strip()


def _spawn_refresh(
    *,
    local_dir: str,
    token: str,
    incremental: bool,
    health_refresh_token: str,
) -> None:
    captured_health = health_refresh_token or (os.environ.get("GOOGLE_REFRESH_TOKEN") or "")
    captured_gh = token or (os.environ.get("GITHUB_TOKEN") or "")

    def runner() -> None:
        if not _refresh_lock.acquire(blocking=False):
            return
        try:
            _refresh_health(
                local_dir=local_dir,
                token=captured_gh,
                incremental=incremental,
                health_refresh_token=captured_health,
            )
            _refresh_github(token=captured_gh)
        except Exception as e:  # noqa: BLE001
            log.warning("background refresh failed: %s", e)
        finally:
            _refresh_lock.release()

    threading.Thread(target=runner, name="rdash-bg-refresh", daemon=True).start()


def _refresh_health(
    *,
    local_dir: str,
    token: str,
    incremental: bool,
    health_refresh_token: str = "",
) -> None:
    client = GoogleHealthClient(refresh_token=health_refresh_token or None)
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
