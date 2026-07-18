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
DEFAULT_GROUPS_PATH = IOT_DIR / "groups.json"


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


def load_groups(path: Optional[Path | str] = None) -> dict[str, dict[str, Any]]:
    """Load named groups → {label, members: [device ids]}."""
    p = Path(path) if path else DEFAULT_GROUPS_PATH
    if not p.is_file():
        return {}
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("groups.json must be an object")
    out: dict[str, dict[str, Any]] = {}
    for gid, info in data.items():
        if not isinstance(info, dict):
            continue
        members = info.get("members") or []
        out[str(gid)] = {
            "id": str(gid),
            "label": str(info.get("label") or gid),
            "members": [str(m) for m in members],
        }
    return out


def list_groups(
    groups: Optional[Mapping[str, Mapping[str, Any]]] = None,
    registry: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Groups with member lists filtered to devices present in the registry."""
    gmap = dict(groups) if groups is not None else load_groups()
    reg = dict(registry) if registry is not None else load_bulbs()
    out: list[dict[str, Any]] = []
    for gid, info in gmap.items():
        members = [m for m in (info.get("members") or []) if m in reg]
        missing = [m for m in (info.get("members") or []) if m not in reg]
        out.append(
            {
                "id": gid,
                "label": info.get("label") or gid,
                "members": members,
                "missing": missing,
                "count": len(members),
            }
        )
    return out


def expand_target_members(
    target: str,
    *,
    registry: Optional[Mapping[str, Mapping[str, Any]]] = None,
    groups: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> list[str]:
    """Resolve a control target to device names (empty if unknown)."""
    reg = dict(registry) if registry is not None else load_bulbs()
    gmap = dict(groups) if groups is not None else load_groups()
    name = (target or "").strip()
    if not name:
        return []
    low = name.lower()
    if low == "all":
        return list(reg.keys())
    # group:entryway or bare group id
    if low.startswith("group:"):
        gid = name.split(":", 1)[1].strip()
        info = gmap.get(gid) or gmap.get(gid.lower())
        if not info:
            return []
        return [m for m in (info.get("members") or []) if m in reg]
    if name in gmap or low in gmap:
        info = gmap.get(name) or gmap.get(low)
        assert info is not None
        return [m for m in (info.get("members") or []) if m in reg]
    if name in reg:
        return [name]
    return []


def list_color_presets() -> list[str]:
    """Return available preset names including 'off'."""
    return list(COLORS.keys())


def resolve_rgb(color: str) -> Optional[tuple[int, int, int]]:
    """Map a color name to RGB, or None for off. Unknown names → white."""
    key = (color or "").strip().lower()
    if key not in COLORS:
        return COLORS["white"]
    return COLORS[key]


def _looks_like_ip(s: str) -> bool:
    parts = s.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def extract_ip_from_target(target: str) -> Optional[str]:
    """Pull an IPv4 from a bare IP or discovery-style id like ``wiz-192.168.1.5``."""
    name = (target or "").strip()
    if not name:
        return None
    if _looks_like_ip(name):
        return name
    # discovery ids: wiz-{ip}, wiz-192-168-1-5, etc.
    if name.lower().startswith("wiz-"):
        rest = name[4:]
        if _looks_like_ip(rest):
            return rest
        dotted = rest.replace("-", ".")
        if _looks_like_ip(dotted):
            return dotted
    # last-ditch: first IPv4-looking token in the string
    for token in name.replace("_", " ").replace("/", " ").split():
        if _looks_like_ip(token):
            return token
    return None


def control_target_for_device(device: Mapping[str, Any]) -> str:
    """Best target string for UI/API control of a device dict.

    Prefer registry id/name when present in config; for discovery-only Wiz
    devices prefer bare IP so ``build_control_intent`` can resolve them.
    """
    source = (device.get("source") or "config").lower()
    dev_id = str(device.get("id") or device.get("name") or "").strip()
    ip = device.get("ip")
    if source == "config" and dev_id:
        return dev_id
    if ip and _looks_like_ip(str(ip)):
        return str(ip)
    if dev_id:
        extracted = extract_ip_from_target(dev_id)
        if extracted:
            return extracted
        return dev_id
    return str(ip or "")


def build_control_intent(
    target: str,
    color: str,
    brightness: int = DEFAULT_BRIGHTNESS,
    *,
    registry: Optional[Mapping[str, Mapping[str, Any]]] = None,
    groups: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """Build a control intent from target name + color preset.

    Pure: does not touch the network. ``targets`` is the resolved list of
    device dicts to act on (empty if unknown name and not "all").

    Accepts: registry names, group ids (``entryway``, ``livingroom``),
    ``group:<id>``, ``all``, bare IPv4, and discovery ids ``wiz-{ip}``.
    """
    reg = dict(registry) if registry is not None else load_bulbs()
    gmap = dict(groups) if groups is not None else load_groups()
    name = (target or "").strip()
    color_key = (color or "").strip().lower() or "white"
    action = "off" if color_key == "off" else "on"
    rgb = resolve_rgb(color_key)
    bright = max(1, min(255, int(brightness)))

    member_names = expand_target_members(name, registry=reg, groups=gmap)
    if member_names:
        targets = [dict(reg[m], name=m) for m in member_names if m in reg]
    else:
        # Bare IP, wiz-{ip}, or other discovery-style target
        ip = extract_ip_from_target(name)
        if ip:
            match_name = None
            match_info: Optional[Mapping[str, Any]] = None
            for k, v in reg.items():
                if str(v.get("ip") or "") == ip:
                    match_name = k
                    match_info = v
                    break
            if match_info is not None:
                targets = [dict(match_info, name=match_name)]
            else:
                targets = [{"name": name, "ip": ip, "mac": None}]
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
