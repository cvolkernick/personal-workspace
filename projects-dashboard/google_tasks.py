#!/usr/bin/env python3
"""Google Tasks API client for Workflow Management dashboard.

Uses the same OAuth files as google-tasks-mcp:
  ~/.config/google-tasks-mcp/client_secret.json
  ~/.config/google-tasks-mcp/token.json

Never logs or returns secrets. Safe to call from the dashboard server on Mac/Pi.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCOPES = ["https://www.googleapis.com/auth/tasks"]
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "google-tasks-mcp"
# Parse gh / github.com issue refs from notes/title for UI chips
_GH_ISSUE_RE = re.compile(
    r"(?:https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/(\d+))"
    r"|(?:gh:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#(\d+))"
    r"|(?:\b([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#(\d+)\b)",
    re.I,
)


def _config_dir() -> Path:
    override = os.environ.get("GOOGLE_TASKS_CONFIG_DIR")
    return Path(override).expanduser() if override else DEFAULT_CONFIG_DIR


def _token_blob_from_env() -> dict[str, Any]:
    """Vercel/serverless: use env secrets. Never invent tokens. No file copy."""
    raw = (os.environ.get("GOOGLE_TASKS_TOKEN_JSON") or "").strip()
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and (data.get("refresh_token") or data.get("token")):
            return data
    refresh = (os.environ.get("GOOGLE_TASKS_REFRESH_TOKEN") or "").strip()
    if not refresh:
        return {}
    client_id = (
        os.environ.get("GOOGLE_TASKS_CLIENT_ID")
        or os.environ.get("GOOGLE_CLIENT_ID")
        or ""
    ).strip()
    client_secret = (
        os.environ.get("GOOGLE_TASKS_CLIENT_SECRET")
        or os.environ.get("GOOGLE_CLIENT_SECRET")
        or ""
    ).strip()
    if not client_id:
        return {}
    return {
        "refresh_token": refresh,
        "client_id": client_id,
        "client_secret": client_secret,
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def _load_token_blob() -> dict[str, Any]:
    path = _config_dir() / "token.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    env_blob = _token_blob_from_env()
    if env_blob:
        return env_blob
    raise FileNotFoundError(
        f"Missing {path} — run: npx google-tasks-mcp auth "
        "(or set GOOGLE_TASKS_REFRESH_TOKEN + client id/secret)"
    )


def _load_client_blob() -> dict[str, Any]:
    """Optional client_secret.json (Desktop OAuth). token.json often already has ids."""
    path = _config_dir() / "client_secret.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        # Google desktop download wraps under "installed" or "web"
        return data.get("installed") or data.get("web") or data
    env_blob = _token_blob_from_env()
    if env_blob.get("client_id"):
        return {
            "client_id": env_blob.get("client_id"),
            "client_secret": env_blob.get("client_secret"),
            "token_uri": env_blob.get("token_uri") or "https://oauth2.googleapis.com/token",
        }
    return {}


def credentials_status() -> dict[str, Any]:
    """Non-secret readiness check for health / UI."""
    cfg = _config_dir()
    token_path = cfg / "token.json"
    secret_path = cfg / "client_secret.json"
    env_blob = _token_blob_from_env()
    has_token = token_path.is_file() or bool(env_blob)
    has_secret = secret_path.is_file() or bool(env_blob.get("client_secret"))
    has_refresh = False
    if token_path.is_file():
        try:
            blob = json.loads(token_path.read_text(encoding="utf-8"))
            has_refresh = bool(blob.get("refresh_token"))
        except Exception:
            has_refresh = False
    elif env_blob:
        has_refresh = bool(env_blob.get("refresh_token"))
    ok = has_token and has_refresh
    return {
        "ok": ok,
        "config_dir": str(cfg),
        "source": "file" if token_path.is_file() else ("env" if env_blob else None),
        "token_present": has_token,
        "client_secret_present": has_secret,
        "refresh_token_present": has_refresh,
        "hint": None
        if ok
        else "Place client_secret.json + run npx google-tasks-mcp auth "
        "(or set GOOGLE_TASKS_REFRESH_TOKEN)",
    }


def _build_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token = _load_token_blob()
    client = _load_client_blob()
    client_id = token.get("client_id") or client.get("client_id")
    client_secret = token.get("client_secret") or client.get("client_secret")
    if not client_id or not token.get("refresh_token"):
        raise RuntimeError(
            "token.json missing client_id or refresh_token — re-run auth"
        )
    creds = Credentials(
        token=token.get("token") or token.get("access_token"),
        refresh_token=token.get("refresh_token"),
        token_uri=token.get("token_uri")
        or client.get("token_uri")
        or "https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    if not creds.valid:
        if creds.expired or not creds.token:
            creds.refresh(Request())
            # Persist refreshed access token when a local token.json exists (not env/Vercel).
            token_path = _config_dir() / "token.json"
            if token_path.is_file():
                try:
                    blob = json.loads(token_path.read_text(encoding="utf-8"))
                    if creds.token:
                        blob["token"] = creds.token
                    if getattr(creds, "expiry", None):
                        blob["expiry"] = creds.expiry.isoformat()
                    token_path.write_text(
                        json.dumps(blob, indent=2) + "\n", encoding="utf-8"
                    )
                    try:
                        os.chmod(token_path, 0o600)
                    except OSError:
                        pass
                except Exception:
                    pass
    return creds


def _service():
    from googleapiclient.discovery import build

    return build("tasks", "v1", credentials=_build_credentials(), cache_discovery=False)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def extract_github_links(text: str | None) -> list[dict[str, str]]:
    """Pull GitHub issue references out of free text for UI chips / bridge."""
    if not text:
        return []
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in _GH_ISSUE_RE.finditer(text):
        g = m.groups()
        if g[0]:
            owner, repo, num = g[0], g[1], g[2]
        elif g[3]:
            owner, repo, num = g[3], g[4], g[5]
        else:
            owner, repo, num = g[6], g[7], g[8]
        key = f"{owner}/{repo}#{num}".lower()
        if key in seen:
            continue
        seen.add(key)
        url = f"https://github.com/{owner}/{repo}/issues/{num}"
        found.append(
            {
                "owner": owner,
                "repo": repo,
                "number": num,
                "url": url,
                "label": f"{owner}/{repo}#{num}",
            }
        )
    return found


def _normalize_task(raw: dict[str, Any], list_id: str, list_title: str = "") -> dict[str, Any]:
    title = raw.get("title") or ""
    notes = raw.get("notes") or ""
    links = extract_github_links(f"{title}\n{notes}")
    return {
        "id": raw.get("id"),
        "list_id": list_id,
        "list_title": list_title,
        "title": title,
        "notes": notes,
        "status": raw.get("status") or "needsAction",
        "due": raw.get("due"),
        "updated": raw.get("updated"),
        "completed": raw.get("completed"),
        "parent": raw.get("parent"),
        "position": raw.get("position"),
        "links": raw.get("links") or [],
        "self_link": raw.get("selfLink"),
        "github": links,
        "deleted": bool(raw.get("deleted")),
        "hidden": bool(raw.get("hidden")),
    }


def list_tasklists() -> dict[str, Any]:
    svc = _service()
    items: list[dict[str, Any]] = []
    page_token = None
    while True:
        kwargs: dict[str, Any] = {"maxResults": 100}
        if page_token:
            kwargs["pageToken"] = page_token
        resp = svc.tasklists().list(**kwargs).execute()
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
    list_id: str,
    *,
    show_completed: bool = False,
    show_hidden: bool = False,
    show_deleted: bool = False,
    max_results: int = 100,
    list_title: str = "",
) -> dict[str, Any]:
    if not list_id:
        return {"ok": False, "error": "missing list_id"}
    svc = _service()
    items: list[dict[str, Any]] = []
    page_token = None
    while True:
        kwargs: dict[str, Any] = {
            "tasklist": list_id,
            "maxResults": min(max(1, max_results), 100),
            "showCompleted": show_completed,
            "showHidden": show_hidden,
            "showDeleted": show_deleted,
        }
        if show_completed:
            # API returns completed only when showCompleted=true; still need
            # showHidden for some completed items that are auto-hidden.
            kwargs["showHidden"] = True
        if page_token:
            kwargs["pageToken"] = page_token
        resp = svc.tasks().list(**kwargs).execute()
        for t in resp.get("items") or []:
            items.append(_normalize_task(t, list_id, list_title))
        page_token = resp.get("nextPageToken")
        if not page_token or len(items) >= max_results:
            break
    # Stable order: position then title
    items.sort(key=lambda t: (t.get("position") or "", (t.get("title") or "").lower()))
    open_n = sum(1 for t in items if t.get("status") == "needsAction")
    done_n = sum(1 for t in items if t.get("status") == "completed")
    return {
        "ok": True,
        "list_id": list_id,
        "list_title": list_title,
        "tasks": items,
        "count": len(items),
        "open_count": open_n,
        "completed_count": done_n,
        "fetched_at": _now_iso(),
    }


def overview(
    *,
    show_completed: bool = False,
    max_per_list: int = 100,
) -> dict[str, Any]:
    """All lists + tasks — ideal for the dashboard Tasks tab."""
    status = credentials_status()
    if not status.get("ok"):
        return {
            "ok": False,
            "error": "Google Tasks not authenticated",
            "auth": status,
            "lists": [],
            "tasks_by_list": {},
            "all_open": [],
        }
    try:
        lists_payload = list_tasklists()
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "auth": status,
            "lists": [],
            "tasks_by_list": {},
            "all_open": [],
        }

    lists = lists_payload.get("lists") or []
    by_list: dict[str, Any] = {}
    all_open: list[dict[str, Any]] = []
    total_open = 0
    total_done = 0
    errors: list[dict[str, str]] = []

    for tl in lists:
        lid = tl["id"]
        try:
            tp = list_tasks(
                lid,
                show_completed=show_completed,
                list_title=tl.get("title") or "",
                max_results=max_per_list,
            )
            by_list[lid] = tp
            total_open += tp.get("open_count") or 0
            total_done += tp.get("completed_count") or 0
            for t in tp.get("tasks") or []:
                if t.get("status") == "needsAction":
                    all_open.append(t)
        except Exception as e:
            errors.append({"list_id": lid, "error": str(e)})
            by_list[lid] = {"ok": False, "error": str(e), "tasks": []}

    # Due-soon first among open (missing due → end)
    def due_key(t: dict[str, Any]) -> str:
        return t.get("due") or "9999"

    all_open.sort(key=lambda t: (due_key(t), (t.get("title") or "").lower()))

    return {
        "ok": True,
        "auth": status,
        "lists": lists,
        "tasks_by_list": by_list,
        "all_open": all_open,
        "summary": {
            "lists": len(lists),
            "open": total_open,
            "completed_shown": total_done if show_completed else None,
        },
        "errors": errors,
        "fetched_at": _now_iso(),
    }


def get_task(list_id: str, task_id: str) -> dict[str, Any]:
    if not list_id or not task_id:
        return {"ok": False, "error": "missing list_id or task_id"}
    svc = _service()
    raw = svc.tasks().get(tasklist=list_id, task=task_id).execute()
    return {"ok": True, "task": _normalize_task(raw, list_id)}


def create_task(
    list_id: str,
    title: str,
    *,
    notes: str = "",
    due: str | None = None,
    parent: str | None = None,
    previous: str | None = None,
) -> dict[str, Any]:
    title = (title or "").strip()
    if not list_id:
        return {"ok": False, "error": "missing list_id"}
    if not title:
        return {"ok": False, "error": "title required"}
    body: dict[str, Any] = {"title": title}
    if notes:
        body["notes"] = notes
    if due:
        # Accept YYYY-MM-DD or full RFC3339; API wants RFC3339 date
        body["due"] = _normalize_due(due)
    svc = _service()
    kwargs: dict[str, Any] = {"tasklist": list_id, "body": body}
    if parent:
        kwargs["parent"] = parent
    if previous:
        kwargs["previous"] = previous
    raw = svc.tasks().insert(**kwargs).execute()
    return {"ok": True, "task": _normalize_task(raw, list_id)}


def update_task(
    list_id: str,
    task_id: str,
    *,
    title: str | None = None,
    notes: str | None = None,
    due: str | None = None,
    status: str | None = None,
    clear_due: bool = False,
) -> dict[str, Any]:
    if not list_id or not task_id:
        return {"ok": False, "error": "missing list_id or task_id"}
    svc = _service()
    # Patch: get then update (API requires full resource for update)
    raw = svc.tasks().get(tasklist=list_id, task=task_id).execute()
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
        if status == "needsAction":
            raw.pop("completed", None)
    updated = (
        svc.tasks()
        .update(tasklist=list_id, task=task_id, body=raw)
        .execute()
    )
    return {"ok": True, "task": _normalize_task(updated, list_id)}


def complete_task(list_id: str, task_id: str, *, completed: bool = True) -> dict[str, Any]:
    return update_task(
        list_id,
        task_id,
        status="completed" if completed else "needsAction",
    )


def delete_task(list_id: str, task_id: str) -> dict[str, Any]:
    if not list_id or not task_id:
        return {"ok": False, "error": "missing list_id or task_id"}
    svc = _service()
    svc.tasks().delete(tasklist=list_id, task=task_id).execute()
    return {"ok": True, "deleted": True, "task_id": task_id, "list_id": list_id}


def move_task(
    list_id: str,
    task_id: str,
    *,
    parent: str | None = None,
    previous: str | None = None,
    destination_list: str | None = None,
) -> dict[str, Any]:
    if not list_id or not task_id:
        return {"ok": False, "error": "missing list_id or task_id"}
    svc = _service()
    kwargs: dict[str, Any] = {"tasklist": list_id, "task": task_id}
    if parent is not None:
        kwargs["parent"] = parent or None
    if previous is not None:
        kwargs["previous"] = previous or None
    if destination_list:
        kwargs["destinationTasklist"] = destination_list
    raw = svc.tasks().move(**kwargs).execute()
    dest = destination_list or list_id
    return {"ok": True, "task": _normalize_task(raw, dest)}


def create_tasklist(title: str) -> dict[str, Any]:
    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "title required"}
    svc = _service()
    raw = svc.tasklists().insert(body={"title": title}).execute()
    return {
        "ok": True,
        "list": {
            "id": raw.get("id"),
            "title": raw.get("title") or title,
            "updated": raw.get("updated"),
        },
    }


def update_tasklist(list_id: str, title: str) -> dict[str, Any]:
    title = (title or "").strip()
    if not list_id:
        return {"ok": False, "error": "missing list_id"}
    if not title:
        return {"ok": False, "error": "title required"}
    svc = _service()
    raw = svc.tasklists().update(
        tasklist=list_id, body={"id": list_id, "title": title}
    ).execute()
    return {
        "ok": True,
        "list": {
            "id": raw.get("id"),
            "title": raw.get("title") or title,
            "updated": raw.get("updated"),
        },
    }


def delete_tasklist(list_id: str) -> dict[str, Any]:
    if not list_id:
        return {"ok": False, "error": "missing list_id"}
    svc = _service()
    svc.tasklists().delete(tasklist=list_id).execute()
    return {"ok": True, "deleted": True, "list_id": list_id}


def create_task_from_github_issue(
    list_id: str,
    *,
    owner: str,
    repo: str,
    number: str | int,
    title: str,
    html_url: str = "",
    body: str = "",
) -> dict[str, Any]:
    """Mirror a GitHub/Buzz board issue into a Google Task with a durable link in notes."""
    num = str(number)
    url = html_url or f"https://github.com/{owner}/{repo}/issues/{num}"
    notes_parts = [
        f"gh:{owner}/{repo}#{num}",
        url,
    ]
    if body:
        notes_parts.append("")
        notes_parts.append(body[:1500])
    notes = "\n".join(notes_parts)
    task_title = title.strip() or f"{repo}#{num}"
    # Prefix so it's scannable in the list
    if not task_title.startswith(f"{repo}#"):
        task_title = f"{repo}#{num}: {task_title}"
    return create_task(list_id, task_title, notes=notes)


def _normalize_due(due: str) -> str:
    due = due.strip()
    if not due:
        return due
    # Date-only → RFC3339 midnight UTC (Google Tasks convention)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
        return f"{due}T00:00:00.000Z"
    return due


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(description="Google Tasks CLI (dashboard helper)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("lists")
    o = sub.add_parser("overview")
    o.add_argument("--completed", action="store_true")
    t = sub.add_parser("tasks")
    t.add_argument("list_id")
    t.add_argument("--completed", action="store_true")
    args = p.parse_args(argv)
    try:
        if args.cmd == "status":
            print(json.dumps(credentials_status(), indent=2))
        elif args.cmd == "lists":
            print(json.dumps(list_tasklists(), indent=2))
        elif args.cmd == "overview":
            print(json.dumps(overview(show_completed=args.completed), indent=2))
        elif args.cmd == "tasks":
            print(
                json.dumps(
                    list_tasks(args.list_id, show_completed=args.completed),
                    indent=2,
                )
            )
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
