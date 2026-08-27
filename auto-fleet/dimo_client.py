"""DIMO telemetry client — stub when env is missing; never crash the dashboard.

Reads ``~/.config/auto-fleet/env`` (mode 600 expected):

  DIMO_CLIENT_ID
  DIMO_DOMAIN
  DIMO_API_KEY          (or DIMO_PRIVATE_KEY)
  DIMO_DEVELOPER_JWT    (optional pre-minted JWT)
  DIMO_VEHICLE_TOKENS   JSON map of unit id → token id
  DIMO_TOKEN_<UNIT>     per-unit token, e.g. DIMO_TOKEN_M3_2022=123

Missing license / tokens → ``status: unconfigured``. Tests inject ``transport``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

TELEMETRY_URL = "https://telemetry-api.dimo.zone/query"

# Injectable for tests: (token_id, env) -> signal dict or raises
_FETCH: Optional[Callable[[int, Mapping[str, str]], dict[str, Any]]] = None

Transport = Callable[[str, str, bytes, Mapping[str, str]], Any]


def _load_env_file(path: Path | None) -> dict[str, str]:
    try:
        from envutil import load_env, DEFAULT_ENV_PATH
    except ImportError:
        try:
            from .envutil import load_env, DEFAULT_ENV_PATH  # type: ignore
        except ImportError:
            from envfile import DEFAULT_ENV_PATH, load_env_file, merge_env  # type: ignore

            file_env = load_env_file(path if path is not None else DEFAULT_ENV_PATH)
            return merge_env(file_env, os.environ)
    if path is not None:
        return load_env(path)
    return load_env(DEFAULT_ENV_PATH)


def default_env_path() -> Path:
    try:
        from envutil import DEFAULT_ENV_PATH
    except ImportError:
        try:
            from .envutil import DEFAULT_ENV_PATH  # type: ignore
        except ImportError:
            from envfile import DEFAULT_ENV_PATH  # type: ignore
    return DEFAULT_ENV_PATH


def load_dimo_env(
    path: Path | None = None,
    process_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = _load_env_file(path)
    if process_env:
        for key, val in process_env.items():
            if key.startswith("DIMO_") and val:
                env[key] = val
    return env


def is_configured(env: Mapping[str, str]) -> bool:
    client = (env.get("DIMO_CLIENT_ID") or "").strip()
    domain = (env.get("DIMO_DOMAIN") or "").strip()
    secret = (env.get("DIMO_API_KEY") or env.get("DIMO_PRIVATE_KEY") or "").strip()
    jwt = (env.get("DIMO_DEVELOPER_JWT") or "").strip()
    return bool(client and domain and (secret or jwt))


def vehicle_token_id(unit_id: str, env: Mapping[str, str]) -> Optional[int]:
    raw_map = (env.get("DIMO_VEHICLE_TOKENS") or "").strip()
    if raw_map:
        try:
            data = json.loads(raw_map)
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict) and unit_id in data:
            try:
                return int(data[unit_id])
            except (TypeError, ValueError):
                return None
    key = "DIMO_TOKEN_" + unit_id.upper().replace("-", "_")
    raw = (env.get(key) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


token_id_for_unit = vehicle_token_id


def empty_dimo(
    *,
    status: str,
    error: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": status,
        "last_seen": None,
        "odometer": None,
        "range": None,
        "soc": None,
        "speed": None,
        "heading": None,
        "ignition_on": None,
        "location": None,
        "location_timestamp": None,
    }
    if error:
        out["error"] = error
    if reason:
        out["reason"] = reason
    return out


def _value(node: Any) -> Any:
    if isinstance(node, dict) and "value" in node:
        return node.get("value")
    return node


def _signal_timestamp(node: Any) -> Optional[str]:
    if isinstance(node, dict) and node.get("timestamp"):
        return str(node.get("timestamp"))
    return None


def _as_float(node: Any) -> Optional[float]:
    val = _value(node)
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _as_bool(node: Any) -> Optional[bool]:
    val = _value(node)
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    text = str(val).strip().lower()
    if text in ("true", "on", "yes"):
        return True
    if text in ("false", "off", "no"):
        return False
    try:
        return bool(int(text))
    except (TypeError, ValueError):
        return None


def _normalize_telemetry(payload: Any) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    latest = (data or {}).get("signalsLatest") if isinstance(data, dict) else None
    if not isinstance(latest, dict):
        latest = payload if isinstance(payload, dict) else {}
    odo = latest.get("odometer") or latest.get(
        "powertrainTransmissionTravelledDistance"
    )
    rng = latest.get("powertrainRange") or latest.get("range")
    soc = latest.get("powertrainTractionBatteryStateOfChargeCurrent") or latest.get(
        "soc"
    )
    speed_node = latest.get("speed")
    heading_node = latest.get("currentLocationHeading") or latest.get("heading")
    ignition_node = latest.get("isIgnitionOn") or latest.get("ignition_on")
    coords = latest.get("currentLocationCoordinates")
    lat = lon = None
    if isinstance(coords, dict):
        val = coords.get("value") if isinstance(coords.get("value"), dict) else coords
        if isinstance(val, dict):
            lat = val.get("latitude")
            lon = val.get("longitude")
    if lat is None:
        lat = _value(latest.get("currentLocationLatitude"))
    if lon is None:
        lon = _value(latest.get("currentLocationLongitude"))
    loc = latest.get("currentLocation") or latest.get("location")
    if lat is not None or lon is not None:
        loc = {"lat": lat, "lon": lon, "latitude": lat, "longitude": lon}
    loc_ts = _signal_timestamp(coords) if isinstance(coords, dict) else None
    if loc_ts is None and isinstance(loc, dict):
        loc_ts = loc.get("timestamp") or loc.get("location_timestamp")
    stamps = [
        s
        for s in (
            latest.get("lastSeen") or latest.get("last_seen"),
            _signal_timestamp(odo) if isinstance(odo, dict) else None,
            _signal_timestamp(rng) if isinstance(rng, dict) else None,
            _signal_timestamp(soc) if isinstance(soc, dict) else None,
            _signal_timestamp(speed_node) if isinstance(speed_node, dict) else None,
            loc_ts,
        )
        if s
    ]
    return {
        "last_seen": max(stamps) if stamps else None,
        "odometer": _value(odo),
        "range": _value(rng),
        "soc": _value(soc),
        "speed": _as_float(speed_node if speed_node is not None else latest.get("speed")),
        "heading": _as_float(heading_node),
        "ignition_on": _as_bool(ignition_node),
        "location": loc if isinstance(loc, dict) else None,
        "location_timestamp": loc_ts,
    }


TELEMETRY_QUERY = (
    "query Latest($tokenId: Int!) { signalsLatest(tokenId: $tokenId) { "
    "powertrainTransmissionTravelledDistance { value timestamp } "
    "powertrainRange { value timestamp } "
    "powertrainTractionBatteryStateOfChargeCurrent { value timestamp } "
    "powertrainFuelSystemRelativeLevel { value timestamp } "
    "speed { value timestamp } "
    "currentLocationHeading { value timestamp } "
    "isIgnitionOn { value timestamp } "
    "currentLocationCoordinates { timestamp value { latitude longitude } } } }"
)


def _telemetry_body(token_id: int) -> bytes:
    query = {
        "query": TELEMETRY_QUERY,
        "variables": {"tokenId": token_id},
    }
    return json.dumps(query).encode("utf-8")


def _auth_header(bearer: str) -> str:
    return f"Bearer {bearer}"


def _fetch_via_sdk(token_id: int, env: Mapping[str, str]) -> Any:
    """Developer JWT → Vehicle JWT → telemetry. Never send the API key as Bearer."""
    from dimo import DIMO  # type: ignore

    dimo = DIMO("Production")
    auth = dimo.auth.get_dev_jwt(
        client_id=env["DIMO_CLIENT_ID"],
        domain=env["DIMO_DOMAIN"],
        private_key=(env.get("DIMO_API_KEY") or env.get("DIMO_PRIVATE_KEY") or ""),
    )
    vehicle = dimo.token_exchange.exchange(
        developer_jwt=auth["access_token"], token_id=int(token_id)
    )
    vjwt = vehicle.get("token") or vehicle.get("access_token")
    if not vjwt:
        raise RuntimeError("token exchange returned no vehicle JWT")
    return dimo.query(
        "Telemetry", TELEMETRY_QUERY, token=vjwt, variables={"tokenId": int(token_id)}
    )


def _http_transport(
    method: str, url: str, body: bytes, headers: Mapping[str, str]
) -> Any:
    req = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"DIMO HTTP {exc.code}") from exc


def fetch_vehicle(
    unit_id: str,
    env: Mapping[str, str] | None = None,
    *,
    env_path: Path | None = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    resolved = dict(env) if env is not None else load_dimo_env(env_path)
    if not is_configured(resolved):
        return empty_dimo(
            status="unconfigured",
            reason="missing DIMO_CLIENT_ID / DIMO_DOMAIN / DIMO_API_KEY",
        )
    token = vehicle_token_id(unit_id, resolved)
    if token is None:
        return empty_dimo(
            status="unconfigured",
            reason=f"no vehicle token id for {unit_id}",
            error=f"no vehicle token id for {unit_id}",
        )
    vehicle_jwt = (resolved.get("DIMO_VEHICLE_JWT") or "").strip()
    headers = {
        "Authorization": _auth_header(vehicle_jwt) if vehicle_jwt else "",
        "Content-Type": "application/json",
    }
    body = _telemetry_body(token)
    try:
        if transport is not None:
            payload = transport("POST", TELEMETRY_URL, body, headers)
        elif _FETCH is not None:
            payload = _FETCH(token, resolved)
            # Injectable may return already-normalized signals.
            if isinstance(payload, dict) and "status" not in payload:
                if "data" not in payload and (
                    "odometer" in payload or "last_seen" in payload
                ):
                    return {
                        "status": "ok",
                        "token_id": token,
                        "last_seen": payload.get("last_seen"),
                        "odometer": payload.get("odometer"),
                        "range": payload.get("range"),
                        "soc": payload.get("soc"),
                        "speed": payload.get("speed"),
                        "heading": payload.get("heading"),
                        "ignition_on": payload.get("ignition_on"),
                        "location": payload.get("location"),
                        "location_timestamp": payload.get("location_timestamp"),
                    }
        else:
            try:
                payload = _fetch_via_sdk(token, resolved)
            except ImportError:
                if not vehicle_jwt:
                    raise RuntimeError(
                        "need dimo-python-sdk (or DIMO_VEHICLE_JWT) — "
                        "do not send DIMO_API_KEY as a telemetry Bearer"
                    )
                payload = _http_transport("POST", TELEMETRY_URL, body, headers)
        signals = _normalize_telemetry(payload)
    except Exception as exc:  # noqa: BLE001
        return {
            **empty_dimo(status="error", error=str(exc), reason=str(exc)),
            "token_id": token,
        }
    return {
        "status": "ok",
        "token_id": token,
        "last_seen": signals.get("last_seen"),
        "odometer": signals.get("odometer"),
        "range": signals.get("range"),
        "soc": signals.get("soc"),
        "speed": signals.get("speed"),
        "heading": signals.get("heading"),
        "ignition_on": signals.get("ignition_on"),
        "location": signals.get("location"),
        "location_timestamp": signals.get("location_timestamp"),
    }


def dimo_for_unit(
    unit: Mapping[str, Any] | str,
    env: Mapping[str, str] | None = None,
    *,
    env_path: Path | None = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    unit_id = unit if isinstance(unit, str) else str(unit.get("id") or "")
    return fetch_vehicle(unit_id, env, env_path=env_path, transport=transport)
