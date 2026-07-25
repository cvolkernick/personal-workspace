"""Natural-language assistant for IoT dashboard control.

Primary path is a deterministic local parser (works offline on the Pi).
When XAI_API_KEY is set and local parsing fails, optionally ask Grok
(api.x.ai) for a structured plan.

Returned actions:
  {"op": "control", "target": "entryway"|"all"|device_id, "color": "magenta", "brightness": 180?}
  {"op": "run_routine", "id": "sunset"|"sunrise"|...}
  {"op": "status"}
  {"op": "help"}
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping, Optional, Sequence

from iot.control import COLORS, DEFAULT_BRIGHTNESS

# Friendly aliases → group id or special "all"
_TARGET_ALIASES: dict[str, str] = {
    "all": "all",
    "everything": "all",
    "every light": "all",
    "every lights": "all",
    "lights": "all",
    "house": "all",
    "home": "all",
    "entryway": "entryway",
    "entry": "entryway",
    "foyer": "entryway",
    "front": "entryway",
    "livingroom": "livingroom",
    "living room": "livingroom",
    "living": "livingroom",
    "lr": "livingroom",
    "masterbedroom": "masterbedroom",
    "master bedroom": "masterbedroom",
    "bedroom": "masterbedroom",
    "master bed": "masterbedroom",
    "masterbathroom": "masterbathroom",
    "master bathroom": "masterbathroom",
    "bathroom": "masterbathroom",
    "bath": "masterbathroom",
    "shower": "masterbathroom",
    "plugs": "plugs",
    "plug": "plugs",
    "outlets": "plugs",
    "outlet": "plugs",
    "lights": "lights",
    "all lights": "lights",

    "plantlight": "plantlight",
    "plant light": "plantlight",
    "kasa": "plantlight",
    "kasa plant": "plantlight",
    "plantlights": "plantlights",
    "plant lights": "plantlights",
    "plants": "plantlights",
    "smallplantlight": "smallplantlight",
    "small plant light": "smallplantlight",
    "small plant": "smallplantlight",

    "officelights": "officelights",
    "office lights": "officelights",
    "office light": "officelights",
    "office": "office",
    "vesync": "plugs",
}

_COLOR_ALIASES: dict[str, str] = {
    "pink": "magenta",
    "violet": "purple",
    "amber": "warm",
    "gold": "warm",
    "daylight": "white",
    "cool": "white",
    "warm white": "warm",
    "soft white": "warm",
}

_ROUTINE_ALIASES: dict[str, str] = {
    "sunset": "sunset",
    "sunrise": "sunrise",
    "sun set": "sunset",
    "sun rise": "sunrise",
    "evening": "sunset",
    "morning": "sunrise",
}

_HELP = (
    "Try: all off · entryway magenta · living room warm 50% · "
    "master bedroom on · run sunset · status"
)


def _normalize(text: str) -> str:
    t = (text or "").strip().lower()
    t = t.replace("’", "'")
    t = re.sub(r"[!?.,;:]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _brightness_from_text(text: str, default: int) -> int:
    # percent: 50%, 50 percent
    m = re.search(r"\b(\d{1,3})\s*%", text)
    if m:
        pct = max(0, min(100, int(m.group(1))))
        return max(10, min(255, int(round(pct * 255 / 100))))
    m = re.search(r"\b(?:bri(?:ghtness)?|level)\s*[:=]?\s*(\d{1,3})\b", text)
    if m:
        return max(10, min(255, int(m.group(1))))
    m = re.search(r"\b(\d{2,3})\s*(?:bri|brightness)?\b", text)
    if m:
        n = int(m.group(1))
        if 10 <= n <= 255:
            return n
    return default


def _catalog_targets(
    groups: Optional[Mapping[str, Any]] = None,
    devices: Optional[Sequence[str]] = None,
) -> dict[str, str]:
    """Map normalized phrase → control target id."""
    out = dict(_TARGET_ALIASES)
    if groups:
        for gid, info in groups.items():
            out[str(gid).lower()] = str(gid)
            label = str((info or {}).get("label") or gid).lower()
            out[label] = str(gid)
            out[label.replace(" ", "")] = str(gid)
    if devices:
        for d in devices:
            out[str(d).lower()] = str(d)
    # longest match first when scanning
    return out


def _find_target(text: str, catalog: Mapping[str, str]) -> Optional[str]:
    # Prefer longer phrases
    keys = sorted(catalog.keys(), key=len, reverse=True)
    for key in keys:
        if not key:
            continue
        # word-boundary-ish match
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", text):
            return catalog[key]
    return None


def _find_color(text: str) -> Optional[str]:
    # "off" / "on" handled separately often
    aliases = {**{c: c for c in COLORS}, **_COLOR_ALIASES}
    keys = sorted(aliases.keys(), key=len, reverse=True)
    for key in keys:
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", text):
            return aliases[key]
    return None


def _find_routine(text: str) -> Optional[str]:
    keys = sorted(_ROUTINE_ALIASES.keys(), key=len, reverse=True)
    for key in keys:
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", text):
            return _ROUTINE_ALIASES[key]
    return None


def parse_local(
    message: str,
    *,
    groups: Optional[Mapping[str, Any]] = None,
    devices: Optional[Sequence[str]] = None,
    default_brightness: int = DEFAULT_BRIGHTNESS,
) -> dict[str, Any]:
    """Parse a free-text command into actions. Never raises."""
    raw = (message or "").strip()
    text = _normalize(raw)
    if not text:
        return {
            "ok": False,
            "engine": "local",
            "reply": "Type a command — e.g. entryway magenta or all off.",
            "actions": [],
            "error": "empty",
        }

    if text in ("help", "?", "commands", "what can you do"):
        return {
            "ok": True,
            "engine": "local",
            "reply": _HELP,
            "actions": [{"op": "help"}],
        }

    if text in ("status", "refresh", "update", "reload", "check"):
        return {
            "ok": True,
            "engine": "local",
            "reply": "Refreshing device status…",
            "actions": [{"op": "status"}],
        }

    # run sunset / fire sunrise routine
    if re.search(r"\b(run|fire|trigger|start|execute)\b", text) or text in (
        "sunset",
        "sunrise",
    ):
        rid = _find_routine(text)
        if rid or text in ("sunset", "sunrise"):
            rid = rid or text
            return {
                "ok": True,
                "engine": "local",
                "reply": f"Running routine **{rid}**.",
                "actions": [{"op": "run_routine", "id": rid}],
            }

    bri = _brightness_from_text(text, default_brightness)
    catalog = _catalog_targets(groups, devices)
    target = _find_target(text, catalog)
    color = _find_color(text)

    # on / off without color word
    wants_off = bool(re.search(r"\b(off|kill|dark|extinguish)\b", text))
    wants_on = bool(
        re.search(r"\b(on|enable|power on|turn on|switch on|lights on)\b", text)
    ) and not wants_off

    if wants_off:
        color = "off"
    elif wants_on and not color:
        color = "warm"  # sensible default for "on"

    # implicit all: "turn off the lights", "magenta everywhere"
    if target is None:
        if re.search(r"\b(all|everything|everywhere|house|home|lights)\b", text):
            target = "all"
        elif color and not re.search(
            r"\b(entry|living|bed|bath|shower|room|way)\b", text
        ):
            # bare "magenta" / "warm 50%" → all
            if re.fullmatch(
                rf"(turn|set|make|lights?)?\s*{re.escape(color)}(\s+\d+%?)?",
                text.replace("  ", " "),
            ) or text in COLORS or text in _COLOR_ALIASES:
                target = "all"

    if color and target:
        action = {
            "op": "control",
            "target": target,
            "color": color,
            "brightness": bri,
        }
        if color == "off":
            reply = f"Turning **{target}** off."
        else:
            reply = f"Setting **{target}** to **{color}** (bri {bri})."
        return {
            "ok": True,
            "engine": "local",
            "reply": reply,
            "actions": [action],
        }

    if target and not color:
        return {
            "ok": False,
            "engine": "local",
            "reply": f"Got target **{target}** — add a color or on/off. {_HELP}",
            "actions": [],
            "error": "missing_color",
        }

    if color and not target:
        return {
            "ok": False,
            "engine": "local",
            "reply": f"Got color **{color}** — which room/device? {_HELP}",
            "actions": [],
            "error": "missing_target",
        }

    return {
        "ok": False,
        "engine": "local",
        "reply": f"Couldn’t parse that. {_HELP}",
        "actions": [],
        "error": "unparsed",
    }


def _grok_available() -> bool:
    return bool(os.environ.get("XAI_API_KEY", "").strip())


def parse_with_grok(
    message: str,
    *,
    groups: Optional[Mapping[str, Any]] = None,
    devices: Optional[Sequence[str]] = None,
    default_brightness: int = DEFAULT_BRIGHTNESS,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Ask Grok for a structured action plan. Requires XAI_API_KEY."""
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        return {
            "ok": False,
            "engine": "grok",
            "reply": "Grok unavailable (no XAI_API_KEY).",
            "actions": [],
            "error": "no_api_key",
        }

    group_ids = list((groups or {}).keys())
    labels = {
        gid: (info or {}).get("label") or gid for gid, info in (groups or {}).items()
    }
    device_ids = list(devices or [])
    colors = list(COLORS.keys())

    system = (
        "You are the IoT dashboard assistant for home Wiz lights. "
        "Map the user message to 0+ JSON actions. Only use known targets and colors. "
        "Respond with ONLY a JSON object: "
        '{"reply":"short confirmation","actions":[...]} '
        "Actions: "
        '{"op":"control","target":"<id|all>","color":"<preset>","brightness":10-255} | '
        '{"op":"run_routine","id":"sunset"|"sunrise"} | '
        '{"op":"status"} | {"op":"help"}. '
        f"Groups: {json.dumps(labels)}. Group ids: {group_ids}. "
        f"Devices: {device_ids}. Colors: {colors}. "
        f"Default brightness: {default_brightness}. "
        "Prefer group ids over device ids when a room is named."
    )

    payload = {
        "model": "grok-4-1-fast-non-reasoning",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": message},
        ],
        "temperature": 0,
    }
    # Prefer a small/fast model name; fall back list if API rejects
    models_try = [
        os.environ.get("XAI_MODEL", "").strip() or "grok-4-1-fast-non-reasoning",
        "grok-4-fast-non-reasoning",
        "grok-3-mini",
        "grok-2-1212",
    ]
    # dedupe
    seen: set[str] = set()
    models: list[str] = []
    for m in models_try:
        if m and m not in seen:
            seen.add(m)
            models.append(m)

    last_err = "unknown"
    for model in models:
        payload["model"] = model
        req = urllib.request.Request(
            "https://api.x.ai/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = e.read().decode("utf-8", errors="replace")[:300]
            continue
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            continue

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            last_err = "bad grok response shape"
            continue

        parsed = _extract_json_object(content)
        if not parsed:
            last_err = "no json in grok reply"
            continue

        actions = _sanitize_actions(
            parsed.get("actions") or [],
            groups=groups,
            devices=devices,
            default_brightness=default_brightness,
        )
        reply = str(parsed.get("reply") or "").strip() or (
            f"Planned {len(actions)} action(s)." if actions else "No actions."
        )
        return {
            "ok": bool(actions) or parsed.get("ok", True),
            "engine": "grok",
            "model": model,
            "reply": reply,
            "actions": actions,
        }

    return {
        "ok": False,
        "engine": "grok",
        "reply": f"Grok failed: {last_err}",
        "actions": [],
        "error": last_err,
    }


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", s)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _sanitize_actions(
    actions: Any,
    *,
    groups: Optional[Mapping[str, Any]],
    devices: Optional[Sequence[str]],
    default_brightness: int,
) -> list[dict[str, Any]]:
    if not isinstance(actions, list):
        return []
    known = set((groups or {}).keys()) | set(devices or []) | {"all"}
    colors = set(COLORS.keys())
    out: list[dict[str, Any]] = []
    for a in actions:
        if not isinstance(a, dict):
            continue
        op = str(a.get("op") or "").strip().lower()
        if op == "control":
            target = str(a.get("target") or "").strip()
            color = str(a.get("color") or "").strip().lower()
            color = _COLOR_ALIASES.get(color, color)
            if not target or color not in colors:
                continue
            # resolve label → id
            catalog = _catalog_targets(groups, devices)
            resolved = catalog.get(target.lower(), target)
            if resolved not in known and resolved.lower() not in {
                k.lower() for k in known
            }:
                # try catalog match
                t2 = _find_target(target.lower(), catalog)
                if not t2:
                    continue
                resolved = t2
            bri = a.get("brightness", default_brightness)
            try:
                bri_i = max(10, min(255, int(bri)))
            except (TypeError, ValueError):
                bri_i = default_brightness
            out.append(
                {
                    "op": "control",
                    "target": resolved,
                    "color": color,
                    "brightness": bri_i,
                }
            )
        elif op == "run_routine":
            rid = str(a.get("id") or "").strip().lower()
            rid = _ROUTINE_ALIASES.get(rid, rid)
            if rid:
                out.append({"op": "run_routine", "id": rid})
        elif op in ("status", "help"):
            out.append({"op": op})
    return out


def plan_command(
    message: str,
    *,
    groups: Optional[Mapping[str, Any]] = None,
    devices: Optional[Sequence[str]] = None,
    default_brightness: int = DEFAULT_BRIGHTNESS,
    use_grok: Optional[bool] = None,
) -> dict[str, Any]:
    """Local parse first; optionally fall back to Grok."""
    local = parse_local(
        message,
        groups=groups,
        devices=devices,
        default_brightness=default_brightness,
    )
    if local.get("ok") and local.get("actions"):
        return local
    # help/status with ok already handled
    if local.get("ok") and local.get("actions") and local["actions"][0].get("op") in (
        "help",
        "status",
    ):
        return local

    want_grok = _grok_available() if use_grok is None else use_grok
    if want_grok and local.get("error") in (
        "unparsed",
        "missing_color",
        "missing_target",
        "empty",
    ):
        # empty shouldn't hit grok if empty message already returned
        if local.get("error") == "empty":
            return local
        grok = parse_with_grok(
            message,
            groups=groups,
            devices=devices,
            default_brightness=default_brightness,
        )
        if grok.get("actions"):
            return grok
        # merge replies
        if not local.get("ok"):
            local = dict(local)
            local["grok_error"] = grok.get("error") or grok.get("reply")
        return local
    return local


def execute_plan(
    plan: Mapping[str, Any],
    *,
    control_fn: Callable[[str, str, int], dict[str, Any]],
    run_routine_fn: Optional[Callable[[str], dict[str, Any]]] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute planned actions. control_fn(target, color, brightness) → result dict."""
    actions = list(plan.get("actions") or [])
    results: list[dict[str, Any]] = []
    if dry_run:
        return {
            "ok": bool(plan.get("ok")),
            "reply": plan.get("reply"),
            "engine": plan.get("engine"),
            "actions": actions,
            "results": [],
            "dry_run": True,
        }

    for act in actions:
        op = act.get("op")
        if op == "control":
            r = control_fn(
                str(act["target"]),
                str(act["color"]),
                int(act.get("brightness") or DEFAULT_BRIGHTNESS),
            )
            results.append({"action": act, "result": r})
        elif op == "run_routine":
            if run_routine_fn is None:
                results.append(
                    {
                        "action": act,
                        "result": {"ok": False, "error": "routines not available"},
                    }
                )
            else:
                r = run_routine_fn(str(act["id"]))
                results.append({"action": act, "result": r})
        elif op in ("status", "help"):
            results.append({"action": act, "result": {"ok": True}})
        else:
            results.append(
                {"action": act, "result": {"ok": False, "error": f"unknown op {op}"}}
            )

    # overall ok if every executable action succeeded (or no actions)
    exec_ok = True
    for item in results:
        act = item["action"]
        if act.get("op") in ("control", "run_routine"):
            r = item.get("result") or {}
            if not (
                r.get("ok")
                or r.get("results")
                or (r.get("control") or {}).get("ok")
            ):
                exec_ok = False

    reply = str(plan.get("reply") or "")
    if actions and not exec_ok:
        reply = (reply + " Some actions failed.").strip()

    return {
        "ok": bool(plan.get("ok")) and exec_ok if actions else bool(plan.get("ok")),
        "reply": reply,
        "engine": plan.get("engine"),
        "model": plan.get("model"),
        "actions": actions,
        "results": results,
        "dry_run": False,
        "error": plan.get("error"),
        "grok_available": _grok_available(),
    }
