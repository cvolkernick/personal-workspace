"""Smart-plug adapters: TP-Link Kasa (local) and VeSync (cloud).

Registry entries (in bulbs.json) examples:

  "plantlight": {
    "type": "kasa",
    "ip": "192.168.100.116",
    "mac": "d807b6a69ec2",
    "label": "Plant Light"
  }

  "vesync_plug": {
    "type": "vesync",
    "device_name": "Outlet",   # VeSync app display name (preferred)
    "cid": null,              # optional stable cloud id
    "label": "VeSync Plug"
  }

VeSync credentials (never commit):
  env: VESYNC_EMAIL, VESYNC_PASSWORD
  or iot/secrets.json → {"vesync": {"email": "...", "password": "..."}}
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

IOT_DIR = Path(__file__).resolve().parent
DEFAULT_SECRETS_PATH = IOT_DIR / "secrets.json"

# Cache VeSync managers per event loop (asyncio.run closes loops each request
# if a persistent loop isn't used — never reuse a manager across loops).
_VESYNC_BY_LOOP: dict[int, tuple[Any, str]] = {}
_VESYNC_LOCKS: dict[int, asyncio.Lock] = {}


def is_plug_type(dtype: Optional[str]) -> bool:
    return (dtype or "").lower() in ("kasa", "vesync", "plug", "outlet")


def color_to_power(color: str) -> str:
    """Map light color intent → plug on/off. 'off' → off; anything else → on."""
    key = (color or "").strip().lower()
    return "off" if key in ("off", "0", "false", "dark") else "on"


def load_secrets(path: Optional[Path] = None) -> dict[str, Any]:
    p = path or DEFAULT_SECRETS_PATH
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def vesync_credentials(
    secrets: Optional[Mapping[str, Any]] = None,
) -> tuple[Optional[str], Optional[str]]:
    email = (os.environ.get("VESYNC_EMAIL") or "").strip()
    password = (os.environ.get("VESYNC_PASSWORD") or "").strip()
    if email and password:
        return email, password
    sec = dict(secrets) if secrets is not None else load_secrets()
    vs = sec.get("vesync") or {}
    if isinstance(vs, dict):
        email = str(vs.get("email") or vs.get("username") or "").strip()
        password = str(vs.get("password") or "").strip()
        if email and password:
            return email, password
    return None, None


# ── Kasa (local LAN) ─────────────────────────────────────────────────────────


async def kasa_get_state(ip: str, mac: Optional[str] = None) -> dict[str, Any]:
    try:
        from kasa import Device
    except ImportError:
        return {
            "ok": False,
            "ip": ip,
            "error": "python-kasa not installed (pip install python-kasa)",
        }
    try:
        dev = await Device.connect(host=ip)
        try:
            await dev.update()
            return {
                "ok": True,
                "ip": ip,
                "mac": (getattr(dev, "mac", None) or mac or "").replace(":", "").lower()
                or None,
                "on": bool(dev.is_on),
                "brightness": None,
                "rgb": None,
                "label": getattr(dev, "alias", None),
                "model": getattr(dev, "model", None),
                "type": "kasa",
            }
        finally:
            # disconnect if available
            close = getattr(dev, "disconnect", None)
            if callable(close):
                await close()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "ip": ip, "error": f"{type(e).__name__}: {e}"}


async def kasa_set_power(ip: str, on: bool, mac: Optional[str] = None) -> dict[str, Any]:
    try:
        from kasa import Device
    except ImportError:
        return {
            "ok": False,
            "ip": ip,
            "error": "python-kasa not installed (pip install python-kasa)",
        }
    try:
        dev = await Device.connect(host=ip)
        try:
            await dev.update()
            if on:
                await dev.turn_on()
            else:
                await dev.turn_off()
            await dev.update()
            return {
                "ok": True,
                "ip": ip,
                "action": "on" if on else "off",
                "on": bool(dev.is_on),
                "type": "kasa",
            }
        finally:
            close = getattr(dev, "disconnect", None)
            if callable(close):
                await close()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "ip": ip, "error": f"{type(e).__name__}: {e}"}


# ── VeSync (cloud) ───────────────────────────────────────────────────────────


async def _vesync_manager() -> tuple[Any, Optional[str]]:
    """Return (manager, error). Caches successful login per event loop."""
    email, password = vesync_credentials()
    if not email or not password:
        return None, (
            "VeSync credentials missing — set VESYNC_EMAIL/VESYNC_PASSWORD "
            "or iot/secrets.json {\"vesync\":{\"email\":\"…\",\"password\":\"…\"}}"
        )
    try:
        from pyvesync import VeSync
    except ImportError:
        return None, "pyvesync not installed (pip install pyvesync)"

    loop = asyncio.get_running_loop()
    key = id(loop)
    lock = _VESYNC_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _VESYNC_BY_LOOP.get(key)
        if cached is not None and cached[1] == email:
            return cached[0], None
        # Drop caches for other (likely closed) loops
        for old_key in list(_VESYNC_BY_LOOP):
            if old_key != key:
                _VESYNC_BY_LOOP.pop(old_key, None)
                _VESYNC_LOCKS.pop(old_key, None)
        try:
            manager = VeSync(email, password)
            ok = await manager.login()
            if not ok:
                return None, "VeSync login failed (check email/password / 2FA off)"
            await manager.get_devices()
            _VESYNC_BY_LOOP[key] = (manager, email)
            return manager, None
        except Exception as e:  # noqa: BLE001
            _VESYNC_BY_LOOP.pop(key, None)
            return None, f"{type(e).__name__}: {e}"


def _match_vesync_outlet(
    manager: Any, *, device_name: Optional[str], cid: Optional[str]
) -> Any:
    outlets = list(getattr(getattr(manager, "devices", None), "outlets", None) or [])
    if cid:
        for o in outlets:
            if str(getattr(o, "cid", "") or "") == str(cid):
                return o
    if device_name:
        want = device_name.strip().lower()
        for o in outlets:
            dn = str(getattr(o, "device_name", None) or getattr(o, "name", "") or "")
            if dn.strip().lower() == want:
                return o
        # partial
        for o in outlets:
            dn = str(getattr(o, "device_name", None) or getattr(o, "name", "") or "")
            if want in dn.strip().lower():
                return o
    if len(outlets) == 1:
        return outlets[0]
    return None


async def vesync_list_outlets() -> dict[str, Any]:
    manager, err = await _vesync_manager()
    if err:
        return {"ok": False, "error": err, "outlets": []}
    await manager.get_devices()
    outlets = list(getattr(manager.devices, "outlets", None) or [])
    out = []
    for o in outlets:
        out.append(
            {
                "device_name": getattr(o, "device_name", None) or getattr(o, "name", None),
                "cid": getattr(o, "cid", None),
                "model": getattr(o, "device_type", None) or getattr(o, "model", None),
                "status": getattr(o, "device_status", None),
                "is_on": bool(
                    getattr(o, "is_on", None)
                    if getattr(o, "is_on", None) is not None
                    else str(getattr(o, "device_status", "")).lower() == "on"
                ),
            }
        )
    return {"ok": True, "outlets": out, "count": len(out)}


async def vesync_get_state(
    *,
    device_name: Optional[str] = None,
    cid: Optional[str] = None,
) -> dict[str, Any]:
    manager, err = await _vesync_manager()
    if err:
        return {"ok": False, "error": err}
    try:
        await manager.update()
    except Exception:
        await manager.get_devices()
    o = _match_vesync_outlet(manager, device_name=device_name, cid=cid)
    if o is None:
        names = [
            getattr(x, "device_name", None) or getattr(x, "name", "?")
            for x in (getattr(manager.devices, "outlets", None) or [])
        ]
        return {
            "ok": False,
            "error": f"VeSync outlet not found (name={device_name!r} cid={cid!r}; have {names})",
        }
    try:
        # refresh single device if possible
        upd = getattr(o, "update", None)
        if callable(upd):
            await upd()
    except Exception:  # noqa: BLE001
        pass
    is_on = getattr(o, "is_on", None)
    if is_on is None:
        is_on = str(getattr(o, "device_status", "")).lower() in ("on", "1", "true")
    return {
        "ok": True,
        "on": bool(is_on),
        "brightness": None,
        "rgb": None,
        "label": getattr(o, "device_name", None) or getattr(o, "name", None),
        "cid": getattr(o, "cid", None),
        "model": getattr(o, "device_type", None) or getattr(o, "model", None),
        "type": "vesync",
    }


def _vesync_is_on(outlet: Any) -> bool:
    is_on = getattr(outlet, "is_on", None)
    if is_on is not None:
        return bool(is_on)
    return str(getattr(outlet, "device_status", "")).lower() in ("on", "1", "true")


async def _vesync_refresh_outlet(
    manager: Any, *, device_name: Optional[str], cid: Optional[str]
) -> Any:
    """Reload device list / state and re-match outlet (fresh cloud view)."""
    try:
        if hasattr(manager, "update"):
            await manager.update()
        else:
            await manager.get_devices()
    except Exception:  # noqa: BLE001
        try:
            await manager.get_devices()
        except Exception:  # noqa: BLE001
            pass
    o = _match_vesync_outlet(manager, device_name=device_name, cid=cid)
    if o is None:
        return None
    try:
        upd = getattr(o, "update", None)
        if callable(upd):
            await upd()
    except Exception:  # noqa: BLE001
        pass
    return o


async def vesync_set_power(
    on: bool,
    *,
    device_name: Optional[str] = None,
    cid: Optional[str] = None,
    attempts: int = 3,
    settle_seconds: float = 1.5,
) -> dict[str, Any]:
    """Turn a VeSync outlet on/off with delayed re-read and retries.

    VeSync often ACKs immediately while the physical plug lags or never
    switches; we wait, re-fetch devices, and re-command until state matches.
    """
    import asyncio

    manager, err = await _vesync_manager()
    if err:
        return {"ok": False, "error": err}

    last_on: Optional[bool] = None
    label: Optional[str] = None
    out_cid: Optional[str] = cid
    last_cmd_ok: Any = None

    try:
        for attempt in range(max(1, int(attempts))):
            o = await _vesync_refresh_outlet(
                manager, device_name=device_name, cid=cid
            )
            if o is None:
                return {
                    "ok": False,
                    "error": (
                        f"VeSync outlet not found "
                        f"(name={device_name!r} cid={cid!r})"
                    ),
                    "type": "vesync",
                }
            label = getattr(o, "device_name", None) or getattr(o, "name", None)
            out_cid = getattr(o, "cid", None) or cid

            # Force-cycle when cloud already claims the desired state but we
            # still need a reliable edge (stale "on" is a common failure mode).
            if on:
                if _vesync_is_on(o):
                    last_cmd_ok = await o.turn_off()
                    await asyncio.sleep(0.6)
                    o = await _vesync_refresh_outlet(
                        manager, device_name=device_name, cid=cid
                    ) or o
                last_cmd_ok = await o.turn_on()
            else:
                if not _vesync_is_on(o):
                    last_cmd_ok = await o.turn_on()
                    await asyncio.sleep(0.6)
                    o = await _vesync_refresh_outlet(
                        manager, device_name=device_name, cid=cid
                    ) or o
                last_cmd_ok = await o.turn_off()

            if last_cmd_ok is False:
                continue

            await asyncio.sleep(float(settle_seconds))
            o2 = await _vesync_refresh_outlet(
                manager, device_name=device_name, cid=cid
            )
            if o2 is None:
                continue
            last_on = _vesync_is_on(o2)
            label = getattr(o2, "device_name", None) or label
            out_cid = getattr(o2, "cid", None) or out_cid
            if last_on == on:
                return {
                    "ok": True,
                    "action": "on" if on else "off",
                    "on": last_on,
                    "type": "vesync",
                    "label": label,
                    "cid": out_cid,
                    "verified": True,
                    "attempts": attempt + 1,
                }

        return {
            "ok": False,
            "error": (
                f"VeSync still {'on' if last_on else 'off'} after {attempts} "
                f"attempt(s) (wanted {'on' if on else 'off'})"
            ),
            "action": "on" if on else "off",
            "on": last_on,
            "type": "vesync",
            "label": label,
            "cid": out_cid,
            "verified": False,
            "attempts": attempts,
            "cmd_ok": last_cmd_ok,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "type": "vesync"}


async def control_plug_device(
    info: Mapping[str, Any],
    *,
    action: str,
) -> dict[str, Any]:
    """action is 'on' or 'off'. info is registry entry (with name)."""
    dtype = str(info.get("type") or "").lower()
    name = info.get("name")
    on = action != "off"
    if dtype == "kasa":
        ip = info.get("ip")
        if not ip:
            return {"ok": False, "name": name, "error": "kasa device missing ip"}
        r = await kasa_set_power(str(ip), on, mac=info.get("mac"))
        r = dict(r)
        r.setdefault("name", name)
        return r
    if dtype == "vesync":
        r = await vesync_set_power(
            on,
            device_name=str(info.get("device_name") or info.get("label") or name or ""),
            cid=info.get("cid"),
        )
        r = dict(r)
        r.setdefault("name", name)
        return r
    return {"ok": False, "name": name, "error": f"unknown plug type: {dtype}"}


async def status_plug_device(info: Mapping[str, Any]) -> dict[str, Any]:
    dtype = str(info.get("type") or "").lower()
    if dtype == "kasa":
        ip = info.get("ip")
        if not ip:
            return {"ok": False, "error": "kasa device missing ip"}
        return await kasa_get_state(str(ip), mac=info.get("mac"))
    if dtype == "vesync":
        return await vesync_get_state(
            device_name=str(
                info.get("device_name") or info.get("label") or info.get("name") or ""
            ),
            cid=info.get("cid"),
        )
    return {"ok": False, "error": f"unknown plug type: {dtype}"}
