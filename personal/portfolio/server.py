#!/usr/bin/env python3
"""Minimal static server for the personal portfolio MVP."""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import socketserver
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 8770


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve personal portfolio")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args(argv)
    os.chdir(ROOT)
    handler = functools.partial(QuietHandler, directory=str(ROOT))
    with socketserver.TCPServer((args.bind, args.port), handler) as httpd:
        httpd.allow_reuse_address = True
        print(f"Personal portfolio: http://{args.bind}:{args.port}/", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
