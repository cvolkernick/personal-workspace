#!/usr/bin/env python3
"""Local server for personal-workspace graceful-exit dashboard.

  GET  /api/projects       — readiness + branches + resume kit + areas
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
from git_workflow import protect_work, start_work, sync_after_work  # noqa: E402
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
            self._json(
                200,
                {
                    "ok": True,
                    "service": "projects-dashboard",
                    "workspace": str(WORKSPACE_ROOT),
                },
            )
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
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, payload)
            return

        if path == "/api/backlog":
            qs = parse_qs(parsed.query)
            include_done = (qs.get("all") or ["0"])[0] in ("1", "true", "yes")
            try:
                self._json(200, backlog_payload(include_done=include_done))
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
                result = protect_work(
                    message=body.get("message"),
                    push=body.get("push", True),
                    include_snapshots=body.get("include_snapshots", True),
                    ensure_work_branch=body.get("ensure_work_branch", True),
                )
                self._json(200 if result.get("ok") else 500, result)
                return

            if path == "/api/sync":
                result = sync_after_work(
                    message=body.get("message"),
                    snapshot_sessions=body.get("snapshot_sessions", True),
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
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})
            return

        self._json(404, {"ok": False, "error": "unknown endpoint"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="personal-workspace Graceful Exit dashboard"
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args(argv)

    url = f"http://{args.bind}:{args.port}/"
    print(f"Graceful Exit dashboard → {url}")
    print(f"Workspace: {WORKSPACE_ROOT}")
    print("API: GET /api/projects | POST /api/sync /api/protect /api/start-work")
    httpd = ThreadingHTTPServer((args.bind, args.port), ProjectsHandler)
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
