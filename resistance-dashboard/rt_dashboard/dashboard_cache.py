"""Local disk cache for slow remote sources (Google Health + GitHub sessions).

Default TTL: 1 hour. Local workout markdown and inventory are always read live.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    CaloriesBurnedDay,
    HealthSnapshot,
    HydrationDay,
    NutritionDay,
    Session,
    SleepSample,
    WeightSample,
)

DEFAULT_TTL_SEC = int(os.environ.get("DASHBOARD_CACHE_TTL_SEC", "3600"))
CACHE_DIR = Path(
    os.environ.get(
        "DASHBOARD_CACHE_DIR",
        str(Path.home() / ".config" / "resistance-dashboard" / "cache"),
    )
)
HEALTH_CACHE = "health.json"
GITHUB_SESSIONS_CACHE = "github_sessions.json"


def cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def ttl_sec() -> int:
    try:
        return max(60, int(os.environ.get("DASHBOARD_CACHE_TTL_SEC", str(DEFAULT_TTL_SEC))))
    except ValueError:
        return DEFAULT_TTL_SEC


def _read_cache_file(name: str) -> Optional[dict]:
    path = cache_dir() / name
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache_file(name: str, payload: dict) -> None:
    path = cache_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def cache_age_sec(fetched_at: Optional[float]) -> Optional[float]:
    if fetched_at is None:
        return None
    return max(0.0, time.time() - float(fetched_at))


def is_fresh(fetched_at: Optional[float], ttl: Optional[int] = None) -> bool:
    age = cache_age_sec(fetched_at)
    if age is None:
        return False
    return age < float(ttl if ttl is not None else ttl_sec())


# --- Health -----------------------------------------------------------------

def health_from_dict(data: dict) -> HealthSnapshot:
    weights = [
        WeightSample(
            date=str(w.get("date") or ""),
            weight_lbs=float(w.get("weight_lbs") or 0),
            source=str(w.get("source") or "cache"),
        )
        for w in (data.get("weight") or [])
        if isinstance(w, dict) and w.get("date")
    ]
    sleep = [
        SleepSample(
            date=str(s.get("date") or ""),
            sleep_hours=float(s.get("sleep_hours") or 0),
            efficiency_pct=s.get("efficiency_pct"),
            source=str(s.get("source") or "cache"),
        )
        for s in (data.get("sleep") or [])
        if isinstance(s, dict) and s.get("date")
    ]
    nutrition = [
        NutritionDay(
            date=str(n.get("date") or ""),
            calories=n.get("calories"),
            protein_g=n.get("protein_g"),
            carbs_g=n.get("carbs_g"),
            fat_g=n.get("fat_g"),
            source=str(n.get("source") or "cache"),
        )
        for n in (data.get("nutrition") or [])
        if isinstance(n, dict) and n.get("date")
    ]
    hydration = [
        HydrationDay(
            date=str(h.get("date") or ""),
            water_ml=float(h.get("water_ml") or 0),
            source=str(h.get("source") or "cache"),
        )
        for h in (data.get("hydration") or [])
        if isinstance(h, dict) and h.get("date")
    ]
    burned = [
        CaloriesBurnedDay(
            date=str(c.get("date") or ""),
            calories=float(c.get("calories") or 0),
            source=str(c.get("source") or "cache"),
        )
        for c in (data.get("calories_burned") or [])
        if isinstance(c, dict) and c.get("date")
    ]
    return HealthSnapshot(
        weight=weights,
        sleep=sleep,
        nutrition=nutrition,
        hydration=hydration,
        calories_burned=burned,
        error=data.get("error"),
    )


def load_health_cache() -> Tuple[Optional[HealthSnapshot], Optional[float], dict]:
    """Returns (snapshot|None, fetched_at|None, meta)."""
    raw = _read_cache_file(HEALTH_CACHE)
    if not raw:
        return None, None, {"hit": False, "reason": "missing"}
    fetched_at = raw.get("fetched_at")
    try:
        fetched_at_f = float(fetched_at) if fetched_at is not None else None
    except (TypeError, ValueError):
        fetched_at_f = None
    snap = health_from_dict(raw.get("health") or {})
    age = cache_age_sec(fetched_at_f)
    return (
        snap,
        fetched_at_f,
        {
            "hit": True,
            "fresh": is_fresh(fetched_at_f),
            "age_sec": round(age, 1) if age is not None else None,
            "fetched_at": raw.get("fetched_at_iso"),
            "path": str(cache_dir() / HEALTH_CACHE),
        },
    )


def save_health_cache(
    health: Optional[HealthSnapshot] = None,
    *,
    error: Optional[str] = None,
    keep_previous_if_empty: bool = True,
) -> dict:
    """
    Persist health snapshot and/or mark a fetch attempt.

    Always updates ``fetched_at`` so a failed/timeout pull still suppresses
    remote retries until TTL expires (uses last good data when available).
    """
    now = time.time()
    prev_raw = _read_cache_file(HEALTH_CACHE) or {}
    prev_health = prev_raw.get("health") if isinstance(prev_raw.get("health"), dict) else {}

    if health is not None:
        hdict = health.to_dict()
        has_data = bool(
            hdict.get("weight")
            or hdict.get("sleep")
            or hdict.get("nutrition")
            or hdict.get("hydration")
            or hdict.get("calories_burned")
        )
        if not has_data and keep_previous_if_empty and prev_health:
            # Keep last good points; attach latest error if any
            merged = dict(prev_health)
            if error or hdict.get("error"):
                merged["error"] = error or hdict.get("error")
            hdict = merged
        elif error and not hdict.get("error"):
            hdict["error"] = error
    elif keep_previous_if_empty and prev_health:
        hdict = dict(prev_health)
        if error:
            hdict["error"] = error
    else:
        hdict = {"weight": [], "sleep": [], "nutrition": [], "hydration": [], "calories_burned": [], "error": error}

    payload = {
        "fetched_at": now,
        "fetched_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "ttl_sec": ttl_sec(),
        "last_error": error,
        "health": hdict,
    }
    _write_cache_file(HEALTH_CACHE, payload)
    return {
        "saved": True,
        "fetched_at": payload["fetched_at_iso"],
        "path": str(cache_dir() / HEALTH_CACHE),
        "had_data": bool(
            hdict.get("weight")
            or hdict.get("sleep")
            or hdict.get("nutrition")
            or hdict.get("hydration")
            or hdict.get("calories_burned")
        ),
    }


# --- GitHub remote sessions -------------------------------------------------

def sessions_to_dicts(sessions: List[Session]) -> List[dict]:
    return [s.to_dict() for s in sessions]


def sessions_from_dicts(items: Any) -> List[Session]:
    """Best-effort rebuild Session objects from cached to_dict() payloads."""
    out: List[Session] = []
    if not isinstance(items, list):
        return out
    for raw in items:
        if not isinstance(raw, dict):
            continue
        # Prefer re-parse from a synthetic markdown if structure is rich enough;
        # otherwise rebuild from dict fields used by analytics.
        try:
            from .models import ExerciseEntry, SetEntry

            exercises = []
            for ex in raw.get("exercises") or []:
                if not isinstance(ex, dict):
                    continue
                # Cached exercise dicts from to_dict may be flattened
                sets: List[SetEntry] = []
                if ex.get("sets") and isinstance(ex["sets"], list) and ex["sets"]:
                    first = ex["sets"][0]
                    if isinstance(first, dict) and "weight_lbs" in first:
                        for st in ex["sets"]:
                            if not isinstance(st, dict):
                                continue
                            sets.append(
                                SetEntry(
                                    weight_lbs=float(st.get("weight_lbs") or 0),
                                    sets=int(st.get("sets") or 1),
                                    reps=int(st.get("reps") or 0),
                                )
                            )
                if not sets and (
                    ex.get("weight_lbs") is not None
                    or ex.get("sets") is not None
                    or ex.get("reps") is not None
                ):
                    # Flattened form from analytics/dashboard_payload path
                    sets.append(
                        SetEntry(
                            weight_lbs=float(ex.get("weight_lbs") or 0),
                            sets=int(ex.get("sets") or 1),
                            reps=int(ex.get("reps") or 0),
                        )
                    )
                exercises.append(
                    ExerciseEntry(
                        name=str(ex.get("name") or "exercise"),
                        sets=sets,
                        is_pr=bool(ex.get("is_pr")),
                        raw=str(ex.get("raw") or ""),
                    )
                )
            out.append(
                Session(
                    date=str(raw.get("date") or ""),
                    session_type=str(raw.get("session_type") or "other"),
                    exercises=exercises,
                    notes=str(raw.get("notes") or ""),
                    source_file=str(raw.get("source_file") or ""),
                )
            )
        except Exception:
            continue
    return out


def load_github_sessions_cache() -> Tuple[List[Session], Optional[float], dict]:
    raw = _read_cache_file(GITHUB_SESSIONS_CACHE)
    if not raw:
        return [], None, {"hit": False, "reason": "missing"}
    fetched_at = raw.get("fetched_at")
    try:
        fetched_at_f = float(fetched_at) if fetched_at is not None else None
    except (TypeError, ValueError):
        fetched_at_f = None
    sessions = sessions_from_dicts(raw.get("sessions") or [])
    age = cache_age_sec(fetched_at_f)
    return (
        sessions,
        fetched_at_f,
        {
            "hit": True,
            "fresh": is_fresh(fetched_at_f),
            "age_sec": round(age, 1) if age is not None else None,
            "count": len(sessions),
            "fetched_at": raw.get("fetched_at_iso"),
            "path": str(cache_dir() / GITHUB_SESSIONS_CACHE),
        },
    )


def save_github_sessions_cache(sessions: List[Session]) -> dict:
    now = time.time()
    payload = {
        "fetched_at": now,
        "fetched_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "ttl_sec": ttl_sec(),
        "sessions": sessions_to_dicts(sessions),
    }
    _write_cache_file(GITHUB_SESSIONS_CACHE, payload)
    return {
        "saved": True,
        "count": len(sessions),
        "fetched_at": payload["fetched_at_iso"],
        "path": str(cache_dir() / GITHUB_SESSIONS_CACHE),
    }


def cache_status() -> dict:
    h_snap, h_at, h_meta = load_health_cache()
    g_sess, g_at, g_meta = load_github_sessions_cache()
    t = ttl_sec()
    return {
        "ttl_sec": t,
        "cache_dir": str(cache_dir()),
        "health": {
            **h_meta,
            "weight_points": len(h_snap.weight) if h_snap else 0,
            "sleep_points": len(h_snap.sleep) if h_snap else 0,
            "nutrition_days": len(h_snap.nutrition) if h_snap else 0,
            "seconds_until_refresh": (
                max(0, int(t - (cache_age_sec(h_at) or t)))
                if h_at is not None
                else 0
            ),
        },
        "github_sessions": {
            **g_meta,
            "seconds_until_refresh": (
                max(0, int(t - (cache_age_sec(g_at) or t)))
                if g_at is not None
                else 0
            ),
        },
    }
