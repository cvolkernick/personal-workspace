#!/usr/bin/env python3
"""Local server for Financial Command Center.

Serves static UI + APIs:
  GET  /api/treasury   — latest evaluation JSON
  GET  /api/config     — treasury/config.json
  POST /api/config     — merge-save manual fields / policy
  POST /api/refresh    — re-run treasury evaluation (live Coinbase)

Usage:
  python3 financial-command/server.py
  python3 financial-command/server.py --port 8000
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.adapters import load_config, save_config  # noqa: E402
from treasury.run_treasury import main as run_treasury_main  # noqa: E402


class FCCHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[fcc] " + (fmt % args) + "\n")

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
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
        path = urlparse(self.path).path
        if path == "/api/treasury":
            p = ROOT / "financial-command" / "treasury_latest.json"
            if not p.is_file():
                self._json(404, {"ok": False, "error": "no treasury_latest.json — POST /api/refresh"})
                return
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, data)
            return
        if path == "/api/config":
            self._json(200, {"ok": True, "config": load_config()})
            return
        if path in ("/", "/financial-command", "/financial-command/"):
            self.path = "/financial-command/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/config":
            body = self._read_json()
            try:
                save_config(body)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            # Re-evaluate offline (use latest snapshots + new manual)
            try:
                run_treasury_main(["--offline"])
            except SystemExit as e:
                if e.code not in (0, None):
                    self._json(500, {"ok": False, "error": f"treasury exit {e.code}"})
                    return
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, {"ok": True, "config": load_config()})
            return

        if path == "/api/refresh":
            offline = False
            body = self._read_json()
            if body.get("offline"):
                offline = True
            # Prefer live YNAB + Coinbase unless offline
            args = ["--offline"] if offline else []
            try:
                if not offline:
                    try:
                        from treasury.ynab_sync import main as ynab_main

                        ynab_main([])
                    except SystemExit:
                        pass
                    except Exception as ye:
                        sys.stderr.write(f"[fcc] ynab_sync warning: {ye}\n")
                code = run_treasury_main(args)
            except SystemExit as e:
                code = e.code if e.code is not None else 0
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            if code not in (0, None):
                self._json(500, {"ok": False, "error": f"treasury exit {code}"})
                return
            p = ROOT / "financial-command" / "treasury_latest.json"
            data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
            self._json(200, {"ok": True, "treasury": data})
            return

        self._json(404, {"ok": False, "error": "unknown endpoint"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Financial Command Center server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Initial refresh offline")
    args = parser.parse_args(argv)

    # Initial YNAB + treasury refresh
    try:
        if not args.offline:
            try:
                from treasury.ynab_sync import main as ynab_main

                ynab_main([])
            except SystemExit:
                pass
            except Exception as ye:
                print(f"ynab_sync warning: {ye}", file=sys.stderr)
        run_treasury_main(["--offline"] if args.offline else [])
    except SystemExit:
        pass
    except Exception as e:
        print(f"initial treasury refresh warning: {e}", file=sys.stderr)

    url = f"http://127.0.0.1:{args.port}/financial-command/index.html"
    print(f"Financial Command Center → {url}")
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), FCCHandler)
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
