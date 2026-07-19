#!/usr/bin/env python3
"""Resistance training dashboard server — real entry path."""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar
from urllib.parse import parse_qs, urlparse

T = TypeVar("T")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


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
from rt_dashboard.background_refresh import maybe_schedule_background_refresh  # noqa: E402
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
from rt_dashboard.pr_detect import apply_auto_prs  # noqa: E402
from rt_dashboard.timeutil import local_today_iso, local_tz_name  # noqa: E402
from rt_dashboard.github_client import GitHubError, GitHubLiftClient  # noqa: E402
from rt_dashboard.google_auth import (  # noqa: E402
    auth_flow_status as google_auth_status,
    start_auth_flow as start_google_auth_flow,
)
from rt_dashboard.google_health import GoogleHealthClient  # noqa: E402
from rt_dashboard.health_metrics_store import resolve_health_snapshot  # noqa: E402
from rt_dashboard.models import ExerciseEntry, HealthSnapshot, Session, SetEntry  # noqa: E402
from rt_dashboard.labs_store import load_labs  # noqa: E402
from rt_dashboard.nutrition_planner import (  # noqa: E402
    add_ingredient,
    food_logs_for_day,
    generate_meal_plan,
    remove_ingredient,
    set_in_stock,
    suggest_inventory_staples,
    today_consumed_from_nutrition,
    update_targets,
)
from rt_dashboard.nutrition_store import (  # noqa: E402
    INVENTORY_PATH,
    TARGETS_PATH,
    load_inventory_and_targets,
    write_nutrition_file,
)
from rt_dashboard.grok_ask import (  # noqa: E402
    GrokAskError,
    ask_about_dashboard,
    auth_status as grok_auth_status,
)
from rt_dashboard.recovery import compute_recovery_status  # noqa: E402
from rt_dashboard.session_merge import merge_sessions  # noqa: E402
from rt_dashboard.workout_planner import (  # noqa: E402
    add_or_update_exercise,
    generate_workout_plan,
    set_exercise_available,
    update_goals,
)
from rt_dashboard.workout_store import (  # noqa: E402
    load_catalog_and_goals,
    write_catalog,
    write_goals,
)

STATIC_DIR = ROOT / "static"
DEFAULT_PORT = int(os.environ.get("PORT", "8787"))


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


def pull_merged_sessions() -> Tuple[List[Any], str, Optional[str], GitHubLiftClient]:
    """
    Pull from live GitHub and local workspace, then merge.

    Local is loaded first (fast). Remote GitHub is best-effort with a hard timeout
    so a stalled network cannot freeze the dashboard.
    """
    local_dir = os.environ.get("LOCAL_WORKSPACE_DIR") or _default_local_workspace()
    force_local = os.environ.get("GITHUB_PREFER_LOCAL", "").lower() in ("1", "true", "yes")
    token = os.environ.get("GITHUB_TOKEN", "")
    remote_timeout = float(os.environ.get("GITHUB_PULL_TIMEOUT_SEC", "18"))
    errors: List[str] = []
    remote_sessions: List[Session] = []
    local_sessions: List[Session] = []
    source_parts: List[str] = []

    # Local first — this is what makes the UI fill quickly offline.
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

    # Local wins on key collision so freshly logged (not-yet-pushed) workouts stick
    sessions = merge_sessions(local_sessions, remote_sessions, prefer_first=True)
    source = "+".join(source_parts) if source_parts else "none"
    error = "; ".join(errors) if errors else None
    meta_client = build_github_client(for_write=False)
    return sessions, source, error, meta_client


def load_dashboard_data(*, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Build dashboard payload.

    Always reads local workout logs + inventory (fast).
    Google Health and remote GitHub sessions are cached on disk for
    DASHBOARD_CACHE_TTL_SEC (default 3600 = 1 hour). Set force_refresh=True
    (Refresh button / ?refresh=1) to pull remotes now.
    """
    t0 = datetime.utcnow()
    local_dir = os.environ.get("LOCAL_WORKSPACE_DIR") or _default_local_workspace()
    remote_timeout = float(os.environ.get("GITHUB_PULL_TIMEOUT_SEC", "12"))
    # Full Health pull (parallel streams) typically finishes in ~10–25s; keep headroom.
    health_timeout = float(os.environ.get("HEALTH_PULL_TIMEOUT_SEC", "60"))
    force_local = os.environ.get("GITHUB_PREFER_LOCAL", "").lower() in ("1", "true", "yes")
    token = os.environ.get("GITHUB_TOKEN", "")
    cache_ttl = ttl_sec()
    local_today = local_today_iso()
    tz_name = local_tz_name()
    try:
        incremental_days = max(3, int(os.environ.get("HEALTH_INCREMENTAL_DAYS", "14")))
    except ValueError:
        incremental_days = 14

    # --- Local lifts + inventory (always live) ---
    local_sessions: List[Session] = []
    source_parts: List[str] = []
    errors: List[str] = []
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
    need_github = (not force_local) and (force_refresh or not gh_fresh)

    remote_sessions: List[Session] = list(cached_remote) if gh_cache_meta.get("hit") else []
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
            "skipped": force_local,
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

    recovery = compute_recovery_status(
        weight=health.weight,
        sleep=health.sleep,
        sessions=sessions,
        as_of=local_today,
    )
    payload = dashboard_payload(sessions)
    payload["health"] = health.to_dict()
    payload["recovery"] = recovery.to_dict()

    today_logs = food_logs_for_day(health.food_logs or [], as_of=local_today)
    consumed = today_consumed_from_nutrition(
        health.nutrition,
        as_of=local_today,
        food_logs=health.food_logs or [],
    )
    auto_plan = generate_meal_plan(
        nut["inventory"] or {"ingredients": []},
        nut["targets"] or {},
        consumed,
        food_logs_today=today_logs,
    )
    inv_suggestions = suggest_inventory_staples(
        nut["inventory"] or {"ingredients": []},
        targets=nut.get("targets") or {},
        food_logs=health.food_logs or [],
        consumed=consumed,
    )
    labs = load_labs(local_dir or "")
    payload["nutrition_store"] = {
        "inventory": nut["inventory"],
        "targets": nut["targets"],
        "sources": nut["sources"],
        "today_consumed": consumed,
        "food_logs_today": today_logs,
        "food_logs_recent": [f.to_dict() for f in (health.food_logs or [])[-80:]],
        "meal_plan": auto_plan,
        "inventory_suggestions": inv_suggestions,
        "labs": labs,
    }

    # Exercise catalog + daily workout plan (local-first, same pattern as meals)
    try:
        wo = load_catalog_and_goals(nut_client)
        workout_plan = generate_workout_plan(
            wo["catalog"],
            wo["goals"],
            sessions,
            recovery_label=(recovery.label if recovery else None),
            recovery_score=(recovery.score if recovery else None),
            as_of=local_today,
        )
        payload["workout_store"] = {
            "catalog": wo["catalog"],
            "goals": wo["goals"],
            "sources": wo["sources"],
            "plan": workout_plan,
        }
    except Exception as e:  # noqa: BLE001
        errors.append(f"workout_plan: {e}")
        workout_plan = {"message": f"Workout plan failed: {e}", "exercises": []}
        payload["workout_store"] = {
            "catalog": {"exercises": []},
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
        )
    except Exception as e:  # noqa: BLE001
        errors.append(f"coach: {e}")
        payload["coach"] = {
            "today": {"date": local_today, "recommendation": "train"},
            "adherence_7d": {},
            "weekly_review": {"bullets": [f"Coach layer error: {e}"]},
            "brief": {"title": "Coach brief", "markdown": f"Coach unavailable: {e}"},
        }

    elapsed_ms = int((datetime.utcnow() - t0).total_seconds() * 1000)
    payload["meta"] = {
        "source": source,
        "error": "; ".join(errors) if errors else None,
        "github_owner": gh.owner,
        "github_repo": gh.repo,
        "github_branch": gh.branch,
        "prefer_local": gh.prefer_local,
        "health_credentials": health_client.credentials_present(),
        "health_weight_points": len(health.weight),
        "health_sleep_points": len(health.sleep),
        "health_nutrition_days": len(health.nutrition),
        "health_food_logs": len(health.food_logs or []),
        "health_hydration_days": len(health.hydration),
        "health_calories_burned_days": len(health.calories_burned),
        "inventory_count": len((nut["inventory"].get("ingredients") or [])),
        "labs_panels": len((labs.get("panels") or [])),
        "load_ms": elapsed_ms,
        "cache": cache_notes,
        "cache_ttl_sec": cache_ttl,
        "local_today": local_today,
        "timezone": tz_name,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }

    # Serve cache-fast responses, then refresh remotes in the background.
    if not force_refresh:
        maybe_schedule_background_refresh(
            local_dir=local_dir or "",
            token=token,
            health_age_sec=(health_cache_meta or {}).get("age_sec"),
        )
    return payload


def _execute_coach_action(action: dict) -> dict:
    """Run a structured coach action against local/GitHub stores."""
    kind = action.get("action")
    client = build_github_client(for_write=True)
    try:
        if kind == "set_stock":
            store = load_inventory_and_targets(client)
            ident = str(action.get("id_or_name") or "").strip()
            inv = store["inventory"] or {"ingredients": []}
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
            updated = set_in_stock(
                inv, ingredient_id=match_id, in_stock=bool(action.get("in_stock"))
            )
            write = write_nutrition_file(
                client,
                INVENTORY_PATH,
                updated,
                message=f"nutrition: stock via coach {match_id}",
            )
            return {
                "ok": True,
                "action": kind,
                "id": match_id,
                "name": match_name,
                "in_stock": bool(action.get("in_stock")),
                "write": write,
            }
        if kind == "set_targets":
            raw = action.get("targets") or {}
            # Merge with existing targets
            store = load_inventory_and_targets(client)
            base = dict(store.get("targets") or {})
            base.update(raw)
            updated = update_targets(base)
            write = write_nutrition_file(
                client,
                TARGETS_PATH,
                updated,
                message="nutrition: targets via coach",
            )
            return {"ok": True, "action": kind, "targets": updated, "write": write}
        if kind == "refresh_meal_plan":
            data = load_dashboard_data(force_refresh=False)
            store = data.get("nutrition_store") or {}
            plan = generate_meal_plan(
                store.get("inventory") or {"ingredients": []},
                store.get("targets") or {},
                store.get("today_consumed") or {},
            )
            return {"ok": True, "action": kind, "plan": plan}
        if kind == "refresh_workout_plan":
            data = load_dashboard_data(force_refresh=False)
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
                session_type=action.get("session_type"),
            )
            return {"ok": True, "action": kind, "plan": plan}
        return {"ok": False, "action": kind, "error": f"unknown action {kind}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "action": kind, "error": str(e)}


def parse_log_body(data: dict) -> Session:
    st = str(data.get("session_type", "")).lower().strip()
    date = str(data.get("date", "")).strip()
    if st not in ("push", "pull", "legs"):
        raise ValueError("session_type must be push, pull, or legs")
    if not date:
        date = local_today_iso()
    # validate date
    datetime.strptime(date, "%Y-%m-%d")
    exercises_in = data.get("exercises") or []
    if not exercises_in:
        raise ValueError("exercises required")
    exercises = []
    for ex in exercises_in:
        name = str(ex.get("name", "")).strip()
        if not name:
            raise ValueError("exercise name required")
        # Flat form: {name, weight_lbs, sets, reps}
        # Nested form: {name, sets: [{weight_lbs, sets, reps}, ...]}
        raw_sets = ex.get("sets")
        if isinstance(raw_sets, list):
            sets_in = raw_sets
        elif all(k in ex for k in ("weight_lbs", "reps")):
            sets_in = [
                {
                    "weight_lbs": ex["weight_lbs"],
                    "sets": int(ex.get("sets") or 1),
                    "reps": ex["reps"],
                }
            ]
        else:
            sets_in = []
        set_entries = []
        for s in sets_in:
            if not isinstance(s, dict):
                continue
            try:
                w = float(s.get("weight_lbs"))
                sn = int(s.get("sets") if s.get("sets") is not None else 1)
                r = int(s.get("reps"))
            except (TypeError, ValueError) as e:
                raise ValueError(f"invalid set for {name}: {e}") from e
            if sn < 1 or r < 1:
                raise ValueError(f"sets and reps must be >= 1 for {name}")
            set_entries.append(SetEntry(weight_lbs=w, sets=sn, reps=r))
        if not set_entries:
            raise ValueError(f"no sets for exercise {name}")
        exercises.append(
            ExerciseEntry(
                name=name,
                sets=set_entries,
                is_pr=False,  # set by apply_auto_prs after history is loaded
            )
        )
    notes = str(data.get("notes") or "")
    return Session(
        date=date,
        session_type=st,
        exercises=exercises,
        notes=notes,
    )


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

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

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/healthz":
            self._send_json({"ok": True, "service": "resistance-dashboard"})
            return
        if parsed.path == "/api/dashboard":
            try:
                qs = parse_qs(parsed.query or "")
                force = (qs.get("refresh") or qs.get("force") or ["0"])[0].lower() in (
                    "1",
                    "true",
                    "yes",
                )
                self._send_json(load_dashboard_data(force_refresh=force))
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return
        if parsed.path == "/api/sessions":
            try:
                data = load_dashboard_data(force_refresh=False)
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
            try:
                data = load_dashboard_data(force_refresh=False)
                self._send_json(data.get("nutrition_store") or {})
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return
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
        parsed = urlparse(self.path)
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
            try:
                body = self._read_json()
                session = parse_log_body(body)
                # Auto-tag PRs from history (prior sessions only), then write.
                history, _, _, _ = pull_merged_sessions()
                apply_auto_prs(session, history)
                pr_names = [e.name for e in session.exercises if e.is_pr]
                client = build_github_client(for_write=True)
                result = client.append_workout_safe(session)
                # Reload via merged pull so response matches subsequent GET /api/dashboard
                sessions, source, _err, _gh = pull_merged_sessions()
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
        if parsed.path == "/api/refresh":
            try:
                # Explicit refresh always bypasses remote caches.
                self._send_json(load_dashboard_data(force_refresh=True))
            except Exception as e:
                self._send_json({"error": str(e)}, status=500)
            return
        if parsed.path == "/api/inventory/add":
            try:
                body = self._read_json()
                client = build_github_client(for_write=True)
                store = load_inventory_and_targets(client)
                updated = add_ingredient(store["inventory"], body)
                write = write_nutrition_file(
                    client,
                    INVENTORY_PATH,
                    updated,
                    message=f"nutrition: add/update ingredient {body.get('name', '')}",
                )
                self._send_json({"ok": True, "inventory": updated, "write": write})
            except (ValueError, json.JSONDecodeError) as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/inventory/remove":
            try:
                body = self._read_json()
                client = build_github_client(for_write=True)
                store = load_inventory_and_targets(client)
                updated = remove_ingredient(
                    store["inventory"],
                    ingredient_id=str(body.get("id") or ""),
                    name=str(body.get("name") or ""),
                )
                write = write_nutrition_file(
                    client,
                    INVENTORY_PATH,
                    updated,
                    message=f"nutrition: remove ingredient {body.get('id') or body.get('name')}",
                )
                self._send_json({"ok": True, "inventory": updated, "write": write})
            except (ValueError, json.JSONDecodeError) as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/inventory/stock":
            try:
                body = self._read_json()
                client = build_github_client(for_write=True)
                store = load_inventory_and_targets(client)
                updated = set_in_stock(
                    store["inventory"],
                    ingredient_id=str(body.get("id") or ""),
                    in_stock=bool(body.get("in_stock", True)),
                )
                write = write_nutrition_file(
                    client,
                    INVENTORY_PATH,
                    updated,
                    message=f"nutrition: stock {'on' if body.get('in_stock') else 'off'} {body.get('id')}",
                )
                self._send_json({"ok": True, "inventory": updated, "write": write})
            except (ValueError, json.JSONDecodeError) as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/targets":
            try:
                body = self._read_json()
                client = build_github_client(for_write=True)
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
                action = try_parse_coach_action(question)
                if action:
                    act_result = _execute_coach_action(action)
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
                dashboard = load_dashboard_data(force_refresh=False)
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
        if parsed.path == "/api/meal-plan/generate":
            try:
                body = self._read_json() if int(self.headers.get("Content-Length") or 0) else {}
                data = load_dashboard_data()
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
                plan = generate_meal_plan(
                    store.get("inventory") or {"ingredients": []},
                    store.get("targets") or {},
                    consumed,
                    food_logs_today=today_logs,
                )
                self._send_json({"ok": True, "plan": plan})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        if parsed.path == "/api/workout-plan/generate":
            try:
                body = self._read_json() if int(self.headers.get("Content-Length") or 0) else {}
                data = load_dashboard_data(force_refresh=False)
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
                    session_type=str(session_type).lower() if session_type else None,
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
        if parsed.path == "/api/workout/exercise/available":
            try:
                body = self._read_json()
                client = build_github_client(for_write=True)
                store = load_catalog_and_goals(client)
                updated = set_exercise_available(
                    store["catalog"],
                    exercise_id=str(body.get("id") or ""),
                    available=bool(body.get("available", True)),
                )
                write = write_catalog(
                    client,
                    updated,
                    message=f"workout: available {body.get('id')}",
                )
                self._send_json({"ok": True, "catalog": updated, "write": write})
            except (ValueError, json.JSONDecodeError) as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=500)
            return
        self._send_json({"error": "not found"}, status=404)


def main() -> None:
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    # Ensure static exists
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"Resistance dashboard listening on http://127.0.0.1:{port}/", flush=True)
    print(f"API: http://127.0.0.1:{port}/api/dashboard", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", flush=True)
        server.shutdown()


if __name__ == "__main__":
    main()
