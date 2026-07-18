"""Best-effort LAN / mDNS discovery for additional controllable devices.

Wiz discovery lives in wiz_adapter; this module adds lightweight mDNS notes
and optional UDP port probes without requiring extra dependencies.
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
from typing import Any, Optional


# Services that often indicate smart-home / media devices
MDNS_SERVICES = (
    "_googlecast._tcp",
    "_hap._tcp",  # HomeKit Accessory Protocol
    "_airplay._tcp",
    "_raop._tcp",
    "_spotify-connect._tcp",
    "_vivint-admin._tcp",
    "_http._tcp",
)


def probe_tcp(host: str, port: int, timeout: float = 0.4) -> bool:
    """Return True if TCP connect succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def browse_mdns(
    service: str = "_services._dns-sd._udp",
    timeout: float = 1.2,
) -> list[dict[str, Any]]:
    """Browse mDNS via dns-sd (macOS) if available. Returns instance notes.

    Uses a hard wall-clock timeout around the subprocess so readline cannot hang
    the dashboard when dns-sd blocks on stdout.
    """
    if not shutil.which("dns-sd"):
        return []
    try:
        proc = subprocess.run(
            ["dns-sd", "-B", service, "local."],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        # dns-sd is long-running; timeout raises, partial output may be empty
        raw = (proc.stdout or "") + (proc.stderr or "")
        lines = raw.splitlines()
    except subprocess.TimeoutExpired as e:
        raw = ""
        if e.stdout:
            raw += e.stdout if isinstance(e.stdout, str) else e.stdout.decode("utf-8", "replace")
        if e.stderr:
            raw += e.stderr if isinstance(e.stderr, str) else e.stderr.decode("utf-8", "replace")
        lines = raw.splitlines()
    except OSError:
        return []

    results: list[dict[str, Any]] = []
    # dns-sd lines look like:
    # Timestamp A/R Flags if Domain Service Type Instance Name
    # ... Add 2 11 local. _googlecast._tcp. Nest-Audio-...
    add_re = re.compile(
        r"\bAdd\b.*?\s(local\.)\s+(_[\w-]+\._(?:tcp|udp)\.?)\s+(.+)$",
        re.I,
    )
    seen: set[str] = set()
    for line in lines:
        m = add_re.search(line)
        if not m:
            continue
        stype = m.group(2).rstrip(".")
        instance = m.group(3).strip()
        key = f"{stype}|{instance}"
        if key in seen:
            continue
        seen.add(key)
        results.append(
            {
                "id": f"mdns-{stype}-{instance}"[:80],
                "name": instance,
                "type": "mdns",
                "service": stype,
                "source": "mdns",
                "controllable": False,  # notes only unless we add a driver
                "ip": None,
                "mac": None,
            }
        )
    return results


def browse_common_mdns(timeout_each: float = 0.9) -> list[dict[str, Any]]:
    """Browse a short list of smart-home related service types.

    Keep total wall time small (~ few seconds) so /api/discover stays usable.
    """
    all_found: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Prefer the services most likely to matter; skip noisy _http by default
    for svc in (
        "_googlecast._tcp",
        "_hap._tcp",
        "_vivint-admin._tcp",
        "_airplay._tcp",
        "_spotify-connect._tcp",
    ):
        for item in browse_mdns(svc, timeout=timeout_each):
            key = item.get("id") or item.get("name")
            if key in seen:
                continue
            seen.add(str(key))
            all_found.append(item)
    return all_found


def local_ipv4_broadcast() -> Optional[str]:
    """Best-effort guess of LAN broadcast (e.g. 192.168.100.255)."""
    try:
        # UDP connect trick to pick outbound interface
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except OSError:
        return None
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    return f"{parts[0]}.{parts[1]}.{parts[2]}.255"


def lan_notes() -> dict[str, Any]:
    """Collect non-Wiz discovery notes for inventory / API."""
    mdns = browse_common_mdns()
    return {
        "ok": True,
        "mdns": mdns,
        "mdns_count": len(mdns),
        "broadcast_guess": local_ipv4_broadcast(),
        "notes": [
            "mDNS entries are observational; only type=wiz devices are controllable "
            "via this MVP without additional drivers.",
            "Google Cast / AirPlay / Vivint / HomeKit may appear but are not "
            "driven by the current control path.",
        ],
    }
