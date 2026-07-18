"""Pure IoT control helpers (no network I/O).

Loads the bulb registry, maps color/preset intents, and merges discovery
results with configured devices. Unit-tested without live hardware.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Optional, Sequence

# Color presets (name → RGB or off)
COLORS: dict[str, Optional[tuple[int, int, int]]] = {
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "yellow": (255, 255, 0),
    "orange": (255, 128, 0),
    "purple": (128, 0, 255),
    "warm": (255, 180, 100),
    "off": None,
}

DEFAULT_BRIGHTNESS = 200
DEFAULT_PORT = 38899

IOT_DIR = Path(__file__).resolve().parent
DEFAULT_BULBS_PATH = IOT_DIR / "wiz-lights" / "bulbs.json"


def load_bulbs(path: Optional[Path | str] = None) -> dict[str, dict[str, Any]]:
    """Load configured bulbs from JSON. Returns name → {ip, mac, ...}."""
    p = Path(path) if path else DEFAULT_BULBS_PATH
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("bulbs.json must be an object of name → device")
    out: dict[str, dict[str, Any]] = {}
    for name, info in data.items():
        if not isinstance(info, dict):
            continue
        out[str(name)] = dict(info)
    return out


def list_color_presets() -> list[str]:
    """Return available preset names including 'off'."""
    return list(COLORS.keys())


def resolve_rgb(color: str) -> Optional[tuple[int, int, int]]:
    """Map a color name to RGB, or None for off. Unknown names → white."""
    key = (color or "").strip().lower()
    if key not in COLORS:
        return COLORS["white"]
    return COLORS[key]


def build_control_intent(
    target: str,
    color: str,
    brightness: int = DEFAULT_BRIGHTNESS,
    *,
    registry: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """Build a control intent from target name + color preset.

    Pure: does not touch the network. ``targets`` is the resolved list of
    device dicts to act on (empty if unknown name and not "all").
    """
    reg = dict(registry) if registry is not None else load_bulbs()
    name = (target or "").strip()
    color_key = (color or "").strip().lower() or "white"
    action = "off" if color_key == "off" else "on"
    rgb = resolve_rgb(color_key)
    bright = max(1, min(255, int(brightness)))

    if name.lower() == "all":
        targets = [dict(v, name=k) for k, v in reg.items()]
    elif name in reg:
        targets = [dict(reg[name], name=name)]
    else:
        # allow IP as target for ad-hoc control
        if _looks_like_ip(name):
            targets = [{"name": name, "ip": name, "mac": None}]
        else:
            targets = []

    return {
        "target": name,
        "color": color_key,
        "action": action,
        "rgb": list(rgb) if rgb is not None else None,
        "brightness": bright if action == "on" else None,
        "targets": targets,
        "ok": bool(targets),
        "error": None if targets else f"unknown device: {name}",
    }


def _looks_like_ip(s: str) -> bool:
    parts = s.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def list_configured_devices(
    registry: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Normalize configured bulbs into a device list for the API/UI."""
    reg = dict(registry) if registry is not None else load_bulbs()
    devices: list[dict[str, Any]] = []
    for name, info in reg.items():
        devices.append(
            {
                "id": name,
                "name": name,
                "ip": info.get("ip"),
                "mac": _norm_mac(info.get("mac")),
                "port": int(info.get("port") or DEFAULT_PORT),
                "type": info.get("type") or "wiz",
                "source": "config",
                "controllable": True,
            }
        )
    return devices


def _norm_mac(mac: Any) -> Optional[str]:
    if mac is None:
        return None
    s = str(mac).replace(":", "").replace("-", "").lower().strip()
    return s or None


def device_key(device: Mapping[str, Any]) -> str:
    """Stable identity: prefer MAC, else IP."""
    mac = _norm_mac(device.get("mac"))
    if mac:
        return f"mac:{mac}"
    ip = device.get("ip")
    if ip:
        return f"ip:{ip}"
    return f"id:{device.get('id') or device.get('name') or 'unknown'}"


def merge_devices(
    configured: Sequence[Mapping[str, Any]],
    discovered: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Merge configured + discovered devices.

    Config entries win for name/id. Discovery-only devices are appended with
    source='discovery'. Matching is by MAC first, then IP.
    """
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for dev in configured:
        d = dict(dev)
        d.setdefault("source", "config")
        d.setdefault("controllable", True)
        d.setdefault("type", "wiz")
        key = device_key(d)
        by_key[key] = d
        order.append(key)
        # also index by ip for discovery match
        if d.get("ip"):
            by_key[f"ip:{d['ip']}"] = d

    for disc in discovered:
        d = dict(disc)
        d.setdefault("source", "discovery")
        d.setdefault("controllable", d.get("type") == "wiz")
        d.setdefault("type", "wiz")
        mac = _norm_mac(d.get("mac"))
        ip = d.get("ip")
        match: Optional[dict[str, Any]] = None
        if mac and f"mac:{mac}" in by_key:
            match = by_key[f"mac:{mac}"]
        elif ip and f"ip:{ip}" in by_key:
            match = by_key[f"ip:{ip}"]

        if match is not None:
            # Enrich config with discovery IP if MAC matched and IP differs
            if ip and match.get("ip") != ip:
                match["discovered_ip"] = ip
                match["ip_mismatch"] = True
            if mac and not match.get("mac"):
                match["mac"] = mac
            match["seen_on_network"] = True
            continue

        # New device
        name = d.get("name") or d.get("id") or (f"wiz-{ip}" if ip else "unknown")
        entry = {
            "id": d.get("id") or name,
            "name": name,
            "ip": ip,
            "mac": mac,
            "port": int(d.get("port") or DEFAULT_PORT),
            "type": d.get("type") or "wiz",
            "source": "discovery",
            "controllable": bool(d.get("controllable", d.get("type") == "wiz")),
            "seen_on_network": True,
        }
        key = device_key(entry)
        if key not in by_key:
            by_key[key] = entry
            order.append(key)

    # Deduplicate order (config may have dual-indexed keys)
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for key in order:
        # Prefer mac: keys and unique objects
        dev = by_key.get(key)
        if dev is None:
            continue
        identity = id(dev)
        if identity in seen:
            continue
        # skip pure ip: index duplicates of mac-keyed config
        if key.startswith("ip:") and _norm_mac(dev.get("mac")):
            mac_key = f"mac:{_norm_mac(dev.get('mac'))}"
            if mac_key in by_key and by_key[mac_key] is dev:
                if identity not in seen:
                    # will be emitted via mac key if in order; if only ip key, emit
                    if mac_key not in order:
                        seen.add(identity)
                        result.append(dev)
                continue
        seen.add(identity)
        result.append(dev)
    return result


def apply_control_results(
    intent: Mapping[str, Any],
    results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Combine a control intent with per-target network results into API shape."""
    ok_all = bool(intent.get("ok")) and all(r.get("ok") for r in results) if results else False
    return {
        "ok": ok_all,
        "target": intent.get("target"),
        "color": intent.get("color"),
        "action": intent.get("action"),
        "rgb": intent.get("rgb"),
        "brightness": intent.get("brightness"),
        "results": list(results),
        "error": intent.get("error")
        if not intent.get("ok")
        else (None if ok_all else "one or more targets failed"),
    }


def summarize_registry(registry: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Short inventory summary for health/docs."""
    return {
        "count": len(registry),
        "names": list(registry.keys()),
        "ips": [v.get("ip") for v in registry.values()],
    }
