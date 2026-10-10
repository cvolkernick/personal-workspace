"""Domain registry: subordinate dashboards, ports, and on-disk sources."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import prism_public  # noqa: E402

# Loopback links when PRISM_PUBLIC_BASE is unset or invalid. Probes stay on
# 127.0.0.1 as well (orchestra.collectors._default_probe_host).
FINANCE_LOOPBACK_URL = "http://127.0.0.1:8000/financial-command/"
FINANCE_LOOPBACK_HEALTH = "http://127.0.0.1:8000/api/health"
HORIZON_LOOPBACK_URL = "http://127.0.0.1:8795/"
HORIZON_LOOPBACK_HEALTH = "http://127.0.0.1:8795/api/health"


def public_origin(environ: dict[str, str] | None = None) -> str:
    """Validated https origin, or "" when unset or invalid."""
    return prism_public.prism_public_origin(environ)


def _service_key(name: str) -> str:
    key = (name or "").strip().lower()
    if key in ("finance", "financial-command", "fcc"):
        return "finance"
    if key in ("horizon", "horizon-dashboard"):
        return "horizon"
    raise KeyError(name)


def service_url(name: str, environ: dict[str, str] | None = None) -> str:
    """Browser link for Finance or Horizon.

    A valid PRISM_PUBLIC_BASE uses the tailnet FCC origin. Otherwise the
    link is loopback (the previous direct dashboard URL).
    """
    key = _service_key(name)
    origin = public_origin(environ)
    if not origin:
        if key == "finance":
            return FINANCE_LOOPBACK_URL
        return HORIZON_LOOPBACK_URL
    if key == "finance":
        return origin + "/financial-command/"
    return origin + "/horizon/"


def health_url(name: str, environ: dict[str, str] | None = None) -> str:
    key = _service_key(name)
    origin = public_origin(environ)
    if not origin:
        if key == "finance":
            return FINANCE_LOOPBACK_HEALTH
        return HORIZON_LOOPBACK_HEALTH
    if key == "finance":
        return origin + "/api/health"
    return origin + "/horizon/api/health"

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
        "url": FINANCE_LOOPBACK_URL,
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
