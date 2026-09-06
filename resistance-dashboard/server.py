#!/usr/bin/env python3
"""FitDash (resistance training dashboard) server — real entry path.

Usage:
  python3 server.py                          # http://127.0.0.1:8787/
  python3 server.py 8787                     # legacy positional port
  python3 server.py --port 8787 --host 0.0.0.0 --no-browser --local
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar
from urllib.parse import parse_qs, quote, urlparse

T = TypeVar("T")

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
sys.path.insert(0, str(ROOT))
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))


def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE or export KEY=VALUE lines into os.environ (does not override existing)."""
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        # Expand $HOME / ~
        val = os.path.expanduser(val.replace("$HOME", os.path.expanduser("~")))
        if key and key not in os.environ:
            # Skip unfilled placeholders
            if val.startswith("PASTE_YOUR_") or val == "":
                continue
            os.environ[key] = val


def _bootstrap_env() -> None:
    """Load secrets from well-known paths so double-click / bare python3 server.py works."""
    candidates = [
        Path.home() / ".config" / "resistance-dashboard" / "env",
        ROOT / ".env",
    ]
    for p in candidates:
        _load_env_file(p)


_bootstrap_env()

from rt_dashboard.analytics import dashboard_payload  # noqa: E402
from rt_dashboard.background_refresh import (  # noqa: E402
    maybe_schedule_background_refresh,
    schedule_incremental_warm,
    start_warm_loop,
    warm_interval_sec,
)
from rt_dashboard.dashboard_cache import (  # noqa: E402
    cache_status,
    health_cache_is_fresh,
    is_fresh,
    load_github_sessions_cache,
    load_health_cache,
    merge_health_snapshots,
    save_github_sessions_cache,
    save_health_cache,
    ttl_sec,
)
from rt_dashboard.coach import build_coach_payload  # noqa: E402
from rt_dashboard.coach_actions import format_action_reply, try_parse_coach_action  # noqa: E402
from rt_dashboard.agent_today import export_agent_today  # noqa: E402
from rt_dashboard.day_constraints import (  # noqa: E402
    export_day_constraints_from_dashboard,
)
from rt_dashboard.service_auth import (  # noqa: E402
    account_hint_mismatch,
    inventory_agent_denied,
    inventory_agent_principal,
    inventory_session_uid,
    service_auth_denied,
    service_auth_ok as _service_auth_ok,
    service_token_from_headers as _service_token_from_headers,
)
from rt_dashboard.pr_detect import apply_auto_prs  # noqa: E402
from rt_dashboard.workout_log import parse_log_body  # noqa: E402
from rt_dashboard.timeutil import local_now, local_today_iso, local_tz_name  # noqa: E402
from rt_dashboard.github_client import GitHubError, GitHubLiftClient  # noqa: E402
from rt_dashboard.google_auth import (  # noqa: E402
    auth_flow_status as google_auth_status,
    start_auth_flow as start_google_auth_flow,
)
from rt_dashboard.google_health import GoogleHealthClient  # noqa: E402
from rt_dashboard.health_metrics_store import resolve_health_snapshot  # noqa: E402
from rt_dashboard.hidrate_client import (  # noqa: E402
    hidrate_bottle_charge,
    hidrate_credentials_present,
    hidrate_hydration_samples,
    overlay_hidrate_hydration,
)
from rt_dashboard.models import HealthSnapshot, Session  # noqa: E402
from rt_dashboard.labs_parse import LabParseError, MAX_PDF_BYTES, parse_lab_pdf  # noqa: E402
from rt_dashboard.labs_store import delete_panel, load_labs, save_panel  # noqa: E402
from rt_dashboard.nutrition_planner import (  # noqa: E402
    add_ingredient,
    food_logs_for_day,
    generate_meal_plan,
    remove_ingredient,
    set_stock,
    stock_from_write_payload,
    suggest_inventory_removals,
    suggest_inventory_staples,
    today_consumed_from_nutrition,
    update_ingredient,
    update_targets,
)
from rt_dashboard.inventory_store import (  # noqa: E402
    inventory_principal_uid,
    inventory_source_fields,
    load_preview_inventory,
    persist_inventory,
)
from rt_dashboard.nutrition_store import (  # noqa: E402
    TARGETS_PATH,
    load_inventory_and_targets,
    write_nutrition_file,
)
from rt_dashboard.nutrition_targets import (  # noqa: E402
    merge_recommended_into_applied,
)
from rt_dashboard.grok_ask import (  # noqa: E402
    GrokAskError,
    ask_about_dashboard,
    auth_status as grok_auth_status,
)
from rt_dashboard.recovery import compute_recovery_status  # noqa: E402
from rt_dashboard.session_merge import merge_sessions  # noqa: E402
from rt_dashboard.equipment_store import (  # noqa: E402
    add_equipment_item,
    load_preview_equipment,
    remove_equipment_item,
    save_preview_equipment,
    update_equipment_item,
)
from rt_dashboard.workout_planner import (  # noqa: E402
    add_or_update_exercise,
    generate_workout_plan,
    update_goals,
)
from rt_dashboard.library_groom import (  # noqa: E402
    suggest_library_additions,
    suggest_library_removals,
)
from rt_dashboard.library_store import (  # noqa: E402
    apply_library_overlay,
    load_library_overlay,
    save_library_overlay,
    set_library_available,
)
from rt_dashboard.workout_store import (  # noqa: E402
    apply_goals_volume_caps,
    load_catalog_and_goals,
    write_catalog,
    write_goals,
)
from rt_dashboard.workout_repo import (  # noqa: E402
    get_repo as get_workout_repo,
    use_sqlite as workout_use_sqlite,
)
from rt_dashboard.user_store import UserStore  # noqa: E402
from rt_dashboard.auth_login import (  # noqa: E402
    build_login_url,
    complete_login,
    public_base_url,
    redirect_uri as auth_redirect_uri,
)
from rt_dashboard import crypto_box as _crypto_box  # noqa: E402

SESSION_COOKIE = "fitdash_session"
AUTH_PUBLIC_PATHS = {
    "/api/healthz",
    "/api/health",
    "/api/auth/status",
    "/api/auth/google/start",
    "/api/auth/google/callback",
    "/api/auth/logout",
}


def _auth_required() -> bool:
    """When true (default), personal APIs need a signed-in session."""
    return (os.environ.get("FITDASH_REQUIRE_AUTH") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _resolve_warm_user_and_token(
    user_id: Optional[str] = None,
) -> Tuple[Optional[str], str]:
    """Health user + refresh token for the incremental warmer.

    Prefer the explicit session user, then the most recently logged-in user
    who has a sealed token, then ``GOOGLE_REFRESH_TOKEN`` from env.
    """
    store = UserStore()
    uid = (user_id or "").strip() or None
    token = ""
    if uid:
        token = store.get_health_refresh_token(uid) or ""
    if not token:
        users = store.list_users_with_health_token()
        if users:
            uid = users[0]["id"]
            token = store.get_health_refresh_token(uid) or ""
    if not token:
        token = (os.environ.get("GOOGLE_REFRESH_TOKEN") or "").strip()
    return uid, token


def _parse_cookie_header(header: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in (header or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        out[k.strip()] = v.strip()
    return out


def _session_user_from_headers(headers) -> Optional[Dict[str, Any]]:
    cookies = _parse_cookie_header(headers.get("Cookie") or "")
    sid = cookies.get(SESSION_COOKIE) or ""
    if not sid:
        return None
    return UserStore().resolve_session(sid)

STATIC_DIR = ROOT / "static"
DEFAULT_PORT = int(os.environ.get("PORT", "8787"))
DEFAULT_BACKEND_CONFIG = ROOT / "backend.json"
_BACKEND_URL: Optional[str] = None
_BACKEND_LABEL: str = ""
_FRONTEND: str = ""

try:
    from remote_backend import add_backend_args, resolve_backend, try_proxy_api
except ImportError:  # pragma: no cover — monorepo root must be on path
    add_backend_args = None  # type: ignore
    resolve_backend = None  # type: ignore
    try_proxy_api = None  # type: ignore


def _default_local_workspace() -> str:
    # personal-workspace parent of resistance-dashboard
    candidate = ROOT.parent
    if (candidate / "fitness" / "workouts").is_dir():
        return str(candidate)
    return os.environ.get("LOCAL_WORKSPACE_DIR", "")


def build_github_client(*, for_write: bool = False) -> GitHubLiftClient:
    local = os.environ.get("LOCAL_WORKSPACE_DIR") or _default_local_workspace()
    prefer_local = os.environ.get("GITHUB_PREFER_LOCAL", "").lower() in ("1", "true", "yes")
    token = os.environ.get("GITHUB_TOKEN", "")
    # Writes without a token use the local workspace clone.
    if for_write and not token and local and not prefer_local:
        prefer_local = True
    return GitHubLiftClient(
        local_fallback_dir=local,
        prefer_local=prefer_local,
        token=token,
    )


def _call_with_timeout(
    fn: Callable[[], T],
    timeout_sec: float,
    label: str,
) -> Tuple[Optional[T], Optional[str]]:
    """Run fn in a worker thread; return (result, error). Never hangs past timeout.

    Important: do not use ``with ThreadPoolExecutor`` here — on timeout its
    ``shutdown(wait=True)`` would block until the hung worker finishes.
    """
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        fut = pool.submit(fn)
        try:
            return fut.result(timeout=timeout_sec), None
        except FuturesTimeout:
            return None, f"{label}: timed out after {timeout_sec:.0f}s"
        except Exception as e:  # noqa: BLE001
            return None, f"{label}: {e}"
    finally:
        # Python 3.9+ supports cancel_futures; ignore if not available.
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            pool.shutdown(wait=False)


def pull_merged_sessions(
    user_id: Optional[str] = None,
) -> Tuple[List[Any], str, Optional[str], GitHubLiftClient]:
    """
    Load lift sessions for one user (when auth on) or legacy default.

    Phase 1a/1b: **SQLite** is the primary store. With auth required, never
    returns another user's rows. GitHub remote merge when
    ``FITDASH_USE_SQLITE=0`` (legacy).
    """
    local_dir = os.environ.get("LOCAL_WORKSPACE_DIR") or _default_local_workspace()
    force_local = os.environ.get("GITHUB_PREFER_LOCAL", "").lower() in ("1", "true", "yes")
    token = os.environ.get("GITHUB_TOKEN", "")
    remote_timeout = float(os.environ.get("GITHUB_PULL_TIMEOUT_SEC", "18"))
    errors: List[str] = []
    remote_sessions: List[Session] = []
    local_sessions: List[Session] = []
    source_parts: List[str] = []
    uid = (user_id or "").strip() or None

    if workout_use_sqlite():
        try:
            repo = get_workout_repo(user_id=uid) if uid else get_workout_repo()
            # Empty user (or legacy default): one-time seed from workspace markdown
            if local_dir and repo.count() == 0:
                seed = repo.ensure_seeded_from_workspace(local_dir)
                if seed.get("seeded"):
                    source_parts.append("sqlite_seed")
                elif uid:
                    source_parts.append("sqlite_empty")
            local_sessions = repo.list_sessions()
            source_parts.append("sqlite")
        except Exception as e:  # noqa: BLE001
            errors.append(f"sqlite_pull: {e}")
            if (
                not _auth_required()
                and local_dir
                and (Path(local_dir) / "fitness" / "workouts").is_dir()
            ):
                try:
                    local_client = GitHubLiftClient(
                        prefer_local=True,
                        local_fallback_dir=local_dir,
                        token=token,
                    )
                    local_sessions = local_client.pull_sessions()
                    source_parts.append("local_fallback")
                except Exception as e2:  # noqa: BLE001
                    errors.append(f"local_pull: {e2}")
        sessions = local_sessions
        source = "+".join(source_parts) if source_parts else "sqlite"
        error = "; ".join(errors) if errors else None
        meta_client = build_github_client(for_write=False)
        return sessions, source, error, meta_client

    # --- Legacy: markdown local + optional GitHub merge ---
    if local_dir and (Path(local_dir) / "fitness" / "workouts").is_dir():
        try:
            local_client = GitHubLiftClient(
                prefer_local=True,
                local_fallback_dir=local_dir,
                token=token,
            )
            local_sessions = local_client.pull_sessions()
            source_parts.append("local")
        except Exception as e:  # noqa: BLE001
            errors.append(f"local_pull: {e}")

    if force_local:
        source_parts.append("local_only_mode")
    else:
        def _remote_pull() -> List[Session]:
            remote = GitHubLiftClient(
                prefer_local=False,
                token=token,
                local_fallback_dir="",
            )
            return remote.pull_sessions()

        remote_sessions_or_none, remote_err = _call_with_timeout(
            _remote_pull, remote_timeout, "github_pull"
        )
        if remote_err:
            errors.append(remote_err)
        elif remote_sessions_or_none is not None:
            remote_sessions = remote_sessions_or_none
            source_parts.append("github")

    sessions = merge_sessions(local_sessions, remote_sessions, prefer_first=True)
    source = "+".join(source_parts) if source_parts else "none"
    error = "; ".join(errors) if errors else None
    meta_client = build_github_client(for_write=False)
    return sessions, source, error, meta_client


def load_dashboard_data(
    *,
    force_refresh: bool = False,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build dashboard payload for one authenticated user (or legacy default).

    Personal data is only loaded for the given user_id. Google Health uses
    that user's encrypted refresh token when present.
    """
    t0 = datetime.utcnow()
    local_dir = os.environ.get("LOCAL_WORKSPACE_DIR") or _default_local_workspace()
    remote_timeout = float(os.environ.get("GITHUB_PULL_TIMEOUT_SEC", "12"))
    # Full Health pull (parallel streams) typically finishes in ~10–25s; keep headroom.
    health_timeout = float(os.environ.get("HEALTH_PULL_TIMEOUT_SEC", "60"))
    force_local = os.environ.get("GITHUB_PREFER_LOCAL", "").lower() in ("1", "true", "yes")
    token = os.environ.get("GITHUB_TOKEN", "")
    cache_ttl = ttl_sec()
    tz_name = local_tz_name()
    now = local_now(tz_name)
    local_today = local_today_iso(tz_name, now=now)
    uid = (user_id or "").strip() or None
    try:
        incremental_days = max(3, int(os.environ.get("HEALTH_INCREMENTAL_DAYS", "14")))
    except ValueError:
        incremental_days = 14

    # Per-user Health token (encrypted at rest) for this request only
    prev_rt = os.environ.get("GOOGLE_REFRESH_TOKEN")
    if uid:
        urt = UserStore().get_health_refresh_token(uid)
        if urt:
            os.environ["GOOGLE_REFRESH_TOKEN"] = urt

    # --- Local lifts + inventory (always live) ---
    local_sessions: List[Session] = []
    source_parts: List[str] = []
    errors: List[str] = []
    if workout_use_sqlite():
        try:
            repo = get_workout_repo(user_id=uid) if uid else get_workout_repo()
            if local_dir and not _auth_required() and repo.count() == 0:
                seed = repo.ensure_seeded_from_workspace(local_dir)
                if seed.get("seeded"):
                    source_parts.append("sqlite_seed")
            local_sessions = repo.list_sessions()
            source_parts.append("sqlite")
        except Exception as e:  # noqa: BLE001
            errors.append(f"sqlite_pull: {e}")
            if (
                not _auth_required()
                and local_dir
                and (Path(local_dir) / "fitness" / "workouts").is_dir()
            ):
                try:
                    local_client = GitHubLiftClient(
                        prefer_local=True,
                        local_fallback_dir=local_dir,
                        token=token,
                    )
                    local_sessions = local_client.pull_sessions()
                    source_parts.append("local_fallback")
                except Exception as e2:  # noqa: BLE001
                    errors.append(f"local_pull: {e2}")
    elif local_dir and (Path(local_dir) / "fitness" / "workouts").is_dir():
        try:
            local_client = GitHubLiftClient(
                prefer_local=True,
                local_fallback_dir=local_dir,
                token=token,
            )
            local_sessions = local_client.pull_sessions()
            source_parts.append("local")
        except Exception as e:  # noqa: BLE001
            errors.append(f"local_pull: {e}")

    nut_client = GitHubLiftClient(
        prefer_local=bool(local_dir),
        local_fallback_dir=local_dir or "",
        token=token,
    )
    try:
        nut = load_inventory_and_targets(nut_client)
    except Exception as e:  # noqa: BLE001
        nut = {
            "inventory": {"ingredients": []},
            "targets": {},
            "sources": {"inventory": "error", "targets": "error"},
        }
        errors.append(f"nutrition_store: {e}")
    # Named pantry SoT (same contract as public FitDash). Targets stay file/GH.
    inv, inv_src = load_preview_inventory(str(uid or ""))
    nut["inventory"] = inv
    sources = dict(nut.get("sources") or {})
    sources.update(inventory_source_fields(inv_src))
    nut["sources"] = sources

    # --- Cached remote layers ---
    health_client = GoogleHealthClient()
    cached_health, health_fetched_at, health_cache_meta = load_health_cache()
    cached_remote, gh_fetched_at, gh_cache_meta = load_github_sessions_cache()

    # Use health-aware freshness (empty/error pulls expire in ~5m, not 1h).
    health_fresh = bool(
        health_cache_meta.get("hit")
        and health_cache_meta.get("fresh")
        and health_cache_is_fresh(
            health_fetched_at,
            cached_health,
            last_error=health_cache_meta.get("last_error"),
            ttl=cache_ttl,
        )
    )
    gh_fresh = bool(gh_cache_meta.get("hit") and is_fresh(gh_fetched_at, cache_ttl))

    need_health = force_refresh or not health_fresh
    # Phase 1a: SQLite is the workout source of truth — skip GitHub session merge
    skip_github_workouts = workout_use_sqlite()
    need_github = (
        (not force_local)
        and (not skip_github_workouts)
        and (force_refresh or not gh_fresh)
    )

    remote_sessions: List[Session] = (
        []
        if skip_github_workouts
        else (list(cached_remote) if gh_cache_meta.get("hit") else [])
    )
    health: HealthSnapshot
    if cached_health is not None:
        health = cached_health
    else:
        health = HealthSnapshot()

    cache_notes: Dict[str, Any] = {
        "ttl_sec": cache_ttl,
        "force_refresh": force_refresh,
        "health": {
            **health_cache_meta,
            "used_cache": not need_health and cached_health is not None,
            "refreshed": False,
        },
        "github": {
            **gh_cache_meta,
            "used_cache": not need_github and bool(gh_cache_meta.get("hit")),
            "refreshed": False,
            "skipped": force_local or skip_github_workouts,
            "sqlite_primary": skip_github_workouts,
        },
    }

    def _remote_pull() -> List[Session]:
        remote = GitHubLiftClient(
            prefer_local=False,
            token=token,
            local_fallback_dir="",
        )
        return remote.pull_sessions()

    def _fetch_health() -> HealthSnapshot:
        # Full 90d on force / cold cache; otherwise recent window + merge.
        use_full = force_refresh or cached_health is None
        days = 90 if use_full else incremental_days
        google_health = health_client.fetch_health(days=days)
        resolved = resolve_health_snapshot(
            google_health,
            workspace_dir=local_dir,
            github_token=token,
        )
        # Hidrate cloud Day totals are SoT for water when HIDRATE_* is set.
        # Overlapping GH dates (partial HC / Fitbit) are replaced — no double-count.
        resolved, hidrate_meta = overlay_hidrate_hydration(resolved, days=days)
        cache_notes.setdefault("hidrate", {}).update(hidrate_meta)
        if not use_full and cached_health is not None:
            return merge_health_snapshots(cached_health, resolved)
        if use_full and cached_health is not None:
            # Force refresh still merges so we don't drop older points if
            # pagination was capped short.
            return merge_health_snapshots(cached_health, resolved)
        return resolved

    if need_health or need_github:
        pool = ThreadPoolExecutor(max_workers=2)
        wall = max(
            health_timeout if need_health else 0.1,
            remote_timeout if need_github else 0.1,
        )
        wall_deadline = time.monotonic() + wall
        try:
            fut_remote = pool.submit(_remote_pull) if need_github else None
            fut_health = pool.submit(_fetch_health) if need_health else None

            def _remaining() -> float:
                return max(0.05, wall_deadline - time.monotonic())

            if fut_remote is not None:
                try:
                    remote_sessions = fut_remote.result(
                        timeout=min(remote_timeout, _remaining())
                    )
                    save_github_sessions_cache(remote_sessions)
                    source_parts.append("github")
                    cache_notes["github"]["refreshed"] = True
                    cache_notes["github"]["used_cache"] = False
                except FuturesTimeout:
                    errors.append(f"github_pull: timed out after {remote_timeout:.0f}s")
                    if remote_sessions:
                        source_parts.append("github_cache_stale")
                        cache_notes["github"]["used_cache"] = True
                        cache_notes["github"]["stale_fallback"] = True
                except Exception as e:  # noqa: BLE001
                    errors.append(f"github_pull: {e}")
                    if remote_sessions:
                        source_parts.append("github_cache_stale")
                        cache_notes["github"]["used_cache"] = True
                        cache_notes["github"]["stale_fallback"] = True
            elif force_local:
                source_parts.append("local_only_mode")
            elif gh_fresh or gh_cache_meta.get("hit"):
                source_parts.append("github_cache")

            if fut_health is not None:
                try:
                    health = fut_health.result(timeout=min(health_timeout, _remaining()))
                    # Always stamp attempt time so the next hour uses cache/TTL.
                    save_health_cache(health, error=health.error)
                    cache_notes["health"]["refreshed"] = True
                    cache_notes["health"]["used_cache"] = False
                except FuturesTimeout:
                    err = f"health_pull: timed out after {health_timeout:.0f}s"
                    errors.append(err)
                    save_health_cache(cached_health, error=err)
                    if cached_health is not None:
                        health = cached_health
                        cache_notes["health"]["used_cache"] = True
                        cache_notes["health"]["stale_fallback"] = True
                    else:
                        health = HealthSnapshot(error=err)
                    cache_notes["health"]["refreshed"] = True  # attempt recorded
                except Exception as e:  # noqa: BLE001
                    err = f"health_pull: {e}"
                    errors.append(err)
                    save_health_cache(cached_health, error=err)
                    if cached_health is not None:
                        health = cached_health
                        cache_notes["health"]["used_cache"] = True
                        cache_notes["health"]["stale_fallback"] = True
                    else:
                        health = HealthSnapshot(error=err)
                    cache_notes["health"]["refreshed"] = True
        finally:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                pool.shutdown(wait=False)
    else:
        # Fully served from cache for remotes
        if gh_fresh or gh_cache_meta.get("hit"):
            source_parts.append("github_cache")
        if force_local:
            source_parts.append("local_only_mode")

    sessions = merge_sessions(local_sessions, remote_sessions, prefer_first=True)
    source = "+".join(source_parts) if source_parts else "none"
    gh = build_github_client(for_write=False)

    # Stale cache often keeps "token refresh HTTP 400" after a successful re-auth.
    # If OAuth works now, drop that error so the UI stops alarming every load.
    if health.error and re.search(
        r"token|refresh|invalid_grant|oauth", str(health.error), re.I
    ):
        try:
            health_client.ensure_access_token()
            health.error = None
        except Exception:
            pass

    # Always re-apply Hidrate on the assembled snapshot (including cache hits).
    # Series is process-cached ~5 min so bottle totals stay current without a full GH pull.
    if hidrate_credentials_present():
        try:
            health, hidrate_meta = overlay_hidrate_hydration(
                health, days=90 if force_refresh else max(14, incremental_days)
            )
            cache_notes.setdefault("hidrate", {}).update(hidrate_meta)
            if hidrate_meta.get("applied") and not cache_notes["health"].get("refreshed"):
                # Persist overlay so next cold read already has source=hidrate days.
                save_health_cache(health, error=health.error)
        except Exception as e:  # noqa: BLE001
            cache_notes.setdefault("hidrate", {})["error"] = str(e)

    # Unlogged nights = 0h sleep debt for charts, recovery, and coach.
    from rt_dashboard.sleep_series import expand_sleep_calendar

    # Real sleep logs (before implied-zero fill). Missing Health must not
    # auto-force a rest day via a ~30 "Caution" score from zero-filled nights.
    had_real_sleep = any(
        float(getattr(s, "sleep_hours", 0) or 0) > 0
        and str(getattr(s, "source", "") or "") != "implied_zero"
        for s in (health.sleep or [])
    )

    health.sleep = expand_sleep_calendar(
        health.sleep or [],
        as_of=local_today,
        window_days=90,
        fill_hours=0.0,
        fill_source="implied_zero",
    )

    recovery = compute_recovery_status(
        weight=health.weight,
        sleep=health.sleep,
        sessions=sessions,
        as_of=local_today,
    )
    from rt_dashboard.sleep_battery import sleep_battery_from_fitdash_sleep

    # Prefer timed Google sleep intervals (same as Time Allocator); daily
    # totals alone assume a fixed 7am wake and skew the battery badly.
    sleep_battery = sleep_battery_from_fitdash_sleep(
        [s for s in (health.sleep or []) if float(s.sleep_hours or 0) > 0],
        now=now,
        tz_name=tz_name,
        sleep_target_hours=8.0,
        sleep_intervals=list(getattr(health, "sleep_intervals", None) or []),
    )
    recovery_dict = recovery.to_dict()
    recovery_dict["sleep_battery"] = sleep_battery
    recovery_dict["sparse"] = not had_real_sleep
    payload = dashboard_payload(sessions)
    payload["health"] = health.to_dict()
    payload["recovery"] = recovery_dict
    payload["sleep_battery"] = sleep_battery

    today_logs = food_logs_for_day(health.food_logs or [], as_of=local_today)
    consumed = today_consumed_from_nutrition(
        health.nutrition,
        as_of=local_today,
        food_logs=health.food_logs or [],
    )
    inv_base = nut["inventory"] or {"ingredients": []}
    auto_plan = generate_meal_plan(
        inv_base,
        nut["targets"] or {},
        consumed,
        food_logs_today=today_logs,
        now=now,
        tz_name=tz_name,
        sleep_battery=sleep_battery,
    )
    from rt_dashboard.meal_plan_store import resolve_dashboard_meal_plan

    auto_plan = resolve_dashboard_meal_plan(
        str(uid or ""),
        local_today,
        auto_plan,
        inv_base,
    )
    inv_suggestions = suggest_inventory_staples(
        inv_base,
        targets=nut.get("targets") or {},
        food_logs=health.food_logs or [],
        consumed=consumed,
    )
    inv_removals = suggest_inventory_removals(
        inv_base,
        targets=nut.get("targets") or {},
        food_logs=health.food_logs or [],
    )
    labs = load_labs(
        local_dir or "",
        user_id=uid or "",
        targets=nut.get("targets") or {},
        as_of=local_today,
    )
    payload["nutrition_store"] = {
        "inventory": nut["inventory"],
        "targets": nut["targets"],
        "sources": nut["sources"],
        "today_consumed": consumed,
        "food_logs_today": today_logs,
        "food_logs_recent": [f.to_dict() for f in (health.food_logs or [])[-80:]],
        "meal_plan": auto_plan,
        "inventory_suggestions": inv_suggestions,
        "inventory_removals": inv_removals,
        "labs": labs,
    }

    # Full-width calorie pacing + same-day in/out delta bars
    try:
        from rt_dashboard.calorie_bars import build_calorie_bars_payload

        burned_today = None
        for b in health.calories_burned or []:
            if str(getattr(b, "date", "") or "")[:10] == str(local_today)[:10]:
                try:
                    burned_today = float(getattr(b, "calories", None) or 0)
                except (TypeError, ValueError):
                    burned_today = None
                break
        payload["calorie_bars"] = build_calorie_bars_payload(
            today_consumed=consumed,
            targets=nut.get("targets") or {},
            sleep_battery=sleep_battery,
            calories_burned_today=burned_today,
            # Timed logs so pacing can span midnight inside the wake window
            food_logs=health.food_logs or [],
            now=now,
            tz_name=tz_name,
        )
    except Exception as e:  # noqa: BLE001
        errors.append(f"calorie_bars: {e}")
        payload["calorie_bars"] = {"pacing": None, "delta": None}

    # Hydration pacing over the same wake window as calorie eating-window
    try:
        from rt_dashboard.hydration_bars import build_hydration_bars_payload

        payload["hydration_bars"] = build_hydration_bars_payload(
            hydration=health.hydration or [],
            samples=hidrate_hydration_samples(),
            weight=health.weight or [],
            sleep_battery=sleep_battery,
            as_of=local_today,
            now=now,
            tz_name=tz_name,
        )
    except Exception as e:  # noqa: BLE001
        errors.append(f"hydration_bars: {e}")
        payload["hydration_bars"] = {"pacing": None}

    bottle = hidrate_bottle_charge()
    payload["hidrate_bottle"] = bottle
    if isinstance(payload.get("hydration_bars"), dict):
        payload["hydration_bars"]["bottle"] = bottle

    # Exercise catalog + daily workout plan (local-first, same pattern as meals)
    try:
        wo = load_catalog_and_goals(nut_client)
        equipment, equipment_src = load_preview_equipment(str(uid or ""))
        overlay, library_src = load_library_overlay(str(uid or ""))
        catalog = apply_library_overlay(wo["catalog"], overlay)
        catalog = apply_goals_volume_caps(catalog, wo["goals"])
        workout_plan = generate_workout_plan(
            catalog,
            wo["goals"],
            sessions,
            recovery_label=(recovery.label if recovery else None),
            recovery_score=(recovery.score if recovery else None),
            # Sparse Health (no real sleep) → do not auto-rest on debt-filled score
            recovery_sparse=not had_real_sleep,
            as_of=local_today,
            equipment=equipment,
        )
        # Effective goals include autonomous focus_muscles from plan gen
        effective_goals = dict(wo["goals"] or {})
        if isinstance(workout_plan.get("goals"), dict):
            effective_goals = {
                **effective_goals,
                **{
                    k: v
                    for k, v in workout_plan["goals"].items()
                    if not str(k).startswith("_")
                },
            }
        payload["workout_store"] = {
            "catalog": catalog,
            "equipment": equipment,
            "goals": effective_goals,
            "library_suggestions": suggest_library_additions(
                catalog, equipment, sessions
            ),
            "library_removals": suggest_library_removals(
                catalog, equipment, sessions
            ),
            "sources": {
                **wo["sources"],
                "equipment": equipment_src,
                "library": library_src,
            },
            "plan": workout_plan,
        }
    except Exception as e:  # noqa: BLE001
        errors.append(f"workout_plan: {e}")
        workout_plan = {"message": f"Workout plan failed: {e}", "exercises": []}
        payload["workout_store"] = {
            "catalog": {"exercises": []},
            "equipment": {"items": []},
            "goals": {},
            "sources": {},
            "plan": workout_plan,
        }

    try:
        payload["coach"] = build_coach_payload(
            health=health,
            sessions=sessions,
            recovery=recovery,
            targets=nut.get("targets") or {},
            consumed=consumed,
            meal_plan=auto_plan,
            workout_plan=(payload.get("workout_store") or {}).get("plan") or {},
            as_of=local_today,
            labs=labs,
            inventory_suggestions=inv_suggestions,
            inventory_removals=inv_removals,
            sleep_battery=sleep_battery,
            calorie_bars=payload.get("calorie_bars"),
            inventory=inv_base,
        )
    except Exception as e:  # noqa: BLE001
        errors.append(f"coach: {e}")
        payload["coach"] = {
            "today": {"date": local_today, "recommendation": "train"},
            "adherence_7d": {},
            "weekly_review": {"bullets": [f"Coach layer error: {e}"]},
            "brief": {"title": "Coach brief", "markdown": f"Coach unavailable: {e}"},
        }

    # Daily quests: fast local plan on dashboard paint; GT ensure is async via GET /api/daily-tasks
    try:
        from rt_dashboard.daily_plan_tasks import plan_preview

        today_board = (payload.get("coach") or {}).get("today") or {}
        daily = plan_preview(today_board, day=local_today)
        daily["needs_sync"] = True
        payload["daily_tasks"] = daily
        if isinstance(payload.get("coach"), dict) and isinstance(
            payload["coach"].get("today"), dict
        ):
            payload["coach"]["today"]["daily_tasks"] = daily
    except Exception as e:  # noqa: BLE001
        errors.append(f"daily_tasks: {e}")
        payload["daily_tasks"] = {
            "ok": False,
            "error": str(e),
            "groups": [],
            "summary": {"done": 0, "total": 0},
            "needs_sync": True,
        }

    # Orchestra day packet (P3-F): freeze fields → fitness/data/day_constraints.json
    # Best-effort — never fail the dashboard if disk write fails.
    try:
        day_pkt = export_day_constraints_from_dashboard(
            payload,
            workspace=local_dir or None,
            sessions=sessions,
            sleep=health.sleep or [],
            write=bool(local_dir),
        )
        # Internal path keys stay off the public payload
        public_pkt = {
            k: v for k, v in day_pkt.items() if not str(k).startswith("_")
        }
        payload["day_constraints"] = public_pkt
        if day_pkt.get("_write_error"):
            errors.append(f"day_constraints_write: {day_pkt['_write_error']}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"day_constraints: {e}")
        payload["day_constraints"] = None

    elapsed_ms = int((datetime.utcnow() - t0).total_seconds() * 1000)
    payload["meta"] = {
        "source": source,
        "error": "; ".join(errors) if errors else None,
        "github_owner": gh.owner,
        "github_repo": gh.repo,
        "github_branch": gh.branch,
        "prefer_local": gh.prefer_local,
        "health_credentials": health_client.credentials_present(),
        "hidrate_credentials": hidrate_credentials_present(),
        "health_weight_points": len(health.weight),
        "health_sleep_points": len(health.sleep),
        "health_nutrition_days": len(health.nutrition),
        "health_food_logs": len(health.food_logs or []),
        "health_hydration_days": len(health.hydration),
        "health_calories_burned_days": len(health.calories_burned),
        "health_active_zone_minutes_days": len(health.active_zone_minutes or []),
        "inventory_count": len((nut["inventory"].get("ingredients") or [])),
        "labs_panels": len((labs.get("panels") or [])),
        "load_ms": elapsed_ms,
        "cache": cache_notes,
        "cache_ttl_sec": cache_ttl,
        "local_today": local_today,
        "timezone": tz_name,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "user_id": uid,
        "auth_required": _auth_required(),
    }

    # Serve cache-fast responses, then refresh remotes in the background.
    if not force_refresh:
        maybe_schedule_background_refresh(
            local_dir=local_dir or "",
            token=token,
            health_age_sec=(health_cache_meta or {}).get("age_sec"),
            health_refresh_token=os.environ.get("GOOGLE_REFRESH_TOKEN") or "",
        )
    # Restore process-wide Health token after per-user override
    if uid:
        if prev_rt is None:
            os.environ.pop("GOOGLE_REFRESH_TOKEN", None)
        else:
            os.environ["GOOGLE_REFRESH_TOKEN"] = prev_rt
    return payload


def _apply_coach_targets(client, *, user_id: Optional[str] = None) -> dict:
    """Write subroutine recommended macros. Never called from dashboard load."""
    data = load_dashboard_data(force_refresh=False, user_id=user_id)
    rec = ((data.get("coach") or {}).get("nutrition_targets") or {})
    if rec.get("abstain") or not rec.get("recommended"):
        return {
            "ok": False,
            "action": "apply_coach_targets",
            "error": "Coach has no recommendation to apply",
            "reasons": rec.get("reasons") or [],
            "recommendation": rec,
        }
    store = load_inventory_and_targets(client)
    base = merge_recommended_into_applied(store.get("targets") or {}, rec)
    updated = update_targets(base)
    write = write_nutrition_file(
        client,
        TARGETS_PATH,
        updated,
        message="nutrition: apply coach targets",
    )
    return {
        "ok": True,
        "action": "apply_coach_targets",
        "targets": updated,
        "recommendation": rec,
        "write": write,
    }


def _execute_coach_action(action: dict, *, user_id: Optional[str] = None) -> dict:
    """Run a structured coach action against local/GitHub stores."""
    kind = action.get("action")
    client = build_github_client(for_write=True)
    uid = user_id
    try:
        if kind == "set_stock":
            inv, _src = load_preview_inventory(str(uid or ""))
            ident = str(action.get("id_or_name") or "").strip()
            inv = inv or {"ingredients": []}
            match_id = ""
            match_name = ""
            for ing in inv.get("ingredients") or []:
                iid = str(ing.get("id") or "")
                iname = str(ing.get("name") or "")
                if iid.lower() == ident.lower() or iname.lower() == ident.lower():
                    match_id = iid
                    match_name = iname
                    break
            if not match_id:
                return {
                    "ok": False,
                    "action": kind,
                    "error": f"ingredient not found: {ident}",
                }
            stock = action.get("stock")
            if stock not in ("in", "low", "out"):
                stock = "in" if action.get("in_stock") else "out"
            updated = set_stock(inv, ingredient_id=match_id, stock=stock)
            write = persist_inventory(
                updated,
                str(uid or ""),
                file_client=client,
                message=f"nutrition: stock {stock} via coach {match_id}",
            )
            return {
                "ok": True,
                "action": kind,
                "id": match_id,
                "name": match_name,
                "stock": stock,
                "in_stock": stock != "out",
                "write": write,
            }
        if kind == "set_targets":
            raw = action.get("targets") or {}
            # Merge with existing targets
            store = load_inventory_and_targets(client)
            base = dict(store.get("targets") or {})
            base.update(raw)
            # Guard: refuse absurd calorie targets that look like gram values
            try:
                cal = float(base.get("calories") or 0)
            except (TypeError, ValueError):
                cal = 0
            if cal and cal < 800:
                # If only fat/protein/carbs were meant to change, drop bad calories
                if "calories" in raw and float(raw.get("calories") or 0) < 800:
                    base.pop("calories", None)
                    # re-merge without bad cal so normalize can heal from macros
                    base = dict(store.get("targets") or {})
                    for k, v in raw.items():
                        if k == "calories":
                            continue
                        base[k] = v
            updated = update_targets(base)
            write = write_nutrition_file(
                client,
                TARGETS_PATH,
                updated,
                message="nutrition: targets via coach",
            )
            return {"ok": True, "action": kind, "targets": updated, "write": write}
        if kind == "apply_coach_targets":
            return _apply_coach_targets(client, user_id=uid)
        if kind == "set_focus_muscles":
            from rt_dashboard.workout_planner import (
                suggest_focus_muscles,
                weekly_set_tally,
            )
            from rt_dashboard.dashboard_cache import sessions_from_dicts

            store = load_catalog_and_goals(client)
            goals = dict(store.get("goals") or {})
            reason = ""
            if action.get("clear"):
                muscles: list = []
                # Re-enable autonomous focus after clear
                goals["auto_focus_muscles"] = True
                reason = "Cleared pin — coach will auto-pick lagging muscles again."
            elif action.get("auto"):
                goals["auto_focus_muscles"] = True
                data = load_dashboard_data(force_refresh=False, user_id=uid)
                sessions = sessions_from_dicts(data.get("sessions") or [])
                catalog = store.get("catalog") or {"exercises": []}
                tally = weekly_set_tally(
                    sessions,
                    catalog,
                    secondary_fraction=float(
                        goals.get("secondary_set_fraction") or 0.5
                    ),
                )
                sug = suggest_focus_muscles(tally, goals, max_focus=2)
                muscles = list(sug.get("muscles") or [])
                reason = str(sug.get("reason") or "")
            else:
                # Manual pin disables auto until user re-enables
                muscles = list(action.get("muscles") or [])
                goals["auto_focus_muscles"] = False
            goals["focus_muscles"] = muscles
            updated = update_goals(goals)
            write = write_goals(
                client, updated, message="workout: focus muscles via coach"
            )
            return {
                "ok": True,
                "action": kind,
                "muscles": updated.get("focus_muscles") or [],
                "goals": updated,
                "reason": reason,
                "write": write,
            }
        if kind == "refresh_meal_plan":
            data = load_dashboard_data(force_refresh=False, user_id=uid)
            store = data.get("nutrition_store") or {}
            bat = data.get("sleep_battery") or (data.get("recovery") or {}).get(
                "sleep_battery"
            )
            win = ((data.get("calorie_bars") or {}).get("pacing") or {}).get(
                "window"
            ) or {}
            plan = generate_meal_plan(
                store.get("inventory") or {"ingredients": []},
                store.get("targets") or {},
                store.get("today_consumed") or {},
                food_logs_today=store.get("food_logs_today") or [],
                now=local_now(),
                tz_name=local_tz_name(),
                window_start=win.get("window_start"),
                window_end=win.get("window_end"),
                sleep_battery=bat if isinstance(bat, dict) else None,
            )
            from rt_dashboard.meal_plan_store import resolve_dashboard_meal_plan

            plan = resolve_dashboard_meal_plan(
                str(uid or ""),
                (data.get("meta") or {}).get("local_today") or "",
                plan,
                store.get("inventory") or {"ingredients": []},
            )
            return {"ok": True, "action": kind, "plan": plan}
        if kind == "refresh_workout_plan":
            data = load_dashboard_data(force_refresh=False, user_id=uid)
            wo = data.get("workout_store") or {}
            rec = data.get("recovery") or {}
            from rt_dashboard.dashboard_cache import sessions_from_dicts

            sessions = sessions_from_dicts(data.get("sessions") or [])
            plan = generate_workout_plan(
                wo.get("catalog") or {"exercises": []},
                wo.get("goals") or {},
                sessions,
                recovery_label=rec.get("label"),
                recovery_score=rec.get("score"),
                recovery_sparse=bool(rec.get("sparse")),
                session_type=action.get("session_type"),
            )
            return {"ok": True, "action": kind, "plan": plan}
        return {"ok": False, "action": kind, "error": f"unknown action {kind}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "action": kind, "error": str(e)}


class DashboardHandler(SimpleHTTPRequestHandler):
    # PWA manifest MIME (stdlib map often serves .webmanifest as octet-stream)
    extensions_map = {
        **getattr(SimpleHTTPRequestHandler, "extensions_map", {}),
        ".webmanifest": "application/manifest+json",
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".png": "image/png",
        ".html": "text/html; charset=utf-8",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def end_headers(self) -> None:
        # Avoid stale app.js/index after auth deploys (browsers heuristic-cache static).
        path = urlparse(getattr(self, "path", "") or "").path
        if path in ("/", "/index.html") or path.endswith(
            (".js", ".css", ".html", ".webmanifest")
        ):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send_json(self, obj: Any, status: int = 200) -> None:
        raw = json.dumps(obj, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _set_session_cookie(self, session_id: str, *, clear: bool = False) -> None:
        if clear:
            cookie = (
                f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
            )
        else:
            max_age = 14 * 24 * 3600
            cookie = (
                f"{SESSION_COOKIE}={session_id}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
            )
        self.send_header("Set-Cookie", cookie)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _require_user(self) -> Optional[Dict[str, Any]]:
        """Return session user or send 401 and return None."""
        if not _auth_required():
            return {"user_id": None, "email": "", "display_name": "legacy"}
        user = _session_user_from_headers(self.headers)
        if not user:
            self._send_json(
                {
                    "ok": False,
                    "error": "auth_required",
                    "message": "Sign in with Google to view your data.",
                    "login": "/api/auth/google/start",
                },
                status=401,
            )
            return None
        return user

    def do_GET(self) -> None:  # noqa: N802
        if try_proxy_api is not None and try_proxy_api(
            self,
            _BACKEND_URL,
            method="GET",
            backend_label=_BACKEND_LABEL,
            frontend=_FRONTEND,
            health_paths=("/api/health", "/api/healthz"),
        ):
            return
        parsed = urlparse(self.path)
        if parsed.path in ("/api/healthz", "/api/health"):
            self._send_json(
                {
                    "ok": True,
                    "service": "resistance-dashboard",
                    "proxy": False,
                    "backend": None,
                    "auth_required": _auth_required(),
                }
            )
            return
        if parsed.path == "/api/auth/status":
            user = _session_user_from_headers(self.headers)
            try:
                _crypto_box.load_or_create_master_key()
                master_ok = True
            except Exception:
                master_ok = False
            self._send_json(
                {
                    "ok": True,
                    "authenticated": bool(user),
                    "auth_required": _auth_required(),
                    "user": (
                        {
                            "id": user["user_id"],
                            "email": user.get("email"),
                            "display_name": user.get("display_name"),
                        }
                        if user
                        else None
                    ),
                    "public_url": public_base_url(),
                    "oauth_redirect_uri": auth_redirect_uri(),
                    "master_key_ready": master_ok,
                }
            )
            return
        if parsed.path == "/api/auth/google/start":
            try:
                url, _state = build_login_url()
                # Redirect browser to Google
                self.send_response(302)
                self.send_header("Location", url)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/auth/google/callback":
            try:
                qs = parse_qs(parsed.query or "")
                code = (qs.get("code") or [""])[0]
                state = (qs.get("state") or [""])[0]
                if qs.get("error"):
                    raise RuntimeError(f"Google OAuth error: {qs.get('error')}")
                result = complete_login(code, state)
                # Set cookie + redirect home
                self.send_response(302)
                self._set_session_cookie(result["session_id"])
                self.send_header("Location", "/")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            except Exception as e:
                self.send_response(302)
                self.send_header(
                    "Location",
                    "/?auth_error=" + quote(str(e)[:200]),
                )
                self.end_headers()
            return
        if parsed.path == "/api/auth/logout":
            cookies = _parse_cookie_header(self.headers.get("Cookie") or "")
            sid = cookies.get(SESSION_COOKIE) or ""
            if sid:
                UserStore().destroy_session(sid)
            self.send_response(302)
            self._set_session_cookie("", clear=True)
            self.send_header("Location", "/")
            self.end_headers()
            return
        if parsed.path == "/api/warm":
            # Incremental Health + Hidrate cache warm. Loopback / service-token
            # (Pi timer or curl). Never a 90-day ?refresh=1 pull.
            client_host = (self.client_address or ("", 0))[0]
            user = _session_user_from_headers(self.headers)
            if _auth_required() and not user and not _service_auth_ok(
                self.headers, client_host
            ):
                self._send_json(service_auth_denied("warm the cache"), status=401)
                return
            try:
                uid = user.get("user_id") if user else None
                warm_uid, health_rt = _resolve_warm_user_and_token(uid)
                local_dir = (
                    os.environ.get("LOCAL_WORKSPACE_DIR") or _default_local_workspace()
                )
                scheduled = schedule_incremental_warm(
                    local_dir=local_dir or "",
                    token=os.environ.get("GITHUB_TOKEN") or "",
                    health_refresh_token=health_rt,
                    force_schedule=True,
                )
                self._send_json(
                    {
                        "ok": True,
                        "scheduled": scheduled,
                        "incremental": True,
                        "force_refresh": False,
                        "interval_sec": warm_interval_sec(),
                        "user_id": warm_uid,
                        "health_token": bool(health_rt),
                    }
                )
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/sleep_battery":
            # Machine-friendly: IoT post-sunset bedroom dim. No browser session
            # required when service token or loopback (see _service_auth_ok).
            client_host = (self.client_address or ("", 0))[0]
            user = _session_user_from_headers(self.headers)
            if _auth_required() and not user and not _service_auth_ok(
                self.headers, client_host
            ):
                self._send_json(service_auth_denied("IoT"), status=401)
                return
            try:
                data = load_dashboard_data(force_refresh=False)
                bat = data.get("sleep_battery") or {}
                if not isinstance(bat, dict):
                    bat = {}
                self._send_json(
                    {
                        "ok": True,
                        **bat,
                        "pct_charged": bat.get("pct_charged"),
                        "empty_at": bat.get("empty_at"),
                        "mode": bat.get("mode"),
                        "summary": bat.get("summary"),
                    }
                )
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/day_constraints":
            # Machine-friendly: Orchestra 15m poke. Same loopback / service-token
            # path as /api/sleep_battery (see _service_auth_ok). Writes
            # fitness/data/day_constraints.json via load_dashboard_data.
            client_host = (self.client_address or ("", 0))[0]
            user = _session_user_from_headers(self.headers)
            if _auth_required() and not user and not _service_auth_ok(
                self.headers, client_host
            ):
                self._send_json(service_auth_denied("Orchestra"), status=401)
                return
            try:
                uid = user.get("user_id") if user else None
                data = load_dashboard_data(force_refresh=False, user_id=uid)
                pkt = data.get("day_constraints")
                if not isinstance(pkt, dict):
                    pkt = export_day_constraints_from_dashboard(
                        data,
                        workspace=os.environ.get("LOCAL_WORKSPACE_DIR")
                        or _default_local_workspace()
                        or None,
                        write=True,
                    )
                    pkt = {k: v for k, v in pkt.items() if not str(k).startswith("_")}
                self._send_json({"ok": True, "day_constraints": pkt})
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/agent/today":
            # Machine-friendly Today + week brief. Same loopback / service-token
            # path as /api/sleep_battery (see _service_auth_ok). Auto-generates a
            # SuperGrok plan when the letter stamps, it is not rest, and the
            # store is empty — no invented ml / loads / sessions / AZM / intake /
            # sleep / lifts.
            client_host = (self.client_address or ("", 0))[0]
            user = _session_user_from_headers(self.headers)
            if _auth_required() and not user and not _service_auth_ok(
                self.headers, client_host
            ):
                self._send_json(service_auth_denied("agents"), status=401)
                return
            try:
                uid = user.get("user_id") if user else None
                data = load_dashboard_data(force_refresh=False, user_id=uid)
                from rt_dashboard.agent_plan import (
                    house_plan_user_id,
                    overlay_workout_on_payload,
                    stamp_and_fill_workout,
                )
                from rt_dashboard.timeutil import local_today_iso

                plan_uid = str(uid or house_plan_user_id())
                day = (
                    ((data.get("coach") or {}).get("today") or {}).get("date")
                    or (data.get("meta") or {}).get("local_today")
                    or local_today_iso()
                )
                store = (
                    data.get("workout_store")
                    if isinstance(data.get("workout_store"), dict)
                    else {}
                )
                goals = store.get("goals")
                if not isinstance(goals, dict):
                    goals = (
                        data.get("goals")
                        if isinstance(data.get("goals"), dict)
                        else None
                    )
                filled = stamp_and_fill_workout(
                    plan_uid,
                    day=str(day)[:10],
                    sessions=data.get("sessions") or [],
                    goals=goals,
                    recovery=data.get("recovery")
                    if isinstance(data.get("recovery"), dict)
                    else {},
                    headers=self.headers,
                    query=parsed.query or "",
                )
                data = overlay_workout_on_payload(data, filled)
                self._send_json(export_agent_today(data))
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/dashboard":
            user = self._require_user()
            if user is None and _auth_required():
                return
            try:
                qs = parse_qs(parsed.query or "")
                force = (qs.get("refresh") or qs.get("force") or ["0"])[0].lower() in (
                    "1",
                    "true",
                    "yes",
                )
                uid = user.get("user_id") if user else None
                self._send_json(
                    load_dashboard_data(force_refresh=force, user_id=uid)
                )
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return
        if parsed.path == "/api/daily-tasks":
            user = self._require_user()
            if user is None and _auth_required():
                return
            try:
                uid = user.get("user_id") if user else None
                data = load_dashboard_data(force_refresh=False, user_id=uid)
                today = ((data.get("coach") or {}).get("today")) or {}
                from rt_dashboard.daily_plan_tasks import ensure_daily_tasks
                from rt_dashboard.timeutil import local_today_iso

                day = today.get("date") or local_today_iso()
                result = ensure_daily_tasks(today, day=day)
                self._send_json({"ok": True, "daily_tasks": result})
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/sessions":
            user = self._require_user()
            if user is None and _auth_required():
                return
            try:
                uid = user.get("user_id") if user else None
                data = load_dashboard_data(force_refresh=False, user_id=uid)
                self._send_json(
                    {
                        "sessions": data.get("sessions", []),
                        "meta": data.get("meta"),
                    }
                )
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return
        if parsed.path == "/api/nutrition":
            user = self._require_user()
            if user is None and _auth_required():
                return
            try:
                uid = user.get("user_id") if user else None
                data = load_dashboard_data(force_refresh=False, user_id=uid)
                self._send_json(data.get("nutrition_store") or {})
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return
        # All remaining /api/* GETs require auth when FITDASH_REQUIRE_AUTH=1
        if parsed.path.startswith("/api/") and parsed.path not in AUTH_PUBLIC_PATHS:
            user = self._require_user()
            if user is None and _auth_required():
                return
            self._request_user = user  # type: ignore[attr-defined]

        if parsed.path == "/api/cache/status":
            try:
                self._send_json({"ok": True, **cache_status()})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/ask/status":
            try:
                self._send_json(grok_auth_status())
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/google-health/auth/status":
            try:
                self._send_json(google_auth_status())
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path in ("/", "/index.html"):
            return super().do_GET()
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if try_proxy_api is not None and try_proxy_api(
            self,
            _BACKEND_URL,
            method="POST",
            backend_label=_BACKEND_LABEL,
            frontend=_FRONTEND,
            health_paths=("/api/health", "/api/healthz"),
        ):
            return
        parsed = urlparse(self.path)
        # Gate all personal POST APIs. Inventory stock also accepts a
        # Chris-bound agent token (not the house FITDASH_SERVICE_TOKEN).
        if parsed.path.startswith("/api/") and parsed.path not in AUTH_PUBLIC_PATHS:
            if parsed.path == "/api/agent/generate-plan":
                user = _session_user_from_headers(self.headers)
                client_host = (self.client_address or ("", 0))[0]
                if user:
                    self._request_user = user  # type: ignore[attr-defined]
                elif _service_auth_ok(self.headers, client_host):
                    self._request_user = {  # type: ignore[attr-defined]
                        "user_id": None,
                        "email": "",
                        "display_name": "agent",
                    }
                elif _auth_required():
                    self._send_json(service_auth_denied("generate-plan"), status=401)
                    return
                else:
                    self._request_user = {  # type: ignore[attr-defined]
                        "user_id": None,
                        "email": "",
                        "display_name": "legacy",
                    }
            elif parsed.path == "/api/inventory/stock":
                user = _session_user_from_headers(self.headers)
                if not user:
                    user = inventory_agent_principal(self.headers)
                if user:
                    self._request_user = user  # type: ignore[attr-defined]
                elif _auth_required():
                    self._send_json(inventory_agent_denied(), status=401)
                    return
                else:
                    self._request_user = {  # type: ignore[attr-defined]
                        "user_id": None,
                        "email": "",
                        "display_name": "legacy",
                    }
            else:
                user = self._require_user()
                if user is None and _auth_required():
                    return
                self._request_user = user  # type: ignore[attr-defined]
        if parsed.path == "/api/agent/generate-plan":
            try:
                body = (
                    self._read_json()
                    if int(self.headers.get("Content-Length") or 0)
                    else {}
                )
                from api.workout._util import agent_generate_plan_body

                client_host = (self.client_address or ("", 0))[0]
                status, payload = agent_generate_plan_body(
                    self.headers, body, parsed.query, client_host=client_host
                )
                self._send_json(payload, status=status)
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/google-health/auth/start":
            try:
                body = (
                    self._read_json()
                    if int(self.headers.get("Content-Length") or 0)
                    else {}
                )
                force = bool(body.get("force"))
                result = start_google_auth_flow(force=force)
                status = 200 if result.get("ok") else 400
                self._send_json(result, status=status)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/workouts":
            user = self._require_user()
            if user is None and _auth_required():
                return
            try:
                uid = user.get("user_id") if user else None
                body = self._read_json()
                session = parse_log_body(body)
                # Auto-tag PRs from history (prior sessions only), then write.
                history, _, _, _ = pull_merged_sessions(user_id=uid)
                apply_auto_prs(session, history)
                pr_names = [e.name for e in session.exercises if e.is_pr]
                if workout_use_sqlite():
                    repo = get_workout_repo(user_id=uid) if uid else get_workout_repo()
                    result = repo.upsert_session(session)
                    # Optional dual-write to markdown/GitHub for backup
                    if os.environ.get("FITDASH_DUAL_WRITE", "").lower() in (
                        "1",
                        "true",
                        "yes",
                    ):
                        try:
                            gh = build_github_client(for_write=True)
                            result["github"] = gh.append_workout_safe(session)
                        except Exception as e:  # noqa: BLE001
                            result["github_error"] = str(e)
                else:
                    client = build_github_client(for_write=True)
                    result = client.append_workout_safe(session)
                # Reload via merged pull so response matches subsequent GET /api/dashboard
                sessions, source, _err, _gh = pull_merged_sessions(user_id=uid)
                self._send_json(
                    {
                        "ok": True,
                        "write": result,
                        "source": source,
                        "session_count": len(sessions),
                        "sessions_head": [s.to_dict() for s in sessions[:5]],
                        "auto_prs": pr_names,
                        "session": session.to_dict(),
                    }
                )
            except (ValueError, json.JSONDecodeError) as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except GitHubError as e:
                self._send_json(
                    {
                        "ok": False,
                        "error": str(e),
                        "status": e.status,
                        "body": e.body[:2000] if e.body else "",
                    },
                    status=502,
                )
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/workouts/import":
            # Seed/re-import from local fitness/workouts markdown into SQLite.
            user = self._require_user()
            if user is None and _auth_required():
                return
            try:
                uid = user.get("user_id") if user else None
                body = (
                    self._read_json()
                    if int(self.headers.get("Content-Length") or 0)
                    else {}
                )
                replace = bool(body.get("replace"))
                local_dir = os.environ.get("LOCAL_WORKSPACE_DIR") or _default_local_workspace()
                if not local_dir:
                    self._send_json(
                        {"ok": False, "error": "LOCAL_WORKSPACE_DIR not set"},
                        status=400,
                    )
                    return
                repo = get_workout_repo(user_id=uid) if uid else get_workout_repo()
                result = repo.import_from_markdown_dir(local_dir, replace=replace)
                status = 200 if result.get("ok") else 400
                self._send_json(result, status=status)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/refresh":
            try:
                # Explicit refresh always bypasses remote caches.
                uid = (getattr(self, "_request_user", None) or {}).get("user_id")
                self._send_json(
                    load_dashboard_data(force_refresh=True, user_id=uid)
                )
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return
        if parsed.path == "/api/labs/upload":
            try:
                uid = (getattr(self, "_request_user", None) or {}).get("user_id") or ""
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_PDF_BYTES * 2:
                    self._send_json(
                        {"ok": False, "error": "Upload too large (8 MB PDF max)."},
                        status=400,
                    )
                    return
                body = self._read_json() if length else {}
                filename = str(body.get("filename") or "lab.pdf")
                b64 = str(body.get("content_base64") or "").strip()
                if not b64:
                    raise ValueError("Missing content_base64.")
                try:
                    raw = base64.b64decode(b64)
                except Exception as e:  # noqa: BLE001
                    raise ValueError(f"Invalid base64: {e}") from e
                panel = parse_lab_pdf(raw, filename=filename)
                labs = save_panel(panel, user_id=str(uid), pdf_bytes=raw)
                markers = panel.get("markers") or {}
                self._send_json(
                    {
                        "ok": True,
                        "panel": panel,
                        "labs": labs,
                        "marker_count": len(markers),
                    }
                )
            except (LabParseError, ValueError, json.JSONDecodeError) as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/labs/delete":
            try:
                uid = (getattr(self, "_request_user", None) or {}).get("user_id") or ""
                body = self._read_json()
                date = str(body.get("date") or "")
                if not date:
                    raise ValueError("date required")
                labs = delete_panel(
                    date=date,
                    lab=str(body.get("lab") or ""),
                    order_id=str(body.get("order_id") or ""),
                    user_id=str(uid),
                )
                self._send_json({"ok": True, "labs": labs})
            except (ValueError, json.JSONDecodeError) as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/inventory/add":
            try:
                body = self._read_json()
                uid = (getattr(self, "_request_user", None) or {}).get("user_id") or ""
                client = build_github_client(for_write=True)
                current, _src = load_preview_inventory(str(uid))
                updated = add_ingredient(current, body)
                write = persist_inventory(
                    updated,
                    str(uid),
                    file_client=client,
                    message=f"nutrition: add/update ingredient {body.get('name', '')}",
                )
                self._send_json(
                    {"ok": True, "inventory": write.get("inventory") or updated, "write": write}
                )
            except (ValueError, json.JSONDecodeError) as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/inventory/update":
            try:
                body = self._read_json()
                uid = (getattr(self, "_request_user", None) or {}).get("user_id") or ""
                client = build_github_client(for_write=True)
                current, _src = load_preview_inventory(str(uid))
                updated = update_ingredient(current, body)
                write = persist_inventory(
                    updated,
                    str(uid),
                    file_client=client,
                    message=f"nutrition: edit ingredient {body.get('id') or body.get('name', '')}",
                )
                self._send_json(
                    {"ok": True, "inventory": write.get("inventory") or updated, "write": write}
                )
            except (ValueError, json.JSONDecodeError) as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/inventory/remove":
            try:
                body = self._read_json()
                uid = (getattr(self, "_request_user", None) or {}).get("user_id") or ""
                client = build_github_client(for_write=True)
                current, _src = load_preview_inventory(str(uid))
                updated = remove_ingredient(
                    current,
                    ingredient_id=str(body.get("id") or ""),
                    name=str(body.get("name") or ""),
                )
                write = persist_inventory(
                    updated,
                    str(uid),
                    file_client=client,
                    message=f"nutrition: remove ingredient {body.get('id') or body.get('name')}",
                )
                self._send_json(
                    {"ok": True, "inventory": write.get("inventory") or updated, "write": write}
                )
            except (ValueError, json.JSONDecodeError) as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/inventory/stock":
            try:
                body = self._read_json()
                user = getattr(self, "_request_user", None) or {}
                uid = inventory_session_uid(user)
                if user.get("agent_inventory"):
                    uid = inventory_principal_uid(uid)
                mismatch = account_hint_mismatch(uid, body)
                if mismatch:
                    status, payload = mismatch
                    self._send_json(payload, status=status)
                    return
                client = build_github_client(for_write=True)
                current, _src = load_preview_inventory(str(uid))
                stock = stock_from_write_payload(body)
                updated = set_stock(
                    current,
                    ingredient_id=str(body.get("id") or ""),
                    stock=stock,
                )
                write = persist_inventory(
                    updated,
                    str(uid),
                    file_client=client,
                    message=f"nutrition: stock {stock} {body.get('id')}",
                )
                self._send_json(
                    {
                        "ok": True,
                        "inventory": write.get("inventory") or updated,
                        "write": write,
                        "user_id": uid,
                    }
                )
            except ValueError as e:
                msg = str(e)
                if msg == "inventory user_id required":
                    self._send_json(inventory_agent_denied(), status=401)
                elif msg == "ingredient not found":
                    self._send_json(
                        {"ok": False, "error": "not_found", "message": msg},
                        status=404,
                    )
                else:
                    self._send_json({"ok": False, "error": msg}, status=400)
            except json.JSONDecodeError as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/targets":
            try:
                body = self._read_json()
                client = build_github_client(for_write=True)
                uid = (getattr(self, "_request_user", None) or {}).get("user_id")
                if body.get("apply_coach"):
                    result = _apply_coach_targets(client, user_id=uid)
                    status = 200 if result.get("ok") else 400
                    self._send_json(result, status=status)
                    return
                updated = update_targets(body)
                write = write_nutrition_file(
                    client,
                    TARGETS_PATH,
                    updated,
                    message="nutrition: update daily macro targets",
                )
                self._send_json({"ok": True, "targets": updated, "write": write})
            except (ValueError, json.JSONDecodeError) as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/ask":
            try:
                body = self._read_json()
                question = str(body.get("question") or body.get("q") or "").strip()
                history = body.get("history") if isinstance(body.get("history"), list) else []
                model = body.get("model")
                # Local coach actions (stock / targets / refresh plans) — no model call.
                # Pass chat history so "apply those recommendations" can reuse numbers.
                uid = (getattr(self, "_request_user", None) or {}).get("user_id")
                action = try_parse_coach_action(question, history=history)
                if action:
                    act_result = _execute_coach_action(action, user_id=uid)
                    self._send_json(
                        {
                            "ok": True,
                            "answer": format_action_reply(act_result),
                            "model": "local-coach-actions",
                            "auth_source": "local",
                            "action": act_result,
                            "context_chars": 0,
                            "session_count": 0,
                        }
                    )
                    return
                # Never force remote Health on Ask — use disk cache + local lifts.
                dashboard = load_dashboard_data(force_refresh=False, user_id=uid)
                result = ask_about_dashboard(
                    question,
                    dashboard,
                    history=history,
                    model=str(model).strip() if model else None,
                )
                self._send_json({"ok": True, **result})
            except GrokAskError as e:
                if e.status in (400, 401, 403, 404):
                    status = e.status
                else:
                    status = 502
                self._send_json(
                    {
                        "ok": False,
                        "error": str(e),
                        "status": e.status,
                        "body": (e.body or "")[:1500],
                        "auth": grok_auth_status(),
                    },
                    status=status,
                )
            except (ValueError, json.JSONDecodeError) as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/daily-tasks/complete":
            try:
                body = self._read_json()
                list_id = str(body.get("list_id") or "").strip()
                task_id = str(body.get("task_id") or "").strip()
                completed = body.get("completed", True)
                if isinstance(completed, str):
                    completed = completed.lower() in ("1", "true", "yes")
                parent_id = body.get("parent_id")
                parent_id = str(parent_id).strip() if parent_id else None
                sibling_all_done = body.get("sibling_all_done")
                from rt_dashboard.daily_plan_tasks import complete_leaf

                result = complete_leaf(
                    list_id,
                    task_id,
                    completed=bool(completed),
                    parent_id=parent_id,
                    sibling_all_done=sibling_all_done
                    if sibling_all_done is None
                    else bool(sibling_all_done),
                )
                if result.get("ok"):
                    from rt_dashboard.quest_inventory_stock import (
                        attach_shopping_quest_stock,
                    )
                    from rt_dashboard.quest_workout_log import (
                        attach_lift_quest_log,
                        quest_log_context,
                    )

                    uid = (
                        (getattr(self, "_request_user", None) or {}).get("user_id")
                        or (getattr(self, "_request_user", None) or {}).get("id")
                        or "default"
                    )
                    headers = {k: v for k, v in self.headers.items()}
                    day, sessions, today_workout = quest_log_context(
                        str(uid), body, headers=headers
                    )
                    result = attach_lift_quest_log(
                        result,
                        body,
                        bool(completed),
                        user_id=str(uid),
                        sessions=sessions,
                        today=day,
                        today_workout=today_workout,
                    )
                    result = attach_shopping_quest_stock(
                        result,
                        body,
                        bool(completed),
                        user_id=str(uid),
                    )
                status = 200 if result.get("ok") else 400
                self._send_json(result, status=status)
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return

        if parsed.path == "/api/meal-plan/generate":
            try:
                body = self._read_json() if int(self.headers.get("Content-Length") or 0) else {}
                uid = (getattr(self, "_request_user", None) or {}).get("user_id")
                data = load_dashboard_data(force_refresh=False, user_id=uid)
                store = data.get("nutrition_store") or {}
                health = data.get("health") or {}
                # Prefer live consumed from dashboard nutrition days
                from rt_dashboard.models import NutritionDay

                from rt_dashboard.models import FoodLogEntry

                nutrition_days = [
                    NutritionDay(
                        date=n["date"],
                        calories=n.get("calories"),
                        protein_g=n.get("protein_g"),
                        carbs_g=n.get("carbs_g"),
                        fat_g=n.get("fat_g"),
                        source=n.get("source") or "google_health",
                    )
                    for n in (health.get("nutrition") or [])
                ]
                food_entries = [
                    FoodLogEntry(
                        date=str(f.get("date") or ""),
                        name=str(f.get("name") or "Logged food"),
                        calories=f.get("calories"),
                        protein_g=f.get("protein_g"),
                        carbs_g=f.get("carbs_g"),
                        fat_g=f.get("fat_g"),
                        meal_type=f.get("meal_type"),
                        serving_label=f.get("serving_label"),
                        time=f.get("time"),
                        nutrients=f.get("nutrients") or {},
                        source=f.get("source") or "google_health",
                    )
                    for f in (health.get("food_logs") or [])
                    if isinstance(f, dict) and f.get("date")
                ]
                # Prefer store's already-serialized today logs when present
                today_logs = store.get("food_logs_today") or food_logs_for_day(
                    food_entries
                )
                consumed = today_consumed_from_nutrition(
                    nutrition_days, food_logs=food_entries
                )
                # allow override for testing
                if body.get("consumed"):
                    consumed.update({k: float(body["consumed"][k]) for k in body["consumed"] if k in consumed})
                bat = data.get("sleep_battery") or (data.get("recovery") or {}).get(
                    "sleep_battery"
                )
                win = ((data.get("calorie_bars") or {}).get("pacing") or {}).get(
                    "window"
                ) or {}
                plan = generate_meal_plan(
                    store.get("inventory") or {"ingredients": []},
                    store.get("targets") or {},
                    consumed,
                    food_logs_today=today_logs,
                    now=local_now(),
                    tz_name=local_tz_name(),
                    window_start=win.get("window_start") or body.get("window_start"),
                    window_end=win.get("window_end") or body.get("window_end"),
                    eat_slots=body.get("eat_slots") or body.get("slots"),
                    sleep_battery=bat if isinstance(bat, dict) else None,
                )
                from rt_dashboard.meal_plan_store import resolve_dashboard_meal_plan
                from rt_dashboard.timeutil import local_today_iso as _local_today_iso

                plan = resolve_dashboard_meal_plan(
                    str(uid or ""),
                    (data.get("meta") or {}).get("local_today") or _local_today_iso(),
                    plan,
                    store.get("inventory") or {"ingredients": []},
                )
                self._send_json({"ok": True, "plan": plan})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/workout-plan/generate":
            try:
                body = self._read_json() if int(self.headers.get("Content-Length") or 0) else {}
                uid = (getattr(self, "_request_user", None) or {}).get("user_id")
                data = load_dashboard_data(force_refresh=False, user_id=uid)
                wo = data.get("workout_store") or {}
                rec = data.get("recovery") or {}
                sessions_raw = data.get("sessions") or []
                # Rebuild minimal Session objects from payload for planner
                from rt_dashboard.dashboard_cache import sessions_from_dicts

                sessions = sessions_from_dicts(sessions_raw)
                session_type = body.get("session_type")
                plan = generate_workout_plan(
                    wo.get("catalog") or {"exercises": []},
                    wo.get("goals") or {},
                    sessions,
                    recovery_label=rec.get("label"),
                    recovery_score=rec.get("score"),
                    recovery_sparse=bool(rec.get("sparse")),
                    session_type=str(session_type).lower() if session_type else None,
                    equipment=wo.get("equipment"),
                )
                self._send_json({"ok": True, "plan": plan})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/workout/goals":
            try:
                body = self._read_json()
                client = build_github_client(for_write=True)
                updated = update_goals(body)
                write = write_goals(
                    client, updated, message="workout: update training goals"
                )
                self._send_json({"ok": True, "goals": updated, "write": write})
            except (ValueError, json.JSONDecodeError) as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/workout/exercise":
            try:
                body = self._read_json()
                client = build_github_client(for_write=True)
                store = load_catalog_and_goals(client)
                updated = add_or_update_exercise(store["catalog"], body)
                write = write_catalog(
                    client,
                    updated,
                    message=f"workout: add/update exercise {body.get('name', '')}",
                )
                self._send_json({"ok": True, "catalog": updated, "write": write})
            except (ValueError, json.JSONDecodeError) as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/equipment/add":
            try:
                body = self._read_json()
                uid = (getattr(self, "_request_user", None) or {}).get("user_id") or ""
                current, _src = load_preview_equipment(str(uid))
                updated = add_equipment_item(current, body)
                saved = save_preview_equipment(updated, str(uid))
                self._send_json(
                    {
                        "ok": True,
                        "equipment": saved,
                        "write": {"ok": True, "source": "turso"},
                    }
                )
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/equipment/update":
            try:
                body = self._read_json()
                uid = (getattr(self, "_request_user", None) or {}).get("user_id") or ""
                current, _src = load_preview_equipment(str(uid))
                updated = update_equipment_item(current, body)
                saved = save_preview_equipment(updated, str(uid))
                self._send_json(
                    {
                        "ok": True,
                        "equipment": saved,
                        "write": {"ok": True, "source": "turso"},
                    }
                )
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/equipment/remove":
            try:
                body = self._read_json()
                uid = (getattr(self, "_request_user", None) or {}).get("user_id") or ""
                current, _src = load_preview_equipment(str(uid))
                updated = remove_equipment_item(
                    current,
                    equipment_id=str(body.get("id") or ""),
                    tag=str(body.get("tag") or ""),
                    name=str(body.get("name") or ""),
                )
                saved = save_preview_equipment(updated, str(uid))
                self._send_json(
                    {
                        "ok": True,
                        "equipment": saved,
                        "write": {"ok": True, "source": "turso"},
                    }
                )
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/workout/exercise/available":
            try:
                body = self._read_json()
                eid = str(body.get("id") or "").strip()
                if not eid:
                    raise ValueError("exercise id required")
                want = bool(body.get("available", True))
                uid = (getattr(self, "_request_user", None) or {}).get("user_id") or ""
                client = build_github_client(for_write=False)
                store = load_catalog_and_goals(client)
                match = None
                for raw in (store.get("catalog") or {}).get("exercises") or []:
                    if isinstance(raw, dict) and str(raw.get("id") or "") == eid:
                        match = raw
                        break
                if not match:
                    raise ValueError("exercise not found")
                if want:
                    from rt_dashboard.workout_planner import (
                        movement_feasible,
                        normalize_exercise,
                    )

                    equipment, _src = load_preview_equipment(str(uid))
                    if not movement_feasible(normalize_exercise(match), equipment):
                        raise ValueError(
                            "exercise needs equipment that is not in the inventory"
                        )
                overlay, _src = load_library_overlay(str(uid))
                updated_ov = set_library_available(overlay, eid, want)
                saved = save_library_overlay(updated_ov, str(uid))
                catalog = apply_library_overlay(store["catalog"], saved)
                self._send_json(
                    {
                        "ok": True,
                        "catalog": catalog,
                        "library": saved,
                        "write": {"ok": True, "source": "turso"},
                    }
                )
            except (ValueError, json.JSONDecodeError) as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        self._send_json({"error": "not found"}, status=404)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """CLI aligned with monorepo Pi deploy units (host/port/no-browser/local)."""
    parser = argparse.ArgumentParser(description="FitDash resistance training dashboard")
    parser.add_argument(
        "port_positional",
        nargs="?",
        type=int,
        default=None,
        help="Listen port (legacy positional; prefer --port)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Listen port (default {DEFAULT_PORT} or $PORT)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default 127.0.0.1; use 0.0.0.0 on Pi / LAN)",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Force full local stack on this host (Pi unit flag; no remote API proxy)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser tab (always true for systemd)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = _parse_args(argv)
    port = args.port if args.port is not None else (
        args.port_positional if args.port_positional is not None else DEFAULT_PORT
    )
    host = str(args.host or "127.0.0.1")
    # Ensure static exists
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, int(port)), DashboardHandler)
    display_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    print(f"FitDash listening on http://{display_host}:{port}/", flush=True)
    if host == "0.0.0.0":
        print(f"LAN bind: 0.0.0.0:{port} (reachable on LAN / Tailscale)", flush=True)
    print(f"API: http://{display_host}:{port}/api/dashboard", flush=True)
    if args.local:
        print("mode → local full stack (UI + API on this process)", flush=True)
    # --no-browser: intentional no-op for FitDash (never auto-opens a browser)
    _ = args.no_browser
    local_dir = os.environ.get("LOCAL_WORKSPACE_DIR") or _default_local_workspace()
    if start_warm_loop(
        local_dir=local_dir or "",
        token=os.environ.get("GITHUB_TOKEN") or "",
    ):
        print(
            f"warm loop → incremental Health + Hidrate every {warm_interval_sec():.0f}s",
            flush=True,
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", flush=True)
        server.shutdown()


if __name__ == "__main__":
    main()
