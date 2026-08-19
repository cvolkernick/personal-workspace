"""Single Vercel function for FCC preview. All /api/* and FCC HTML rewrite here. Hobby cap: 1 of 12."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from api._lib import (
    AUTH_REQUIRED,
    dispatch,
    page_name_from,
    route_from_path,
    serve_page,
    vercel_auth_present,
)


def _headers_from_handler(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, val in handler.headers.items():
        out[str(key)] = str(val)
    return out


def handle(method: str, path: str, headers: dict[str, str] | None = None):
    parsed = urlparse(path or "")
    route = route_from_path(path, headers)
    query = parse_qs(parsed.query)

    # Writes stay 403 even when cookie-less.
    status, body = dispatch(method, route, query=query)
    if status == 403 and isinstance(body, dict) and body.get("error") == "read_only":
        return status, body
    if route == "denied_static":
        return status, body

    if not vercel_auth_present(headers):
        return 401, dict(AUTH_REQUIRED)

    if route == "page":
        name = page_name_from(path, query)
        if not name:
            return 404, {"ok": False, "error": "not_found", "route": "page"}
        return serve_page(name)

    return status, body


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body) -> None:
        if isinstance(body, str):
            raw = body.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
            content_type = "application/json"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _dispatch(self, method: str) -> None:
        try:
            status, body = handle(method, self.path, _headers_from_handler(self))
        except Exception as exc:  # noqa: BLE001
            status, body = 500, {"ok": False, "error": f"preview_failed: {type(exc).__name__}"}
        self._send(status, body)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def log_message(self, format: str, *args) -> None:
        return


app = handler
application = handler
