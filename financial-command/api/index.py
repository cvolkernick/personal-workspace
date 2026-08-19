"""Single Vercel function for FCC preview. All /api/* rewrite here. Hobby cap: 1 of 12."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from api._lib import dispatch, route_from_path


def _headers_from_handler(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, val in handler.headers.items():
        out[str(key)] = str(val)
    return out


def handle(method: str, path: str, headers: dict[str, str] | None = None):
    parsed = urlparse(path or "")
    route = route_from_path(path, headers)
    query = parse_qs(parsed.query)
    return dispatch(method, route, query=query)


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict) -> None:
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
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
