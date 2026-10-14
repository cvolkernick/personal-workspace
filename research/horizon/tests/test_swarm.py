"""Horizon /swarm: diagram generated from ops/NODES.md."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from research.horizon import server  # noqa: E402
from research.horizon.swarm import parse_nodes_md, render_page  # noqa: E402

NODES_MD = (ROOT / "ops" / "NODES.md").read_text(encoding="utf-8")


def test_parses_both_nodes_units_and_topology():
    t = parse_nodes_md(NODES_MD)
    ids = [n["id"] for n in t["nodes"]]
    assert ids == ["prism-gateway", "finley-gateway"]
    prism = t["nodes"][0]
    assert any(u["unit"] == "orchestra-dashboard" and u["port"].startswith("8790") for u in prism["units"])
    assert "prod" in prism["role"]
    finley = t["nodes"][1]
    assert any(u["unit"] == "b2" for u in finley["units"])
    assert "success" in finley["backup_status"]
    assert {e["id"] for e in t["extras"]} >= {"pi3-relay", "r2", "vercel", "internet"}
    assert {"from": "prism-gateway", "to": "finley-gateway"}.items() <= t["flows"][0].items()


def test_diagram_follows_markdown():
    md = NODES_MD.replace("| orchestra-dashboard | 8790 |", "| orchestra-dashboard | 9999 |")
    page = render_page(parse_nodes_md(md))
    assert "9999" in page and "TAILSCALE" in page and "<svg" in page
    assert "Cloudflare R2" in page and "Pi 3" in page and "Vercel" in page


def test_nav_has_swarm_after_directory():
    keys = [k for k, _, _ in server.SITE_NAV_LINKS]
    assert keys.index("swarm") == keys.index("directory") + 1
    assert server.site_nav_active("/swarm") == "swarm"
