#!/usr/bin/env python3
"""Minimal static file server for the Panamerica Auto website MVP."""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import socketserver
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 8795 — free of monorepo dashboard ports (8000/8765/8770/8780/8787/8790)
DEFAULT_PORT = 8795
DEFAULT_BIND = "127.0.0.1"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve Panamerica Auto website")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", DEFAULT_PORT)),
        help=f"Port to bind (default {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--bind",
        "--host",
        dest="bind",
        default=os.environ.get("BIND", DEFAULT_BIND),
        help=f"Address to bind (default {DEFAULT_BIND}; use 0.0.0.0 on Pi)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Accepted for deploy-script parity (no-op; this server never opens a browser)",
    )
    args = parser.parse_args(argv)

    os.chdir(ROOT)
    handler = functools.partial(QuietHandler, directory=str(ROOT))
    # Allow quick restarts during local dev
    socketserver.TCPServer.allow_reuse_address = True

    with socketserver.TCPServer((args.bind, args.port), handler) as httpd:
        display_host = "127.0.0.1" if args.bind in ("0.0.0.0", "") else args.bind
        url = f"http://{display_host}:{args.port}/"
        print(f"Panamerica Auto site: {url}", flush=True)
        if args.bind == "0.0.0.0":
            print(f"LAN bind: 0.0.0.0:{args.port}", flush=True)
        print("Press Ctrl+C to stop.", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
