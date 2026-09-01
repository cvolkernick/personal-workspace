"""Single Vercel function for /api/status and /api/health. Hobby cap: 1 of 12.

Public GitHub reads only. Edge-cache status so unauthenticated rate limits hold.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from status import build_status  # noqa: E402

STATUS_CACHE_CONTROL = "public, s-maxage=900, stale-while-revalidate=300"


def route_from(path: str) -> str:
    parsed = urlparse(path or "")
    qs = parse_qs(parsed.query or "")
    explicit = (qs.get("_r") or [""])[0].strip()
    if explicit in ("status", "health"):
        return explicit
    tail = parsed.path.rstrip("/").split("/")[-1] if parsed.path else ""
    if tail == "health":
        return "health"
    return "status"


def handle(method: str, path: str) -> tuple[int, dict, str]:
    """Return (status, json_body, cache_control)."""
    if method == "OPTIONS":
        return 204, {}, "no-store"
    if method not in ("GET", "HEAD"):
        return 405, {"ok": False, "error": "method_not_allowed"}, "no-store"

    route = route_from(path)
    if route == "health":
        return (
            200,
            {
                "ok": True,
                "service": "oomwoo",
                "host": "vercel",
                "note": "makerspet/oomwoo project-status MVP",
            },
            "no-store",
        )

    parsed = urlparse(path or "")
    qs = parse_qs(parsed.query or "")
    # Public edge cache is the rate-limit budget. Ignore refresh=1 here.
    _ = qs
    try:
        payload = build_status(refresh=False)
        if not isinstance(payload, dict):
            return 500, {"ok": False, "error": "bad_payload"}, "no-store"
        payload.setdefault("host", "vercel")
        return 200, payload, STATUS_CACHE_CONTROL
    except Exception as exc:  # noqa: BLE001
        return 500, {"ok": False, "error": str(exc)}, "no-store"


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict, cache_control: str) -> None:
        raw = json.dumps(body, default=str).encode("utf-8") if body else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", cache_control)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def _dispatch(self, method: str) -> None:
        try:
            status, body, cache = handle(method, self.path)
        except Exception as exc:  # noqa: BLE001
            status, body, cache = 500, {"ok": False, "error": type(exc).__name__}, "no-store"
        self._send(status, body, cache)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._dispatch("OPTIONS")

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[oomwoo-vercel] " + (fmt % args) + "\n")
