"""Pi → Orchestra heartbeat contract (schema_version 1).

Collector writes atomic latest.json under orchestra/data/heartbeat/.
Orchestra GET /api/heartbeat serves the latest file (or a missing payload).

Contract: nest RESEARCH/HEARTBEAT_CONTRACT_V0.md · issue #50
Lock-in: for critical services, health_ok outweighs unit active when both exist.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

COLLECTOR_NAME = "pi-heartbeat"
COLLECTOR_VERSION = "0.1.0"
SCHEMA_VERSION = 1

# Relative to monorepo root (workspace)
HEARTBEAT_REL_DIR = Path("orchestra") / "data" / "heartbeat"
LATEST_NAME = "latest.json"

# v0 watchlist — ports from deploy/endpoints.json + contract
# severity: "critical" → ok:false on failure; "yellow" → degraded only
WATCHLIST: list[dict[str, Any]] = [
    {
        "name": "orchestra-dashboard",
        "unit": "orchestra-dashboard.service",
        "port": 8790,
        "health_path": "/api/health",
        "severity": "critical",
    },
    {
        "name": "workflow-dashboard",
        "unit": "workflow-dashboard.service",
        "port": 8765,
        "health_path": "/api/health",
        "severity": "critical",
    },
    {
        "name": "horizon-dashboard",
        "unit": "horizon-dashboard.service",
        "port": 8795,
        "health_path": "/api/health",
        "severity": "critical",
    },
    {
        "name": "resistance-dashboard",
        "unit": "resistance-dashboard.service",
        "port": 8787,
        "health_path": "/api/healthz",
        "severity": "critical",
    },
    {
        "name": "financial-command",
        "unit": "financial-command.service",
        "port": 8000,
        "health_path": "/api/health",
        "severity": "critical",
    },
    {
        "name": "iot-dashboard",
        "unit": "iot-dashboard.service",
        "port": 8780,
        "health_path": "/api/health",
        "severity": "yellow",
    },
    {
        "name": "b2",
        "unit": "b2.service",  # may not exist yet
        "port": 8792,
        "health_path": "/api/health",
        "severity": "yellow",
        "optional_unit": True,
    },
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def heartbeat_dir(workspace: Path) -> Path:
    return Path(workspace) / HEARTBEAT_REL_DIR


def latest_path(workspace: Path) -> Path:
    return heartbeat_dir(workspace) / LATEST_NAME


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically (tmp + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=2, default=str) + "\n"
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def unit_is_active(unit: str, *, timeout: float = 3.0) -> tuple[bool, str]:
    """Return (active, raw_state). Uses systemctl --user (Pi prod pattern)."""
    if not unit:
        return False, "no-unit"
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        state = (proc.stdout or proc.stderr or "").strip().splitlines()
        raw = state[0] if state else ("active" if proc.returncode == 0 else "unknown")
        return proc.returncode == 0 and raw == "active", raw
    except FileNotFoundError:
        return False, "systemctl-missing"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except OSError as e:
        return False, f"error:{e}"


def probe_health(
    url: str, *, timeout: float = 2.0
) -> tuple[Optional[bool], Optional[int], str]:
    """GET health URL. Returns (health_ok, latency_ms, note).

    health_ok is None when the probe could not decide (connection refused → False).
    """
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency = int((time.monotonic() - t0) * 1000)
            code = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read(4096)
            if code != 200:
                return False, latency, f"http_{code}"
            try:
                data = json.loads(raw.decode("utf-8", errors="replace"))
                if isinstance(data, dict) and "ok" in data:
                    return bool(data["ok"]), latency, ""
            except (json.JSONDecodeError, UnicodeError):
                pass
            # 200 without ok field — treat as healthy
            return True, latency, "no_ok_field"
    except urllib.error.HTTPError as e:
        latency = int((time.monotonic() - t0) * 1000)
        return False, latency, f"http_{e.code}"
    except urllib.error.URLError as e:
        latency = int((time.monotonic() - t0) * 1000)
        return False, latency, f"url_error:{getattr(e, 'reason', e)}"
    except TimeoutError:
        latency = int((time.monotonic() - t0) * 1000)
        return False, latency, "timeout"
    except OSError as e:
        latency = int((time.monotonic() - t0) * 1000)
        return False, latency, f"os_error:{e}"


def service_healthy(*, active: bool, health_ok: Optional[bool]) -> bool:
    """Lock-in: health_ok outweighs active when health was probed."""
    if health_ok is not None:
        return bool(health_ok)
    return bool(active)


def detect_mesh() -> dict[str, Any]:
    """Best-effort LAN + Tailscale addresses from the collector host."""
    lan_ip: Optional[str] = None
    tailscale_ip: Optional[str] = None

    # hostname -I style
    try:
        proc = subprocess.run(
            ["hostname", "-I"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            for tok in proc.stdout.split():
                if tok.startswith("100."):
                    tailscale_ip = tailscale_ip or tok
                elif tok.startswith("192.168.") or tok.startswith("10."):
                    lan_ip = lan_ip or tok
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    try:
        proc = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            tailscale_ip = proc.stdout.strip().splitlines()[0].strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    if not lan_ip:
        try:
            # UDP connect trick — no packets sent
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                lan_ip = s.getsockname()[0]
            finally:
                s.close()
        except OSError:
            pass

    return {
        "lan_ip": lan_ip,
        "tailscale_ip": tailscale_ip,
        "reachable_lan": bool(lan_ip),
        "reachable_tailscale": bool(tailscale_ip),
    }


def detect_host() -> tuple[str, str]:
    """Return (host, host_role). Prod Pi is prism-gateway."""
    host = socket.gethostname().split(".")[0]
    env_role = (os.environ.get("HEARTBEAT_HOST_ROLE") or "").strip().lower()
    if env_role in ("prod", "dev"):
        role = env_role
    elif host in ("prism-gateway", "prism"):
        role = "prod"
    else:
        role = "dev"
    env_host = (os.environ.get("HEARTBEAT_HOST") or "").strip()
    if env_host:
        host = env_host
    return host, role


def collect_service(spec: dict[str, Any]) -> dict[str, Any]:
    unit = str(spec.get("unit") or "")
    port = int(spec.get("port") or 0)
    health_path = str(spec.get("health_path") or "/api/health")
    active, unit_state = unit_is_active(unit)
    health_url = f"http://127.0.0.1:{port}{health_path}" if port else ""
    health_ok: Optional[bool] = None
    latency_ms: Optional[int] = None
    note = ""
    if health_url:
        health_ok, latency_ms, note = probe_health(health_url)
    if not active and unit_state and unit_state != "active":
        unit_note = f"unit:{unit_state}"
        note = f"{note}; {unit_note}" if note else unit_note

    return {
        "name": spec["name"],
        "unit": unit,
        "active": active,
        "port": port or None,
        "health_url": health_url or None,
        "health_ok": health_ok,
        "latency_ms": latency_ms,
        "note": note,
        "severity": spec.get("severity") or "yellow",
        "optional_unit": bool(spec.get("optional_unit")),
    }


def build_degraded(services: list[dict[str, Any]]) -> list[dict[str, Any]]:
    degraded: list[dict[str, Any]] = []
    for svc in services:
        healthy = service_healthy(
            active=bool(svc.get("active")),
            health_ok=svc.get("health_ok"),
        )
        if healthy:
            continue
        # optional missing unit + no listener: skip noise for b2-style
        if svc.get("optional_unit") and not svc.get("active") and svc.get("health_ok") is False:
            note = str(svc.get("note") or "")
            if "url_error" in note or "http_" in note or "unit:" in note:
                # still report yellow if we listed it
                pass
        sev = "red" if svc.get("severity") == "critical" else "yellow"
        reasons = []
        if svc.get("health_ok") is False:
            reasons.append("health_ok=false")
        if not svc.get("active"):
            reasons.append("unit_inactive")
        if svc.get("note"):
            reasons.append(str(svc["note"]))
        degraded.append(
            {
                "service": svc["name"],
                "reason": "; ".join(reasons) or "unhealthy",
                "severity": sev,
            }
        )
    return degraded


def overall_ok(services: list[dict[str, Any]], degraded: list[dict[str, Any]]) -> bool:
    """ok is false if any critical service is unhealthy (red degraded)."""
    for d in degraded:
        if d.get("severity") == "red":
            return False
    # belt-and-suspenders: re-check critical rows
    for svc in services:
        if svc.get("severity") != "critical":
            continue
        if not service_healthy(
            active=bool(svc.get("active")), health_ok=svc.get("health_ok")
        ):
            return False
    return True


def collect_heartbeat(
    workspace: Optional[Path] = None,
    *,
    watchlist: Optional[list[dict[str, Any]]] = None,
    as_of: Optional[str] = None,
    mesh: Optional[dict[str, Any]] = None,
    host: Optional[str] = None,
    host_role: Optional[str] = None,
) -> dict[str, Any]:
    """Run one collection pass and return the schema_version 1 document."""
    det_host, det_role = detect_host()
    host = host or det_host
    host_role = host_role or det_role
    specs = watchlist if watchlist is not None else WATCHLIST
    services = [collect_service(s) for s in specs]
    # Strip internal-only fields before emit
    public_services = []
    for s in services:
        public_services.append(
            {
                "name": s["name"],
                "unit": s["unit"],
                "active": s["active"],
                "port": s["port"],
                "health_url": s["health_url"],
                "health_ok": s["health_ok"],
                "latency_ms": s["latency_ms"],
                "note": s.get("note") or "",
            }
        )
    # attach severity back only for degraded calc
    for pub, full in zip(public_services, services):
        full["_pub"] = pub
    degraded = build_degraded(services)
    ok = overall_ok(services, degraded)
    mesh_info = mesh if mesh is not None else detect_mesh()
    notes: list[str] = []
    if workspace is not None:
        notes.append(f"workspace={workspace}")

    return {
        "schema_version": SCHEMA_VERSION,
        "host": host,
        "host_role": host_role,
        "as_of": as_of or utc_now_iso(),
        "ok": ok,
        "collector": {"name": COLLECTOR_NAME, "version": COLLECTOR_VERSION},
        "services": public_services,
        "mesh": mesh_info,
        "notes": notes,
        "degraded": degraded,
    }


def write_heartbeat(
    workspace: Path,
    *,
    payload: Optional[dict[str, Any]] = None,
    **collect_kwargs: Any,
) -> dict[str, Any]:
    """Collect (if needed) and atomically write latest.json under workspace."""
    doc = payload if payload is not None else collect_heartbeat(workspace, **collect_kwargs)
    path = latest_path(workspace)
    atomic_write_json(path, doc)
    return doc


def load_heartbeat(workspace: Path) -> Optional[dict[str, Any]]:
    path = latest_path(workspace)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def heartbeat_api_payload(workspace: Path) -> dict[str, Any]:
    """Shape returned by GET /api/heartbeat."""
    doc = load_heartbeat(workspace)
    path = latest_path(workspace)
    if doc is None:
        return {
            "ok": False,
            "available": False,
            "error": "heartbeat_missing",
            "path": str(path),
            "hint": "Run pi-heartbeat.service / orchestra.heartbeat.write_heartbeat",
        }
    age_s: Optional[float] = None
    as_of = doc.get("as_of")
    if isinstance(as_of, str):
        try:
            ts = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_s = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
        except ValueError:
            age_s = None
    return {
        "ok": bool(doc.get("ok")),
        "available": True,
        "age_seconds": age_s,
        "path": str(path),
        "heartbeat": doc,
    }
