#!/usr/bin/env python3
"""Local IoT dashboard — control Wiz lights, groups, and sun schedules.

  GET  /api/health
  GET  /api/devices          — configured devices (+ optional ?status=1)
  GET  /api/groups           — room groups (entryway, livingroom)
  GET  /api/presets
  GET  /api/discover
  GET  /api/schedule         — routines + today's sunrise/sunset
  POST /api/control          — {target, color, brightness?}  target may be group id
  POST /api/status
  POST /api/schedule/location — {latitude, longitude, timezone?}
  POST /api/schedule/routine  — patch a routine {id, enabled?, ...}

When a remote backend is configured (backend.json or --backend URL), this process
only serves the UI and reverse-proxies /api/* to the Pi (or other always-on host).
Local schedule worker is disabled in proxy mode so routines fire once on the Pi.

Usage:
  python3 iot/server.py
  python3 iot/server.py --backend http://192.168.100.98:8780
  python3 iot/server.py --local          # force direct LAN control (no proxy)
  python3 iot/server.py --port 8780 --no-browser
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

IOT_DIR = Path(__file__).resolve().parent
ROOT = IOT_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_BACKEND_CONFIG = IOT_DIR / "backend.json"

from iot.control import (  # noqa: E402
    DEFAULT_BRIGHTNESS,
    DEFAULT_BULBS_PATH,
    DEFAULT_GROUPS_PATH,
    list_color_presets,
    list_configured_devices,
    list_groups,
    load_bulbs,
    load_groups,
    summarize_registry,
)
from iot.discover import lan_notes, local_ipv4_broadcast  # noqa: E402
from iot.schedule import (  # noqa: E402
    DEFAULT_SCHEDULE_PATH,
    DEFAULT_STATE_PATH,
    load_schedule,
    load_state,
    location_from_schedule,
    resolve_timezone,
    run_due,
    run_routine_now,
    save_schedule,
    schedule_status,
)
from iot.sleep_follow import (  # noqa: E402
    DEFAULT_FOLLOW_STATE_KEY,
    follow_config,
    tick_sleep_follow,
)
from iot.wiz_adapter import (  # noqa: E402
    discover_and_merge,
    execute_control,
    fetch_device_statuses,
    run_async,
)

DEFAULT_PORT = 8780
SCHEDULE_POLL_SECONDS = 30

# Injectable for tests
_BULBS_PATH: Optional[Path] = None
_GROUPS_PATH: Optional[Path] = None
_SCHEDULE_PATH: Optional[Path] = None
_STATE_PATH: Optional[Path] = None
_TRANSPORT = None  # type: ignore
_BOUND_PORT: int = DEFAULT_PORT
_BOUND_HOST: str = "127.0.0.1"
_SCHEDULER_STOP: Optional[threading.Event] = None
# Remote Pi / always-on backend (None = direct local control)
_BACKEND_URL: Optional[str] = None
_BACKEND_LABEL: str = ""


def load_backend_config(path: Optional[Path] = None) -> dict[str, Any]:
    p = path or DEFAULT_BACKEND_CONFIG
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _registry() -> dict:
    return load_bulbs(_BULBS_PATH or DEFAULT_BULBS_PATH)


def _groups() -> dict:
    return load_groups(_GROUPS_PATH or DEFAULT_GROUPS_PATH)


def _sched_path() -> Path:
    return _SCHEDULE_PATH or DEFAULT_SCHEDULE_PATH


def _state_path() -> Path:
    return _STATE_PATH or DEFAULT_STATE_PATH


def _control_sync(target: str, color: str, brightness: Optional[int]) -> dict[str, Any]:
    bri = DEFAULT_BRIGHTNESS if brightness is None else int(brightness)
    return run_async(
        execute_control(
            target,
            color,
            bri,
            registry=_registry(),
            groups=_groups(),
            transport=_TRANSPORT,
        )
    )


def _scheduler_loop(stop: threading.Event) -> None:
    sys.stderr.write("[iot] schedule worker started\n")
    while not stop.is_set():
        try:
            if location_from_schedule(load_schedule(_sched_path())):
                results = run_due(
                    control=_control_sync,
                    schedule_path=_sched_path(),
                    state_path=_state_path(),
                )
                for r in results:
                    rid = (r.get("routine") or {}).get("id")
                    ok = (r.get("control") or {}).get("ok")
                    sys.stderr.write(f"[iot] routine fired {rid} ok={ok}\n")
                try:
                    sf = tick_sleep_follow(
                        control=_control_sync,
                        schedule_path=_sched_path(),
                        state_path=_state_path(),
                    )
                    if not sf.get("skipped"):
                        sys.stderr.write(
                            f"[iot] sleep_follow ok={sf.get('ok')} "
                            f"pct={sf.get('pct_charged')} "
                            f"err={sf.get('error')}\n"
                        )
                except Exception as e:  # noqa: BLE001
                    sys.stderr.write(f"[iot] sleep_follow error: {e}\n")
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[iot] schedule error: {e}\n")
        stop.wait(SCHEDULE_POLL_SECONDS)
    sys.stderr.write("[iot] schedule worker stopped\n")


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

    def _proxy_api(self, method: str) -> bool:
        """If backend configured, forward /api/* to remote host. Returns True if handled."""
        if not _BACKEND_URL:
            return False
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            return False

        url = _BACKEND_URL.rstrip("/") + self.path
        body: Optional[bytes] = None
        headers = {"Accept": "application/json"}
        if method in ("POST", "PUT", "PATCH"):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length > 0 else b"{}"
            headers["Content-Type"] = (
                self.headers.get("Content-Type") or "application/json"
            )

        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = resp.read()
                code = resp.status
                ctype = resp.headers.get("Content-Type") or "application/json; charset=utf-8"
        except urllib.error.HTTPError as e:
            data = e.read()
            code = e.code
            ctype = e.headers.get("Content-Type") or "application/json; charset=utf-8"
        except Exception as e:  # noqa: BLE001
            self._json(
                502,
                {
                    "ok": False,
                    "error": f"backend unreachable: {type(e).__name__}: {e}",
                    "backend": _BACKEND_URL,
                    "backend_label": _BACKEND_LABEL,
                    "proxy": True,
                },
            )
            return True

        # Annotate health so UI can show proxy → Pi
        if parsed.path == "/api/health":
            try:
                payload = json.loads(data.decode("utf-8") if data else "{}")
                if isinstance(payload, dict):
                    payload["proxy"] = True
                    payload["backend"] = _BACKEND_URL
                    payload["backend_label"] = _BACKEND_LABEL or _BACKEND_URL
                    payload["frontend"] = f"http://{_BOUND_HOST}:{_BOUND_PORT}/"
                    data = json.dumps(payload, default=str).encode("utf-8")
                    ctype = "application/json; charset=utf-8"
            except json.JSONDecodeError:
                pass

        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-IoT-Proxy-Backend", _BACKEND_URL)
        self.end_headers()
        self.wfile.write(data)
        return True

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self._proxy_api("GET"):
            return
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/health":
            reg = _registry()
            try:
                bound_port = int(self.server.server_address[1])  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                bound_port = _BOUND_PORT
            try:
                bound_host = str(self.server.server_address[0])  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                bound_host = _BOUND_HOST
            self._json(
                200,
                {
                    "ok": True,
                    "service": "iot",
                    "bulbs_path": str(_BULBS_PATH or DEFAULT_BULBS_PATH),
                    "registry": summarize_registry(reg),
                    "groups": [g["id"] for g in list_groups(_groups(), reg)],
                    "port": bound_port,
                    "host": bound_host,
                    "schedule_enabled": location_from_schedule(
                        load_schedule(_sched_path())
                    )
                    is not None,
                    "proxy": False,
                    "backend": None,
                },
            )
            return

        if path == "/api/presets":
            self._json(200, {"ok": True, "presets": list_color_presets()})
            return

        if path == "/api/groups":
            reg = _registry()
            groups = list_groups(_groups(), reg)
            self._json(200, {"ok": True, "groups": groups, "count": len(groups)})
            return

        if path == "/api/schedule":
            try:
                self._json(
                    200,
                    schedule_status(
                        load_schedule(_sched_path()),
                    ),
                )
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/sleep-follow":
            try:
                sched = load_schedule(_sched_path())
                st = load_state(_state_path())
                self._json(
                    200,
                    {
                        "ok": True,
                        "config": follow_config(sched),
                        "state": st.get(DEFAULT_FOLLOW_STATE_KEY) or {},
                    },
                )
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/devices":
            want_status = (qs.get("status") or ["0"])[0].lower() in (
                "1",
                "true",
                "yes",
            )
            try:
                reg = _registry()
                if want_status:
                    devices = run_async(
                        fetch_device_statuses(registry=reg, transport=_TRANSPORT)
                    )
                else:
                    devices = list_configured_devices(reg)
                self._json(
                    200,
                    {
                        "ok": True,
                        "devices": devices,
                        "count": len(devices),
                        "presets": list_color_presets(),
                        "groups": list_groups(_groups(), reg),
                    },
                )
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/discover":
            broadcast = (qs.get("broadcast") or [None])[0] or local_ipv4_broadcast() or "255.255.255.255"
            wait = float((qs.get("wait") or ["4"])[0])
            wait = max(1.0, min(wait, 15.0))
            want_mdns = (qs.get("mdns") or ["1"])[0].lower() not in ("0", "false", "no")
            try:
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
        if self._proxy_api("POST"):
            return
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
                        groups=_groups(),
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
                    g = _groups()
                    members = None
                    if target in g or target.lower() in g:
                        info = g.get(target) or g.get(target.lower())
                        members = set(info.get("members") or []) if info else None
                    if members is not None:
                        devices = [
                            d
                            for d in devices
                            if d.get("id") in members or d.get("name") in members
                        ]
                    else:
                        devices = [
                            d
                            for d in devices
                            if d.get("id") == target or d.get("name") == target
                        ]
                self._json(200, {"ok": True, "devices": devices, "count": len(devices)})
                return

            if path == "/api/schedule/location":
                lat = body.get("latitude")
                lon = body.get("longitude")
                if lat is None or lon is None:
                    self._json(
                        400,
                        {"ok": False, "error": "latitude and longitude required"},
                    )
                    return
                sched = load_schedule(_sched_path())
                loc = dict(sched.get("location") or {})
                loc["latitude"] = float(lat)
                loc["longitude"] = float(lon)
                if body.get("timezone"):
                    loc["timezone"] = str(body["timezone"])
                else:
                    loc.setdefault("timezone", resolve_timezone())
                if body.get("label"):
                    loc["label"] = str(body["label"])
                sched["location"] = loc
                save_schedule(sched, _sched_path())
                self._json(200, schedule_status(sched))
                return

            if path == "/api/schedule/routine":
                rid = str(body.get("id") or "").strip()
                if not rid:
                    self._json(400, {"ok": False, "error": "id required"})
                    return
                sched = load_schedule(_sched_path())
                routines = list(sched.get("routines") or [])
                found = False
                for i, r in enumerate(routines):
                    if r.get("id") == rid:
                        patch = {
                            k: body[k]
                            for k in (
                                "enabled",
                                "name",
                                "trigger",
                                "offset_minutes",
                                "target",
                                "color",
                                "brightness",
                            )
                            if k in body
                        }
                        routines[i] = {**r, **patch}
                        found = True
                        break
                if not found:
                    self._json(404, {"ok": False, "error": f"unknown routine: {rid}"})
                    return
                sched["routines"] = routines
                save_schedule(sched, _sched_path())
                self._json(200, schedule_status(sched))
                return

            if path == "/api/schedule/run-due":
                # Manual tick (tests / force check)
                results = run_due(
                    control=_control_sync,
                    schedule_path=_sched_path(),
                    state_path=_state_path(),
                )
                self._json(
                    200,
                    {"ok": True, "fired": len(results), "results": results},
                )
                return

            if path == "/api/sleep-follow/tick":
                force = bool(body.get("force", False))
                result = tick_sleep_follow(
                    control=_control_sync,
                    schedule_path=_sched_path(),
                    state_path=_state_path(),
                    force=force,
                )
                self._json(200 if result.get("ok") or result.get("skipped") else 502, result)
                return

            if path == "/api/schedule/run":
                rid = str(body.get("id") or "").strip()
                if not rid:
                    self._json(400, {"ok": False, "error": "id required"})
                    return
                mark = bool(body.get("mark", False))
                result = run_routine_now(
                    rid,
                    control=_control_sync,
                    schedule_path=_sched_path(),
                    state_path=_state_path(),
                    mark=mark,
                )
                code = 200 if result.get("ok") or result.get("control") else 400
                if result.get("error") and "unknown" in str(result.get("error")):
                    code = 404
                self._json(code, result)
                return

            self._json(404, {"ok": False, "error": f"unknown route: {path}"})
        except Exception as e:  # noqa: BLE001
            self._json(500, {"ok": False, "error": str(e)})


def main(argv: Optional[list[str]] = None) -> int:
    global _BULBS_PATH, _BOUND_PORT, _BOUND_HOST, _SCHEDULER_STOP
    global _GROUPS_PATH, _SCHEDULE_PATH, _STATE_PATH
    global _BACKEND_URL, _BACKEND_LABEL
    parser = argparse.ArgumentParser(description="IoT local dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--bulbs", type=Path, default=None, help="Path to bulbs.json")
    parser.add_argument("--groups", type=Path, default=None)
    parser.add_argument("--schedule", type=Path, default=None)
    parser.add_argument(
        "--backend",
        default=None,
        help="Proxy /api/* to this base URL (e.g. http://192.168.100.98:8780)",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Force direct local control (ignore backend.json)",
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-scheduler", action="store_true")
    args = parser.parse_args(argv)
    _BULBS_PATH = args.bulbs.resolve() if args.bulbs else None
    _GROUPS_PATH = args.groups.resolve() if args.groups else None
    _SCHEDULE_PATH = args.schedule.resolve() if args.schedule else None
    _BOUND_PORT = int(args.port)
    _BOUND_HOST = str(args.host)

    # Resolve backend: --local wins, then --backend, then backend.json
    if args.local:
        _BACKEND_URL = None
        _BACKEND_LABEL = ""
    elif args.backend:
        _BACKEND_URL = str(args.backend).rstrip("/")
        _BACKEND_LABEL = urlparse(_BACKEND_URL).hostname or _BACKEND_URL
    else:
        cfg = load_backend_config()
        url = (cfg.get("url") or "").strip()
        _BACKEND_URL = url.rstrip("/") if url else None
        _BACKEND_LABEL = str(cfg.get("label") or "") or (
            urlparse(_BACKEND_URL).hostname if _BACKEND_URL else ""
        )

    # Proxy mode: never run a second schedule loop on the Mac
    use_scheduler = (not args.no_scheduler) and (_BACKEND_URL is None)

    server = ThreadingHTTPServer((args.host, args.port), IoTHandler)
    _BOUND_HOST, _BOUND_PORT = server.server_address[0], int(server.server_address[1])
    url = f"http://{_BOUND_HOST}:{_BOUND_PORT}/"
    print(f"IoT dashboard → {url}")
    if _BACKEND_URL:
        print(f"backend  → {_BACKEND_URL} ({_BACKEND_LABEL or 'remote'}) [proxy mode]")
        print("scheduler → remote backend (local worker off)")
    else:
        print(f"bulbs → {_BULBS_PATH or DEFAULT_BULBS_PATH}")
        print("mode → local direct control")
    print("API: /api/health /api/devices /api/groups /api/schedule /api/control")

    stop = threading.Event()
    _SCHEDULER_STOP = stop
    if use_scheduler:
        worker = threading.Thread(
            target=_scheduler_loop, args=(stop,), name="iot-schedule", daemon=True
        )
        worker.start()

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
        stop.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
