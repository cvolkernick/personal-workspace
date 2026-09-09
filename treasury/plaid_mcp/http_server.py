"""Streamable HTTP + JSON REST for plaid-bank. Bearer required off-loopback."""

from __future__ import annotations

import json
import os
import secrets
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from treasury.plaid_mcp import SERVER_NAME, __version__
from treasury.plaid_mcp.plaid_http import PlaidError, load_secret
from treasury.plaid_mcp.protocol import PlaidBankSession, handle_rpc

MCP_TOKEN_ENV = "PLAID_MCP_TOKEN"


def load_mcp_token() -> str:
    return load_secret(MCP_TOKEN_ENV, "mcp_token")


def bind_is_public(host: str) -> bool:
    h = (host or "").strip().lower()
    return h not in ("127.0.0.1", "localhost", "::1")


class PlaidBankHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr: Tuple[str, int], session: PlaidBankSession, token: str):
        super().__init__(addr, PlaidBankHandler)
        self.session = session
        self.token = token
        self.sessions: Dict[str, str] = {}


class PlaidBankHandler(BaseHTTPRequestHandler):
    server_version = f"{SERVER_NAME}/{__version__}"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Path + method only — never headers or bodies (tokens).
        import sys

        sys.stderr.write("%s - %s %s\n" % (self.address_string(), self.command, self.path.split("?", 1)[0]))

    def _send(self, status: int, body: bytes, content_type: str, extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload: Any, extra: Optional[Dict[str, str]] = None) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self._send(status, raw, "application/json", extra)

    def _unauthorized(self) -> None:
        self._send_json(401, {"error": "unauthorized"}, {"WWW-Authenticate": "Bearer"})

    def _auth_ok(self) -> bool:
        token = getattr(self.server, "token", "") or ""
        if not token:
            # Loopback-only mode: handler is only constructed when bind is loopback
            # or a token is set. If we got here without a token, allow.
            return True
        header = self.headers.get("Authorization") or ""
        alt = self.headers.get("X-Plaid-Mcp-Token") or ""
        got = ""
        if header.lower().startswith("bearer "):
            got = header[7:].strip()
        elif alt:
            got = alt.strip()
        if not got:
            return False
        return secrets.compare_digest(got, token)

    def do_GET(self) -> None:  # noqa: N802
        if not self._auth_ok() and not self.path.startswith("/healthz"):
            # healthz stays unauthenticated for systemd / mesh probes
            if self.path.split("?", 1)[0] != "/healthz":
                self._unauthorized()
                return
        path = urlparse(self.path).path
        if path == "/healthz":
            self._send(200, b"ok\n", "text/plain; charset=utf-8")
            return
        if path in ("/mcp", "/"):
            # Some MCP clients GET /mcp to open SSE. Advertise JSON-only.
            self._send_json(200, {"server": SERVER_NAME, "transport": "streamable-http", "version": __version__})
            return
        if path == "/api/balances":
            self._api_tool("get_balances", {})
            return
        if path == "/api/accounts":
            self._api_tool("list_accounts", {})
            return
        if path == "/api/transactions":
            qs = parse_qs(urlparse(self.path).query)
            days = 14
            if qs.get("days"):
                try:
                    days = int(qs["days"][0])
                except (TypeError, ValueError):
                    days = 14
            self._api_tool("list_transactions", {"days": days})
            return
        self._send_json(404, {"error": "not found"})

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if not self._auth_ok():
            self._unauthorized()
            return
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or "0")
        if length > 1_000_000:
            self._send_json(413, {"error": "payload too large"})
            return
        raw = self.rfile.read(length) if length else b""
        if path not in ("/mcp", "/"):
            self._send_json(404, {"error": "not found"})
            return
        try:
            message = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            return
        session_id = self.headers.get("Mcp-Session-Id") or str(uuid.uuid4())
        extra = {
            "Mcp-Session-Id": session_id,
            "MCP-Protocol-Version": "2024-11-05",
        }
        if isinstance(message, list):
            replies = []
            for item in message:
                reply = handle_rpc(self.server.session, item)
                if reply is not None:
                    replies.append(reply)
            if not replies:
                self.send_response(202)
                for k, v in extra.items():
                    self.send_header(k, v)
                self.end_headers()
                return
            self._send_json(200, replies, extra)
            return
        reply = handle_rpc(self.server.session, message)
        if reply is None:
            self.send_response(202)
            for k, v in extra.items():
                self.send_header(k, v)
            self.end_headers()
            return
        accept = (self.headers.get("Accept") or "").lower()
        if "text/event-stream" in accept and "application/json" not in accept:
            data = json.dumps(reply)
            body = f"event: message\ndata: {data}\n\n".encode("utf-8")
            extra["Content-Type"] = "text/event-stream"
            self._send(200, body, "text/event-stream", extra)
            return
        self._send_json(200, reply, extra)

    def _api_tool(self, name: str, arguments: Dict[str, Any]) -> None:
        if not self._auth_ok():
            self._unauthorized()
            return
        try:
            payload = self.server.session.call_tool(name, arguments)
        except PlaidError as e:
            self._send_json(502, {"error": str(e)})
            return
        except Exception as e:
            self._send_json(500, {"error": f"internal error: {e}"})
            return
        self._send_json(200, payload)


def serve(host: str, port: int, session: PlaidBankSession, token: str) -> PlaidBankHTTPServer:
    if bind_is_public(host) and not token:
        raise SystemExit(
            "refusing to bind {host}:{port} without PLAID_MCP_TOKEN "
            "(set env or ~/.config/plaid-bank/mcp_token)".format(host=host, port=port)
        )
    httpd = PlaidBankHTTPServer((host, port), session, token)
    return httpd
