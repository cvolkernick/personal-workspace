"""Automatic bulk/cut flip from body-fat medians.

For each America/New_York civil day D, the daily value is the median of
deduped body-fat readings in [D-6, D]. That is the same window and the same
validity and merge-twin dedupe as the chart helper in
``static/body-fat-series.js``. The chart overlay stays an arithmetic mean.
This module's decision uses the median.

A day is countable when that window has at least 4 deduped readings.
Otherwise it is thin. A thin day is skipped: the 14-counted-day window
stretches further back. It does not reset the count. A counted day whose
median misses the bound is one miss. It does not reset the other days.

Flip when both of these are true over the last 14 counted days:

* the daily median is past the bound on at least 12 of them, and
* the median of all deduped readings whose dates fall in the closed span
  of those 14 days (the earliest counted day through the latest) is past
  the same bound.

In a cut, past the bound means <= 10.0% and the flip is to bulk. In a bulk
(stored ``bulk`` / ``slow_bulk`` / ``gain``), past the bound means >= 15.0%
and the flip is to cut. Anything else holds.

When the condition is met and ``bf_auto_flip`` is on (the default in
``coach_rules.json``), the flip is applied through ``update_targets`` and
``applied_targets_write`` / ``persist_targets`` — the same writers
``switch_phase`` uses. Cut to bulk writes maintenance at the live coach-card
TDEE, then TDEE+200 rounded to the nearest 50 on day 14. Bulk to cut
restores the cut targets stored on the way into bulk. One persisted flip
record per date and leaving-phase makes a second evaluation, including a
racing request, a no-op. Days on or before that flip date do not count
toward the new phase.

The audit log is ``bf_phase_flips``: a Turso table beside ``nutrition_targets``
when Turso is live, and ``bf_phase_flips.json`` in the coach state directory
(the same directory as ``coach_weekly.json``) when Turso is dark. The log
does not live inside the targets payload, because ``normalize_targets``
would drop it on the next calorie save.
"""

from __future__ import annotations

import fcntl
import json
import logging
import math
import os
import re
import statistics
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence
from zoneinfo import ZoneInfo

log = logging.getLogger("fitdash.bf_phase")

WINDOW_DAYS = 7
COUNTED_NEEDED = 14
HITS_NEEDED = 12
MIN_READINGS = 4
LOOKBACK_DAYS = 240
CUT_AT = 10.0
BULK_AT = 15.0
PROTEIN_FLOOR_G = 170
FAT_CAP_G = 65
RAMP_AFTER_DAYS = 14
BANNER_DAYS = 7
ET = ZoneInfo("America/New_York")

_FRAC_SECONDS = re.compile(r"\.(\d+)(?=(Z|[+-]\d{2}:?\d{2})?$)")
_THREAD_LOCK = threading.Lock()

_TARGET_KEYS = ("calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g", "sodium_mg")


def add_days(iso: str, delta: int) -> str:
    """Civil-date shift. Calendar arithmetic, so a DST boundary cannot skip a day."""
    day = datetime.strptime(str(iso)[:10], "%Y-%m-%d")
    return (day + timedelta(days=int(delta))).strftime("%Y-%m-%d")


def _civil_day(value: Any) -> str:
    day = str(value or "")[:10]
    if len(day) == 10 and day[4] == "-" and day[7] == "-":
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            return ""
        return day
    return ""


def _days_between(start: str, end: str) -> Optional[int]:
    a = _civil_day(start)
    b = _civil_day(end)
    if not a or not b:
        return None
    return (datetime.strptime(b, "%Y-%m-%d") - datetime.strptime(a, "%Y-%m-%d")).days


def _round_pct(pct: float) -> float:
    """Hundredths, half away from zero for positive percentages (matches JS Math.round)."""
    return math.floor(float(pct) * 100.0 + 0.5) / 100.0


def percentage_of(row: Any) -> Optional[float]:
    """Valid logged percentage. Matches body-fat-series.js: finite and inside [0, 100]."""
    if not isinstance(row, dict):
        return None
    raw = row.get("percentage")
    if raw is None:
        raw = row.get("body_fat_pct")
    if isinstance(raw, bool) or raw is None or raw == "":
        return None
    try:
        pct = float(raw)
    except (TypeError, ValueError):
        return None
    if pct != pct or pct < 0 or pct > 100:
        return None
    return _round_pct(pct)


def _second_stamp(recorded_at: Any) -> str:
    text = str(recorded_at or "").strip()
    if not text:
        return ""
    return _FRAC_SECONDS.sub("", text)


def measurements(samples: Sequence[Any]) -> List[dict]:
    """Every valid reading. Same-day rows stay separate. Invalid rows are omitted."""
    out: List[dict] = []
    for row in samples or []:
        if not isinstance(row, dict):
            continue
        day = _civil_day(row.get("date"))
        pct = percentage_of(row)
        if not day or pct is None:
            continue
        out.append(
            {
                "date": day,
                "percentage": pct,
                "recorded_at": str(row.get("recorded_at") or ""),
            }
        )
    out.sort(key=lambda item: (item["date"], item["recorded_at"]))
    return out


def dedupe_twins(rows: Sequence[dict]) -> List[dict]:
    """Collapse same-day, same-value readings whose timestamps differ only by fractional seconds.

    A reading with no timestamp is kept. It is not treated as a twin of another
    untimestamped row, because nothing shows the stamps differ only by a fraction.
    """
    seen = set()
    out: List[dict] = []
    for row in rows or []:
        stamp = _second_stamp(row.get("recorded_at"))
        if not stamp:
            out.append(row)
            continue
        key = (row["date"], row["percentage"], stamp)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _in_window(rows: Sequence[dict], day: str, window_days: int) -> List[dict]:
    start = add_days(day, -(int(window_days) - 1))
    return [row for row in rows if start <= row["date"] <= day]


def _median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return float(statistics.median(list(values)))


def window_stats(samples: Sequence[Any], day: str, window_days: int = WINDOW_DAYS) -> dict:
    """Median, counts, and the thin flag for the window ending on ``day``.

    An empty window has a null median, not 0. Thin means fewer than 4 deduped
    readings. Distinct days are reported and do not, by themselves, make a
    day thin.
    """
    civil = _civil_day(day)
    empty = {
        "median": None,
        "n_readings": 0,
        "n_days": 0,
        "thin": True,
        "window_start": "",
        "window_end": civil,
    }
    if not civil:
        return empty
    rows = _in_window(dedupe_twins(measurements(samples)), civil, window_days)
    empty["window_start"] = add_days(civil, -(int(window_days) - 1))
    if not rows:
        return empty
    days = {row["date"] for row in rows}
    return {
        "median": _median([row["percentage"] for row in rows]),
        "n_readings": len(rows),
        "n_days": len(days),
        "thin": len(rows) < MIN_READINGS,
        "window_start": empty["window_start"],
        "window_end": civil,
    }


def phase_kind(phase: Any) -> str:
    """``cut`` (including unset), ``bulk`` (including slow_bulk and gain), or the raw token."""
    if phase is None:
        return "cut"
    text = str(phase).strip().lower().replace("-", "_").replace(" ", "_")
    if text in ("", "none", "null"):
        return "cut"
    if text in ("bulk", "slow_bulk", "gain"):
        return "bulk"
    if text == "cut":
        return "cut"
    return text


def phase_label(kind: Any) -> str:
    token = phase_kind(kind) if kind not in ("cut", "bulk") else str(kind)
    if token == "bulk":
        return "BULK"
    if token == "cut":
        return "CUT"
    text = str(kind or token or "").strip()
    return text.upper() if text else "CUT"


def _meets(kind: str, median: Optional[float]) -> bool:
    if median is None:
        return False
    if kind == "cut":
        return float(median) <= CUT_AT
    if kind == "bulk":
        return float(median) >= BULK_AT
    return False


def threshold_label(kind: str) -> str:
    if kind == "cut":
        return "Bulk at <=10.0%"
    if kind == "bulk":
        return "Cut at >=15.0%"
    return "Hold"


def _number(value: Any) -> Optional[float]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num:
        return None
    return num


def _round_nearest_50(kcal: float) -> int:
    return int(round(float(kcal) / 50.0) * 50)


def _carbs_for(calories: int, protein: int = PROTEIN_FLOOR_G, fat: int = FAT_CAP_G) -> int:
    remain = int(calories) - int(protein) * 4 - int(fat) * 9
    carbs = int(round(remain / 4.0))
    return carbs if carbs > 0 else 0


def maintenance_targets(tdee: Any) -> Optional[dict]:
    """Cut to bulk, flip day: live TDEE, protein floor 170 g, fat cap 65 g, carbs the remainder."""
    live = _number(tdee)
    if live is None:
        return None
    calories = int(round(live))
    return {
        "phase": "slow_bulk",
        "calories": calories,
        "protein_g": PROTEIN_FLOOR_G,
        "fat_g": FAT_CAP_G,
        "carbs_g": _carbs_for(calories),
        "tdee_kcal": calories,
    }


def ramp_targets(tdee: Any) -> Optional[dict]:
    """Day 14 of a bulk flip: that flip's TDEE + 200, rounded to the nearest 50."""
    live = _number(tdee)
    if live is None:
        return None
    calories = _round_nearest_50(int(round(live)) + 200)
    return {
        "phase": "slow_bulk",
        "calories": calories,
        "protein_g": PROTEIN_FLOOR_G,
        "fat_g": FAT_CAP_G,
        "carbs_g": _carbs_for(calories),
        "tdee_kcal": int(round(live)),
    }


def snapshot_targets(applied: Optional[dict]) -> dict:
    """Macros and phase actually stored. Missing fields stay missing."""
    src = applied if isinstance(applied, dict) else {}
    out: Dict[str, Any] = {}
    for key in _TARGET_KEYS:
        num = _number(src.get(key))
        if num is None:
            continue
        out[key] = int(round(num)) if key == "calories" else num
    if src.get("phase") is not None and str(src.get("phase")).strip() != "":
        out["phase"] = src.get("phase")
    return out


def counted_window(
    samples: Sequence[Any],
    as_of: Any,
    kind: str,
    *,
    anchor: Optional[str] = None,
    needed: int = COUNTED_NEEDED,
    lookback: int = LOOKBACK_DAYS,
) -> List[dict]:
    """Newest-first counted days. Thin days are skipped. The walk stops at ``anchor``.

    ``anchor`` is the latest flip date. Only civil days strictly after it can
    count, so the new phase starts fresh and a same-day re-evaluation cannot
    flip back on the days that caused the flip.
    """
    day = _civil_day(as_of)
    if not day or kind not in ("cut", "bulk"):
        return []
    stop = _civil_day(anchor) if anchor else ""
    rows = dedupe_twins(measurements(samples))
    if not rows:
        return []
    out: List[dict] = []
    for _ in range(max(1, int(lookback))):
        if stop and day <= stop:
            break
        stats = window_stats(rows, day, WINDOW_DAYS)
        if not stats["thin"]:
            out.append(
                {
                    "date": day,
                    "median": stats["median"],
                    "past": _meets(kind, stats["median"]),
                    "n_readings": stats["n_readings"],
                    "n_days": stats["n_days"],
                }
            )
            if len(out) >= int(needed):
                break
        nxt = add_days(day, -1)
        if nxt >= day:
            break
        day = nxt
    return out


def span_median(samples: Sequence[Any], counted: Sequence[dict]) -> Optional[float]:
    """Median of deduped readings on the closed span of the counted days.

    Readings that sit only in a counted day's lookback, on a date before the
    earliest counted day, are outside the span and do not count.
    """
    dates = [str(row.get("date") or "") for row in counted or [] if row.get("date")]
    if not dates:
        return None
    start, end = min(dates), max(dates)
    rows = dedupe_twins(measurements(samples))
    values = [row["percentage"] for row in rows if start <= row["date"] <= end]
    return _median(values)


def evaluate(
    samples: Sequence[Any],
    *,
    as_of: Any,
    phase: Any = None,
    anchor: Optional[str] = None,
) -> dict:
    """Decision for one civil day. Does not write phase or calories."""
    kind = phase_kind(phase)
    day = _civil_day(as_of)
    today = window_stats(samples, day, WINDOW_DAYS) if day else window_stats([], "", WINDOW_DAYS)
    counted = counted_window(samples, day, kind, anchor=anchor) if day else []
    chronological = list(reversed(counted))
    hits = sum(1 for row in counted if row.get("past"))
    median_14 = span_median(samples, counted) if len(counted) >= COUNTED_NEEDED else None
    should = (
        kind in ("cut", "bulk")
        and len(counted) >= COUNTED_NEEDED
        and hits >= HITS_NEEDED
        and _meets(kind, median_14)
    )
    return {
        "phase": kind,
        "phase_label": phase_label(kind),
        "as_of": day,
        "median_7d": today["median"],
        "thin": bool(today["thin"]),
        "n_readings_7d": today["n_readings"],
        "n_days_7d": today["n_days"],
        "hits": hits,
        "hits_needed": HITS_NEEDED,
        "counted": len(counted),
        "counted_needed": COUNTED_NEEDED,
        "counted_days": chronological,
        "median_14d": median_14,
        "threshold_label": threshold_label(kind),
        "anchor": _civil_day(anchor) or None,
        "should_flip": should,
        "status": "flip" if should else "hold",
        "applied": False,
        "note": None,
        "banner": None,
        "last_flip": None,
        "auto_flip": True,
        "new_targets": None,
    }


def auto_flip_enabled() -> bool:
    """``coach_rules.json`` key ``bf_auto_flip``. Missing means on. Read on each call, not cached."""
    try:
        from rt_dashboard.coach_steps import RULES_PATH, read_json

        data = read_json(RULES_PATH) or {}
    except Exception:
        return True
    if not isinstance(data, dict) or "bf_auto_flip" not in data:
        return True
    raw = data.get("bf_auto_flip")
    if isinstance(raw, str):
        return raw.strip().lower() not in ("0", "false", "off", "no")
    return bool(raw)


def _state_dir() -> Path:
    base = os.environ.get("FITDASH_COACH_STATE_DIR") or os.path.expanduser(
        "~/.local/state/fitdash-coach"
    )
    return Path(base)


def _log_path() -> Path:
    return _state_dir() / "bf_phase_flips.json"


def _lock_path() -> Path:
    return _state_dir() / "bf_phase_flips.lock"


def _uid(user_id: str = "") -> str:
    from rt_dashboard.nutrition_store import _targets_uid

    return _targets_uid(user_id)


def _at_et(as_of: Optional[str] = None) -> str:
    day = _civil_day(as_of) if as_of else ""
    if day:
        stamped = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=ET, hour=12)
        return stamped.isoformat(timespec="seconds")
    return datetime.now(ET).isoformat(timespec="seconds")


def _today_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


def _turso_on() -> bool:
    try:
        from rt_dashboard.turso_http import turso_enabled

        return bool(turso_enabled())
    except Exception:
        return False


def _file_load() -> List[dict]:
    path = _log_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    events = raw.get("events") if isinstance(raw, dict) else None
    if not isinstance(events, list):
        return []
    return [row for row in events if isinstance(row, dict)]


def _file_save(events: Sequence[dict]) -> None:
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "events": list(events)}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _upsert(events: List[dict], event: dict) -> List[dict]:
    key = event.get("key")
    out = [row for row in events if row.get("key") != key]
    out.append(event)
    return out


def _find(events: Sequence[dict], key: str) -> Optional[dict]:
    for row in reversed(list(events)):
        if row.get("key") == key:
            return row
    return None


@contextmanager
def _exclusive() -> Iterator[None]:
    """One in-process lock plus an exclusive file lock for other processes."""
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+", encoding="utf-8")
    _THREAD_LOCK.acquire()
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        _THREAD_LOCK.release()
        fh.close()


_ENSURE_SQL = """
CREATE TABLE IF NOT EXISTS bf_phase_flips (
  user_id TEXT NOT NULL,
  flip_key TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (user_id, flip_key)
)
"""


def _turso_connect():
    from rt_dashboard.turso_http import connect

    return connect()


def _turso_load(user_id: str) -> List[dict]:
    with _turso_connect() as conn:
        conn.execute(_ENSURE_SQL)
        cursor = conn.execute(
            "SELECT payload FROM bf_phase_flips WHERE user_id = ? ORDER BY created_at ASC",
            (user_id,),
        )
        rows = cursor.fetchall()
    out: List[dict] = []
    for row in rows or []:
        raw = row["payload"] if isinstance(row, dict) else row[0]
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def _turso_get(user_id: str, key: str) -> Optional[dict]:
    with _turso_connect() as conn:
        conn.execute(_ENSURE_SQL)
        cursor = conn.execute(
            "SELECT payload FROM bf_phase_flips WHERE user_id = ? AND flip_key = ?",
            (user_id, key),
        )
        row = cursor.fetchone()
    if not row:
        return None
    raw = row["payload"] if isinstance(row, dict) else row[0]
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _turso_insert(event: dict) -> bool:
    """Claim the key. True only when this event's claim_token is the row that stuck.

    ``ON CONFLICT DO NOTHING`` plus a read-back, so two hosts cannot both
    believe they inserted. This does not depend on ``RETURNING``.
    """
    token = str(event.get("claim_token") or "")
    if not token:
        return False
    blob = json.dumps(event, separators=(",", ":"))
    with _turso_connect() as conn:
        conn.execute(_ENSURE_SQL)
        conn.execute(
            """
            INSERT INTO bf_phase_flips(user_id, flip_key, payload, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, flip_key) DO NOTHING
            """,
            (event.get("user_id"), event.get("key"), blob, event.get("at_et") or ""),
        )
        cursor = conn.execute(
            "SELECT payload FROM bf_phase_flips WHERE user_id = ? AND flip_key = ?",
            (event.get("user_id"), event.get("key")),
        )
        row = cursor.fetchone()
    if not row:
        return False
    raw = row["payload"] if isinstance(row, dict) else row[0]
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and data.get("claim_token") == token


def _turso_update(event: dict) -> None:
    blob = json.dumps(event, separators=(",", ":"))
    with _turso_connect() as conn:
        conn.execute(
            "UPDATE bf_phase_flips SET payload = ? WHERE user_id = ? AND flip_key = ?",
            (blob, event.get("user_id"), event.get("key")),
        )


def _turso_delete(user_id: str, key: str) -> None:
    with _turso_connect() as conn:
        conn.execute(
            "DELETE FROM bf_phase_flips WHERE user_id = ? AND flip_key = ?",
            (user_id, key),
        )


def load_events(user_id: str = "") -> List[dict]:
    """Flip log. Turso when it is live, otherwise the coach-state JSON file.

    A read does not create the file. Callers that already hold the lock pass
    ``user_id`` so a Turso read stays scoped to that person.
    """
    uid = _uid(user_id) if str(user_id or "").strip() or _turso_on() else ""
    if _turso_on():
        try:
            if uid:
                return _turso_load(uid)
        except Exception:
            log.exception("bf phase log turso read failed")
    events = _file_load()
    if uid:
        return [row for row in events if row.get("user_id") in (uid, "", None)]
    return events


def _events_for(user_id: str, events: Optional[Sequence[dict]] = None) -> List[dict]:
    uid = _uid(user_id)
    rows = list(events) if events is not None else load_events(uid)
    return [row for row in rows if row.get("user_id") in (uid, "", None)]


def latest_phase_event(user_id: str = "", events: Optional[Sequence[dict]] = None) -> Optional[dict]:
    for row in reversed(_events_for(user_id, events)):
        if row.get("kind") == "phase" and row.get("status", "applied") == "applied":
            return row
    return None


def latest_anchor(user_id: str = "", events: Optional[Sequence[dict]] = None) -> Optional[str]:
    row = latest_phase_event(user_id, events)
    if not row:
        return None
    return _civil_day(row.get("date")) or None


def _public_flip(event: Optional[dict]) -> Optional[dict]:
    if not isinstance(event, dict):
        return None
    kind = phase_kind(event.get("new_phase"))
    return {
        "date": event.get("date"),
        "at_et": event.get("at_et"),
        "phase": kind,
        "phase_label": phase_label(kind),
        "trigger": event.get("trigger"),
        "old_phase": event.get("old_phase"),
        "new_phase": event.get("new_phase"),
    }


def banner_for(event: Optional[dict], as_of: Any) -> Optional[dict]:
    """Visible for the flip day and the next 7 days, until dismissed."""
    if not isinstance(event, dict) or event.get("kind") != "phase":
        return None
    if event.get("banner_dismissed"):
        return None
    if event.get("status") not in (None, "applied"):
        return None
    day = _civil_day(as_of)
    flipped = _civil_day(event.get("date"))
    if not day or not flipped:
        return None
    delta = _days_between(flipped, day)
    if delta is None or delta < 0 or delta > BANNER_DAYS:
        return None
    label = phase_label(event.get("new_phase"))
    return {
        "show": True,
        "text": f"Phase switched to {label} on {flipped}",
        "date": flipped,
        "phase": label,
    }


def _stored_cut(user_id: str, events: Sequence[dict]) -> Optional[dict]:
    for row in reversed(_events_for(user_id, events)):
        snap = row.get("stored_cut_targets")
        if isinstance(snap, dict) and _number(snap.get("calories")) is not None:
            return dict(snap)
    return None


def _attach_log(decision: dict, user_id: str, events: Sequence[dict], as_of: str) -> dict:
    latest = latest_phase_event(user_id, events)
    decision["last_flip"] = _public_flip(latest)
    decision["banner"] = banner_for(latest, as_of)
    decision["anchor"] = latest_anchor(user_id, events)
    decision["auto_flip"] = auto_flip_enabled()
    return decision


def commit_targets(
    fields: dict,
    *,
    base: Optional[dict],
    user_id: str,
    message: str,
) -> tuple:
    """Same write ``switch_phase`` performs: update_targets then applied_targets_write."""
    from rt_dashboard.nutrition_planner import update_targets
    from rt_dashboard.nutrition_store import applied_targets_write

    merged = dict(base or {})
    merged.update(fields or {})
    updated = update_targets(merged)
    return applied_targets_write(updated, _uid(user_id), message=message)


def _claim_write(event: dict, write_fn: Callable[[], tuple]) -> tuple:
    """Persist one flip. A second caller with the same key does not write targets."""
    key = str(event.get("key") or "")
    uid = str(event.get("user_id") or "")
    with _exclusive():
        if _turso_on():
            # Turso is the claim authority across hosts. A failed claim does
            # not write targets. The file lock above still serializes one machine.
            try:
                existing = _turso_get(uid, key)
                if existing and existing.get("status") == "applied":
                    return False, existing
                if existing:
                    return False, existing
                pending = dict(event, status="pending", claim_token=uuid.uuid4().hex)
                inserted = _turso_insert(pending)
            except Exception:
                log.exception("bf phase claim failed")
                return False, event
            if not inserted:
                try:
                    existing = _turso_get(uid, key)
                except Exception:
                    log.exception("bf phase claim reread failed")
                    existing = None
                return False, existing or event
            try:
                stuck, err, _write, persisted = write_fn()
            except Exception:
                try:
                    _turso_delete(uid, key)
                except Exception:
                    log.exception("bf phase claim rollback failed")
                raise
            if not stuck:
                try:
                    _turso_delete(uid, key)
                except Exception:
                    log.exception("bf phase claim rollback failed")
                event["error"] = err
                return False, event
            done = dict(event, status="applied", new_targets=snapshot_targets(persisted))
            try:
                _turso_update(done)
            except Exception:
                log.exception("bf phase claim update failed")
            events = _upsert(_file_load(), done)
            _file_save(events)
            return True, done

        events = _file_load()
        existing = _find(events, key)
        if existing and existing.get("status") == "applied":
            return False, existing
        stuck, err, write, persisted = write_fn()
        if not stuck:
            event["error"] = err
            return False, event
        done = dict(event, status="applied", new_targets=snapshot_targets(persisted))
        _file_save(_upsert(events, done))
        return True, done


def _base_event(decision: dict, *, user_id: str, trigger: str, old_targets: dict, new_phase: str) -> dict:
    day = decision.get("as_of") or _today_et()
    kind = decision.get("phase") or "cut"
    return {
        "kind": "phase",
        "key": f"{_uid(user_id)}|{day}|{kind}",
        "user_id": _uid(user_id),
        "date": day,
        "at_et": _at_et(day),
        "trigger": trigger,
        "old_phase": kind,
        "new_phase": new_phase,
        "old_targets": snapshot_targets(old_targets),
        "new_targets": None,
        "counted_days": decision.get("counted_days") or [],
        "hits": decision.get("hits"),
        "counted": decision.get("counted"),
        "median_14d": decision.get("median_14d"),
        "median_7d": decision.get("median_7d"),
        "threshold_label": decision.get("threshold_label"),
        "banner_dismissed": False,
        "status": "applied",
    }


def _apply_flip(decision: dict, *, base: dict, user_id: str, tdee: Any, events: Sequence[dict]) -> dict:
    kind = decision.get("phase")
    if kind == "cut":
        proposed = maintenance_targets(tdee)
        if proposed is None:
            decision["status"] = "hold"
            decision["should_flip"] = False
            decision["note"] = "TDEE estimate unavailable; calories not invented"
            return decision
        fields = {k: proposed[k] for k in ("phase", "calories", "protein_g", "carbs_g", "fat_g")}
        event = _base_event(decision, user_id=user_id, trigger="auto", old_targets=base, new_phase="slow_bulk")
        event["tdee_kcal"] = proposed["tdee_kcal"]
        event["ramp_due"] = add_days(decision["as_of"], RAMP_AFTER_DAYS)
        event["ramp_applied_on"] = None
        event["stored_cut_targets"] = snapshot_targets(base) or {"phase": "cut"}
        event["stored_cut_targets"]["phase"] = base.get("phase") or "cut"
        message = "nutrition: auto body-fat phase flip cut to bulk"
    elif kind == "bulk":
        stored = _stored_cut(user_id, events)
        if not stored or _number(stored.get("calories")) is None:
            decision["status"] = "hold"
            decision["should_flip"] = False
            decision["note"] = "No stored cut targets to restore"
            return decision
        fields = dict(stored)
        fields["phase"] = "cut"
        event = _base_event(decision, user_id=user_id, trigger="auto", old_targets=base, new_phase="cut")
        event["stored_cut_targets"] = snapshot_targets(stored)
        message = "nutrition: auto body-fat phase flip bulk to cut"
    else:
        decision["status"] = "hold"
        return decision

    def _write():
        return commit_targets(fields, base=base, user_id=user_id, message=message)

    wrote, saved = _claim_write(event, _write)
    if not wrote:
        if isinstance(saved, dict) and saved.get("status") == "applied":
            decision["status"] = "hold"
            decision["skipped"] = "already_flipped"
            decision["phase"] = phase_kind(saved.get("new_phase"))
            decision["phase_label"] = phase_label(decision["phase"])
        else:
            decision["status"] = "hold"
            decision["note"] = (saved or {}).get("error") if isinstance(saved, dict) else "flip did not persist"
        return decision
    decision["status"] = "flipped"
    decision["applied"] = True
    decision["phase"] = phase_kind(saved.get("new_phase"))
    decision["phase_label"] = phase_label(decision["phase"])
    decision["new_targets"] = saved.get("new_targets")
    log.info(
        "bf phase auto flip %s -> %s on %s",
        event.get("old_phase"),
        saved.get("new_phase"),
        decision.get("as_of"),
    )
    return decision


def _maybe_ramp(decision: dict, *, base: dict, user_id: str, events: Sequence[dict]) -> dict:
    if decision.get("phase") != "bulk":
        return decision
    latest = latest_phase_event(user_id, events)
    if not isinstance(latest, dict):
        return decision
    if latest.get("trigger") != "auto" or phase_kind(latest.get("new_phase")) != "bulk":
        return decision
    if latest.get("ramp_applied_on"):
        return decision
    tdee = latest.get("tdee_kcal")
    proposed = ramp_targets(tdee)
    if proposed is None:
        return decision
    delta = _days_between(str(latest.get("date") or ""), str(decision.get("as_of") or ""))
    if delta is None or delta < RAMP_AFTER_DAYS:
        return decision
    fields = {k: proposed[k] for k in ("phase", "calories", "protein_g", "carbs_g", "fat_g")}
    event = {
        "kind": "ramp",
        "key": f"ramp|{latest.get('date')}|{_uid(user_id)}",
        "user_id": _uid(user_id),
        "date": decision.get("as_of"),
        "at_et": _at_et(decision.get("as_of")),
        "trigger": "auto",
        "old_phase": "bulk",
        "new_phase": "slow_bulk",
        "old_targets": snapshot_targets(base),
        "counted_days": [],
        "hits": None,
        "counted": None,
        "median_14d": None,
        "threshold_label": decision.get("threshold_label"),
        "tdee_kcal": proposed["tdee_kcal"],
        "flip_date": latest.get("date"),
        "banner_dismissed": False,
        "status": "applied",
    }

    def _write():
        return commit_targets(
            fields,
            base=base,
            user_id=user_id,
            message="nutrition: auto body-fat bulk ramp TDEE+200",
        )

    wrote, saved = _claim_write(event, _write)
    if not wrote:
        return decision
    stamped = dict(latest)
    stamped["ramp_applied_on"] = decision.get("as_of")
    with _exclusive():
        _file_save(_upsert(_file_load(), stamped))
        if _turso_on():
            try:
                _turso_update(stamped)
            except Exception:
                log.exception("bf phase ramp stamp failed")
    decision["status"] = "ramp"
    decision["applied"] = True
    decision["new_targets"] = saved.get("new_targets")
    return decision


def run_phase(
    samples: Sequence[Any],
    *,
    as_of: Any,
    phase: Any = None,
    tdee: Any = None,
    applied: Optional[dict] = None,
    user_id: str = "",
    apply: bool = True,
) -> dict:
    """Evaluate and, when asked, auto-apply through the target writers."""
    day = _civil_day(as_of)
    uid = _uid(user_id)
    events = load_events(uid)
    anchor = latest_anchor(uid, events)
    decision = evaluate(samples, as_of=day, phase=phase, anchor=anchor)
    _attach_log(decision, uid, events, day)
    base = dict(applied or {})
    if decision["should_flip"] and not decision["auto_flip"]:
        decision["status"] = "disabled"
        decision["applied"] = False
        return _with_report(decision, base)
    if apply and decision["should_flip"] and decision["auto_flip"]:
        decision = _apply_flip(decision, base=base, user_id=uid, tdee=tdee, events=events)
        events = load_events(uid)
        _attach_log(decision, uid, events, day)
        if decision.get("applied"):
            decision["auto_flip"] = True
            return _with_report(decision, base)
    if apply and decision["auto_flip"] and not decision.get("applied") and decision.get("status") != "disabled":
        before = decision.get("status")
        decision = _maybe_ramp(decision, base=base, user_id=uid, events=events)
        if decision.get("status") == "ramp":
            events = load_events(uid)
            _attach_log(decision, uid, events, day)
            decision["status"] = "ramp"
        elif before == "flip" and decision.get("skipped") == "already_flipped":
            decision["status"] = "hold"
    return _with_report(decision, base)


def record_manual_flip(
    *,
    previous: Optional[dict],
    updated: Optional[dict],
    user_id: str = "",
    as_of: Optional[str] = None,
) -> dict:
    """Log a manual phase switch and reset the auto count from that date.

    The target write has already happened through switch_phase. This only
    appends the audit row. A later auto evaluation will not count days on
    or before this date.
    """
    old = previous if isinstance(previous, dict) else {}
    new = updated if isinstance(updated, dict) else {}
    day = _civil_day(as_of) or _today_et()
    old_kind = phase_kind(old.get("phase"))
    new_kind = phase_kind(new.get("phase"))
    event = {
        "kind": "phase",
        "key": f"manual|{day}|{old_kind}|{new_kind}|{_at_et(day)}",
        "user_id": _uid(user_id),
        "date": day,
        "at_et": _at_et(day),
        "trigger": "manual",
        "old_phase": old_kind,
        "new_phase": new.get("phase") or new_kind,
        "old_targets": snapshot_targets(old),
        "new_targets": snapshot_targets(new),
        "counted_days": [],
        "hits": None,
        "counted": None,
        "median_14d": None,
        "threshold_label": threshold_label(old_kind),
        "banner_dismissed": False,
        "status": "applied",
    }
    if old_kind == "cut":
        snap = snapshot_targets(old)
        snap["phase"] = old.get("phase") or "cut"
        event["stored_cut_targets"] = snap
    else:
        event["stored_cut_targets"] = _stored_cut(_uid(user_id), load_events(_uid(user_id)))
    with _exclusive():
        events = _file_load()
        events = _upsert(events, event)
        _file_save(events)
        if _turso_on():
            try:
                if not _turso_insert(event):
                    _turso_update(event)
            except Exception:
                log.exception("manual bf phase log turso write failed")
    return event


def phase_report(decision: Optional[dict], targets: Optional[dict] = None) -> dict:
    """Stable fields for MCP ``get_phase`` and the coach-card payload.

    Built only from an evaluation this module already produced. ``phase`` is
    ``cut`` or ``bulk``. ``paused`` is the thin-day pause: today is not
    counted, and the window stretches back instead of resetting.
    """
    src = decision if isinstance(decision, dict) else {}
    last = src.get("last_flip") if isinstance(src.get("last_flip"), dict) else None
    flip_date = _civil_day(last.get("date")) if last else ""
    flip_trigger = last.get("trigger") if last and flip_date else None
    if not flip_date and src.get("applied"):
        flip_date = _civil_day(src.get("as_of"))
        flip_trigger = "auto"
    kind = src.get("phase")
    if kind not in ("cut", "bulk"):
        kind = phase_kind(kind)
    snap = snapshot_targets(targets)
    thin = bool(src.get("thin"))
    return {
        "phase": kind if kind in ("cut", "bulk") else kind,
        "median_7d": src.get("median_7d"),
        "hits": int(src.get("hits") or 0),
        "hits_needed": int(src.get("hits_needed") or HITS_NEEDED),
        "counted": int(src.get("counted") or 0),
        "counted_needed": int(src.get("counted_needed") or COUNTED_NEEDED),
        "median_14d": src.get("median_14d"),
        "thin": thin,
        "paused": thin,
        "flip_applied": bool(flip_date),
        "flip_date": flip_date or None,
        "flip_trigger": flip_trigger,
        "targets": {
            "calories": snap.get("calories"),
            "protein_g": snap.get("protein_g"),
            "carbs_g": snap.get("carbs_g"),
            "fat_g": snap.get("fat_g"),
            "phase": snap.get("phase"),
        },
        "as_of": src.get("as_of"),
        "status": src.get("status"),
        "threshold_label": src.get("threshold_label"),
        "auto_flip": src.get("auto_flip"),
    }


def _with_report(decision: dict, base: Optional[dict]) -> dict:
    shown = dict(base or {})
    if isinstance(decision.get("new_targets"), dict):
        shown.update(decision["new_targets"])
    decision["phase_report"] = phase_report(decision, shown)
    return decision


def dismiss_bf_banner(*, user_id: str = "", as_of: Optional[str] = None) -> dict:
    """Hide the phase-switch banner for the latest flip. The flip itself stays logged."""
    uid = _uid(user_id)
    day = _civil_day(as_of) or _today_et()
    with _exclusive():
        events = _file_load() if not _turso_on() else load_events(uid)
        if _turso_on():
            try:
                events = _turso_load(uid)
            except Exception:
                log.exception("bf banner dismiss read failed")
                events = _file_load()
        latest = latest_phase_event(uid, events)
        if not latest or not banner_for(latest, day):
            return {"ok": True, "dismissed": False}
        stamped = dict(latest)
        stamped["banner_dismissed"] = True
        _file_save(_upsert(_file_load(), stamped))
        if _turso_on():
            try:
                _turso_update(stamped)
            except Exception:
                log.exception("bf banner dismiss turso write failed")
    return {"ok": True, "dismissed": True, "date": stamped.get("date")}


def _payload_bits(payload: Optional[dict]) -> tuple:
    data = payload if isinstance(payload, dict) else {}
    nut = data.get("nutrition_store") if isinstance(data.get("nutrition_store"), dict) else {}
    targets = nut.get("targets") if isinstance(nut.get("targets"), dict) else {}
    health = data.get("health") if isinstance(data.get("health"), dict) else {}
    samples = health.get("body_fat") or []
    coach = data.get("coach") if isinstance(data.get("coach"), dict) else {}
    nt = coach.get("nutrition_targets") if isinstance(coach.get("nutrition_targets"), dict) else {}
    applied = nt.get("applied") if isinstance(nt.get("applied"), dict) else {}
    merged = dict(applied)
    for key, value in targets.items():
        if value is not None:
            merged[key] = value
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    return samples, targets, merged, str(meta.get("user_id") or "")


def bf_phase_for_payload(
    payload: Optional[dict],
    *,
    as_of: Any,
    tdee: Any = None,
    user_id: str = "",
    apply: bool = True,
) -> dict:
    """Coach-card body-fat phase. Applies a flip when ``apply`` is true and the rule is met."""
    data = payload if isinstance(payload, dict) else {}
    samples, targets, merged, meta_uid = _payload_bits(data)
    uid = user_id or meta_uid
    if tdee is None:
        card = data.get("coach_card") if isinstance(data.get("coach_card"), dict) else {}
        weekly = card.get("weekly") if isinstance(card.get("weekly"), dict) else {}
        block = weekly.get("tdee") if isinstance(weekly.get("tdee"), dict) else {}
        tdee = block.get("tdee")
    try:
        view = run_phase(
            samples,
            as_of=as_of,
            phase=targets.get("phase", merged.get("phase")),
            tdee=tdee,
            applied=merged,
            user_id=uid,
            apply=apply,
        )
    except Exception:
        log.exception("bf phase evaluation failed")
        view = evaluate(samples, as_of=as_of, phase=targets.get("phase"))
        view["note"] = "evaluation failed"
        view["auto_flip"] = auto_flip_enabled()
        view = _with_report(view, merged)
    if apply and view.get("applied") and isinstance(view.get("new_targets"), dict) and isinstance(data, dict):
        nut = data.get("nutrition_store")
        if not isinstance(nut, dict):
            nut = {}
            data["nutrition_store"] = nut
        current = dict(nut.get("targets") or {})
        current.update(view["new_targets"])
        nut["targets"] = current
    return view
