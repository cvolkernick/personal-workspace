#!/usr/bin/env python3
"""Local static server for Howell Home Services demo site."""
from __future__ import annotations

import argparse
import functools
import http.server
import os
import socketserver

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PORT = 8796


def main() -> None:
    parser = argparse.ArgumentParser(description="Howell Home Services demo server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=ROOT)
    with socketserver.TCPServer((args.bind, args.port), handler) as httpd:
        print(f"Howell Home Services demo → http://{args.bind}:{args.port}/")
        print(f"Serving {ROOT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
