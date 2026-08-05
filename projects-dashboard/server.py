#!/usr/bin/env python3
"""Local server for personal-workspace workflow-management dashboard.

  GET  /api/projects       — readiness + branches + resume kit + areas
  GET  /api/branch-graph   — gitk-style commit lane graph (SVG data)
  GET  /api/tasks          — Google Tasks overview (lists + open tasks)
  GET  /api/tasks/lists    — task lists only
  GET  /api/tasks/list     — tasks in one list (?list_id=)
  POST /api/tasks          — create / update / complete / delete / move
  POST /api/protect        — commit durable dirty work + push (auto branch)
  POST /api/sync           — session index + protect + push
  POST /api/session-index  — write ops/session-index only
  POST /api/start-work     — body {"area":"treasury"} → work/treasury
  GET  /api/health

Usage:
  python3 projects-dashboard/server.py
  python3 projects-dashboard/server.py --port 8765 --no-browser
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backlog import (  # noqa: E402
    add_item,
    backlog_payload,
    delete_item,
    import_initiatives,
    initiate_item,
    update_item,
)
from backlog_groom import groom_backlog  # noqa: E402
from branch_graph import collect_branch_graph  # noqa: E402
from bridge import (  # noqa: E402
    list_bridge_status,
    send_backlog_to_allocator,
    send_top_to_allocator,
)
from git_workflow import protect_work, start_work, sync_after_work  # noqa: E402
from google_tasks import (  # noqa: E402
    complete_task,
    create_task,
    create_task_from_github_issue,
    create_tasklist,
    credentials_status as gtasks_credentials_status,
    delete_task,
    delete_tasklist,
    get_task,
    list_tasklists,
    list_tasks,
    move_task,
    overview as gtasks_overview,
    update_task,
    update_tasklist,
)
from recommendations import (  # noqa: E402
    approve_suggestion,
    recommendations_payload,
    reject_suggestion,
    generate_recommendations,
)
from session_backup import write_full_archive, write_session_index  # noqa: E402
from workspace import WORKSPACE_ROOT, collect_workspace_dashboard  # noqa: E402

DEFAULT_PORT = 8765


class ProjectsHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[projects] " + (fmt % args) + "\n")

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            auth = gtasks_credentials_status()
            self._json(
                200,
                {
                    "ok": True,
                    "service": "projects-dashboard",
                    "workspace": str(WORKSPACE_ROOT),
                    "google_tasks": {
                        "ok": bool(auth.get("ok")),
                        "refresh_token_present": bool(
                            auth.get("refresh_token_present")
                        ),
                    },
                },
            )
            return

        if path in ("/api/tasks", "/api/tasks/overview"):
            qs = parse_qs(parsed.query)
            show_completed = (qs.get("completed") or qs.get("show_completed") or ["0"])[
                0
            ] in ("1", "true", "yes")
            try:
                max_per = int((qs.get("max") or ["100"])[0])
            except ValueError:
                max_per = 100
            try:
                payload = gtasks_overview(
                    show_completed=show_completed, max_per_list=max_per
                )
                self._json(200 if payload.get("ok") else 503, payload)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/tasks/auth":
            try:
                self._json(200, gtasks_credentials_status())
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/tasks/lists":
            try:
                self._json(200, list_tasklists())
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path in ("/api/tasks/list", "/api/tasks/items"):
            qs = parse_qs(parsed.query)
            list_id = (qs.get("list_id") or qs.get("list") or [""])[0]
            show_completed = (qs.get("completed") or ["0"])[0] in (
                "1",
                "true",
                "yes",
            )
            try:
                payload = list_tasks(list_id, show_completed=show_completed)
                self._json(200 if payload.get("ok") else 400, payload)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/tasks/get":
            qs = parse_qs(parsed.query)
            list_id = (qs.get("list_id") or [""])[0]
            task_id = (qs.get("task_id") or qs.get("id") or [""])[0]
            try:
                payload = get_task(list_id, task_id)
                self._json(200 if payload.get("ok") else 400, payload)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/projects":
            qs = parse_qs(parsed.query)
            only_touched = (qs.get("only_touched") or ["0"])[0] in (
                "1",
                "true",
                "yes",
            )
            try:
                payload = collect_workspace_dashboard(only_touched=only_touched)
                payload["backlog"] = backlog_payload(
                    include_done=(qs.get("backlog_all") or ["0"])[0]
                    in ("1", "true", "yes")
                )
                refresh_rec = (qs.get("refresh_recommendations") or ["0"])[0] in (
                    "1",
                    "true",
                    "yes",
                )
                payload["recommendations"] = recommendations_payload(
                    refresh=refresh_rec
                )
                try:
                    payload["bridge"] = list_bridge_status()
                except Exception as be:
                    payload["bridge"] = {"ok": False, "error": str(be)}
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, payload)
            return

        if path == "/api/bridge":
            try:
                self._json(200, list_bridge_status())
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/branch-graph":
            qs = parse_qs(parsed.query)
            try:
                max_c = int((qs.get("max") or ["80"])[0])
            except ValueError:
                max_c = 80
            include_remotes = (qs.get("remotes") or ["1"])[0] not in (
                "0",
                "false",
                "no",
            )
            try:
                payload = collect_branch_graph(
                    WORKSPACE_ROOT,
                    max_commits=max_c,
                    include_remotes=include_remotes,
                )
                self._json(200 if payload.get("ok") else 500, payload)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/backlog":
            qs = parse_qs(parsed.query)
            include_done = (qs.get("all") or ["0"])[0] in ("1", "true", "yes")
            try:
                self._json(200, backlog_payload(include_done=include_done))
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/recommendations":
            qs = parse_qs(parsed.query)
            refresh = (qs.get("refresh") or ["0"])[0] in ("1", "true", "yes")
            try:
                self._json(200, recommendations_payload(refresh=refresh))
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path in ("/", "/index.html", "/projects", "/projects/"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._read_json()

        try:
            if path == "/api/protect":
                # Dashboard button is intentional full protect (not auto durable-only)
                result = protect_work(
                    message=body.get("message") or "protect: dashboard Protect & push",
                    push=body.get("push", True),
                    include_snapshots=body.get("include_snapshots", True),
                    ensure_work_branch=body.get("ensure_work_branch", True),
                    mode=body.get("mode") or "full",
                )
                self._json(200 if result.get("ok") else 500, result)
                return

            if path == "/api/sync":
                result = sync_after_work(
                    message=body.get("message") or "sync: dashboard Protect & push",
                    snapshot_sessions=body.get("snapshot_sessions", True),
                    mode=body.get("mode") or "full",
                )
                self._json(200 if result.get("ok") else 500, result)
                return

            if path == "/api/session-index":
                result = write_session_index(
                    commit=bool(body.get("commit")),
                    keep_history=body.get("keep_history", True),
                )
                self._json(200 if result.get("ok") else 500, result)
                return

            if path == "/api/session-archive":
                result = write_full_archive(
                    mode="full" if body.get("full") else "summaries"
                )
                self._json(200 if result.get("ok") else 500, result)
                return

            if path == "/api/start-work":
                area = body.get("area")
                if not area:
                    self._json(400, {"ok": False, "error": "missing area"})
                    return
                result = start_work(str(area))
                self._json(200 if result.get("ok") else 500, result)
                return

            if path == "/api/backlog":
                result = add_item(
                    str(body.get("title") or ""),
                    description=str(body.get("description") or ""),
                    priority=str(body.get("priority") or "medium"),
                    status=str(body.get("status") or "idea"),
                    tags=body.get("tags") if isinstance(body.get("tags"), list) else None,
                    mvp_scope=str(body.get("mvp_scope") or ""),
                    notes=str(body.get("notes") or ""),
                    area=str(body.get("area") or ""),
                )
                self._json(200 if result.get("ok") else 400, result)
                return

            if path == "/api/backlog/update":
                iid = body.get("id")
                if not iid:
                    self._json(400, {"ok": False, "error": "missing id"})
                    return
                result = update_item(str(iid), body)
                self._json(200 if result.get("ok") else 404, result)
                return

            if path == "/api/backlog/delete":
                iid = body.get("id")
                if not iid:
                    self._json(400, {"ok": False, "error": "missing id"})
                    return
                result = delete_item(str(iid))
                self._json(200 if result.get("ok") else 404, result)
                return

            if path == "/api/backlog/initiate":
                iid = body.get("id")
                if not iid:
                    self._json(400, {"ok": False, "error": "missing id"})
                    return
                result = initiate_item(
                    str(iid), try_spawn_grok=bool(body.get("spawn", True))
                )
                self._json(200 if result.get("ok") else 404, result)
                return

            if path == "/api/backlog/import":
                result = import_initiatives()
                self._json(200 if result.get("ok") else 500, result)
                return

            if path == "/api/backlog/groom":
                apply = body.get("apply", True)
                if isinstance(apply, str):
                    apply = apply.lower() not in ("0", "false", "no")
                result = groom_backlog(apply=bool(apply))
                self._json(200 if result.get("ok") else 500, result)
                return

            if path == "/api/bridge/to-allocator":
                bid = body.get("backlog_id") or body.get("id")
                if not bid:
                    self._json(400, {"ok": False, "error": "missing backlog_id"})
                    return
                mins = body.get("minutes")
                pri = body.get("priority")
                result = send_backlog_to_allocator(
                    str(bid),
                    minutes=int(mins) if mins is not None else None,
                    priority=int(pri) if pri is not None else None,
                    rebuild_plan=body.get("rebuild_plan", True),
                )
                self._json(200 if result.get("ok") else 400, result)
                return

            if path == "/api/bridge/send-top":
                limit = int(body.get("limit") or 1)
                result = send_top_to_allocator(limit=limit)
                self._json(200 if result.get("ok") else 500, result)
                return

            if path == "/api/recommendations/refresh":
                result = generate_recommendations(replace_pending=True)
                self._json(200 if result.get("ok") else 500, result)
                return

            if path == "/api/recommendations/approve":
                sid = body.get("id")
                if not sid:
                    self._json(400, {"ok": False, "error": "missing id"})
                    return
                result = approve_suggestion(str(sid))
                self._json(200 if result.get("ok") else 400, result)
                return

            if path == "/api/recommendations/reject":
                sid = body.get("id")
                if not sid:
                    self._json(400, {"ok": False, "error": "missing id"})
                    return
                result = reject_suggestion(str(sid))
                self._json(200 if result.get("ok") else 400, result)
                return

            # ── Google Tasks CRUD ─────────────────────────────────────────
            if path in ("/api/tasks", "/api/tasks/create"):
                result = create_task(
                    str(body.get("list_id") or body.get("list") or ""),
                    str(body.get("title") or ""),
                    notes=str(body.get("notes") or ""),
                    due=body.get("due"),
                    parent=body.get("parent"),
                    previous=body.get("previous"),
                )
                self._json(200 if result.get("ok") else 400, result)
                return

            if path == "/api/tasks/update":
                tid = body.get("task_id") or body.get("id")
                lid = body.get("list_id") or body.get("list")
                if not tid or not lid:
                    self._json(400, {"ok": False, "error": "missing list_id or task_id"})
                    return
                result = update_task(
                    str(lid),
                    str(tid),
                    title=body.get("title"),
                    notes=body.get("notes"),
                    due=body.get("due"),
                    status=body.get("status"),
                    clear_due=bool(body.get("clear_due")),
                )
                self._json(200 if result.get("ok") else 400, result)
                return

            if path == "/api/tasks/complete":
                tid = body.get("task_id") or body.get("id")
                lid = body.get("list_id") or body.get("list")
                if not tid or not lid:
                    self._json(400, {"ok": False, "error": "missing list_id or task_id"})
                    return
                completed = body.get("completed", True)
                if isinstance(completed, str):
                    completed = completed.lower() not in ("0", "false", "no")
                result = complete_task(str(lid), str(tid), completed=bool(completed))
                self._json(200 if result.get("ok") else 400, result)
                return

            if path == "/api/tasks/delete":
                tid = body.get("task_id") or body.get("id")
                lid = body.get("list_id") or body.get("list")
                if not tid or not lid:
                    self._json(400, {"ok": False, "error": "missing list_id or task_id"})
                    return
                result = delete_task(str(lid), str(tid))
                self._json(200 if result.get("ok") else 400, result)
                return

            if path == "/api/tasks/move":
                tid = body.get("task_id") or body.get("id")
                lid = body.get("list_id") or body.get("list")
                if not tid or not lid:
                    self._json(400, {"ok": False, "error": "missing list_id or task_id"})
                    return
                result = move_task(
                    str(lid),
                    str(tid),
                    parent=body.get("parent"),
                    previous=body.get("previous"),
                    destination_list=body.get("destination_list")
                    or body.get("destination"),
                )
                self._json(200 if result.get("ok") else 400, result)
                return

            if path == "/api/tasks/lists/create":
                result = create_tasklist(str(body.get("title") or ""))
                self._json(200 if result.get("ok") else 400, result)
                return

            if path == "/api/tasks/lists/update":
                lid = body.get("list_id") or body.get("id")
                if not lid:
                    self._json(400, {"ok": False, "error": "missing list_id"})
                    return
                result = update_tasklist(str(lid), str(body.get("title") or ""))
                self._json(200 if result.get("ok") else 400, result)
                return

            if path == "/api/tasks/lists/delete":
                lid = body.get("list_id") or body.get("id")
                if not lid:
                    self._json(400, {"ok": False, "error": "missing list_id"})
                    return
                result = delete_tasklist(str(lid))
                self._json(200 if result.get("ok") else 400, result)
                return

            if path == "/api/tasks/from-issue":
                # Mirror GitHub / Buzz board issue → Google Task (link in notes)
                lid = body.get("list_id") or body.get("list")
                if not lid:
                    self._json(400, {"ok": False, "error": "missing list_id"})
                    return
                result = create_task_from_github_issue(
                    str(lid),
                    owner=str(body.get("owner") or ""),
                    repo=str(body.get("repo") or ""),
                    number=body.get("number") or body.get("issue_number") or "",
                    title=str(body.get("title") or ""),
                    html_url=str(body.get("html_url") or body.get("url") or ""),
                    body=str(body.get("body") or body.get("notes") or ""),
                )
                self._json(200 if result.get("ok") else 400, result)
                return
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})
            return

        self._json(404, {"ok": False, "error": "unknown endpoint"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="personal-workspace Workflow Management dashboard"
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--bind",
        default="127.0.0.1",
        help="Bind address. Use 0.0.0.0 on the Pi for LAN/Tailscale access.",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Alias for --bind (matches other dashboard units).",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Force local API (Pi systemd unit flag; this server always serves API locally).",
    )
    parser.add_argument(
        "--backend",
        default=None,
        help="Reserved for terminal frontend proxy mode (ignored when serving as Pi backend).",
    )
    args = parser.parse_args(argv)
    bind = args.host if args.host is not None else args.bind

    url = f"http://{bind}:{args.port}/"
    print(f"Workflow Management dashboard → {url}")
    print(f"Workspace: {WORKSPACE_ROOT}")
    if args.local:
        print("Mode: local API (Pi backend)")
    print(
        "API: GET /api/projects /api/branch-graph /api/tasks | "
        "POST /api/sync /api/protect /api/start-work /api/tasks/*"
    )
    httpd = ThreadingHTTPServer((bind, args.port), ProjectsHandler)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
