"""Google Tasks via the FitDash Google login session (stdlib only).

Vercel never reads the Pi file token and never needs GOOGLE_TASKS_*.
Refresh uses the existing FitDash GOOGLE_CLIENT_ID/SECRET.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from api.auth.session_util import session_has_tasks_scope

TOKEN_URL = "https://oauth2.googleapis.com/token"
TASKS_API = "https://tasks.googleapis.com/tasks/v1"

MISSING_TASKS_SCOPE = (
    "Google sign-in is missing Tasks permission. "
    "Sign in again to allow Google Tasks."
)
TASKS_API_NOT_ENABLED = (
    "Google Tasks API is not enabled for this Google Cloud project."
)
TOKEN_REFRESH_FAILED = "Google token refresh failed."
TOKEN_REJECTED = "Google Tasks rejected the access token. Sign in again."
LOGIN_CLIENT_MISSING = "Google login client is not configured."
FITNESS_LIST_NOT_FOUND = "Task list 'Fitness' not found"


def classify_google_http_error(
    status: int, body: str = "", *, during: str = "tasks"
) -> str:
    """Map Google HTTP errors to distinct user-facing strings.

    Do not rewrite every 401/403 as a missing-Tasks-scope message.
    """
    text = (body or "").lower()
    if (
        "access_not_configured" in text
        or "accessnotconfigured" in text
        or ("has not been used" in text and "task" in text)
        or ("is disabled" in text and "task" in text)
        or "enable it by visiting" in text
        or ("tasks.googleapis.com" in text and "enable" in text)
    ):
        return TASKS_API_NOT_ENABLED
    if (
        "insufficient authentication scopes" in text
        or "insufficientpermissions" in text
        or "access_token_scope_insufficient" in text
        or "insufficient_scope" in text
        or "request had insufficient" in text
    ):
        return MISSING_TASKS_SCOPE
    if during == "refresh":
        if "invalid_grant" in text or status in (400, 401, 403):
            return TOKEN_REFRESH_FAILED
        return f"Google token refresh failed HTTP {status}"
    if status == 401:
        return TOKEN_REJECTED
    if status == 403:
        return "Google Tasks denied access (HTTP 403)."
    return f"Google Tasks error HTTP {status}"

_session_google: ContextVar[Optional[dict]] = ContextVar(
    "fitdash_gtasks_session", default=None
)
_bound: ContextVar[bool] = ContextVar("fitdash_gtasks_session_bound", default=False)


def running_on_vercel() -> bool:
    return bool(
        (os.environ.get("VERCEL") or "").strip()
        or (os.environ.get("VERCEL_ENV") or "").strip()
    )


@contextmanager
def bound_session_google(google: Optional[dict]) -> Iterator[None]:
    tok = _session_google.set(dict(google or {}))
    bound = _bound.set(True)
    try:
        yield
    finally:
        _session_google.reset(tok)
        _bound.reset(bound)


def session_is_bound() -> bool:
    return bool(_bound.get())


def current_session_google() -> Optional[dict]:
    if not session_is_bound():
        return None
    return _session_google.get()


def credentials_status(google: Optional[dict] = None) -> dict[str, Any]:
    blob = google if google is not None else current_session_google()
    if blob is None:
        return {
            "ok": False,
            "source": None,
            "error": MISSING_TASKS_SCOPE,
            "error_code": "missing_tasks_scope",
            "token_present": False,
            "refresh_token_present": False,
        }
    has_refresh = bool((blob.get("refresh_token") or "").strip())
    has_access = bool((blob.get("access_token") or "").strip())
    raw_scope = str(blob.get("scope") or "").strip()
    # Only treat missing Tasks permission when the stored scope is present
    # and does not include Tasks. Tokens with an omitted ``sc`` still try
    # the API — Google sometimes leaves scope off the token JSON.
    if raw_scope and not session_has_tasks_scope(blob):
        return {
            "ok": False,
            "source": "session",
            "error": MISSING_TASKS_SCOPE,
            "error_code": "missing_tasks_scope",
            "token_present": has_refresh or has_access,
            "refresh_token_present": has_refresh,
        }
    if not (has_refresh or has_access):
        return {
            "ok": False,
            "source": "session",
            "error": MISSING_TASKS_SCOPE,
            "error_code": "missing_tasks_scope",
            "token_present": False,
            "refresh_token_present": False,
        }
    return {
        "ok": True,
        "source": "session",
        "error": None,
        "error_code": None,
        "token_present": True,
        "refresh_token_present": has_refresh,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_due(due: str) -> str:
    due = (due or "").strip()
    if not due:
        return due
    if len(due) == 10 and due[4] == "-" and due[7] == "-":
        return f"{due}T00:00:00.000Z"
    return due


def _normalize_task(raw: dict[str, Any], list_id: str, list_title: str = "") -> dict[str, Any]:
    return {
        "id": raw.get("id"),
        "list_id": list_id,
        "list_title": list_title,
        "title": raw.get("title") or "",
        "notes": raw.get("notes") or "",
        "status": raw.get("status") or "needsAction",
        "due": raw.get("due"),
        "updated": raw.get("updated"),
        "completed": raw.get("completed"),
        "parent": raw.get("parent"),
        "position": raw.get("position"),
        "links": raw.get("links") or [],
        "self_link": raw.get("selfLink"),
        "github": [],
        "deleted": bool(raw.get("deleted")),
        "hidden": bool(raw.get("hidden")),
    }


def _login_client() -> tuple[str, str]:
    return (
        (os.environ.get("GOOGLE_CLIENT_ID") or "").strip(),
        (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip(),
    )


def ensure_access_token(google: dict) -> str:
    access = (google.get("access_token") or "").strip()
    try:
        expires_at = int(google.get("access_expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    if access and (not expires_at or time.time() < expires_at - 60):
        return access
    refresh = (google.get("refresh_token") or "").strip()
    client_id, client_secret = _login_client()
    if not refresh:
        if access:
            raise RuntimeError(TOKEN_REFRESH_FAILED)
        raise RuntimeError(MISSING_TASKS_SCOPE)
    if not client_id or not client_secret:
        raise RuntimeError(LOGIN_CLIENT_MISSING)
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            classify_google_http_error(exc.code, detail, during="refresh")
        ) from exc
    token = str(data.get("access_token") or "").strip()
    if not token:
        raise RuntimeError(TOKEN_REFRESH_FAILED)
    google["access_token"] = token
    google["access_expires_at"] = time.time() + int(data.get("expires_in") or 3600)
    if data.get("scope"):
        google["scope"] = data.get("scope")
    return token


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
            {k: v for k, v in query.items() if v is not None}
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
        if (
            exc.code == 401
            and not _retried
            and (google.get("refresh_token") or "").strip()
        ):
            google["access_token"] = ""
            google["access_expires_at"] = 0
            try:
                return _request(
                    google,
                    method,
                    url,
                    body=body,
                    query=query,
                    _retried=True,
                )
            except RuntimeError:
                raise
            except Exception:
                raise RuntimeError(
                    classify_google_http_error(exc.code, detail, during="tasks")
                ) from exc
        raise RuntimeError(
            classify_google_http_error(exc.code, detail, during="tasks")
        ) from exc


def _require_session() -> dict:
    status = credentials_status()
    if not status.get("ok"):
        raise RuntimeError(status.get("error") or MISSING_TASKS_SCOPE)
    google = current_session_google()
    if not google:
        raise RuntimeError(MISSING_TASKS_SCOPE)
    return google


def list_tasklists() -> dict[str, Any]:
    google = _require_session()
    items: list[dict[str, Any]] = []
    page_token = None
    while True:
        query: dict[str, Any] = {"maxResults": 100}
        if page_token:
            query["pageToken"] = page_token
        resp = _request(
            google, "GET", f"{TASKS_API}/users/@me/lists", query=query
        )
        for tl in resp.get("items") or []:
            items.append(
                {
                    "id": tl.get("id"),
                    "title": tl.get("title") or "",
                    "updated": tl.get("updated"),
                    "self_link": tl.get("selfLink"),
                }
            )
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    items.sort(key=lambda x: (x.get("title") or "").lower())
    return {"ok": True, "lists": items, "count": len(items), "fetched_at": _now_iso()}


def list_tasks(
    list_id: str, *, show_completed: bool = True, show_hidden: bool = True
) -> dict[str, Any]:
    if not list_id:
        return {"ok": False, "error": "missing list_id"}
    google = _require_session()
    items: list[dict[str, Any]] = []
    page_token = None
    while True:
        query: dict[str, Any] = {
            "maxResults": 100,
            "showCompleted": "true" if show_completed else "false",
            "showHidden": "true" if (show_hidden or show_completed) else "false",
        }
        if page_token:
            query["pageToken"] = page_token
        resp = _request(
            google,
            "GET",
            f"{TASKS_API}/lists/{urllib.parse.quote(list_id, safe='')}/tasks",
            query=query,
        )
        for raw in resp.get("items") or []:
            items.append(_normalize_task(raw, list_id))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    items.sort(key=lambda t: (t.get("position") or "", (t.get("title") or "").lower()))
    return {
        "ok": True,
        "list_id": list_id,
        "tasks": items,
        "count": len(items),
        "fetched_at": _now_iso(),
    }


def get_task(list_id: str, task_id: str) -> dict[str, Any]:
    if not list_id or not task_id:
        return {"ok": False, "error": "missing list_id or task_id"}
    google = _require_session()
    raw = _request(
        google,
        "GET",
        f"{TASKS_API}/lists/{urllib.parse.quote(list_id, safe='')}/tasks/"
        f"{urllib.parse.quote(task_id, safe='')}",
    )
    return {"ok": True, "task": _normalize_task(raw, list_id)}


def create_task(
    list_id: str,
    title: str,
    *,
    notes: str = "",
    due: Optional[str] = None,
    parent: Optional[str] = None,
) -> dict[str, Any]:
    title = (title or "").strip()
    if not list_id:
        return {"ok": False, "error": "missing list_id"}
    if not title:
        return {"ok": False, "error": "title required"}
    google = _require_session()
    body: dict[str, Any] = {"title": title}
    if notes:
        body["notes"] = notes
    if due:
        body["due"] = _normalize_due(due)
    query = {"parent": parent} if parent else None
    raw = _request(
        google,
        "POST",
        f"{TASKS_API}/lists/{urllib.parse.quote(list_id, safe='')}/tasks",
        body=body,
        query=query,
    )
    return {"ok": True, "task": _normalize_task(raw, list_id)}


def update_task(
    list_id: str,
    task_id: str,
    *,
    title: Optional[str] = None,
    notes: Optional[str] = None,
    due: Optional[str] = None,
    status: Optional[str] = None,
    clear_due: bool = False,
) -> dict[str, Any]:
    if not list_id or not task_id:
        return {"ok": False, "error": "missing list_id or task_id"}
    google = _require_session()
    lid = urllib.parse.quote(list_id, safe="")
    tid = urllib.parse.quote(task_id, safe="")
    raw = _request(google, "GET", f"{TASKS_API}/lists/{lid}/tasks/{tid}")
    if title is not None:
        raw["title"] = title.strip()
    if notes is not None:
        raw["notes"] = notes
    if clear_due:
        raw.pop("due", None)
    elif due is not None:
        if due == "" or due is False:
            raw.pop("due", None)
        else:
            raw["due"] = _normalize_due(str(due))
    if status is not None:
        if status not in ("needsAction", "completed"):
            return {"ok": False, "error": "status must be needsAction or completed"}
        raw["status"] = status
        if status == "completed":
            raw["completed"] = _now_iso()
        else:
            raw.pop("completed", None)
    updated = _request(
        google, "PUT", f"{TASKS_API}/lists/{lid}/tasks/{tid}", body=raw
    )
    return {"ok": True, "task": _normalize_task(updated, list_id)}


def complete_task(
    list_id: str, task_id: str, *, completed: bool = True
) -> dict[str, Any]:
    return update_task(
        list_id,
        task_id,
        status="completed" if completed else "needsAction",
    )


def delete_task(list_id: str, task_id: str) -> dict[str, Any]:
    if not list_id or not task_id:
        return {"ok": False, "error": "missing list_id or task_id"}
    google = _require_session()
    lid = urllib.parse.quote(list_id, safe="")
    tid = urllib.parse.quote(task_id, safe="")
    _request(google, "DELETE", f"{TASKS_API}/lists/{lid}/tasks/{tid}")
    return {"ok": True, "deleted": True, "task_id": task_id, "list_id": list_id}
