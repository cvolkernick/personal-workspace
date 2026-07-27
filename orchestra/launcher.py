"""Start subordinate dashboard servers from Orchestra when they are offline.

Only launches commands registered in domains.DOMAIN_SPECS — never arbitrary shells.
Each domain server has slightly different CLI flags; we build argv per server.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

try:
    from .domains import DOMAIN_SPECS
except ImportError:
    from domains import DOMAIN_SPECS

# Processes we started this session (domain_id -> pid)
_STARTED: dict[str, int] = {}

# How long to wait for a just-started server to accept TCP
DEFAULT_READY_TIMEOUT = 20.0
PROBE_TIMEOUT = 0.35


def _workspace_root() -> Path:
    return Path(__file__).resolve().parent.parent


def probe_port(port: int, host: str = "127.0.0.1", timeout: float = PROBE_TIMEOUT) -> bool:
    if not port:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _public_host() -> str:
    """Hostname for client-facing deep-links (Pi LAN / Tailscale)."""
    for key in (
        "ORCHESTRATOR_PUBLIC_HOST",
        "ORCHESTRA_PUBLIC_HOST",
        "DASHBOARD_PUBLIC_HOST",
    ):
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        if "://" in raw:
            host = urlparse(raw).hostname or ""
        else:
            host = raw.split("/")[0].split(":")[0]
        if host and host not in ("0.0.0.0", "127.0.0.1", "localhost"):
            return host
    return ""


def _public_url(url: str, port: int) -> str:
    """Rewrite loopback URLs to the public host when configured."""
    host = _public_host()
    if not host:
        return url or f"http://127.0.0.1:{port}/"
    if not url:
        return f"http://{host}:{port}/"
    return (
        url.replace("://127.0.0.1", f"://{host}")
        .replace("://localhost", f"://{host}")
        .replace("://[::1]", f"://{host}")
    )


def domain_spec(domain_id: str) -> Optional[dict[str, Any]]:
    did = (domain_id or "").strip().lower()
    aliases = {
        "projects": "workflow",
        "projects-dashboard": "workflow",
        "workflow": "workflow",
        "time": "holistic",
        "allocator": "holistic",
        "treasury": "finance",
        "financial-command": "finance",
        "fcc": "finance",
        "resistance": "fitness",
        "resistance-dashboard": "fitness",
        "health": "fitness",
        "home": "iot",
        "season": "horizon",
        "seasonal": "horizon",
    }
    did = aliases.get(did, did)
    for spec in DOMAIN_SPECS:
        if spec.get("id") == did:
            return spec
    return None


def launchable_domains() -> list[dict[str, Any]]:
    out = []
    for spec in DOMAIN_SPECS:
        if not spec.get("port") or not spec.get("launch"):
            continue
        port = int(spec["port"])
        out.append(
            {
                "id": spec["id"],
                "label": spec.get("label"),
                "port": port,
                "url": _public_url(spec.get("url") or f"http://127.0.0.1:{port}/", port),
                "launch": spec.get("launch"),
                "live": probe_port(port),
            }
        )
    return out


def _server_script_path(launch: str, root: Path) -> Optional[Path]:
    """Resolve 'python3 path/to/server.py' to an absolute script path under root."""
    parts = (launch or "").split()
    script = None
    for p in parts:
        if p.endswith(".py"):
            script = p
            break
    if not script:
        return None
    path = (root / script).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path


def build_launch_argv(
    domain_id: str,
    script: Path,
    port: int,
    *,
    bind_host: str = "127.0.0.1",
) -> list[str]:
    """Build the correct argv for each dashboard server (CLI flags differ)."""
    did = (domain_id or "").strip().lower()
    script_s = str(script).replace("\\", "/")
    py = sys.executable

    # resistance-dashboard: positional port only
    if did == "fitness" or "resistance-dashboard" in script_s:
        return [py, str(script), str(port)]

    # financial-command: --port --no-browser [--offline]; NO --host
    if did == "finance" or "financial-command" in script_s:
        return [py, str(script), "--port", str(port), "--no-browser"]

    # projects-dashboard: --port --no-browser [--bind HOST]
    if did == "workflow" or "projects-dashboard" in script_s:
        return [
            py,
            str(script),
            "--port",
            str(port),
            "--bind",
            bind_host,
            "--no-browser",
        ]

    # horizon / holistic / iot / default: --host --port --no-browser
    if did == "horizon" or "horizon/" in script_s or script_s.endswith("horizon/server.py"):
        return [
            py,
            str(script),
            "--host",
            bind_host,
            "--port",
            str(port),
            "--no-browser",
        ]

    return [
        py,
        str(script),
        "--host",
        bind_host,
        "--port",
        str(port),
        "--no-browser",
    ]


def _log_dir(root: Path) -> Path:
    d = root / "orchestra" / ".launch-logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rel_log(log_path: Path, root: Path) -> str:
    try:
        return str(log_path.relative_to(root))
    except ValueError:
        return str(log_path)


def ensure_domain(
    domain_id: str,
    *,
    workspace: Optional[Path] = None,
    ready_timeout: float = DEFAULT_READY_TIMEOUT,
    force_restart: bool = False,
) -> dict[str, Any]:
    """Ensure a registered domain server is listening; start it if needed.

    Returns status dict: ok, id, live, started, already_running, url, port, pid, error.
    """
    spec = domain_spec(domain_id)
    if not spec:
        return {
            "ok": False,
            "id": domain_id,
            "error": f"Unknown or non-launchable domain: {domain_id}",
        }
    if not spec.get("port") or not spec.get("launch"):
        return {
            "ok": False,
            "id": spec.get("id"),
            "label": spec.get("label"),
            "error": "Domain is files-only (no server to launch).",
        }

    root = Path(workspace or _workspace_root()).resolve()
    port = int(spec["port"])
    url = _public_url(spec.get("url") or f"http://127.0.0.1:{port}/", port)
    did = spec["id"]
    ready_timeout = max(3.0, min(float(ready_timeout), 30.0))

    if not force_restart and probe_port(port):
        return {
            "ok": True,
            "id": did,
            "label": spec.get("label"),
            "live": True,
            "started": False,
            "already_running": True,
            "url": url,
            "port": port,
            "pid": _STARTED.get(did),
            "message": f"{spec.get('label') or did} already listening on {port}",
        }

    script = _server_script_path(str(spec["launch"]), root)
    if not script:
        return {
            "ok": False,
            "id": did,
            "label": spec.get("label"),
            "live": False,
            "error": f"Server script not found for launch: {spec.get('launch')} (cwd={root})",
        }

    log_dir = _log_dir(root)
    log_path = log_dir / f"{did}.log"
    try:
        log_f = open(log_path, "a", encoding="utf-8")
        log_f.write(
            f"\n--- launch {time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"port={port} script={script} ---\n"
        )
        log_f.flush()
    except OSError as e:
        return {
            "ok": False,
            "id": did,
            "error": f"Cannot open launch log: {e}",
        }

    # Bind all interfaces when a public host is configured (Pi → Mac clients)
    bind_host = "0.0.0.0" if _public_host() else "127.0.0.1"
    cmd = build_launch_argv(did, script, port, bind_host=bind_host)
    log_f.write(f"cmd: {' '.join(cmd)}\n")
    log_f.flush()

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(root),
            stdout=log_f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    except OSError as e:
        try:
            log_f.close()
        except OSError:
            pass
        return {
            "ok": False,
            "id": did,
            "label": spec.get("label"),
            "live": False,
            "error": f"Failed to spawn: {e}",
            "log": str(log_path),
        }

    _STARTED[did] = proc.pid
    deadline = time.time() + ready_timeout
    while time.time() < deadline:
        if probe_port(port):
            try:
                log_f.close()
            except OSError:
                pass
            return {
                "ok": True,
                "id": did,
                "label": spec.get("label"),
                "live": True,
                "started": True,
                "already_running": False,
                "url": url,
                "port": port,
                "pid": proc.pid,
                "log": _rel_log(log_path, root),
                "message": f"Started {spec.get('label') or did} on port {port}",
            }
        if proc.poll() is not None:
            try:
                log_f.close()
            except OSError:
                pass
            # Read last lines of log for error detail
            detail = ""
            try:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-800:]
                detail = " " + tail.strip().splitlines()[-1] if tail.strip() else ""
            except OSError:
                pass
            return {
                "ok": False,
                "id": did,
                "label": spec.get("label"),
                "live": False,
                "started": False,
                "pid": proc.pid,
                "error": (
                    f"Process exited early (code {proc.returncode})."
                    f"{detail} See {log_path.name}"
                ),
                "log": str(log_path),
            }
        time.sleep(0.2)

    try:
        log_f.close()
    except OSError:
        pass
    live = probe_port(port)
    return {
        "ok": live,
        "id": did,
        "label": spec.get("label"),
        "live": live,
        "started": True,
        "already_running": False,
        "url": url,
        "port": port,
        "pid": proc.pid,
        "log": str(log_path),
        "error": None
        if live
        else f"Timed out waiting for port {port} after {ready_timeout:.0f}s (pid {proc.pid})",
        "message": f"Spawned pid {proc.pid}; live={live}",
    }


def status_all(*, workspace: Optional[Path] = None) -> dict[str, Any]:
    root = Path(workspace or _workspace_root()).resolve()
    domains = launchable_domains()
    return {
        "ok": True,
        "workspace": str(root),
        "domains": domains,
        "started_pids": dict(_STARTED),
    }
