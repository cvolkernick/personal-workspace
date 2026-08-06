#!/usr/bin/env python3
"""Horizon visual dashboard server (Global Macro Intelligence).

  GET  /                 — dashboard UI
  GET  /api/health
  GET  /api/brief        — latest synthesis brief JSON
  GET  /api/world-state  — latest world-state JSON
  GET  /api/dashboard    — combined payload for UI
  GET  /api/implications — L0 implication packet (alias: /api/packets/latest)
  POST /api/refresh      — re-run pipeline (body: {"offline": true})

Usage (Mac dev):
  python3 research/horizon/server.py --bootstrap
  python3 research/horizon/server.py --port 8795 --no-browser

Usage (Pi prod — LAN + Tailscale):
  python3 research/horizon/server.py --host 0.0.0.0 --port 8795 --no-browser --bootstrap
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HORIZON_DIR = Path(__file__).resolve().parent
ROOT = HORIZON_DIR.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.horizon.packets import validate_packet  # noqa: E402
from research.horizon.pipeline import run_pipeline  # noqa: E402
from research.horizon.store import (  # noqa: E402
    DEFAULT_DATA_DIR,
    brief_latest_paths,
    load_json,
    load_packet,
    load_world_state,
    packet_latest_path,
    world_state_latest_path,
)

DEFAULT_PORT = 8795
DEFAULT_BIND = "127.0.0.1"


def build_dashboard_payload(workspace: Path | None = None, data_dir: Path | None = None) -> dict:
    data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    workspace = Path(workspace or ROOT)
    state = load_world_state(data_dir)
    brief_path, _ = brief_latest_paths(data_dir)
    brief = None
    if brief_path.is_file():
        try:
            brief = load_json(brief_path)
        except (OSError, json.JSONDecodeError):
            brief = None

    domains = (state or {}).get("domains") or {}
    domain_stats = []
    for d, bucket in domains.items():
        nodes = bucket.get("nodes") or []
        top_score = max((float(n.get("priority_score") or 0) for n in nodes), default=0.0)
        avg_conf = (
            sum(float(n.get("confidence") or 0) for n in nodes) / len(nodes) if nodes else 0.0
        )
        domain_stats.append(
            {
                "id": d,
                "label": bucket.get("label") or d,
                "node_count": len(nodes),
                "top_score": round(top_score, 3),
                "avg_confidence": round(avg_conf, 3),
                "summary": bucket.get("summary") or "",
                "intensity": min(1.0, top_score / 3.5) if top_score else 0.0,
            }
        )
    domain_stats.sort(key=lambda x: x["top_score"], reverse=True)

    regime = None
    if isinstance(brief, dict) and isinstance(brief.get("regime"), dict):
        regime = brief["regime"]
    elif isinstance(state, dict) and isinstance(state.get("regime"), dict):
        regime = state["regime"]

    packet = load_packet(data_dir)

    return {
        "ok": True,
        "service": "horizon",
        "workspace": str(workspace),
        "data_dir": str(data_dir),
        "has_world_state": state is not None,
        "has_brief": brief is not None,
        "has_packet": packet is not None,
        "version_id": (brief or state or {}).get("version_id"),
        "generated_at": (brief or {}).get("generated_at") or (state or {}).get("updated_at"),
        "domain_stats": domain_stats,
        "regime": regime,
        "world_state": state,
        "brief": brief,
        "packet": packet,
        "paths": {
            "world_state": str(world_state_latest_path(data_dir)),
            "brief_json": str(brief_path),
            "packet_latest": str(packet_latest_path(data_dir)),
        },
    }


def build_packet_response(data_dir: Path | None = None, *, level: str | None = "L0") -> tuple[int, dict]:
    """Serve L0 implication packet. Read path soft-degrades on invalid JSON shape."""
    data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    lvl = (level or "L0").upper()
    if lvl not in ("L0", ""):
        # v0 producer is L0-only
        return 400, {
            "ok": False,
            "error": f"level={lvl} not produced by Horizon v0 (only L0)",
            "supported_levels": ["L0"],
        }
    packet = load_packet(data_dir)
    path = packet_latest_path(data_dir)
    if packet is None:
        return 404, {
            "ok": False,
            "error": "no implication packet yet — run POST /api/refresh",
            "path": str(path),
        }
    errors = validate_packet(packet)
    # Soft-degrade: still return body with validation flags (do not 500 UI)
    stale = bool((packet.get("freshness") or {}).get("stale"))
    return 200, {
        "ok": len(errors) == 0,
        "level": "L0",
        "path": str(path),
        "stale": stale,
        "validation": {"valid": len(errors) == 0, "errors": errors},
        "packet": packet,
    }


class HorizonHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HORIZON_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[horizon] " + (fmt % args) + "\n")

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "horizon-macro",
                    "label": "Horizon Macro",
                    "port": DEFAULT_PORT,
                    "workspace": str(ROOT),
                    "note": "Global macro intelligence — seasonal plan is horizon/ on :8791",
                },
            )
            return

        if path == "/api/dashboard":
            try:
                self._json(200, build_dashboard_payload(ROOT, DEFAULT_DATA_DIR))
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/brief":
            brief_path, _ = brief_latest_paths(DEFAULT_DATA_DIR)
            if not brief_path.is_file():
                self._json(404, {"ok": False, "error": "no brief yet — run refresh"})
                return
            try:
                self._json(200, {"ok": True, "brief": load_json(brief_path)})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path in ("/api/world-state", "/api/world_state"):
            state = load_world_state(DEFAULT_DATA_DIR)
            if state is None:
                self._json(404, {"ok": False, "error": "no world-state yet — run refresh"})
                return
            self._json(200, {"ok": True, "world_state": state})
            return

        if path in (
            "/api/implications",
            "/api/packets/latest",
            "/api/packets",
        ):
            qs = parse_qs(parsed.query or "")
            level = (qs.get("level") or ["L0"])[0]
            code, body = build_packet_response(DEFAULT_DATA_DIR, level=level)
            self._json(code, body)
            return

        if path in ("/favicon.svg", "/favicon.ico"):
            fav = HORIZON_DIR / "favicon.svg"
            if fav.is_file():
                body = fav.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(body)
                return

        if path in ("/", "/index.html", "/horizon", "/horizon/"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/refresh":
            self._json(404, {"ok": False, "error": "not found"})
            return
        body = self._read_json_body()
        offline = body.get("offline", True)
        link_only = bool(body.get("link_only", False))
        try:
            result = run_pipeline(
                workspace=ROOT,
                data_dir=DEFAULT_DATA_DIR,
                offline=bool(offline),
                link_only=link_only,
            )
            payload = build_dashboard_payload(ROOT, DEFAULT_DATA_DIR)
            payload["refresh"] = {
                "ok": result.get("ok"),
                "version_id": result.get("version_id"),
                "source_modes": result.get("source_modes"),
                "packet_id": (result.get("packet") or {}).get("packet_id"),
                "packet_path": (result.get("paths") or {}).get("packet_latest"),
                "sections": result.get("sections"),
                "linkage_count": result.get("linkage_count"),
            }
            self._json(200, payload)
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Horizon Macro visual dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--bind",
        default=DEFAULT_BIND,
        help="Bind address. Use 0.0.0.0 on the Pi for LAN/Tailscale access.",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Alias for --bind (matches other dashboard units).",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Pi systemd unit flag (informational; API is always local).",
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Run offline pipeline once before serving if no brief exists",
    )
    args = parser.parse_args(argv)
    bind = args.host if args.host is not None else args.bind

    brief_path, _ = brief_latest_paths(DEFAULT_DATA_DIR)
    if args.bootstrap or not brief_path.is_file():
        print("[horizon] bootstrapping offline world-state + brief…", flush=True)
        run_pipeline(workspace=ROOT, data_dir=DEFAULT_DATA_DIR, offline=True)

    server = ThreadingHTTPServer((bind, args.port), HorizonHandler)
    display_host = "127.0.0.1" if bind in ("0.0.0.0", "::") else bind
    url = f"http://{display_host}:{args.port}/"
    print(f"[horizon] dashboard at {url} (bind {bind})", flush=True)
    print(f"[horizon] workspace: {ROOT}", flush=True)
    if args.local:
        print("[horizon] mode: local API (Pi backend)", flush=True)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[horizon] stopped", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
