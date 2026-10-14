"""Horizon /swarm: diagram generated from ops/NODES.md (public) or a private overlay."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from research.horizon import server, swarm  # noqa: E402
from research.horizon.swarm import parse_nodes_md, render_page, resolve_nodes_md, swarm_html  # noqa: E402

PUBLIC = ROOT / "ops" / "NODES.md"
PRIVATE_SAMPLE = """
| | prism-gateway | finley-gateway |
|---|---|---|
| Role | prod | backup |

## prism-gateway

### Units → ports

| Unit | Port | Notes |
|---|---|---|
| orchestra-dashboard | 8790 | heartbeat |

## finley-gateway

### Backup pull (b2-puller): **succeeding**

- Last run 2026-10-14 03:20 ET: Result=success

## Topology

### Planned and external

- r2 | Cloudflare R2 (planned) | off-site

### Flows

- prism-gateway -> finley-gateway | pull
"""


def test_public_md_drives_a_sensible_diagram():
    t = parse_nodes_md(PUBLIC.read_text(encoding="utf-8"))
    assert [n["id"] for n in t["nodes"]] == ["prism-gateway", "finley-gateway"]
    assert all(n["role"] for n in t["nodes"])
    assert {e["id"] for e in t["extras"]} >= {"pi3-relay", "r2", "vercel", "internet"}
    assert len(t["flows"]) >= 4
    page = render_page(t)
    assert "<svg" in page and "TAILSCALE" in page and "Pi 3" in page


def test_private_overlay_preferred_and_detailed(tmp_path, monkeypatch):
    priv = tmp_path / "nodes.private.md"
    priv.write_text(PRIVATE_SAMPLE, encoding="utf-8")
    monkeypatch.setattr(swarm, "PRIVATE_NODES_MD", tmp_path / "absent.md")
    assert resolve_nodes_md(PUBLIC, {})[0] == PUBLIC
    assert resolve_nodes_md(PUBLIC, {"HORIZON_NODES_MD": str(priv)}) == (priv, "private overlay")
    monkeypatch.setattr(swarm, "PRIVATE_NODES_MD", priv)
    page = swarm_html(PUBLIC, {})
    assert "8790" in page and "private overlay" in page and str(priv) not in page


def test_diagram_follows_markdown():
    page = render_page(parse_nodes_md(PRIVATE_SAMPLE.replace("| 8790 |", "| 9999 |")))
    assert "9999" in page and "success" in page


def test_nav_has_swarm_after_directory():
    keys = [k for k, _, _ in server.SITE_NAV_LINKS]
    assert keys.index("swarm") == keys.index("directory") + 1
    assert server.site_nav_active("/swarm") == "swarm"
