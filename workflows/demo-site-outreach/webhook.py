"""Inbound Bland SMS webhook → state machine.

The harness can either:
  python3 run.py webhook --payload-file event.json
or serve HTTP:
  python3 run.py serve-webhook --host 127.0.0.1 --port 8788
"""

from __future__ import annotations

import hashlib
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from pipeline import Pipeline


def extract_inbound(payload: dict[str, Any]) -> dict[str, str]:
    """Accept a few Bland payload shapes without logging secrets."""
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    text = (
        payload.get("text")
        or payload.get("body")
        or payload.get("message")
        or payload.get("agent_message")
        or ""
    )
    if isinstance(text, dict):
        text = text.get("text") or text.get("body") or ""
    phone = (
        payload.get("from")
        or payload.get("user_number")
        or payload.get("phone_number")
        or payload.get("to")
        or ""
    )
    lead_id = str(metadata.get("lead_id") or payload.get("lead_id") or "")
    return {"text": str(text or "").strip(), "phone": str(phone or "").strip(), "lead_id": lead_id}


def check_secret(provided: str, expected: str) -> bool:
    if not expected:
        return True
    left = hashlib.sha256(provided.encode("utf-8")).hexdigest()
    right = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    return hmac.compare_digest(left, right)


def handle_payload(pipeline: Pipeline, payload: dict[str, Any]) -> dict[str, Any]:
    inbound = extract_inbound(payload)
    lead = None
    if inbound["lead_id"]:
        lead = pipeline.store.get(inbound["lead_id"])
    return pipeline.ingest_text(inbound["text"], lead=lead, phone=inbound["phone"])


def make_handler(pipeline: Pipeline) -> type[BaseHTTPRequestHandler]:
    secret = pipeline.cfg.webhook_secret

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
            return data if isinstance(data, dict) else {}

        def _write(self, code: int, body: dict[str, Any]) -> None:
            blob = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in {"/health", "/healthz"}:
                self._write(200, {"ok": True, "service": "demo-site-outreach-webhook"})
                return
            self._write(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            provided = self.headers.get("X-Webhook-Secret") or self.headers.get("Authorization") or ""
            provided = provided.replace("Bearer ", "").strip()
            if not check_secret(provided, secret):
                self._write(401, {"error": "unauthorized"})
                return
            try:
                payload = self._read_json()
                result = handle_payload(pipeline, payload)
                self._write(200, result)
            except Exception as exc:  # noqa: BLE001 — HTTP boundary
                self._write(400, {"error": exc.__class__.__name__, "detail": str(exc)[:200]})

    return Handler


def serve(pipeline: Pipeline, host: str, port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(pipeline))
    return httpd
