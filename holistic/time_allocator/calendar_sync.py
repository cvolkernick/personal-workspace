"""Google Calendar → time-allocator: busy events reduce free active time.

Requires calendar OAuth (separate from Health scopes):
  GOOGLE_CALENDAR_REFRESH_TOKEN  (preferred)
  or re-auth with calendar.readonly on GOOGLE_REFRESH_TOKEN

Run:  python3 holistic/scripts/google_calendar_auth.py
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TOKEN_URL = "https://oauth2.googleapis.com/token"
CAL_BASE = "https://www.googleapis.com/calendar/v3"

# Prefer a calendar-specific refresh token so Health scopes stay intact.
CALENDAR_SCOPES = (
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events.readonly",
)

ENV_PATH = Path.home() / ".config" / "resistance-dashboard" / "env"
CLIENT_CANDIDATES = [
    Path(os.environ.get("GOOGLE_CREDENTIALS_FILE", "")),
    Path.home() / ".config" / "resistance-dashboard" / "google-oauth-client.json",
    Path.home() / "Downloads" / "credentials.json",
    Path.home() / "grok_excel_test" / "credentials.json",
]

# Heuristic: map event titles onto existing targets (optional credit).
TITLE_TARGET_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(sleep|bedtime|nap)\b", re.I), "sleep"),
    (re.compile(r"\b(workout|gym|lift|run|exercise|training)\b", re.I), "workout"),
    (re.compile(r"\b(duchess|dog\s*walk|walk\s*dog)\b", re.I), "duchess-walk"),
    (re.compile(r"\b(lyft|rideshare|drive\s*for)\b", re.I), "lyft"),
]


def _load_env_file() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV_PATH.is_file():
        return out
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[len("export ") :].strip()
        if "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _env(key: str, default: str = "") -> str:
    if key in os.environ and os.environ[key].strip():
        return os.environ[key].strip()
    return _load_env_file().get(key, default).strip() or default


def _load_client() -> tuple[str, str]:
    cid = _env("GOOGLE_CLIENT_ID")
    sec = _env("GOOGLE_CLIENT_SECRET")
    if cid and sec:
        return cid, sec
    for p in CLIENT_CANDIDATES:
        if not p or not str(p) or not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        block = data.get("web") or data.get("installed") or {}
        cid = str(block.get("client_id") or "").strip()
        sec = str(block.get("client_secret") or "").strip()
        if cid and sec:
            return cid, sec
    return "", ""


def _refresh_token() -> str:
    return _env("GOOGLE_CALENDAR_REFRESH_TOKEN") or _env("GOOGLE_REFRESH_TOKEN")


_token_cache: dict[str, Any] = {"access": "", "expiry": 0.0}


def ensure_access_token() -> str:
    now = time.time()
    if _token_cache.get("access") and now < float(_token_cache.get("expiry") or 0) - 60:
        return str(_token_cache["access"])
    cid, sec = _load_client()
    rt = _refresh_token()
    if not (cid and sec and rt):
        raise RuntimeError(
            "Missing Google OAuth for calendar. Run: "
            "python3 holistic/scripts/google_calendar_auth.py"
        )
    body = urllib.parse.urlencode(
        {
            "client_id": cid,
            "client_secret": sec,
            "refresh_token": rt,
            "grant_type": "refresh_token",
        }
    ).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err = e.read().decode(errors="replace")[:400]
        raise RuntimeError(f"Google token refresh failed HTTP {e.code}: {err}") from e
    access = str(data.get("access_token") or "")
    if not access:
        raise RuntimeError("Google token refresh returned no access_token")
    _token_cache["access"] = access
    _token_cache["expiry"] = time.time() + int(data.get("expires_in") or 3600)
    return access


def calendar_credentials_status() -> dict[str, Any]:
    cid, sec = _load_client()
    cal_rt = bool(_env("GOOGLE_CALENDAR_REFRESH_TOKEN"))
    health_rt = bool(_env("GOOGLE_REFRESH_TOKEN"))
    status: dict[str, Any] = {
        "ok": False,
        "client_configured": bool(cid and sec),
        "calendar_refresh_token": cal_rt,
        "health_refresh_token": health_rt,
        "detail": "",
    }
    if not (cid and sec):
        status["detail"] = "No Google OAuth client id/secret"
        return status
    if not (cal_rt or health_rt):
        status["detail"] = (
            "No refresh token — run python3 holistic/scripts/google_calendar_auth.py"
        )
        return status
    # Probe calendar API (lightweight)
    try:
        token = ensure_access_token()
        req = urllib.request.Request(
            f"{CAL_BASE}/users/me/calendarList?maxResults=1",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        status["ok"] = True
        status["detail"] = (
            "Calendar OAuth ready"
            + (" (GOOGLE_CALENDAR_REFRESH_TOKEN)" if cal_rt else " (shared GOOGLE_REFRESH_TOKEN)")
        )
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "403" in msg or "insufficient" in msg.lower() or "ACCESS_TOKEN_SCOPE" in msg:
            status["detail"] = (
                "Token lacks calendar scope — run "
                "python3 holistic/scripts/google_calendar_auth.py"
            )
        else:
            status["detail"] = f"Calendar auth probe failed: {e}"
    return status


def _parse_dt(value: Any, *, all_day_end: bool = False) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        # All-day: YYYY-MM-DD
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            try:
                y, m, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
            except ValueError:
                return None
            dt = datetime(y, m, d, 0, 0, 0, tzinfo=timezone.utc)
            if all_day_end:
                # exclusive end date → keep midnight
                pass
            return dt
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _http_get(url: str, token: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"Calendar API HTTP {e.code}: {err}") from e


def target_hint_for_title(title: str) -> str | None:
    for pat, tid in TITLE_TARGET_HINTS:
        if pat.search(title or ""):
            return tid
    return None


def normalize_event(raw: dict[str, Any], *, calendar_id: str = "primary") -> dict[str, Any] | None:
    """Normalize a Calendar API event into a durable busy block."""
    if not isinstance(raw, dict):
        return None
    status = str(raw.get("status") or "confirmed")
    if status == "cancelled":
        return None
    # Skip free / transparent blocks
    if str(raw.get("transparency") or "").lower() == "transparent":
        return None
    start = raw.get("start") or {}
    end = raw.get("end") or {}
    if not isinstance(start, dict) or not isinstance(end, dict):
        return None
    all_day = bool(start.get("date") and not start.get("dateTime"))
    st = _parse_dt(start.get("dateTime") or start.get("date"))
    en = _parse_dt(end.get("dateTime") or end.get("date"), all_day_end=True)
    if not st or not en or en <= st:
        return None
    # Skip multi-day all-day noise (holidays, birthdays) by default
    if all_day:
        return None
    # Cap absurdly long events
    hours = (en - st).total_seconds() / 3600.0
    if hours > 18:
        return None
    title = str(raw.get("summary") or "(no title)").strip() or "(no title)"
    eid = str(raw.get("id") or raw.get("iCalUID") or f"{st.isoformat()}-{title}")
    return {
        "id": eid,
        "title": title,
        "start": st.isoformat(timespec="seconds"),
        "end": en.isoformat(timespec="seconds"),
        "calendar_id": calendar_id,
        "location": str(raw.get("location") or "")[:200] or None,
        "all_day": False,
        "status": status,
        "html_link": raw.get("htmlLink"),
        "target_hint": target_hint_for_title(title),
        "source": "google_calendar",
    }


def fetch_events_for_range(
    *,
    time_min: datetime,
    time_max: datetime,
    calendar_ids: list[str] | None = None,
    max_per_calendar: int = 100,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch timed busy events from one or more calendars."""
    token = ensure_access_token()
    cals = calendar_ids or ["primary"]
    out: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"calendars": [], "errors": []}
    tmin = time_min.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    tmax = time_max.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    for cal in cals:
        cal_id = urllib.parse.quote(cal, safe="@.")
        items: list[dict[str, Any]] = []
        page_token = ""
        try:
            while True:
                q = {
                    "timeMin": tmin,
                    "timeMax": tmax,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": str(min(250, max_per_calendar)),
                }
                if page_token:
                    q["pageToken"] = page_token
                url = f"{CAL_BASE}/calendars/{cal_id}/events?{urllib.parse.urlencode(q)}"
                data = _http_get(url, token)
                for ev in data.get("items") or []:
                    norm = normalize_event(ev, calendar_id=cal)
                    if norm:
                        items.append(norm)
                page_token = str(data.get("nextPageToken") or "")
                if not page_token or len(items) >= max_per_calendar:
                    break
            meta["calendars"].append({"id": cal, "events": len(items)})
            out.extend(items)
        except Exception as e:  # noqa: BLE001
            meta["errors"].append({"calendar_id": cal, "error": str(e)})

    # Dedup by id
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for ev in sorted(out, key=lambda e: e["start"]):
        if ev["id"] in seen:
            continue
        seen.add(ev["id"])
        uniq.append(ev)
    return uniq, meta


def overlap_minutes(
    a0: datetime, a1: datetime, b0: datetime, b1: datetime
) -> float:
    start = max(a0, b0)
    end = min(a1, b1)
    if end <= start:
        return 0.0
    return (end - start).total_seconds() / 60.0


def merge_busy_intervals(
    events: list[dict[str, Any]],
    *,
    win_start: datetime,
    win_end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Merge overlapping event intervals clipped to [win_start, win_end)."""
    segs: list[tuple[datetime, datetime]] = []
    for ev in events:
        st = _parse_dt(ev.get("start"))
        en = _parse_dt(ev.get("end"))
        if not st or not en:
            continue
        st = st.astimezone(win_start.tzinfo)
        en = en.astimezone(win_start.tzinfo)
        if en <= win_start or st >= win_end:
            continue
        segs.append((max(st, win_start), min(en, win_end)))
    if not segs:
        return []
    segs.sort(key=lambda x: x[0])
    merged = [segs[0]]
    for st, en in segs[1:]:
        last_st, last_en = merged[-1]
        if st <= last_en:
            merged[-1] = (last_st, max(last_en, en))
        else:
            merged.append((st, en))
    return merged


def busy_minutes_in_window(
    events: list[dict[str, Any]] | None,
    *,
    now: datetime,
    window_minutes: int = 24 * 60,
    only_future: bool = False,
) -> tuple[int, list[dict[str, Any]]]:
    """Total non-overlapping busy minutes + per-event window slices."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone()
    win_start = now if only_future else now
    # For full recommended: window is [now, now+W]. Past portion of ongoing events still counts.
    win_end = now + timedelta(minutes=max(0, int(window_minutes)))
    rows: list[dict[str, Any]] = []
    for ev in events or []:
        st = _parse_dt(ev.get("start"))
        en = _parse_dt(ev.get("end"))
        if not st or not en:
            continue
        st = st.astimezone(now.tzinfo)
        en = en.astimezone(now.tzinfo)
        mins = overlap_minutes(st, en, win_start, win_end)
        if mins <= 0:
            continue
        rows.append(
            {
                **{k: ev.get(k) for k in (
                    "id", "title", "calendar_id", "location", "target_hint", "html_link", "source"
                )},
                "start": max(st, win_start).isoformat(timespec="seconds"),
                "end": min(en, win_end).isoformat(timespec="seconds"),
                "minutes": int(round(mins)),
                "full_start": st.isoformat(timespec="seconds"),
                "full_end": en.isoformat(timespec="seconds"),
            }
        )
    merged = merge_busy_intervals(events or [], win_start=win_start, win_end=win_end)
    total = int(round(sum((en - st).total_seconds() / 60.0 for st, en in merged)))
    rows.sort(key=lambda r: r.get("start") or "")
    return total, rows


def calendar_blocks_for_plan(
    state: dict[str, Any],
    *,
    now: datetime,
    window_minutes: int = 24 * 60,
    ignore_progress: bool = False,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Build plan blocks from stored calendar events.

    Returns (blocks, busy_minutes_merged, notes).
    """
    events = list((state or {}).get("calendar_events") or [])
    if not events:
        return [], 0, []

    # Remaining mode: only time still ahead (events fully in the past drop out)
    total, slices = busy_minutes_in_window(
        events, now=now, window_minutes=window_minutes, only_future=True
    )
    if total <= 0:
        return [], 0, []

    notes: list[str] = [
        f"Calendar: {total}m of committed events in the next {window_minutes // 60}h "
        f"({len(slices)} event slice(s)) — reduces free active time"
    ]
    blocks: list[dict[str, Any]] = []
    # One aggregated block keeps pies readable; detail lives in calendar_events
    if total > 0:
        titles = [str(s.get("title") or "") for s in slices[:4]]
        extra = f" +{len(slices) - 4} more" if len(slices) > 4 else ""
        detail = ", ".join(titles) + extra if titles else "scheduled events"
        blocks.append(
            {
                "source": "calendar",
                "id": "calendar",
                "title": "Calendar commitments",
                "minutes": total,
                "role": "calendar",
                "kind": "calendar_busy",
                "priority": 15,
                "reason": detail,
                "event_count": len(slices),
                "events": slices[:20],
            }
        )
    # Hint notes for target-aligned events
    if not ignore_progress:
        for s in slices:
            hint = s.get("target_hint")
            if hint:
                notes.append(
                    f"Calendar “{s.get('title')}” looks like {hint} "
                    f"({s.get('minutes')}m) — still log the target when done"
                )
    return blocks, total, notes


def sync_calendar(
    state: dict[str, Any],
    *,
    days_ahead: int = 2,
    days_back: int = 0,
    calendar_ids: list[str] | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pull events into state.calendar_events for the rolling horizon."""
    out = deepcopy(state) if state else {}
    if now is None:
        now = datetime.now(timezone.utc).astimezone()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone()

    days_ahead = max(1, min(int(days_ahead), 14))
    days_back = max(0, min(int(days_back), 7))
    time_min = now - timedelta(days=days_back)
    time_max = now + timedelta(days=days_ahead)

    # Prefer configured calendars from state
    cfg = out.get("calendar_config") if isinstance(out.get("calendar_config"), dict) else {}
    ids = calendar_ids or list(cfg.get("calendar_ids") or ["primary"])

    meta: dict[str, Any] = {
        "ok": False,
        "synced_at": now.isoformat(timespec="seconds"),
        "time_min": time_min.isoformat(timespec="seconds"),
        "time_max": time_max.isoformat(timespec="seconds"),
        "event_count": 0,
        "source": "google_calendar",
    }
    try:
        events, fetch_meta = fetch_events_for_range(
            time_min=time_min, time_max=time_max, calendar_ids=ids
        )
        out["calendar_events"] = events
        meta["ok"] = True
        meta["event_count"] = len(events)
        meta["calendars"] = fetch_meta.get("calendars")
        if fetch_meta.get("errors"):
            meta["errors"] = fetch_meta["errors"]
            if not events:
                meta["ok"] = False
                meta["error"] = "; ".join(
                    f"{e.get('calendar_id')}: {e.get('error')}" for e in fetch_meta["errors"]
                )
        out["calendar_meta"] = {
            "synced_at": meta["synced_at"],
            "event_count": meta["event_count"],
            "time_min": meta["time_min"],
            "time_max": meta["time_max"],
            "calendar_ids": ids,
            "ok": meta["ok"],
            "error": meta.get("error"),
        }
    except Exception as e:  # noqa: BLE001
        meta["error"] = str(e)
        out.setdefault("calendar_events", list(out.get("calendar_events") or []))
        out["calendar_meta"] = {
            "synced_at": meta["synced_at"],
            "event_count": len(out.get("calendar_events") or []),
            "ok": False,
            "error": str(e),
            "calendar_ids": ids,
        }
    return out, meta


def calendar_summary_for_state(
    state: dict[str, Any],
    *,
    now: datetime | None = None,
    window_minutes: int = 24 * 60,
) -> dict[str, Any]:
    """Compact summary for API / UI / Ask Grok."""
    if now is None:
        now = datetime.now(timezone.utc).astimezone()
    events = list((state or {}).get("calendar_events") or [])
    total, slices = busy_minutes_in_window(
        events, now=now, window_minutes=window_minutes, only_future=True
    )
    meta = (state or {}).get("calendar_meta") or {}
    auth = calendar_credentials_status()
    return {
        "auth": auth,
        "synced_at": meta.get("synced_at"),
        "stored_events": len(events),
        "busy_minutes_24h": total,
        "busy_hours_24h": round(total / 60.0, 2),
        "upcoming": slices[:12],
        "ok": bool(meta.get("ok", auth.get("ok"))),
        "error": meta.get("error") or (None if auth.get("ok") else auth.get("detail")),
        "calendar_ids": meta.get("calendar_ids") or ["primary"],
    }
