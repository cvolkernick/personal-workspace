#!/usr/bin/env python3
"""B2 (Brain 2) local web server — browse vault, search, Ask Grok."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from b2_kb.ask import B2AskError, ask_grok, auth_status  # noqa: E402
from b2_kb.vault import (  # noqa: E402
    DEFAULT_VAULT_PATH,
    build_graph,
    list_notes,
    read_note,
    resolve_vault_path,
    search_notes,
)

STATIC_DIR = ROOT / "static"
DEFAULT_PORT = int(os.environ.get("B2_PORT", "8792"))


def _json_response(handler: SimpleHTTPRequestHandler, code: int, payload: Any) -> None:
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


def _read_json_body(handler: SimpleHTTPRequestHandler, max_bytes: int = 100_000) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    if length > max_bytes:
        raise ValueError("body too large")
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON object required")
    return data


class B2Handler(SimpleHTTPRequestHandler):
    vault_path: Path = DEFAULT_VAULT_PATH

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            return self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        if path == "/styles.css":
            return self._serve_file(STATIC_DIR / "styles.css", "text/css; charset=utf-8")
        if path == "/app.js":
            return self._serve_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")

        if path == "/api/health":
            notes = list_notes(self.vault_path)
            return _json_response(
                self,
                200,
                {
                    "ok": True,
                    "service": "b2",
                    "vault_path": str(self.vault_path),
                    "note_count": len(notes),
                    "auth": auth_status(),
                },
            )

        if path == "/api/notes":
            return _json_response(
                self,
                200,
                {
                    "vault_path": str(self.vault_path),
                    "notes": list_notes(self.vault_path),
                },
            )

        if path == "/api/note":
            rel = (qs.get("path") or [""])[0]
            rel = unquote(rel)
            note = read_note(rel, self.vault_path)
            if not note:
                return _json_response(self, 404, {"error": "note not found", "path": rel})
            return _json_response(
                self,
                200,
                {
                    "path": note.path,
                    "title": note.title,
                    "body": note.body,
                    "wikilinks": note.wikilinks,
                },
            )

        if path == "/api/search":
            q = (qs.get("q") or [""])[0]
            try:
                limit = int((qs.get("limit") or ["20"])[0])
            except ValueError:
                limit = 20
            results = search_notes(q, self.vault_path, limit=limit)
            return _json_response(
                self,
                200,
                {"query": q, "count": len(results), "results": results},
            )

        if path == "/api/graph":
            graph = build_graph(self.vault_path)
            return _json_response(
                self,
                200,
                {
                    "vault_path": str(self.vault_path),
                    **graph,
                },
            )

        if path == "/api/auth":
            return _json_response(self, 200, auth_status())

        # Fall through to static
        return super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path != "/api/ask":
            return _json_response(self, 404, {"error": "not found"})
        try:
            body = _read_json_body(self)
        except (ValueError, json.JSONDecodeError) as e:
            return _json_response(self, 400, {"error": str(e)})

        question = (body.get("question") or body.get("q") or "").strip()
        force_offline = bool(body.get("force_offline") or body.get("offline"))
        try:
            top_k = int(body.get("top_k") or 5)
        except (TypeError, ValueError):
            top_k = 5

        try:
            result = ask_grok(
                question,
                self.vault_path,
                force_offline=force_offline,
                top_k=top_k,
            )
            return _json_response(self, 200, {"ok": True, **result})
        except B2AskError as e:
            code = e.status if e.status and 400 <= e.status < 600 else 400
            return _json_response(self, code, {"ok": False, "error": str(e)})
        except Exception as e:
            traceback.print_exc()
            return _json_response(self, 500, {"ok": False, "error": str(e)})

    def _serve_file(self, fp: Path, content_type: str) -> None:
        if not fp.is_file():
            self.send_error(404, "File not found")
            return
        data = fp.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def make_handler(vault: Path):
    class Handler(B2Handler):
        vault_path = vault

    return Handler


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="B2 Brain 2 knowledge base server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--vault",
        default=str(DEFAULT_VAULT_PATH),
        help="Path to B2 Obsidian vault",
    )
    args = parser.parse_args(argv)

    vault = resolve_vault_path(args.vault)
    if not vault.is_dir():
        print(f"ERROR: vault not found: {vault}", file=sys.stderr)
        return 1

    notes = list_notes(vault)
    handler = make_handler(vault)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"B2 vault:  {vault}")
    print(f"Notes:     {len(notes)}")
    print(f"Listening: http://{args.host}:{args.port}/")
    print(
        f"API:       /api/notes  /api/note?path=  /api/search?q=  "
        f"/api/graph  /api/ask  /api/health"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
