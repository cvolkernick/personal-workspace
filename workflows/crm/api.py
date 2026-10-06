"""crm-api. Bearer token per seat. Stdlib HTTP, tailnet bind is an ops choice."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from workflows.crm.auth import CrmError
from workflows.crm.core import CrmDB
from workflows.crm.schema import default_db_path

_AUDIT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def resolve_day(value: str) -> str:
    text = (value or "").strip()
    today = datetime.now(timezone.utc).date()
    lowered = text.lower()
    if lowered == "today":
        return today.isoformat()
    if lowered == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    return text


def dispatch(
    db: CrmDB,
    method: str,
    path: str,
    headers: Any,
    body: bytes,
) -> tuple[int, dict[str, Any]]:
    parsed = urlparse(path)
    route = parsed.path.rstrip("/") or "/"
    query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
    if route == "/v1/audit" and method in _AUDIT_METHODS:
        return 405, {"error": "method not allowed"}
    actor = db.authenticate(_bearer(headers))
    try:
        payload = _json_body(body) if body else {}
    except json.JSONDecodeError:
        return 400, {"error": "invalid json"}
    except CrmError as exc:
        return exc.status, {"error": exc.message}
    try:
        return _route(db, actor, method, route, query, payload)
    except CrmError as exc:
        return exc.status, {"error": exc.message}


def _bearer(headers: Any) -> str:
    raw = ""
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if callable(getter):
        raw = str(getter("Authorization") or getter("authorization") or "")
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return ""


def _json_body(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    parsed = json.loads(body.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise CrmError(400, "json object required")
    return parsed


def _route(
    db: CrmDB,
    actor: Any,
    method: str,
    route: str,
    query: dict[str, str],
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    if route == "/v1/parties" and method == "POST":
        doc, created = db.create_party(actor, payload)
        if created:
            return 201, doc
        return 200, {**doc, "result": "existing"}
    if route == "/v1/parties/merge" and method == "POST":
        doc = db.merge_parties(
            actor,
            str(payload.get("survivor_id") or ""),
            str(payload.get("loser_id") or ""),
        )
        return 200, doc
    if route == "/v1/parties/by-identity" and method == "GET":
        kind = "phone" if "phone" in query else str(query.get("kind") or "")
        value = query.get("phone") or query.get("value") or ""
        if "email" in query:
            kind, value = "email", query["email"]
        elif "vin" in query:
            kind, value = "vin", query["vin"]
        elif "listing_id" in query:
            kind, value = "listing_id", query["listing_id"]
        found = db.by_identity(actor, kind=kind, value=value)
        if found is None:
            return 404, {"error": "not found"}
        return 200, found
    if route == "/v1/search" and method == "GET":
        results = db.search(
            actor,
            q=query.get("q") or "",
            party_type=query.get("type") or "",
            tag=query.get("tag") or "",
            status=query.get("status") or "",
        )
        return 200, {"results": results}
    if route == "/v1/interactions" and method == "POST":
        row, created = db.add_interaction(actor, payload)
        return (201 if created else 200), row
    if route == "/v1/notes" and method == "POST":
        return 201, db.add_note(actor, payload)
    if route == "/v1/notes" and method == "GET":
        return 200, {
            "results": db.list_notes(
                actor,
                query.get("party_id") or "",
                include_archived=query.get("include_archived") == "1",
            )
        }
    if route == "/v1/tasks" and method == "POST":
        body = dict(payload)
        if body.get("due_at"):
            body["due_at"] = resolve_day(str(body["due_at"]))
        return 201, db.add_task(actor, body)
    if route == "/v1/tasks" and method == "GET":
        due = resolve_day(query.get("due_before") or "")
        return 200, {
            "results": db.list_tasks(
                actor,
                assignee=query.get("assignee") or "",
                due_before=due,
            )
        }
    if route == "/v1/audit" and method == "GET":
        return 200, {"results": db.list_audit(actor, query.get("entity_id") or "")}

    party_id = _tail(route, "/v1/parties/")
    if party_id and "/" not in party_id:
        if method == "GET":
            return 200, db.get_party(actor, party_id)
        if method == "PATCH":
            return 200, db.update_party(actor, party_id, payload)
        if method == "DELETE":
            db.delete_party(actor, party_id)
            return 200, {"deleted": party_id}
    if party_id and party_id.endswith("/timeline") and method == "GET":
        return 200, {"results": db.timeline(actor, party_id[: -len("/timeline")])}

    note_id = _tail(route, "/v1/notes/")
    if note_id and method == "PATCH":
        return 200, db.update_note(actor, note_id, payload)
    if note_id and method == "GET":
        return 200, db.get_note(actor, note_id)

    task_id = _tail(route, "/v1/tasks/")
    if task_id and method == "PATCH":
        return 200, db.update_task(actor, task_id, payload)

    if route == "/health" and method == "GET":
        return 200, {"ok": True}
    return 404, {"error": "not found"}


def _tail(route: str, prefix: str) -> str:
    if route.startswith(prefix):
        return route[len(prefix) :]
    return ""


class CrmHandler(BaseHTTPRequestHandler):
    db: CrmDB

    def do_GET(self) -> None:  # noqa: N802
        self._go()

    def do_POST(self) -> None:  # noqa: N802
        self._go()

    def do_PATCH(self) -> None:  # noqa: N802
        self._go()

    def do_PUT(self) -> None:  # noqa: N802
        self._go()

    def do_DELETE(self) -> None:  # noqa: N802
        self._go()

    def _go(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        status, payload = dispatch(self.db, self.command, self.path, self.headers, raw)
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: object) -> None:
        return


def serve(db: CrmDB, host: str = "127.0.0.1", port: int = 8791) -> ThreadingHTTPServer:
    handler = type("BoundCrmHandler", (CrmHandler,), {"db": db})
    server = ThreadingHTTPServer((host, port), handler)
    return server


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Panamerica CRM API")
    parser.add_argument("--db", default=str(default_db_path()))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--export", default="", help="JSON export path for the 30-day window")
    args = parser.parse_args(argv)
    db = CrmDB(Path(args.db))
    if args.export:
        db.set_export_path(Path(args.export))
    httpd = serve(db, args.host, args.port)
    print(json.dumps({"listening": f"http://{args.host}:{args.port}", "db": args.db}), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
