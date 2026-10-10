"""Domain registry: subordinate dashboards, ports, and on-disk sources."""

from __future__ import annotations

import os
from typing import Any

# Placeholder tailnet origin. Set PRISM_PUBLIC_BASE on prism (systemd drop-in
# or the orchestra process environment). Do not put a real hostname here.
DEFAULT_PUBLIC_ORIGIN = "https://prism.example.ts.net"


def public_origin(environ: dict[str, str] | None = None) -> str:
    """HTTPS origin for tailnet links. Separate from the loopback probe host."""
    env = os.environ if environ is None else environ
    raw = (env.get("PRISM_PUBLIC_BASE") or "").strip()
    if not raw:
        return DEFAULT_PUBLIC_ORIGIN
    if "://" not in raw:
        raw = "https://" + raw
    scheme, _, rest = raw.partition("://")
    hostport = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].strip()
    if scheme.lower() not in ("https", "http") or not hostport:
        return DEFAULT_PUBLIC_ORIGIN
    return f"{scheme.lower()}://{hostport}"


def service_url(name: str, environ: dict[str, str] | None = None) -> str:
    """Browser link for a loopback-only service, via the tailnet FCC origin.

    Finance and Horizon are not opened on a LAN IP. Port probes stay on
    127.0.0.1 (orchestra.collectors._default_probe_host).
    """
    origin = public_origin(environ)
    key = (name or "").strip().lower()
    if key in ("finance", "financial-command", "fcc"):
        return origin + "/financial-command/"
    if key in ("horizon", "horizon-dashboard"):
        return origin + "/horizon/"
    raise KeyError(name)


def health_url(name: str, environ: dict[str, str] | None = None) -> str:
    origin = public_origin(environ)
    key = (name or "").strip().lower()
    if key in ("finance", "financial-command", "fcc"):
        return origin + "/api/health"
    if key in ("horizon", "horizon-dashboard"):
        return origin + "/horizon/api/health"
    raise KeyError(name)

# Subordinate dashboards (ports match monorepo convention)
DOMAIN_SPECS: list[dict[str, Any]] = [
    {
        "id": "strategy",
        "label": "Strategy",
        "description": "High-conviction bets and today's micro plan",
        "port": None,
        "url": None,
        "launch": None,
        "sources": ["strategy/bets.md", "strategy/today.md", "initiatives/"],
        "kind": "files",
    },
    {
        "id": "workflow",
        "label": "Workflow / Projects",
        "description": "Pre-reboot readiness, backlog, Grok sessions",
        "port": 8765,
        "url": "http://127.0.0.1:8765/",
        "launch": "python3 projects-dashboard/server.py",
        "sources": ["ops/backlog/", "ops/session-index/", "projects-dashboard/"],
        "kind": "dashboard",
        "work_branch": "work/projects-dashboard",
    },
    {
        "id": "finance",
        "label": "Finance / Treasury",
        "description": "Dual-venue liquidity (Coinbase + Robinhood)",
        "port": 8000,
        "url": DEFAULT_PUBLIC_ORIGIN + "/financial-command/",
        "launch": "python3 financial-command/server.py",
        "sources": [
            "treasury/",
            "treasury/snapshots/treasury_latest.json",
            "financial-command/",
            "financial-command/treasury_latest.json",
            "investment/",
            "research/",
        ],
        "kind": "dashboard",
        "work_branch": "work/treasury",
    },
    {
        "id": "fitness",
        "label": "Fitness / Health",
        "description": "PPL workouts, nutrition, health metrics",
        "port": 8787,
        "url": "http://127.0.0.1:8787/",
        "launch": "python3 resistance-dashboard/server.py",
        "sources": ["fitness/data/", "fitness/workouts/", "resistance-dashboard/"],
        "kind": "dashboard",
        "work_branch": "work/resistance-dashboard",
    },
    {
        "id": "holistic",
        "label": "Time Allocation",
        "description": "Rolling plan, targets, domain time budgets",
        "port": 8770,
        "url": "http://127.0.0.1:8770/",
        "launch": "python3 holistic/server.py",
        "sources": ["holistic/data/", "holistic/time_allocator/"],
        "kind": "dashboard",
        "work_branch": "work/holistic",
    },
    {
        "id": "iot",
        "label": "IoT / Home",
        "description": "Wiz lights, room groups, sunrise/sunset routines",
        "port": 8780,
        "url": "http://127.0.0.1:8780/",
        "launch": "python3 iot/server.py",
        "sources": [
            "iot/wiz-lights/bulbs.json",
            "iot/groups.json",
            "iot/schedule.json",
            "iot/",
        ],
        "kind": "dashboard",
        "work_branch": "work/iot",
    },
]

# Shared themes used for keyword overlap detection
THEME_KEYWORDS: dict[str, list[str]] = {
    "AI/Autonomy/Robotics": [
        "ai",
        "autonomy",
        "robotics",
        "agent",
        "automation",
        "command center",
        "orchestra",
        "dashboard",
        "tooling",
        "leverage",
    ],
    "Bitcoin": ["bitcoin", "btc", "mstr", "crypto"],
    "Energy": ["energy", "nuclear", "power"],
    "Fitness/Health": [
        "fitness",
        "health",
        "workout",
        "ppl",
        "sleep",
        "nutrition",
        "recovery",
        "weight",
        "vitality",
    ],
    "Investment/Wealth": [
        "investment",
        "treasury",
        "liquidity",
        "dca",
        "robinhood",
        "coinbase",
        "wealth",
        "ltv",
        "buying power",
    ],
    "Time/Focus": [
        "time",
        "allocator",
        "focus",
        "priority",
        "plan",
        "today",
        "schedule",
    ],
    "Home/IoT": [
        "iot",
        "wiz",
        "bulb",
        "light",
        "entryway",
        "livingroom",
        "sunrise",
        "sunset",
        "home",
        "environment",
        "schedule",
    ],
}
