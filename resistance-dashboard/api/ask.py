"""POST /api/ask. Import api._ask_post — never api.ask (Vercel loads this file as api.ask)."""

from __future__ import annotations

try:
    from api._ask_post import application, app, ask_body, handler
except Exception as _boot_exc:  # noqa: BLE001
    import json
    from http.server import BaseHTTPRequestHandler

    def ask_body(headers, payload=None):  # noqa: ARG001
        return 500, {"ok": False, "error": f"ask_boot: {type(_boot_exc).__name__}"}

    class handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            raw = json.dumps(
                {"ok": False, "error": f"ask_boot: {type(_boot_exc).__name__}"}
            ).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            self.do_POST()

        def log_message(self, format: str, *args) -> None:
            return

    app = handler
    application = handler
