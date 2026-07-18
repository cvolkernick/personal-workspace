#!/usr/bin/env python3
"""Local IoT dashboard — control Wiz lights and view discovery.

  GET  /api/health
  GET  /api/devices          — configured devices (+ optional ?status=1)
  GET  /api/presets          — color presets
  GET  /api/discover         — Wiz + mDNS probe, merged device list
  POST /api/control          — {target, color, brightness?}
  POST /api/status           — optional body {target} or all

Usage:
  python3 iot/server.py
  python3 iot/server.py --port 8780 --no-browser
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

IOT_DIR = Path(__file__).resolve().parent
ROOT = IOT_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from iot.control import (  # noqa: E402
    DEFAULT_BRIGHTNESS,
    DEFAULT_BULBS_PATH,
    list_color_presets,
    list_configured_devices,
    load_bulbs,
    summarize_registry,
)
from iot.discover import lan_notes, local_ipv4_broadcast  # noqa: E402
from iot.wiz_adapter import (  # noqa: E402
    discover_and_merge,
    execute_control,
    fetch_device_statuses,
    run_async,
)

DEFAULT_PORT = 8780

# Injectable for tests
_BULBS_PATH: Optional[Path] = None
_TRANSPORT = None  # type: ignore


def _registry() -> dict:
    return load_bulbs(_BULBS_PATH or DEFAULT_BULBS_PATH)


class IoTHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(IOT_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[iot] " + (fmt % args) + "\n")

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
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

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/health":
            reg = _registry()
            self._json(
                200,
                {
                    "ok": True,
                    "service": "iot",
                    "bulbs_path": str(_BULBS_PATH or DEFAULT_BULBS_PATH),
                    "registry": summarize_registry(reg),
                    "port": DEFAULT_PORT,
                },
            )
            return

        if path == "/api/presets":
            self._json(
                200,
                {"ok": True, "presets": list_color_presets()},
            )
            return

        if path == "/api/devices":
            want_status = (qs.get("status") or ["0"])[0].lower() in (
                "1",
                "true",
                "yes",
            )
            try:
                if want_status:
                    devices = run_async(
                        fetch_device_statuses(
                            registry=_registry(), transport=_TRANSPORT
                        )
                    )
                else:
                    devices = list_configured_devices(_registry())
                self._json(
                    200,
                    {
                        "ok": True,
                        "devices": devices,
                        "count": len(devices),
                        "presets": list_color_presets(),
                    },
                )
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/discover":
            broadcast = (qs.get("broadcast") or [None])[0] or local_ipv4_broadcast() or "255.255.255.255"
            wait = float((qs.get("wait") or ["4"])[0])
            wait = max(1.0, min(wait, 15.0))
            # mdns=0 skips slow dns-sd browses (default on for UI, optional off)
            want_mdns = (qs.get("mdns") or ["1"])[0].lower() not in ("0", "false", "no")
            try:
                lan: dict[str, Any]
                if want_mdns:
                    lan = lan_notes()
                else:
                    lan = {
                        "ok": True,
                        "mdns": [],
                        "mdns_count": 0,
                        "broadcast_guess": local_ipv4_broadcast(),
                        "notes": ["mdns skipped (mdns=0)"],
                    }
                extra = list(lan.get("mdns") or [])
                result = run_async(
                    discover_and_merge(
                        registry=_registry(),
                        transport=_TRANSPORT,
                        broadcast=broadcast,
                        wait_time=wait,
                        extra_discovered=extra,
                    )
                )
                result["lan"] = lan
                result["broadcast"] = broadcast
                self._json(200, result)
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path in ("/", "/index.html"):
            self.path = "/index.html"
            return super().do_GET()

        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._read_json()

        try:
            if path == "/api/control":
                target = str(body.get("target") or body.get("name") or "").strip()
                color = str(body.get("color") or body.get("preset") or "").strip()
                if not target or not color:
                    self._json(
                        400,
                        {"ok": False, "error": "target and color are required"},
                    )
                    return
                brightness = body.get("brightness")
                if brightness is None:
                    brightness = DEFAULT_BRIGHTNESS
                else:
                    brightness = int(brightness)
                result = run_async(
                    execute_control(
                        target,
                        color,
                        brightness,
                        registry=_registry(),
                        transport=_TRANSPORT,
                    )
                )
                code = 200 if result.get("ok") or result.get("results") else 400
                self._json(code, result)
                return

            if path == "/api/status":
                target = str(body.get("target") or body.get("name") or "").strip()
                devices = run_async(
                    fetch_device_statuses(
                        registry=_registry(), transport=_TRANSPORT
                    )
                )
                if target and target.lower() != "all":
                    devices = [
                        d
                        for d in devices
                        if d.get("id") == target or d.get("name") == target
                    ]
                self._json(200, {"ok": True, "devices": devices, "count": len(devices)})
                return

            self._json(404, {"ok": False, "error": f"unknown route: {path}"})
        except Exception as e:  # noqa: BLE001
            self._json(500, {"ok": False, "error": str(e)})


def main(argv: Optional[list[str]] = None) -> int:
    global _BULBS_PATH
    parser = argparse.ArgumentParser(description="IoT local dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--bulbs", type=Path, default=None, help="Path to bulbs.json")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    _BULBS_PATH = args.bulbs.resolve() if args.bulbs else None

    server = ThreadingHTTPServer((args.host, args.port), IoTHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"IoT dashboard → {url}")
    print(f"bulbs → {_BULBS_PATH or DEFAULT_BULBS_PATH}")
    print("API: /api/health /api/devices /api/discover /api/control")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
