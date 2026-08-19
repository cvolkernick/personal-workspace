"""Vercel preview helpers for FCC. Stdlib only. No venue keys. No live snapshot in git."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

SERVICE = "financial-command"
ROLE = "vercel-preview"
STALE_HOURS = 6.0

# POSTs (and all methods for trade/mint) that must 403 on Vercel.
WRITE_ROUTES = frozenset({"config", "refresh", "trade", "mint"})
ALWAYS_DENY_ROUTES = frozenset({"trade", "mint"})

READ_ONLY_MESSAGE = (
    "Vercel preview is read-only. Not Mac, not Pi. Do not trade or mint."
)


def health_body() -> dict[str, Any]:
    return {
        "ok": True,
        "service": SERVICE,
        "role": ROLE,
        "canonical": True,
        "read_only": True,
        "stale_hours": STALE_HOURS,
        "features": [
            "glance",
            "do_now",
            "stress",
            "floors",
            "sleeves",
            "cashflow",
            "positions",
        ],
    }


def deny_write(route: str) -> tuple[int, dict[str, Any]]:
    return 403, {
        "ok": False,
        "error": "read_only",
        "route": route,
        "message": READ_ONLY_MESSAGE,
    }


def deny_static_treasury() -> tuple[int, dict[str, Any]]:
    """Cookie-less / public path for treasury_latest.json must not leak numbers."""
    return 404, {
        "ok": False,
        "error": "not_public",
        "message": "treasury_latest.json is not a public URL",
    }


def placeholder_treasury() -> dict[str, Any]:
    """Empty 1:1 shape so panels still render. No live numbers."""
    return {
        "evaluation": {
            "actions": [],
            "agent_brief": (
                "Vercel preview placeholder — no live treasury payload. "
                "Publish from Mac to FCC_TREASURY_JSON (protected env/artifact)."
            ),
            "buckets": {},
            "data_quality": {},
            "dca": {},
            "inputs": {},
            "next_steps": [],
            "policy": {},
            "sleeves": {},
            "strategy_context": {},
            "stress": {},
        },
        "snapshot": {
            "as_of": None,
            "coinbase": {},
            "coinbase_manual": {},
            "expenses": {},
            "meta": {"source": "vercel_placeholder"},
            "one_card": {},
            "policy_overrides": {},
            "rh_checking": {},
            "robinhood": {},
        },
        "preview": {
            "read_only": True,
            "source": "placeholder",
            "role": ROLE,
        },
    }


def _parse_payload(raw: str) -> dict[str, Any] | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def load_treasury_payload(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Serve env artifact if present; otherwise an empty placeholder. Never reads git file."""
    env = env if env is not None else os.environ
    data = _parse_payload(env.get("FCC_TREASURY_JSON") or "")
    if data is None:
        b64 = (env.get("FCC_TREASURY_B64") or "").strip()
        if b64:
            try:
                import base64

                data = _parse_payload(base64.b64decode(b64).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                data = None
    if data is None:
        data = placeholder_treasury()
    else:
        preview = data.get("preview")
        if not isinstance(preview, dict):
            preview = {}
        preview["read_only"] = True
        preview["source"] = "env"
        preview["role"] = ROLE
        data["preview"] = preview
    return data


def snapshot_stale(data: dict[str, Any], *, now: datetime | None = None) -> bool:
    snap = data.get("snapshot") if isinstance(data, dict) else None
    iso = None
    if isinstance(snap, dict):
        iso = snap.get("as_of")
    if not iso and isinstance(data, dict):
        iso = data.get("as_of")
    if not iso:
        return True
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - t).total_seconds() > STALE_HOURS * 3600.0


def empty_config() -> dict[str, Any]:
    return {"ok": True, "config": {}, "read_only": True, "venue_keys": False}


def empty_feed(name: str) -> dict[str, Any]:
    return {
        "ok": True,
        "unavailable": True,
        "feed": name,
        "reason": "vercel_preview_no_live_feeds",
        "read_only": True,
    }


def route_from_path(path: str, headers: dict[str, str] | None = None) -> str:
    parsed = urlparse(path or "")
    qs = parse_qs(parsed.query)
    r = (qs.get("_r") or [""])[0].strip()
    if r:
        return r
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    original = (
        headers.get("x-forwarded-uri")
        or headers.get("x-invoke-path")
        or headers.get("x-vercel-original-path")
        or parsed.path
        or ""
    )
    orig_path = urlparse(original).path
    if not orig_path:
        orig_path = parsed.path or ""
    if orig_path.rstrip("/").endswith("treasury_latest.json"):
        return "denied_static"
    parts = [p for p in orig_path.split("/") if p]
    if parts and parts[0] == "api":
        parts = parts[1:]
    if not parts:
        return "health"
    head = parts[0]
    aliases = {
        "fcc-identity": "health",
        "advisor": "ask",
        "open-orchestra": "orchestra",
        "orchestra-status": "orchestra",
        "launch-orchestra": "orchestra",
        "capital-flows": "capital-flows",
    }
    return aliases.get(head, head)


def dispatch(method: str, route: str, env: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    method = (method or "GET").upper()
    route = (route or "other").strip().lower() or "other"

    if route == "denied_static":
        return deny_static_treasury()
    if route in ALWAYS_DENY_ROUTES:
        return deny_write(route)
    if method == "POST" and route in WRITE_ROUTES:
        return deny_write(route)
    if method == "POST" and route in {"ask", "orchestra"}:
        return deny_write(route)

    if route == "health":
        return 200, health_body()
    if route == "treasury":
        data = load_treasury_payload(env)
        data = dict(data)
        data["stale"] = snapshot_stale(data)
        return 200, data
    if route == "config":
        if method != "GET":
            return deny_write("config")
        return 200, empty_config()
    if route in {"watchlist", "capital-flows", "braiins", "coach", "ask", "orchestra"}:
        if method != "GET":
            return deny_write(route)
        return 200, empty_feed(route)
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        return deny_write(route)
    return 404, {"ok": False, "error": "not_found", "route": route}
