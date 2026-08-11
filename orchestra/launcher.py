"""Start subordinate dashboard servers from Orchestra when they are offline.

Only launches domain ids registered in domains.DOMAIN_SPECS (or known Decide-plane
ports). Never runs arbitrary shells. CLI flags differ per dashboard server.
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

_STARTED: dict[str, int] = {}

DEFAULT_READY_TIMEOUT = 20.0
PROBE_TIMEOUT = 0.35

# Known server scripts (master DOMAIN_SPECS often use open_dashboard bash only)
DOMAIN_SERVER_SCRIPTS: dict[str, str] = {
    "workflow": "projects-dashboard/server.py",
    "finance": "financial-command/server.py",
    "fitness": "resistance-dashboard/server.py",
    "holistic": "holistic/server.py",
    "iot": "iot/server.py",
    "horizon": "research/horizon/server.py",  # Macro L0 (nav Decide · Macro)
    "horizon_macro": "research/horizon/server.py",
    "seasonal": "horizon/server.py",
    "b2": "b2-ux/server.py",
}

# Decide-plane extras not always in DOMAIN_SPECS (open URL + optional local start)
_EXTRA_SPECS: dict[str, dict[str, Any]] = {
    "horizon": {
        "id": "horizon",
        "label": "Horizon Macro",
        "port": 8795,
        "url": "http://127.0.0.1:8795/",
        "launch": "python3 research/horizon/server.py --bootstrap",
    },
    "horizon_macro": {
        "id": "horizon_macro",
        "label": "Horizon Macro",
        "port": 8795,
        "url": "http://127.0.0.1:8795/",
        "launch": "python3 research/horizon/server.py --bootstrap",
    },
    "seasonal": {
        "id": "seasonal",
        "label": "Seasonal",
        "port": 8791,
        "url": "http://127.0.0.1:8791/",
        "launch": "python3 horizon/server.py",
    },
    "b2": {
        "id": "b2",
        "label": "B2",
        "port": 8792,
        "url": "http://127.0.0.1:8792/",
        "launch": "python3 b2-ux/server.py",
    },
}

ALIASES: dict[str, str] = {
    "projects": "workflow",
    "projects-dashboard": "workflow",
    "time": "holistic",
    "allocator": "holistic",
    "treasury": "finance",
    "financial-command": "finance",
    "fcc": "finance",
    "resistance": "fitness",
    "resistance-dashboard": "fitness",
    "health": "fitness",
    "fit": "fitness",
    "fitdash": "fitness",
    "home": "iot",
    "season": "seasonal",
    "seasonal-plan": "seasonal",
    "macro": "horizon",
    "horizon-macro": "horizon_macro",
    "global-macro": "horizon_macro",
    "obsidian": "b2",
    "brain2": "b2",
    "b2-ux": "b2",
    "knowledge": "b2",
}


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


def domain_spec(domain_id: str) -> Optional[dict[str, Any]]:
    did = (domain_id or "").strip().lower()
    did = ALIASES.get(did, did)
    for spec in DOMAIN_SPECS:
        if spec.get("id") == did:
            return dict(spec)
    if did in _EXTRA_SPECS:
        return dict(_EXTRA_SPECS[did])
    return None


def _public_host() -> str:
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


def launchable_domains() -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for spec in list(DOMAIN_SPECS) + list(_EXTRA_SPECS.values()):
        did = spec.get("id")
        if not did or did in seen or did == "strategy":
            continue
        if not spec.get("port"):
            continue
        seen.add(did)
        port = int(spec["port"])
        out.append(
            {
                "id": did,
                "label": spec.get("label"),
                "port": port,
                "url": _public_url(spec.get("url") or f"http://127.0.0.1:{port}/", port),
                "launch": spec.get("launch"),
                "live": probe_port(port),
            }
        )
    return out


def status_all() -> dict[str, Any]:
    items = launchable_domains()
    return {
        "ok": True,
        "domains": items,
        "live_count": sum(1 for d in items if d.get("live")),
        "total": len(items),
    }


def _server_script_path(launch: str, root: Path, domain_id: str = "") -> Optional[Path]:
    parts = (launch or "").split()
    script = None
    for p in parts:
        if p.endswith(".py"):
            script = p
            break
    if not script and domain_id:
        script = DOMAIN_SERVER_SCRIPTS.get(domain_id)
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
    did = (domain_id or "").strip().lower()
    script_s = str(script).replace("\\", "/")
    py = sys.executable

    if did == "fitness" or "resistance-dashboard" in script_s:
        return [py, str(script), str(port)]

    if did == "finance" or "financial-command" in script_s:
        return [py, str(script), "--port", str(port), "--no-browser"]

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

    if did == "b2" or "b2-ux" in script_s:
        return [py, str(script), "--host", bind_host, "--port", str(port)]

    if (
        did in ("horizon", "horizon_macro")
        or "research/horizon" in script_s
    ):
        return [
            py,
            str(script),
            "--port",
            str(port),
            "--no-browser",
            "--bootstrap",
        ]

    if did == "seasonal" or (
        script_s.endswith("horizon/server.py") and "research/horizon" not in script_s
    ):
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


def ensure_domain(
    domain_id: str,
    *,
    workspace: Optional[Path] = None,
    ready_timeout: float = DEFAULT_READY_TIMEOUT,
    force_restart: bool = False,
) -> dict[str, Any]:
    """Ensure a registered domain server is listening; start it if needed."""
    spec = domain_spec(domain_id)
    if not spec:
        return {
            "ok": False,
            "id": domain_id,
            "error": f"Unknown or non-launchable domain: {domain_id}",
        }
    if not spec.get("port"):
        return {
            "ok": False,
            "id": spec.get("id"),
            "label": spec.get("label"),
            "error": "Domain is files-only (no server to launch).",
        }

    root = Path(workspace or _workspace_root()).resolve()
    port = int(spec["port"])
    url = _public_url(spec.get("url") or f"http://127.0.0.1:{port}/", port)
    did = str(spec["id"])

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

    script = _server_script_path(str(spec.get("launch") or ""), root, did)
    if not script:
        return {
            "ok": False,
            "id": did,
            "label": spec.get("label"),
            "live": False,
            "url": url,
            "port": port,
            "error": (
                f"Server not listening on :{port} and no local script to start. "
                "Open publicized URL if Pi unit is up, or start the domain server."
            ),
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
        return {"ok": False, "id": did, "error": f"Cannot open launch log: {e}"}

    cmd = build_launch_argv(did, script, port)
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
            "url": url,
        }

    _STARTED[did] = proc.pid
    deadline = time.time() + max(1.0, ready_timeout)
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
                "message": f"Started {spec.get('label') or did} on port {port}",
            }
        if proc.poll() is not None:
            try:
                log_f.close()
            except OSError:
                pass
            return {
                "ok": False,
                "id": did,
                "label": spec.get("label"),
                "live": False,
                "started": False,
                "pid": proc.pid,
                "error": f"Process exited early (code {proc.returncode}). See {log_path.name}",
                "log": str(log_path),
                "url": url,
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
        "error": None if live else f"Timed out waiting for :{port} after start",
        "message": (
            f"Started {spec.get('label') or did}"
            if live
            else f"Started but :{port} not ready within {ready_timeout}s"
        ),
    }
