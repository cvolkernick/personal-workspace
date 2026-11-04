"""Editable coaching targets (steps, kcal, hydration).

The phase calendar (and the coach-rules hydration target, when the calendar
has no hydration track) stays the fallback. A settings row overrides one
number at a time. Null means "use the calendar."

Storage is the existing Turso-or-local-SQLite settings database:

- Turso when ``TURSO_DATABASE_URL`` and ``TURSO_AUTH_TOKEN`` are set.
- Otherwise a SQLite file (``FITDASH_COACHING_TARGETS_DB`` or
  ``~/.config/resistance-dashboard/coaching_targets.sqlite3``).

Migration is additive and lazy. The first write runs ``CREATE TABLE IF NOT
EXISTS``. Target columns are nullable. Nothing existing is altered. A read
does not create the SQLite file.

Auth for the agent routes is the existing ``FITDASH_AGENT_TOKEN``
(``agent_read_gate``). No session and no loopback bypass.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs

KEYS = ("steps", "kcal", "hydration_ml")
BOUNDS = {
    "steps": (1000, 30000),
    "kcal": (1200, 6000),
    "hydration_ml": (500, 8000),
}
_BODY_KEYS = frozenset({"target", "value", "updated_by"})
_MAX_UPDATED_BY = 64

MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS coaching_target_settings (
  user_id TEXT PRIMARY KEY,
  steps INTEGER,
  kcal INTEGER,
  hydration_ml INTEGER,
  updated_at TEXT,
  updated_by TEXT
)
"""


def _empty() -> Dict[str, Any]:
    return {"steps": None, "kcal": None, "hydration_ml": None, "updated_at": None}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def row_user_id(user_id: str = "") -> str:
    uid = (user_id or "").strip()
    if uid:
        return uid
    for key in ("FITDASH_USER_ID", "FITDASH_INVENTORY_AGENT_USER_ID"):
        bound = (os.environ.get(key) or "").strip()
        if bound and bound.lower() != "default":
            return bound
    return "default"


def backend() -> str:
    forced = (os.environ.get("FITDASH_COACHING_TARGETS_BACKEND") or "").strip().lower()
    if forced in ("sqlite", "local", "file"):
        return "sqlite"
    if forced == "turso":
        return "turso"
    url = (os.environ.get("TURSO_DATABASE_URL") or "").strip()
    token = (os.environ.get("TURSO_AUTH_TOKEN") or "").strip()
    return "turso" if url and token else "sqlite"


def db_path() -> Path:
    override = (os.environ.get("FITDASH_COACHING_TARGETS_DB") or "").strip()
    if override:
        return Path(override).expanduser()
    cfg = (os.environ.get("RESISTANCE_DASHBOARD_CONFIG_DIR") or "").strip()
    base = Path(cfg).expanduser() if cfg else Path.home() / ".config" / "resistance-dashboard"
    return base / "coaching_targets.sqlite3"


def _cell(row: Any, key: str) -> Any:
    if row is None:
        return None
    try:
        if isinstance(row, dict):
            return row.get(key)
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def _int_or_none(raw: Any) -> Optional[int]:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if not number.is_integer():
        return None
    return int(number)


def _row_to_overrides(row: Any) -> Dict[str, Any]:
    out = _empty()
    if row is None:
        return out
    for key in KEYS:
        out[key] = _int_or_none(_cell(row, key))
    updated = _cell(row, "updated_at")
    out["updated_at"] = str(updated) if updated else None
    return out


def _upsert_sql(column: str) -> str:
    if column not in KEYS:
        raise ValueError("unknown target")
    return f"""
        INSERT INTO coaching_target_settings (user_id, {column}, updated_at, updated_by)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
          {column} = excluded.{column},
          updated_at = excluded.updated_at,
          updated_by = excluded.updated_by
    """


def _sqlite_connect(path: Path, *, create: bool) -> Optional[sqlite3.Connection]:
    if not create and not path.is_file():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    if new:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return conn


def _sqlite_load(user_id: str) -> Dict[str, Any]:
    path = db_path()
    conn = _sqlite_connect(path, create=False)
    if conn is None:
        return _empty()
    try:
        conn.execute(MIGRATION_SQL)
        row = conn.execute(
            """
            SELECT steps, kcal, hydration_ml, updated_at
            FROM coaching_target_settings WHERE user_id = ?
            """,
            (row_user_id(user_id),),
        ).fetchone()
        conn.commit()
        return _row_to_overrides(row)
    finally:
        conn.close()


def _sqlite_save(column: str, value: Optional[int], user_id: str, updated_by: str) -> None:
    conn = _sqlite_connect(db_path(), create=True)
    if conn is None:
        raise RuntimeError("coaching target settings could not be saved")
    try:
        conn.execute(MIGRATION_SQL)
        conn.execute(
            _upsert_sql(column),
            (row_user_id(user_id), value, _utc_now(), updated_by),
        )
        conn.commit()
    finally:
        conn.close()


def _turso_load(user_id: str) -> Dict[str, Any]:
    from .turso_http import connect

    with connect() as conn:
        row = conn.execute(
            """
            SELECT steps, kcal, hydration_ml, updated_at
            FROM coaching_target_settings WHERE user_id = ?
            """,
            (row_user_id(user_id),),
        ).fetchone()
    return _row_to_overrides(row)


def _turso_save(column: str, value: Optional[int], user_id: str, updated_by: str) -> None:
    from .turso_http import connect

    with connect() as conn:
        conn.execute(MIGRATION_SQL)
        conn.execute(
            _upsert_sql(column),
            (row_user_id(user_id), value, _utc_now(), updated_by),
        )


def load_overrides(user_id: str = "") -> Dict[str, Any]:
    """Stored overrides. Missing file, missing table, or a store error is empty."""
    try:
        if backend() == "turso":
            return _turso_load(user_id)
        return _sqlite_load(user_id)
    except Exception:
        return _empty()


def stored_override(key: str, user_id: str = "") -> Optional[int]:
    if key not in KEYS:
        return None
    value = load_overrides(user_id).get(key)
    return value if isinstance(value, int) else None


def _who(raw: Any) -> str:
    if raw in (None, ""):
        return "agent"
    if not isinstance(raw, str):
        raise ValueError("updated_by must be a string")
    who = " ".join(raw.split())
    if not who or len(who) > _MAX_UPDATED_BY or "@" in who:
        raise ValueError("updated_by must be a short label without an email")
    return who


def validate_assignment(target: Any, value: Any) -> Tuple[str, Optional[int]]:
    """Return ``(target, int or None)``. None clears the override."""
    name = str(target or "").strip()
    if name not in BOUNDS:
        allowed = ", ".join(KEYS)
        raise ValueError(f"target must be one of: {allowed}")
    if value is None:
        return name, None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be an integer or null")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{name} must be an integer or null")
    number = int(value)
    lo, hi = BOUNDS[name]
    if number < lo or number > hi:
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return name, number


def set_override(
    target: Any,
    value: Any,
    user_id: str = "",
    updated_by: Any = None,
) -> Dict[str, Any]:
    """Validate, store one override, and return the effective targets."""
    column, number = validate_assignment(target, value)
    who = _who(updated_by)
    if backend() == "turso":
        _turso_save(column, number, user_id, who)
    else:
        _sqlite_save(column, number, user_id, who)
    return effective_targets(user_id=user_id)


def _steps_baseline(as_of: str, rules: dict) -> Tuple[Optional[int], str]:
    from .coach_steps import steps_cfg
    from .phase_calendar import steps_resolution

    info = steps_resolution(as_of, rules=rules)
    if info.get("applied") and info.get("step_target") is not None:
        return int(info["step_target"]), "phase_calendar"
    if info.get("past_end") and info.get("carry_step") is not None:
        return int(info["carry_step"]), "phase_calendar"
    return int(steps_cfg(rules)["default_target"]), "coach_rules"


def _kcal_baseline(as_of: str, rules: dict) -> Tuple[Optional[int], str]:
    from .phase_calendar import effective

    phase = effective(as_of, rules=rules)
    if isinstance(phase, dict) and phase.get("kcal") is not None:
        return int(phase["kcal"]), "phase_calendar"
    return None, "phase_calendar"


def _hydration_baseline(rules: dict) -> Tuple[Optional[int], str]:
    from .coach_steps import rules_hydration_ml
    from .phase_calendar import load_calendar, track_active

    track: Dict[str, Any] = {}
    calendar = load_calendar(rules)
    if isinstance(calendar, dict):
        tracks = calendar.get("tracks")
        if isinstance(tracks, dict) and isinstance(tracks.get("hydration"), dict):
            track.update(tracks["hydration"])
    block = (rules or {}).get("phase_calendar")
    if isinstance(block, dict) and isinstance(block.get("hydration"), dict):
        track.update(block["hydration"])
    if track_active(track):
        raw = track.get("target_ml", track.get("hydration_ml"))
        number = _int_or_none(raw)
        if number is not None and number > 0:
            return number, "phase_calendar"
    ml = rules_hydration_ml(rules)
    if ml is None:
        return None, "coach_rules"
    return int(ml) if float(ml).is_integer() else int(float(ml)), "coach_rules"


def _pack(key: str, baseline: Optional[int], baseline_source: str, overrides: Dict[str, Any]) -> Dict[str, Any]:
    override = overrides.get(key)
    if isinstance(override, int):
        return {
            "effective": override,
            "source": "settings",
            "override": override,
            "calendar": baseline,
        }
    return {
        "effective": baseline,
        "source": baseline_source,
        "override": None,
        "calendar": baseline,
    }


def coerce_day(as_of: Any = None) -> str:
    if as_of in (None, ""):
        from .timeutil import local_today_iso

        return local_today_iso()
    text = str(as_of).strip()[:10]
    try:
        date.fromisoformat(text)
    except ValueError:
        raise ValueError("as_of must be YYYY-MM-DD") from None
    return text


def effective_targets(as_of: Any = None, user_id: str = "") -> Dict[str, Any]:
    """Effective steps, kcal, and hydration for one day."""
    from .coach_steps import load_rules

    day = coerce_day(as_of)
    rules = load_rules()
    overrides = load_overrides(user_id)
    steps, steps_src = _steps_baseline(day, rules)
    kcal, kcal_src = _kcal_baseline(day, rules)
    hydration, hydration_src = _hydration_baseline(rules)
    return {
        "ok": True,
        "as_of": day,
        "targets": {
            "steps": _pack("steps", steps, steps_src, overrides),
            "kcal": _pack("kcal", kcal, kcal_src, overrides),
            "hydration_ml": _pack("hydration_ml", hydration, hydration_src, overrides),
        },
    }


def effective_hydration_ml(rules: Optional[dict] = None, user_id: str = "") -> Optional[int]:
    override = stored_override("hydration_ml", user_id)
    if override is not None:
        return override
    from .coach_steps import load_rules, rules_hydration_ml

    ml = rules_hydration_ml(load_rules() if rules is None else rules)
    if ml is None:
        return None
    return int(ml) if float(ml).is_integer() else int(float(ml))


def _as_of_from_query(query: str) -> str:
    raw = (parse_qs(query or "", keep_blank_values=False).get("as_of") or [None])[0]
    return coerce_day(raw)


def respond_get(headers, query: str = "", user_id: str = "") -> Tuple[int, Dict[str, Any]]:
    """GET /api/agent/coaching_targets. Agent token only."""
    from .agent_read import agent_read_gate

    denied = agent_read_gate(headers)
    if denied:
        return 401, denied
    try:
        day = _as_of_from_query(query)
    except ValueError as exc:
        return 400, {"ok": False, "error": "bad_request", "message": str(exc)}
    try:
        return 200, effective_targets(day, user_id)
    except Exception:
        return 500, {"ok": False, "error": "coaching_targets_failed"}


def respond_set(headers, body: Any, user_id: str = "") -> Tuple[int, Dict[str, Any]]:
    """POST /api/agent/coaching_targets. Same agent token. One key per call."""
    from .agent_read import agent_read_gate

    denied = agent_read_gate(headers)
    if denied:
        return 401, denied
    if not isinstance(body, dict):
        return 400, {"ok": False, "error": "bad_request", "message": "body must be a JSON object"}
    extra = sorted(set(body) - _BODY_KEYS)
    if extra:
        return 400, {"ok": False, "error": "bad_request", "message": "unknown fields: " + ", ".join(extra)}
    if "target" not in body or "value" not in body:
        return 400, {"ok": False, "error": "bad_request", "message": "target and value are required"}
    try:
        view = set_override(body.get("target"), body.get("value"), user_id, body.get("updated_by"))
    except ValueError as exc:
        return 400, {"ok": False, "error": "invalid_target", "message": str(exc)}
    except Exception:
        return 500, {"ok": False, "error": "coaching_targets_failed", "message": "settings could not be saved"}
    return 200, view
