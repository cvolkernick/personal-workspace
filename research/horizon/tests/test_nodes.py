"""Horizon Nodes card: classification, staleness, config."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from research.horizon import nodes as nd  # noqa: E402

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def hb(age_s=30, **kw):
    doc = {
        "as_of": (NOW - timedelta(seconds=age_s)).isoformat(),
        "ok": True,
        "degraded": [],
        "services": [{"name": "b2", "active": True, "health_ok": True}],
        "vitals": {"disk_pct": 40, "load_per_core": {"15m": 0.2}, "mem_pct": 30, "temp_c": 50},
    }
    doc.update(kw)
    return {"ok": True, "heartbeat": doc}


N = {"name": "pi", "url": "http://x/api/heartbeat"}


def test_green():
    r = nd.summarize_node(N, hb(status="green"), now=NOW)
    assert r["status"] == "green" and r["vitals"]["disk_pct"] == 40 and r["age_s"] == 30


def test_amber_and_red():
    assert nd.summarize_node(N, hb(status="amber"), now=NOW)["status"] == "amber"
    assert nd.summarize_node(N, hb(degraded=[{"service": "b2"}]), now=NOW)["status"] == "amber"
    r = nd.summarize_node(N, hb(ok=False, services=[{"name": "b2", "active": False, "health_ok": None}]), now=NOW)
    assert r["status"] == "red" and r["services_down"] == ["b2"]


def test_old_heartbeat_is_stale_not_healthy():
    r = nd.summarize_node(N, hb(age_s=900, status="green"), now=NOW)
    assert r["status"] == "stale" and "older" in r["reason"]


def test_unreachable_is_stale():
    def boom(_u):
        raise OSError("down")
    out = nd.build_nodes_payload([N], fetch=boom, now=NOW)
    assert out["nodes"][0]["status"] == "stale" and "unreachable" in out["nodes"][0]["reason"]


def test_payload_without_heartbeat_is_stale():
    assert nd.summarize_node(N, {"ok": False, "available": False}, now=NOW)["status"] == "stale"


def test_node_list_from_file(tmp_path):
    p = tmp_path / "nodes.json"
    p.write_text(json.dumps({"nodes": [{"name": "a", "url": "http://a"}, {"name": "nourl"}]}))
    assert [n["name"] for n in nd.load_nodes({"HORIZON_NODES": str(p)})] == ["a"]
    assert len(nd.load_nodes({})) == 2


def test_index_has_nodes_card():
    html = (ROOT / "research/horizon/index.html").read_text(encoding="utf-8")
    assert 'id="nodes-card"' in html and "/api/nodes" in html
