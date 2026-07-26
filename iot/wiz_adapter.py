"""Network adapter around pywizlight (injectable for tests).

Live path uses pywizlight; unit tests inject a FakeTransport.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional, Protocol, Sequence

from iot.control import (
    DEFAULT_BRIGHTNESS,
    DEFAULT_PORT,
    build_control_intent,
    apply_control_results,
    list_configured_devices,
    load_bulbs,
    load_groups,
    merge_devices,
)


class LightTransport(Protocol):
    """Minimal async transport for Wiz-like devices."""

    async def turn_on(
        self, ip: str, mac: Optional[str], rgb: Optional[Sequence[int]], brightness: int
    ) -> dict[str, Any]:
        ...

    async def turn_off(self, ip: str, mac: Optional[str]) -> dict[str, Any]:
        ...

    async def get_state(self, ip: str, mac: Optional[str]) -> dict[str, Any]:
        ...

    async def discover(
        self, broadcast: str = "255.255.255.255", wait_time: float = 5.0
    ) -> list[dict[str, Any]]:
        ...


def _normalize_pilot(state: Any) -> Optional[Any]:
    """pywizlight 0.6.x may return list[PilotParser] from updateState."""
    if state is None:
        return None
    if isinstance(state, list):
        return state[0] if state else None
    return state


class PyWizTransport:
    """Real pywizlight-backed transport."""

    async def turn_on(
        self, ip: str, mac: Optional[str], rgb: Optional[Sequence[int]], brightness: int
    ) -> dict[str, Any]:
        from pywizlight import PilotBuilder, wizlight

        wl = wizlight(ip, DEFAULT_PORT, mac)
        if rgb is None:
            await wl.turn_on(PilotBuilder(brightness=brightness))
        else:
            await wl.turn_on(
                PilotBuilder(rgb=tuple(int(c) for c in rgb), brightness=int(brightness))
            )
        return await self._confirm_power(
            wl, ip, want_on=True, action="on", brightness=brightness, rgb=rgb
        )

    async def turn_off(self, ip: str, mac: Optional[str]) -> dict[str, Any]:
        from pywizlight import wizlight

        wl = wizlight(ip, DEFAULT_PORT, mac)
        await wl.turn_off()
        return await self._confirm_power(wl, ip, want_on=False, action="off")

    async def _confirm_power(
        self,
        wl: Any,
        ip: str,
        *,
        want_on: bool,
        action: str,
        brightness: Optional[int] = None,
        rgb: Optional[Sequence[int]] = None,
        attempts: int = 2,
    ) -> dict[str, Any]:
        """Re-read pilot after command; retry once if state mismatches."""
        last_on: Optional[bool] = None
        for i in range(attempts):
            try:
                raw = await asyncio.wait_for(wl.updateState(), timeout=4.0)
                pilot = _normalize_pilot(raw) or _normalize_pilot(
                    getattr(wl, "state", None)
                )
                if pilot is not None:
                    last_on = bool(pilot.get_state())
                    if last_on == want_on:
                        return {
                            "ok": True,
                            "ip": ip,
                            "action": action,
                            "on": last_on,
                            "verified": True,
                        }
                    if i + 1 < attempts:
                        if want_on:
                            from pywizlight import PilotBuilder

                            bri = int(brightness or DEFAULT_BRIGHTNESS)
                            if rgb is None:
                                await wl.turn_on(PilotBuilder(brightness=bri))
                            else:
                                await wl.turn_on(
                                    PilotBuilder(
                                        rgb=tuple(int(c) for c in rgb),
                                        brightness=bri,
                                    )
                                )
                        else:
                            await wl.turn_off()
                        await asyncio.sleep(0.35)
                        continue
            except Exception as e:  # noqa: BLE001
                if i + 1 >= attempts:
                    return {
                        "ok": False,
                        "ip": ip,
                        "action": action,
                        "error": f"verify failed: {type(e).__name__}: {e}",
                        "verified": False,
                    }
                await asyncio.sleep(0.35)
        return {
            "ok": False,
            "ip": ip,
            "action": action,
            "on": last_on,
            "error": (
                f"Wiz still {'on' if last_on else 'off'} after {action} "
                f"(wanted {'on' if want_on else 'off'})"
            ),
            "verified": False,
        }

    async def get_state(self, ip: str, mac: Optional[str]) -> dict[str, Any]:
        from pywizlight import wizlight

        wl = wizlight(ip, DEFAULT_PORT, mac)
        try:
            raw = await asyncio.wait_for(wl.updateState(), timeout=4.0)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "ip": ip, "error": f"{type(e).__name__}: {e}"}
        pilot = _normalize_pilot(raw)
        if pilot is None:
            # fall back to wl.state
            pilot = _normalize_pilot(getattr(wl, "state", None))
        if pilot is None:
            return {"ok": False, "ip": ip, "error": "no state returned"}
        try:
            on = bool(pilot.get_state())
            brightness = pilot.get_brightness()
            rgb = pilot.get_rgb()
            mac_out = pilot.get_mac() if hasattr(pilot, "get_mac") else mac
            return {
                "ok": True,
                "ip": ip,
                "mac": mac_out,
                "on": on,
                "brightness": brightness,
                "rgb": list(rgb) if rgb else None,
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "ip": ip, "error": f"{type(e).__name__}: {e}"}

    async def discover(
        self, broadcast: str = "255.255.255.255", wait_time: float = 5.0
    ) -> list[dict[str, Any]]:
        from pywizlight import discovery

        bulbs = await discovery.discover_lights(
            broadcast_space=broadcast, wait_time=wait_time
        )
        out: list[dict[str, Any]] = []
        for b in bulbs or []:
            ip = getattr(b, "ip", None)
            mac = getattr(b, "mac", None)
            port = getattr(b, "port", DEFAULT_PORT) or DEFAULT_PORT
            out.append(
                {
                    "id": f"wiz-{ip}",
                    "name": f"wiz-{ip}",
                    "ip": ip,
                    "mac": str(mac).replace(":", "").lower() if mac else None,
                    "port": int(port),
                    "type": "wiz",
                    "source": "discovery",
                    "controllable": True,
                }
            )
        return out


class FakeTransport:
    """In-memory transport for unit tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.states: dict[str, dict[str, Any]] = {}
        self.discover_result: list[dict[str, Any]] = []
        self.fail_ips: set[str] = set()

    async def turn_on(
        self, ip: str, mac: Optional[str], rgb: Optional[Sequence[int]], brightness: int
    ) -> dict[str, Any]:
        self.calls.append(
            {"op": "on", "ip": ip, "mac": mac, "rgb": list(rgb) if rgb else None, "brightness": brightness}
        )
        if ip in self.fail_ips:
            return {"ok": False, "ip": ip, "error": "simulated failure"}
        self.states[ip] = {
            "ok": True,
            "ip": ip,
            "mac": mac,
            "on": True,
            "brightness": brightness,
            "rgb": list(rgb) if rgb else None,
        }
        return {"ok": True, "ip": ip, "action": "on"}

    async def turn_off(self, ip: str, mac: Optional[str]) -> dict[str, Any]:
        self.calls.append({"op": "off", "ip": ip, "mac": mac})
        if ip in self.fail_ips:
            return {"ok": False, "ip": ip, "error": "simulated failure"}
        self.states[ip] = {
            "ok": True,
            "ip": ip,
            "mac": mac,
            "on": False,
            "brightness": None,
            "rgb": None,
        }
        return {"ok": True, "ip": ip, "action": "off"}

    async def get_state(self, ip: str, mac: Optional[str]) -> dict[str, Any]:
        self.calls.append({"op": "state", "ip": ip, "mac": mac})
        if ip in self.fail_ips:
            return {"ok": False, "ip": ip, "error": "simulated failure"}
        return self.states.get(
            ip,
            {"ok": True, "ip": ip, "mac": mac, "on": False, "brightness": None, "rgb": None},
        )

    async def discover(
        self, broadcast: str = "255.255.255.255", wait_time: float = 5.0
    ) -> list[dict[str, Any]]:
        self.calls.append({"op": "discover", "broadcast": broadcast, "wait_time": wait_time})
        return list(self.discover_result)


def get_default_transport() -> LightTransport:
    return PyWizTransport()


async def execute_control(
    target: str,
    color: str,
    brightness: int = DEFAULT_BRIGHTNESS,
    *,
    registry: Optional[dict] = None,
    groups: Optional[dict] = None,
    transport: Optional[LightTransport] = None,
) -> dict[str, Any]:
    """Build intent and run network control via transport (Wiz) or plug adapters."""
    from iot.plugs import control_plug_device, is_plug_type

    reg = registry if registry is not None else load_bulbs()
    gmap = groups if groups is not None else load_groups()
    intent = build_control_intent(
        target, color, brightness, registry=reg, groups=gmap
    )
    if not intent["ok"]:
        return apply_control_results(intent, [])

    t = transport or get_default_transport()
    results: list[dict[str, Any]] = []
    for dev in intent["targets"]:
        ip = dev.get("ip")
        mac = dev.get("mac")
        name = dev.get("name")
        dtype = str(dev.get("type") or "wiz").lower()
        try:
            if is_plug_type(dtype):
                r = await control_plug_device(dev, action=intent["action"])
                r = dict(r)
                r.setdefault("name", name)
                results.append(r)
                continue
            if not ip:
                results.append({"ok": False, "name": name, "error": "missing ip"})
                continue
            if intent["action"] == "off":
                r = await t.turn_off(ip, mac)
            else:
                r = await t.turn_on(
                    ip, mac, intent["rgb"], intent["brightness"] or DEFAULT_BRIGHTNESS
                )
            r = dict(r)
            r.setdefault("name", name)
            results.append(r)
        except Exception as e:  # noqa: BLE001
            results.append(
                {"ok": False, "name": name, "ip": ip, "error": f"{type(e).__name__}: {e}"}
            )
    return apply_control_results(intent, results)


async def fetch_device_statuses(
    *,
    registry: Optional[dict] = None,
    transport: Optional[LightTransport] = None,
    timeout_each: float = 4.0,
) -> list[dict[str, Any]]:
    """List configured devices with live status when possible."""
    from iot.plugs import is_plug_type, status_plug_device

    reg = registry if registry is not None else load_bulbs()
    devices = list_configured_devices(reg)
    t = transport or get_default_transport()

    async def one(dev: dict[str, Any]) -> dict[str, Any]:
        out = dict(dev)
        dtype = str(dev.get("type") or "wiz").lower()
        if is_plug_type(dtype):
            try:
                to = 12.0 if dtype == "vesync" else timeout_each
                info = dict(reg.get(dev.get("id") or dev.get("name") or "", {}) or {})
                info.setdefault("name", dev.get("id") or dev.get("name"))
                info.setdefault("type", dtype)
                if dev.get("ip"):
                    info.setdefault("ip", dev.get("ip"))
                for k in ("device_name", "cid", "mac", "label"):
                    if dev.get(k) is not None:
                        info.setdefault(k, dev.get(k))
                st = await asyncio.wait_for(status_plug_device(info), timeout=to)
                out["status"] = st
            except Exception as e:  # noqa: BLE001
                out["status"] = {
                    "ok": False,
                    "ip": dev.get("ip"),
                    "error": f"{type(e).__name__}: {e}",
                }
            return out
        ip = dev.get("ip")
        if not ip:
            out["status"] = {"ok": False, "error": "missing ip"}
            return out
        try:
            st = await asyncio.wait_for(
                t.get_state(ip, dev.get("mac")), timeout=timeout_each
            )
            out["status"] = st
        except Exception as e:  # noqa: BLE001
            out["status"] = {"ok": False, "ip": ip, "error": f"{type(e).__name__}: {e}"}
        return out

    return list(await asyncio.gather(*[one(d) for d in devices]))


async def discover_and_merge(
    *,
    registry: Optional[dict] = None,
    transport: Optional[LightTransport] = None,
    broadcast: str = "192.168.100.255",
    wait_time: float = 5.0,
    extra_discovered: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Run Wiz discovery, merge with config, return combined list."""
    reg = registry if registry is not None else load_bulbs()
    configured = list_configured_devices(reg)
    t = transport or get_default_transport()
    discovered: list[dict[str, Any]] = []
    error: Optional[str] = None
    try:
        discovered = await t.discover(broadcast=broadcast, wait_time=wait_time)
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    if extra_discovered:
        discovered = list(discovered) + list(extra_discovered)
    merged = merge_devices(configured, discovered)
    return {
        "ok": error is None,
        "error": error,
        "configured_count": len(configured),
        "discovered_count": len(discovered),
        "discovered": discovered,
        "devices": merged,
    }


# Persistent loop for HTTP handlers — pyvesync/aiohttp break if asyncio.run()
# creates a new loop every request while sessions are still referenced.
_BG_LOOP: Optional[asyncio.AbstractEventLoop] = None
_BG_THREAD = None  # type: ignore
_BG_LOCK = None  # type: ignore


def _ensure_bg_loop() -> asyncio.AbstractEventLoop:
    """Start (once) a background event loop used by run_async."""
    global _BG_LOOP, _BG_THREAD, _BG_LOCK
    import threading

    if _BG_LOCK is None:
        _BG_LOCK = threading.Lock()
    with _BG_LOCK:
        if _BG_LOOP is not None and _BG_LOOP.is_running():
            return _BG_LOOP
        loop = asyncio.new_event_loop()

        def _runner() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        t = threading.Thread(target=_runner, name="iot-asyncio", daemon=True)
        t.start()
        _BG_LOOP = loop
        _BG_THREAD = t
        return loop


def run_async(coro, timeout: float = 120.0):
    """Run coroutine from sync code (CLI / HTTP handlers).

    Uses a process-wide background loop so cloud clients (VeSync) keep a
    stable event loop across requests.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        # Already inside async — caller should await; fallback blocks badly.
        raise RuntimeError("run_async called from within a running event loop")
    loop = _ensure_bg_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=timeout)
