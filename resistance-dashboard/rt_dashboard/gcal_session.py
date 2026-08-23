"""Google Calendar via the FitDash Google login session (stdlib only).

Timed meal reminders only. Same OAuth as Tasks — no second client, no Pi
file token on Vercel. Health-only connect does not request Calendar.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from api.auth.session_util import session_has_calendar_scope

from .gtasks_session import (
    current_session_google,
    ensure_access_token,
    session_is_bound,
)

CALENDAR_API = "https://www.googleapis.com/calendar/v3"
PREFERRED_CALENDAR_ID = "cvolkern@gmail.com"

MISSING_CALENDAR_SCOPE = (
    "Google sign-in is missing Calendar permission. "
    "Sign in again to allow meal reminders."
)
CALENDAR_API_NOT_ENABLED = (
    "Google Calendar API is not enabled for this Google Cloud project."
)
TOKEN_REJECTED = "Google Calendar rejected the access token. Sign in again."


def classify_calendar_http_error(status: int, body: str = "") -> str:
    text = (body or "").lower()
    if (
        "access_not_configured" in text
        or "accessnotconfigured" in text
        or ("has not been used" in text and "calendar" in text)
        or ("is disabled" in text and "calendar" in text)
        or ("calendar-json.googleapis.com" in text and "enable" in text)
        or ("googleapis.com/calendar" in text and "enable" in text)
    ):
        return CALENDAR_API_NOT_ENABLED
    if (
        "insufficient authentication scopes" in text
        or "insufficientpermissions" in text
        or "access_token_scope_insufficient" in text
        or "insufficient_scope" in text
        or "request had insufficient" in text
    ):
        return MISSING_CALENDAR_SCOPE
    if status == 401:
        return TOKEN_REJECTED
    if status == 403:
        return "Google Calendar denied access (HTTP 403)."
    return f"Google Calendar error HTTP {status}"


def credentials_status(google: Optional[dict] = None) -> dict[str, Any]:
    """Honest Calendar grant check. Missing scope is not a fake success."""
    blob = google if google is not None else current_session_google()
    if not session_is_bound() and google is None:
        return {
            "ok": False,
            "skipped": True,
            "source": None,
            "error": MISSING_CALENDAR_SCOPE,
            "error_code": "no_session",
            "token_present": False,
        }
    if blob is None:
        return {
            "ok": False,
            "skipped": True,
            "source": "session",
            "error": MISSING_CALENDAR_SCOPE,
            "error_code": "missing_calendar_scope",
            "token_present": False,
        }
    has_refresh = bool((blob.get("refresh_token") or "").strip())
    has_access = bool((blob.get("access_token") or "").strip())
    if not session_has_calendar_scope(blob):
        return {
            "ok": False,
            "skipped": True,
            "source": "session",
            "error": MISSING_CALENDAR_SCOPE,
            "error_code": "missing_calendar_scope",
            "token_present": has_refresh or has_access,
        }
    if not (has_refresh or has_access):
        return {
            "ok": False,
            "skipped": True,
            "source": "session",
            "error": MISSING_CALENDAR_SCOPE,
            "error_code": "missing_calendar_scope",
            "token_present": False,
        }
    return {
        "ok": True,
        "skipped": False,
        "source": "session",
        "error": None,
        "error_code": None,
        "token_present": True,
    }


def _require_session() -> dict:
    status = credentials_status()
    if not status.get("ok"):
        raise RuntimeError(status.get("error") or MISSING_CALENDAR_SCOPE)
    google = current_session_google()
    if not google:
        raise RuntimeError(MISSING_CALENDAR_SCOPE)
    return google


def _request(
    google: dict,
    method: str,
    url: str,
    *,
    body: Optional[dict] = None,
    query: Optional[dict] = None,
    _retried: bool = False,
) -> dict[str, Any]:
    token = ensure_access_token(google)
    request_url = url
    if query:
        request_url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(
            {k: v for k, v in query.items() if v is not None}, doseq=True
        )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(request_url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code == 404 and method == "DELETE":
            return {}
        if (
            exc.code == 401
            and not _retried
            and (google.get("refresh_token") or "").strip()
        ):
            google["access_token"] = ""
            google["access_expires_at"] = 0
            return _request(
                google,
                method,
                url,
                body=body,
                query=query,
                _retried=True,
            )
        raise RuntimeError(classify_calendar_http_error(exc.code, detail)) from exc


def resolve_calendar_id() -> str:
    """Prefer Personal (cvolkern@gmail.com), else the signed-in primary."""
    google = _require_session()
    try:
        resp = _request(
            google,
            "GET",
            f"{CALENDAR_API}/users/me/calendarList",
            query={"maxResults": 100, "minAccessRole": "writer"},
        )
    except RuntimeError:
        return PREFERRED_CALENDAR_ID
    items = [c for c in (resp.get("items") or []) if isinstance(c, dict)]
    preferred = PREFERRED_CALENDAR_ID.lower()
    for cal in items:
        cid = str(cal.get("id") or "").strip()
        if cid.lower() == preferred:
            return cid
    for cal in items:
        if cal.get("primary"):
            cid = str(cal.get("id") or "").strip()
            if cid:
                return cid
    return PREFERRED_CALENDAR_ID


def list_events(
    calendar_id: str,
    *,
    private_props: Optional[dict[str, str]] = None,
    page_size: int = 50,
) -> list[dict[str, Any]]:
    if not calendar_id:
        return []
    google = _require_session()
    items: list[dict[str, Any]] = []
    page_token = None
    cid = urllib.parse.quote(calendar_id, safe="@.")
    while True:
        query: dict[str, Any] = {
            "maxResults": page_size,
            "singleEvents": "true",
            "showDeleted": "false",
        }
        if page_token:
            query["pageToken"] = page_token
        if private_props:
            query["privateExtendedProperty"] = [
                f"{k}={v}" for k, v in private_props.items() if k and v is not None
            ]
        resp = _request(
            google,
            "GET",
            f"{CALENDAR_API}/calendars/{cid}/events",
            query=query,
        )
        for raw in resp.get("items") or []:
            if isinstance(raw, dict) and raw.get("id"):
                items.append(raw)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items


def create_event(calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
    google = _require_session()
    cid = urllib.parse.quote(calendar_id, safe="@.")
    raw = _request(
        google,
        "POST",
        f"{CALENDAR_API}/calendars/{cid}/events",
        body=body,
    )
    return raw


def update_event(
    calendar_id: str, event_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    google = _require_session()
    cid = urllib.parse.quote(calendar_id, safe="@.")
    eid = urllib.parse.quote(event_id, safe="")
    return _request(
        google,
        "PUT",
        f"{CALENDAR_API}/calendars/{cid}/events/{eid}",
        body=body,
    )


def delete_event(calendar_id: str, event_id: str) -> dict[str, Any]:
    if not calendar_id or not event_id:
        return {"ok": False, "error": "missing calendar_id or event_id"}
    google = _require_session()
    cid = urllib.parse.quote(calendar_id, safe="@.")
    eid = urllib.parse.quote(event_id, safe="")
    _request(
        google,
        "DELETE",
        f"{CALENDAR_API}/calendars/{cid}/events/{eid}",
    )
    return {"ok": True, "deleted": True, "event_id": event_id}
