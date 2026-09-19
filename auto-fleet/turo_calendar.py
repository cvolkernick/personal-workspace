"""Helm two-event Turo calendar apply (pickup-ready + drop-off/turnover).

Timed events, not a multi-day block. Calendar SoT:
8573511898287d1b8f660c06facffcc37498aede9616dd75d2a5c28e51cc25e9@group.calendar.google.com
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

FLEET_TZ = ZoneInfo("America/New_York")
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TURO_CALENDAR_ID = (
    "8573511898287d1b8f660c06facffcc37498aede9616dd75d2a5c28e51cc25e9"
    "@group.calendar.google.com"
)
DEFAULT_TOKEN_PATH = Path.home() / ".config" / "auto-fleet" / "gcal-token.json"
EVENT_MINUTES = 30
_RETURN_MARKERS = ("return", "drop-off", "dropoff", "drop off", "turnover")

HttpFn = Callable[[str, Optional[bytes], Mapping[str, str]], Any]


def classify_turo_kind(title: str, extra: str = "") -> Optional[str]:
    """Map live calendar titles to pickup or return. Helm SoT, not the word return."""
    title_low = (title or "").lower()
    extra_low = (extra or "").lower()
    for hay in (title_low, extra_low):
        if not hay:
            continue
        if "pickup" in hay:
            return "pickup"
        if any(marker in hay for marker in _RETURN_MARKERS):
            return "return"
    return None


def _http_json(
    url: str,
    data: bytes | None = None,
    headers: Mapping[str, str] | None = None,
) -> Any:
    hdrs = dict(headers or {})
    method = hdrs.pop("X-HTTP-Method-Override", None)
    req = urllib.request.Request(
        url, data=data, headers=hdrs, method=method or ("POST" if data else "GET")
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url.split('?', 1)[0]}: {body[:240]}") from exc
    if not raw:
        return {}
    return json.loads(raw)


def resolve_gcal_creds(
    *,
    env: Mapping[str, str] | None = None,
    token_path: Path | None = None,
) -> Optional[dict[str, str]]:
    merged: dict[str, str] = {}
    if env:
        merged.update({k: str(v) for k, v in env.items() if v})
    refresh = (
        merged.get("GCAL_REFRESH_TOKEN")
        or merged.get("AUTO_FLEET_GCAL_REFRESH_TOKEN")
        or ""
    ).strip()
    client_id = (
        merged.get("GCAL_CLIENT_ID")
        or merged.get("GOOGLE_CLIENT_ID")
        or merged.get("AUTO_FLEET_GCAL_CLIENT_ID")
        or ""
    ).strip()
    client_secret = (
        merged.get("GCAL_CLIENT_SECRET")
        or merged.get("GOOGLE_CLIENT_SECRET")
        or merged.get("AUTO_FLEET_GCAL_CLIENT_SECRET")
        or ""
    ).strip()
    token_file = Path(token_path) if token_path is not None else DEFAULT_TOKEN_PATH
    if token_file.is_file():
        try:
            data = json.loads(token_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict):
            refresh = refresh or str(data.get("refresh_token") or "").strip()
            client_id = client_id or str(data.get("client_id") or "").strip()
            client_secret = client_secret or str(data.get("client_secret") or "").strip()
            installed = data.get("installed") or data.get("web")
            if isinstance(installed, dict):
                client_id = client_id or str(installed.get("client_id") or "").strip()
                client_secret = client_secret or str(
                    installed.get("client_secret") or ""
                ).strip()
    if refresh and client_id and client_secret:
        return {
            "refresh_token": refresh,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    return None


def refresh_access_token(creds: Mapping[str, str], *, http: HttpFn | None = None) -> str:
    fn = http or _http_json
    body = urllib.parse.urlencode(
        {
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    data = fn(
        TOKEN_URL,
        body,
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    token = str((data or {}).get("access_token") or "").strip()
    if not token:
        raise RuntimeError("token endpoint returned no access_token")
    return token


def _parse_stored(raw: str | None) -> Optional[datetime]:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=FLEET_TZ)
    return dt.astimezone(FLEET_TZ)


def event_when_body(stored: str | None) -> Optional[dict[str, str]]:
    dt = _parse_stored(stored)
    if dt is None:
        return None
    if "T" not in (stored or "").replace(" ", "T"):
        return None
    return {
        "dateTime": dt.replace(microsecond=0).isoformat(),
        "timeZone": "America/New_York",
    }


def _event_end_body(start_body: Mapping[str, str]) -> dict[str, str]:
    raw = str(start_body.get("dateTime") or "")
    dt = _parse_stored(raw)
    if dt is None:
        return dict(start_body)
    end = dt + timedelta(minutes=EVENT_MINUTES)
    return {
        "dateTime": end.replace(microsecond=0).isoformat(),
        "timeZone": str(start_body.get("timeZone") or "America/New_York"),
    }


def pickup_title(change: Mapping[str, Any]) -> str:
    guest = str(change.get("guest") or "Guest").strip()
    vehicle = str(change.get("vehicle") or "").strip()
    trip = str(change.get("trip_id") or "").strip()
    bits = ["Pickup ready", guest]
    if vehicle:
        bits.append(vehicle)
    if trip:
        bits.append(f"#{trip}")
    return " ".join(bits)


def dropoff_title(change: Mapping[str, Any]) -> str:
    guest = str(change.get("guest") or "Guest").strip()
    vehicle = str(change.get("vehicle") or "").strip()
    trip = str(change.get("trip_id") or "").strip()
    bits = ["Drop-off/turnover", guest]
    if vehicle:
        bits.append(vehicle)
    if trip:
        bits.append(f"#{trip}")
    return " ".join(bits)


def _event_blob(ev: Mapping[str, Any]) -> str:
    return " ".join(
        str(ev.get(k) or "")
        for k in ("summary", "title", "description", "location", "id")
    )


def match_pair(
    events: Sequence[Mapping[str, Any]], change: Mapping[str, Any]
) -> dict[str, Optional[dict[str, Any]]]:
    trip = str(change.get("trip_id") or "").strip()
    guest = str(change.get("guest") or "").strip().lower()
    hits: list[dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        blob = _event_blob(ev).lower()
        if trip and trip.lower() in blob:
            hits.append(dict(ev))
            continue
        if guest and guest in blob:
            hits.append(dict(ev))
    pickup = None
    dropoff = None
    for ev in hits:
        title = str(ev.get("summary") or ev.get("title") or "")
        desc = str(ev.get("description") or "")
        kind = classify_turo_kind(title, desc)
        if kind == "pickup" and pickup is None:
            pickup = ev
        elif kind == "return" and dropoff is None:
            dropoff = ev
    return {"pickup": pickup, "dropoff": dropoff}


class HttpCalendar:
    """stdlib Calendar client. Inject http for tests."""

    def __init__(
        self,
        *,
        http: HttpFn | None = None,
        access_token: str | None = None,
        creds: Mapping[str, str] | None = None,
        calendar_id: str = TURO_CALENDAR_ID,
    ) -> None:
        self.http = http or _http_json
        self.access_token = access_token
        self.creds = dict(creds) if creds else None
        self.calendar_id = calendar_id

    def _auth(self) -> dict[str, str]:
        token = self.access_token
        if not token and self.creds:
            token = refresh_access_token(self.creds, http=self.http)
            self.access_token = token
        if not token:
            raise RuntimeError("Google Calendar not configured")
        return {"Authorization": f"Bearer {token}"}

    def list_events(self, *, query: str = "", max_results: int = 50) -> list[dict[str, Any]]:
        cid = urllib.parse.quote(self.calendar_id, safe="@.")
        params = {"maxResults": max_results, "singleEvents": "true"}
        if query:
            params["q"] = query
        url = f"{CALENDAR_API}/calendars/{cid}/events?{urllib.parse.urlencode(params)}"
        data = self.http(url, None, self._auth()) or {}
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        return [i for i in items if isinstance(i, dict)]

    def patch_event(self, event_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        cid = urllib.parse.quote(self.calendar_id, safe="@.")
        eid = urllib.parse.quote(str(event_id))
        url = f"{CALENDAR_API}/calendars/{cid}/events/{eid}"
        payload = json.dumps(dict(body)).encode("utf-8")
        headers = {
            **self._auth(),
            "Content-Type": "application/json",
            "X-HTTP-Method-Override": "PATCH",
        }
        data = self.http(url, payload, headers) or {}
        if isinstance(data, dict) and data.get("id"):
            return {"ok": True, "event": data}
        return {"ok": True, "event": data if isinstance(data, dict) else {}}

    def create_event(self, body: Mapping[str, Any]) -> dict[str, Any]:
        cid = urllib.parse.quote(self.calendar_id, safe="@.")
        url = f"{CALENDAR_API}/calendars/{cid}/events"
        payload = json.dumps(dict(body)).encode("utf-8")
        headers = {**self._auth(), "Content-Type": "application/json"}
        data = self.http(url, payload, headers) or {}
        if isinstance(data, dict) and data.get("error"):
            return {"ok": False, "error": str(data.get("error"))}
        return {"ok": True, "event": data if isinstance(data, dict) else {}}


def _description(change: Mapping[str, Any]) -> str:
    trip = str(change.get("trip_id") or "").strip()
    guest = str(change.get("guest") or "").strip()
    lines = []
    if trip:
        lines.append(f"Reservation ID #{trip}")
    if guest:
        lines.append(f"Guest: {guest}")
    src = str(change.get("source") or "").strip()
    if src:
        lines.append(f"Source: {src}")
    return "\n".join(lines)


def _patch_body(
    *,
    when: Mapping[str, str],
    location: str | None,
    title: str,
    change: Mapping[str, Any],
    existing: Mapping[str, Any] | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "start": dict(when),
        "end": _event_end_body(when),
        "summary": str((existing or {}).get("summary") or title),
        "description": str((existing or {}).get("description") or _description(change)),
    }
    if location:
        body["location"] = location
    return body


def apply_calendar_change(
    change: Mapping[str, Any],
    calendar: Any,
) -> dict[str, Any]:
    """Update both timed events (pickup-ready + drop-off/turnover). Same calendar day apply."""
    if calendar is None:
        return {"ok": False, "skipped": True, "error": "Google Calendar not configured"}
    trip = str(change.get("trip_id") or "").strip()
    query = trip or str(change.get("guest") or "")
    try:
        events = calendar.list_events(query=query)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc) or "calendar list failed"}
    pair = match_pair(events, change)
    pickup_when = event_when_body(str(change.get("start") or "") or None)
    drop_when = event_when_body(str(change.get("end") or "") or None)
    pickup_loc = str(change.get("pickup") or "") or None
    drop_loc = str(change.get("drop_off") or change.get("pickup") or "") or None
    actions: list[dict[str, Any]] = []
    clock_missing = []
    if pickup_when is None:
        clock_missing.append("pickup")
    if drop_when is None:
        clock_missing.append("dropoff")

    def _upsert(kind: str, existing: Optional[dict[str, Any]], when, location, title):
        if when is None and not location:
            return
        body = {}
        if when is not None:
            body.update(
                _patch_body(
                    when=when,
                    location=location,
                    title=title,
                    change=change,
                    existing=existing,
                )
            )
        elif location:
            body = {
                "location": location,
                "summary": str((existing or {}).get("summary") or title),
                "description": str(
                    (existing or {}).get("description") or _description(change)
                ),
            }
        if existing and existing.get("id"):
            result = calendar.patch_event(str(existing["id"]), body)
            actions.append(
                {
                    "kind": kind,
                    "op": "patch",
                    "event_id": str(existing["id"]),
                    "ok": bool(result.get("ok", True)),
                }
            )
            return
        if when is None:
            actions.append(
                {
                    "kind": kind,
                    "op": "skip_create",
                    "ok": False,
                    "error": "clock_missing",
                }
            )
            return
        create_body = _patch_body(
            when=when,
            location=location,
            title=title,
            change=change,
            existing=None,
        )
        result = calendar.create_event(create_body)
        actions.append(
            {
                "kind": kind,
                "op": "create",
                "ok": bool(result.get("ok", True)),
                "event": result.get("event"),
            }
        )

    _upsert("pickup", pair.get("pickup"), pickup_when, pickup_loc, pickup_title(change))
    _upsert(
        "dropoff", pair.get("dropoff"), drop_when, drop_loc, dropoff_title(change)
    )
    ok = bool(actions) and all(a.get("ok") for a in actions if a.get("op") != "skip_create")
    if not actions:
        return {
            "ok": False,
            "error": "no calendar actions (need timed start/end or location)",
            "clock_missing": clock_missing,
            "actions": actions,
        }
    return {
        "ok": ok,
        "calendar_id": getattr(calendar, "calendar_id", TURO_CALENDAR_ID),
        "clock_missing": clock_missing,
        "actions": actions,
    }


def load_calendar(
    *,
    env: Mapping[str, str] | None = None,
    http: HttpFn | None = None,
    token_path: Path | None = None,
) -> Any | None:
    environ = env if env is not None else os.environ
    creds = resolve_gcal_creds(env=environ, token_path=token_path)
    if creds is None:
        return None
    return HttpCalendar(http=http, creds=creds)
