"""Vercel preview helpers for FCC. Stdlib only. No venue keys. No live snapshot in git."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

SERVICE = "financial-command"
ROLE = "vercel-preview"
STALE_HOURS = 6.0

# POSTs (and all methods for trade/mint) that must 403 on Vercel.
WRITE_ROUTES = frozenset({"config", "refresh", "trade", "mint"})
ALWAYS_DENY_ROUTES = frozenset({"trade", "mint"})

# Cookie-less / missing Vercel login proof. Do not invent a bypass secret.
# Only the Deployment Protection SSO cookie counts. x-vercel-oidc-token is
# auto-injected on every Function request and is NOT user login.
AUTH_REQUIRED = {"ok": False, "error": "auth_required"}
VERCEL_JWT_COOKIES = ("_vercel_jwt", "__Secure-_vercel_jwt")

PAGE_FILES = {
    "index": "index.html",
    "capital-flows": "capital-flows.html",
    "watchlist": "watchlist.html",
}
PAGE_PATHS = {
    "/": "index",
    "/index.html": "index",
    "/financial-command": "index",
    "/financial-command/": "index",
    "/financial-command/index.html": "index",
    "/capital-flows": "capital-flows",
    "/capital-flows.html": "capital-flows",
    "/financial-command/capital-flows": "capital-flows",
    "/financial-command/capital-flows.html": "capital-flows",
    "/watchlist": "watchlist",
    "/watchlist.html": "watchlist",
    "/financial-command/watchlist": "watchlist",
    "/financial-command/watchlist.html": "watchlist",
}

READ_ONLY_MESSAGE = (
    "Vercel preview is read-only. Not Mac, not Pi. Do not trade or mint."
)

# Rebuild agent_brief only when raw embeds a wallet. Page still matches Pi.
_WALLET_RE = re.compile(
    r"(0x[a-fA-F0-9]{40}|bc1[a-z0-9]{25,90})",
    re.IGNORECASE,
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
            "braiins",
            "x_money",
            "coach",
            "watchlist",
            "capital_flows",
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


def _normalize_headers(headers: dict[str, str] | None) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (headers or {}).items()}


def _cookie_value(headers: dict[str, str] | None, name: str) -> str:
    raw = _normalize_headers(headers).get("cookie") or ""
    for part in raw.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        if key.strip() == name and val.strip():
            return val.strip()
    return ""


def vercel_auth_present(headers: dict[str, str] | None) -> bool:
    """True only if the Vercel SSO/OIDC cookie is on this request.

    Fail closed when `_vercel_jwt` is missing. Do not treat
    `x-vercel-oidc-token` as login — Vercel injects that on every Function
    call (deployment identity). Do not read VERCEL_OIDC_TOKEN from the
    environment. Do not invent a shared-secret or query bypass.
    """
    headers_l = _normalize_headers(headers)
    for name in VERCEL_JWT_COOKIES:
        if _cookie_value(headers_l, name):
            return True
    return False


def page_name_from(path: str, query: dict[str, list[str]] | None = None) -> str | None:
    query = query or {}
    explicit = ((query.get("_p") or [""])[0] or "").strip()
    if explicit in PAGE_FILES:
        return explicit
    parsed = urlparse(path or "")
    orig = parsed.path or "/"
    if orig in PAGE_PATHS:
        return PAGE_PATHS[orig]
    stripped = "/" if orig == "/" else (orig.rstrip("/") or "/")
    return PAGE_PATHS.get(stripped)


def resolve_page_file(name: str) -> Path | None:
    filename = PAGE_FILES.get(name)
    if not filename:
        return None
    api_dir = Path(__file__).resolve().parent
    root = api_dir.parent
    for cand in (
        api_dir / "_pages" / filename,
        root / filename,
        Path("/var/task/api/_pages") / filename,
        Path("/var/task") / filename,
    ):
        if cand.is_file():
            return cand
    return None


def serve_page(name: str) -> tuple[int, Any]:
    path = resolve_page_file(name)
    if path is None:
        return 404, {"ok": False, "error": "not_found", "route": "page"}
    return 200, path.read_text(encoding="utf-8")


def placeholder_treasury() -> dict[str, Any]:
    """Empty 1:1 shape so panels still render. No live numbers."""
    return {
        "ok": True,
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


def placeholder_capital_flows() -> dict[str, Any]:
    """1:1 flow-map chrome from the page layout (v39). No live YNAB/treasury numbers."""
    return {
        "ok": True,
        "version": "vercel-placeholder",
        "title": "Capital Flows",
        "as_of": None,
        "read_only": True,
        "layout": {
            "columns": [
                {
                    "id": "income",
                    "label": "Income",
                    "ids": ["turo", "lyft", "x_creator", "tread", "asics"],
                },
                {
                    "id": "liquidity_engine",
                    "label": "Liquidity Engine",
                    "ids": ["x_money", "digital_credit", "margin"],
                },
                {
                    "id": "deploy",
                    "label": "Deploy",
                    "ids": [
                        "bills_essential",
                        "bills_fleet",
                        "bills_collateral",
                        "bills_productive",
                        "bills_consumer",
                    ],
                },
            ]
        },
        "income_sources": [
            {
                "id": "turo",
                "label": "Turo",
                "kind": "income",
                "typical_landing": "x_money",
                "cadence": "intermittent",
            },
            {
                "id": "lyft",
                "label": "Lyft",
                "kind": "income",
                "typical_landing": "x_money",
                "cadence": "semi_daily",
            },
            {
                "id": "x_creator",
                "label": "X Creator",
                "kind": "income",
                "typical_landing": "x_money",
                "cadence": "monthly",
            },
            {
                "id": "tread",
                "label": "Tread",
                "kind": "income",
                "typical_landing": "x_money",
                "cadence": "monthly",
            },
            {
                "id": "asics",
                "label": "ASICs",
                "kind": "income",
                "typical_landing": "digital_credit",
                "cadence": "approx_monthly",
            },
        ],
        "channels": [
            {
                "id": "x_money",
                "label": "X Money",
                "kind": "venue",
                "stroke": "liq-gray",
            },
            {
                "id": "digital_credit",
                "label": "Digital Credit",
                "kind": "venue",
                "stroke": "liq-blue",
            },
            {
                "id": "margin",
                "label": "Margin",
                "kind": "venue",
                "stroke": "liq-green",
            },
            {
                "id": "bills_essential",
                "label": "Essential",
                "kind": "expense",
            },
            {
                "id": "bills_fleet",
                "label": "Fleet",
                "kind": "expense",
            },
            {
                "id": "bills_collateral",
                "label": "Collateral",
                "kind": "invest",
            },
            {
                "id": "bills_productive",
                "label": "Productive",
                "kind": "invest",
            },
            {
                "id": "bills_consumer",
                "label": "Consumer",
                "kind": "expense",
            },
        ],
        "edges": [
            {"from": "turo", "to": "x_money", "weight": 1},
            {"from": "lyft", "to": "x_money", "weight": 1},
            {"from": "lyft", "to": "digital_credit", "weight": 1},
            {"from": "x_creator", "to": "x_money", "weight": 1},
            {"from": "tread", "to": "x_money", "weight": 1},
            {"from": "asics", "to": "digital_credit", "weight": 1},
            {"from": "x_money", "to": "digital_credit", "weight": 1},
            {"from": "digital_credit", "to": "margin", "weight": 1},
            {"from": "x_money", "to": "bills_essential", "weight": 1},
            {"from": "x_money", "to": "bills_fleet", "weight": 1},
            {"from": "x_money", "to": "bills_collateral", "weight": 1},
            {"from": "x_money", "to": "bills_productive", "weight": 1},
            {"from": "x_money", "to": "bills_consumer", "weight": 1},
        ],
        "open_questions": [],
        "integrations": {},
        "live": {},
        "preview": {"read_only": True, "source": "placeholder", "role": ROLE},
    }


def placeholder_watchlist() -> dict[str, Any]:
    """1:1 watchlist dashboard shape. Entries fill from FCC_WATCHLIST_JSON."""
    return {
        "ok": True,
        "as_of": None,
        "watchlist_as_of": None,
        "purpose": (
            "Watchlist research. Vercel preview is read-only. "
            "Publish FCC_WATCHLIST_JSON from Mac for live entries."
        ),
        "policy": {},
        "fund_policy": {},
        "agentic_held": {"symbols": [], "account_last4": None, "as_of": None},
        "fund_manager_watchlist": {},
        "entries": [],
        "count": 0,
        "private_watchlist": {
            "as_of": None,
            "purpose": (
                "Monitor only for a possible public allocation if/when listed. "
                "Not deployable. Not in the public consider set."
            ),
            "policy": {},
            "entries": [],
            "count": 0,
            "path": "investment/private_watchlist.json",
        },
        "research_dir": "investment/research",
        "workflows": {
            "deep_dive": "position-deep-dive",
            "portfolio_research": "fund-manager-research",
        },
        "read_only": True,
        "preview": {"read_only": True, "source": "placeholder", "role": ROLE},
    }


def placeholder_coach() -> dict[str, Any]:
    """Coach plan shape (top-level, matches Pi). No live obligations."""
    return {
        "ok": True,
        "read_only": True,
        "advice": [READ_ONLY_MESSAGE],
        "obligations": [],
        "summary": {
            "obligation_count": 0,
            "total_due": 0,
            "total_allocated": 0,
            "total_gap": 0,
            "overdue_count": 0,
        },
        "habits": {},
        "data_requests": [],
        "venues": {},
        "preview": {"read_only": True, "source": "placeholder", "role": ROLE},
    }


def placeholder_braiins() -> dict[str, Any]:
    return {
        "ok": False,
        "status": "missing",
        "error": "vercel_preview_no_live_feeds",
        "read_only": True,
        "workers": [],
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


def _from_env(env: dict[str, str], json_key: str, b64_key: str) -> dict[str, Any] | None:
    data = _parse_payload(env.get(json_key) or "")
    if data is not None:
        return data
    b64 = (env.get(b64_key) or "").strip()
    if not b64:
        return None
    try:
        import base64

        return _parse_payload(base64.b64decode(b64).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _mark_preview(data: dict[str, Any], source: str) -> dict[str, Any]:
    data = dict(data)
    data.setdefault("ok", True)
    data["read_only"] = True
    preview = data.get("preview")
    if not isinstance(preview, dict):
        preview = {}
    preview["read_only"] = True
    preview["source"] = source
    preview["role"] = ROLE
    data["preview"] = preview
    return data


def scrub_agent_brief(data: dict[str, Any]) -> dict[str, Any]:
    """Rebuild agent_brief only if raw embeds a wallet. Do not print the brief."""
    ev = data.get("evaluation") if isinstance(data, dict) else None
    brief = ev.get("agent_brief") if isinstance(ev, dict) else None
    if not isinstance(brief, str) or not _WALLET_RE.search(brief):
        return data
    ev = dict(ev)
    ev["agent_brief"] = _WALLET_RE.sub("[wallet redacted]", brief)
    out = dict(data)
    out["evaluation"] = ev
    out["agent_brief_rebuilt"] = True
    return out


def load_treasury_payload(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Placeholder unless an explicit env mapping is passed (tests / later publish).

    This ship does not read process FCC_TREASURY_JSON / FCC_TREASURY_B64.
    Never reads the git snapshot file. If a live payload is ever passed in,
    scrub wallets from agent_brief; panels stay 1:1.
    """
    if env is None:
        return scrub_agent_brief(placeholder_treasury())
    data = _from_env(env, "FCC_TREASURY_JSON", "FCC_TREASURY_B64")
    if data is None:
        data = placeholder_treasury()
    else:
        data = _mark_preview(data, "env")
    return scrub_agent_brief(data)


def load_named(
    env: dict[str, str] | None,
    json_key: str,
    b64_key: str,
    placeholder_fn,
) -> dict[str, Any]:
    env = env if env is not None else os.environ
    data = _from_env(env, json_key, b64_key)
    if data is None:
        return placeholder_fn()
    return _mark_preview(data, "env")


def snapshot_stale(data: dict[str, Any], *, now: datetime | None = None) -> bool:
    snap = data.get("snapshot") if isinstance(data, dict) else None
    iso = None
    if isinstance(snap, dict):
        iso = snap.get("as_of")
    if not iso and isinstance(data, dict):
        iso = data.get("as_of") or data.get("watchlist_as_of")
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


def route_from_path(path: str, headers: dict[str, str] | None = None) -> str:
    parsed = urlparse(path or "")
    qs = parse_qs(parsed.query)
    r = (qs.get("_r") or [""])[0].strip()
    if r:
        if r == "watchlist":
            orig = parsed.path or ""
            headers_l = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
            original = (
                headers_l.get("x-forwarded-uri")
                or headers_l.get("x-invoke-path")
                or headers_l.get("x-vercel-original-path")
                or orig
            )
            if "deep-dive" in (original or "") or "deep-dive" in orig:
                return "watchlist-deep-dive"
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
    if orig_path in PAGE_PATHS:
        return "page"
    stripped = "/" if orig_path == "/" else (orig_path.rstrip("/") or "/")
    if stripped in PAGE_PATHS:
        return "page"
    parts = [p for p in orig_path.split("/") if p]
    if parts and parts[0] == "api":
        parts = parts[1:]
    if not parts:
        return "health"
    head = parts[0]
    if head == "watchlist" and any(p == "deep-dive" for p in parts[1:]):
        return "watchlist-deep-dive"
    aliases = {
        "fcc-identity": "health",
        "advisor": "ask",
        "open-orchestra": "orchestra",
        "orchestra-status": "orchestra",
        "launch-orchestra": "orchestra",
        "capital-flows": "capital-flows",
        "watchlist-deep-dive": "watchlist-deep-dive",
    }
    return aliases.get(head, head)


def _deep_dive(env: dict[str, str] | None, query: dict[str, list[str]] | None) -> tuple[int, dict[str, Any]]:
    query = query or {}
    symbol = ((query.get("symbol") or [""])[0] or "").strip().upper()
    pack = load_named(env, "FCC_WATCHLIST_JSON", "FCC_WATCHLIST_B64", placeholder_watchlist)
    dives = pack.get("deep_dives") if isinstance(pack.get("deep_dives"), dict) else {}
    if symbol and symbol in dives:
        md = dives.get(symbol)
        return 200, {
            "ok": True,
            "symbol": symbol,
            "markdown": md if isinstance(md, str) else None,
            "read_only": True,
        }
    for e in pack.get("entries") or []:
        if not isinstance(e, dict):
            continue
        if str(e.get("symbol") or "").upper() == symbol:
            dive = e.get("deep_dive") if isinstance(e.get("deep_dive"), dict) else {}
            md = dive.get("full_markdown") or dive.get("markdown")
            return 200, {
                "ok": bool(md),
                "symbol": symbol,
                "markdown": md,
                "summary": {k: v for k, v in dive.items() if k not in {"full_markdown", "markdown"}},
                "read_only": True,
                "error": None if md else "deep dive not published to Vercel env",
            }
    return 200, {
        "ok": False,
        "symbol": symbol,
        "markdown": None,
        "error": "deep dive not published to Vercel env",
        "read_only": True,
    }


def dispatch(
    method: str,
    route: str,
    env: dict[str, str] | None = None,
    query: dict[str, list[str]] | None = None,
) -> tuple[int, dict[str, Any]]:
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
        cfg = load_named(env, "FCC_CONFIG_JSON", "FCC_CONFIG_B64", empty_config)
        if "config" not in cfg:
            cfg = {"ok": True, "config": cfg, "read_only": True, "venue_keys": False}
        cfg["venue_keys"] = False
        cfg["read_only"] = True
        return 200, cfg
    if route == "capital-flows":
        if method != "GET":
            return deny_write(route)
        return 200, load_named(
            env, "FCC_CAPITAL_FLOWS_JSON", "FCC_CAPITAL_FLOWS_B64", placeholder_capital_flows
        )
    if route == "watchlist":
        if method != "GET":
            return deny_write(route)
        return 200, load_named(
            env, "FCC_WATCHLIST_JSON", "FCC_WATCHLIST_B64", placeholder_watchlist
        )
    if route == "watchlist-deep-dive":
        if method != "GET":
            return deny_write(route)
        return _deep_dive(env, query)
    if route == "coach":
        if method != "GET":
            return deny_write(route)
        return 200, load_named(env, "FCC_COACH_JSON", "FCC_COACH_B64", placeholder_coach)
    if route == "braiins":
        if method != "GET":
            return deny_write(route)
        return 200, load_named(env, "FCC_BRAIINS_JSON", "FCC_BRAIINS_B64", placeholder_braiins)
    if route in {"ask", "orchestra"}:
        if method != "GET":
            return deny_write(route)
        return 200, {
            "ok": True,
            "available": False,
            "reason": "vercel_preview_read_only",
            "read_only": True,
            "live": False,
        }
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        return deny_write(route)
    return 404, {"ok": False, "error": "not_found", "route": route}
