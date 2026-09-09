#!/usr/bin/env python3
"""plaid-bank MCP: stdio or streamable HTTP. Read-only."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

from treasury.plaid_mcp.http_server import load_mcp_token, serve
from treasury.plaid_mcp.plaid_http import PlaidClient
from treasury.plaid_mcp.protocol import PlaidBankSession, handle_rpc


def _read_stdio_message() -> Optional[Dict[str, Any]]:
    """MCP stdio is Content-Length framed; also accept one JSON object per line."""
    header_lines = []
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\n", b"\r\n"):
            break
        header_lines.append(line)
        # Newline-delimited JSON (no headers): first line is the body.
        stripped = line.strip()
        if stripped.startswith(b"{") or stripped.startswith(b"["):
            try:
                msg = json.loads(stripped.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            return msg if isinstance(msg, dict) else None
    headers = b"".join(header_lines).decode("utf-8", errors="replace")
    length = 0
    for raw in headers.splitlines():
        if raw.lower().startswith("content-length:"):
            try:
                length = int(raw.split(":", 1)[1].strip())
            except ValueError:
                length = 0
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    if not body:
        return None
    try:
        msg = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    return msg if isinstance(msg, dict) else None


def _write_stdio(message: Dict[str, Any]) -> None:
    raw = json.dumps(message, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def serve_stdio(session: PlaidBankSession) -> None:
    while True:
        msg = _read_stdio_message()
        if msg is None:
            return
        reply = handle_rpc(session, msg)
        if reply is not None:
            _write_stdio(reply)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Plaid MCP (X Money)")
    parser.add_argument("--stdio", action="store_true", help="MCP stdio transport")
    parser.add_argument("--host", default=os.environ.get("PLAID_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PLAID_MCP_PORT") or "18801"))
    args = parser.parse_args(argv)

    session = PlaidBankSession(PlaidClient.from_env())
    if args.stdio:
        serve_stdio(session)
        return 0
    token = load_mcp_token()
    httpd = serve(args.host, args.port, session, token)
    print(
        f"plaid-bank listening http://{args.host}:{args.port}/mcp  healthz=/healthz",
        file=sys.stderr,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
