"""Domain registry: subordinate dashboards, ports, and on-disk sources.

Deep-link URLs point at the always-on Pi host (see deploy/endpoints.json /
dashboard_endpoints.py), not localhost.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dashboard_endpoints import domain_url_map, service_url
except ImportError:  # pragma: no cover
    def domain_url_map() -> dict[str, str]:  # type: ignore[misc]
        return {
            "workflow": "http://192.168.100.98:8765/",
            "finance": "http://192.168.100.98:8000/financial-command/index.html",
            "fitness": "http://192.168.100.98:8787/",
            "holistic": "http://192.168.100.98:8770/",
            "iot": "http://192.168.100.98:8780/",
        }

    def service_url(name: str) -> str:  # type: ignore[misc]
        return domain_url_map().get(name, f"http://192.168.100.98/")


_URLS = domain_url_map()

# Subordinate dashboards (ports match monorepo convention; URLs → Pi 24/7)
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
        "url": _URLS.get("workflow") or service_url("projects-dashboard"),
        "launch": "bash deploy/open_dashboard.sh projects-dashboard",
        "sources": ["ops/backlog/", "ops/session-index/", "projects-dashboard/"],
        "kind": "dashboard",
        "work_branch": "work/projects-dashboard",
    },
    {
        "id": "finance",
        "label": "Finance / Treasury",
        "description": "Dual-venue liquidity (Coinbase + Robinhood)",
        "port": 8000,
        "url": _URLS.get("finance") or service_url("financial-command"),
        "launch": "bash deploy/open_dashboard.sh financial-command",
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
        "url": _URLS.get("fitness") or service_url("resistance-dashboard"),
        "launch": "bash deploy/open_dashboard.sh resistance-dashboard",
        "sources": ["fitness/data/", "fitness/workouts/", "resistance-dashboard/"],
        "kind": "dashboard",
        "work_branch": "work/resistance-dashboard",
    },
    {
        "id": "holistic",
        "label": "Time Allocation",
        "description": "Rolling plan, targets, domain time budgets",
        "port": 8770,
        "url": _URLS.get("holistic") or service_url("holistic"),
        "launch": "bash deploy/open_dashboard.sh holistic",
        "sources": ["holistic/data/", "holistic/time_allocator/"],
        "kind": "dashboard",
        "work_branch": "work/holistic",
    },
    {
        "id": "iot",
        "label": "IoT / Home",
        "description": "Wiz lights, room groups, sunrise/sunset routines",
        "port": 8780,
        "url": _URLS.get("iot") or service_url("iot"),
        "launch": "bash deploy/open_dashboard.sh iot",
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
