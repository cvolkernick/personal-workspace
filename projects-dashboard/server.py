#!/usr/bin/env python3
"""Local server for personal-workspace workflow-management dashboard.

  GET  /api/projects       — readiness + branches + resume kit + areas
  POST /api/protect        — commit durable dirty work + push (auto branch)
  POST /api/sync           — session index + protect + push
  POST /api/session-index  — write ops/session-index only
  POST /api/start-work     — body {"area":"treasury"} → work/treasury
  GET  /api/health

Usage:
  python3 projects-dashboard/server.py
  python3 projects-dashboard/server.py --port 8765 --no-browser
  python3 projects-dashboard/server.py --backend http://pi-host:8765
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from backlog import (  # noqa: E402
    add_item,
    backlog_payload,
    delete_item,
    import_initiatives,
    initiate_item,
    update_item,
)
from backlog_groom import groom_backlog  # noqa: E402
from git_workflow import protect_work, start_work, sync_after_work  # noqa: E402
from recommendations import (  # noqa: E402
    approve_suggestion,
    recommendations_payload,
    reject_suggestion,
    generate_recommendations,
)
from remote_backend import add_backend_args, resolve_backend, try_proxy_api  # noqa: E402
from session_backup import write_full_archive, write_session_index  # noqa: E402
from workspace import WORKSPACE_ROOT, collect_workspace_dashboard  # noqa: E402

DEFAULT_PORT = 8765
DEFAULT_BACKEND_CONFIG = ROOT / "backend.json"
_BACKEND_URL: Optional[str] = None
_BACKEND_LABEL: str = ""
_FRONTEND: str = ""


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
        if try_proxy_api(
            self,
            _BACKEND_URL,
            method="GET",
            backend_label=_BACKEND_LABEL,
            frontend=_FRONTEND,
        ):
            return
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "projects-dashboard",
                    "workspace": str(WORKSPACE_ROOT),
                    "proxy": False,
                    "backend": None,
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
                refresh_rec = (qs.get("refresh_recommendations") or ["0"])[0] in (
                    "1",
                    "true",
                    "yes",
                )
                payload["recommendations"] = recommendations_payload(
                    refresh=refresh_rec
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
        if try_proxy_api(
            self,
            _BACKEND_URL,
            method="POST",
            backend_label=_BACKEND_LABEL,
            frontend=_FRONTEND,
        ):
            return
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

            if path == "/api/backlog/groom":
                apply = body.get("apply", True)
                if isinstance(apply, str):
                    apply = apply.lower() not in ("0", "false", "no")
                result = groom_backlog(apply=bool(apply))
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
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})
            return

        self._json(404, {"ok": False, "error": "unknown endpoint"})


def main(argv: list[str] | None = None) -> int:
    global _BACKEND_URL, _BACKEND_LABEL, _FRONTEND
    parser = argparse.ArgumentParser(
        description="personal-workspace Workflow Management dashboard"
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--bind", default="127.0.0.1")
    add_backend_args(parser)
    args = parser.parse_args(argv)

    _BACKEND_URL, _BACKEND_LABEL = resolve_backend(
        local=bool(args.local),
        backend=args.backend,
        config_path=DEFAULT_BACKEND_CONFIG,
    )
    url = f"http://{args.bind}:{args.port}/"
    _FRONTEND = url
    print(f"Workflow Management dashboard → {url}")
    print(f"Workspace: {WORKSPACE_ROOT}")
    if _BACKEND_URL:
        print(f"backend  → {_BACKEND_URL} ({_BACKEND_LABEL or 'remote'}) [proxy mode]")
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
