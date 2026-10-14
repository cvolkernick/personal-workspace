"""Vitals from fake /proc + /sys, and #701 state-change alerting."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ORCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ORCH))

import heartbeat as hb  # noqa: E402
import heartbeat_alerts as ha  # noqa: E402


class FakeVfs:
    def __init__(self, total, free, avail, frsize=4096):
        self.f_blocks, self.f_bfree, self.f_bavail, self.f_frsize = total, free, avail, frsize


def fake_tree(tmp: Path, *, load="2.00 4.00 6.80 1/200 99", temp="61234", mem_avail=1_000_000):
    proc, sysr = tmp / "proc", tmp / "sys"
    proc.mkdir()
    (proc / "loadavg").write_text(load + "\n")
    (proc / "meminfo").write_text(f"MemTotal:  4000000 kB\nMemFree: 200000 kB\nMemAvailable: {mem_avail} kB\n")
    (proc / "uptime").write_text("12345.67 40000.00\n")
    (proc / "cpuinfo").write_text("".join(f"processor\t: {i}\n\n" for i in range(4)))
    tz = sysr / "class/thermal/thermal_zone0"
    tz.mkdir(parents=True)
    (tz / "temp").write_text(temp + "\n")
    return proc, sysr


def test_collect_vitals_fake_proc_sys(tmp_path):
    proc, sysr = fake_tree(tmp_path)
    v = hb.collect_vitals(
        proc_root=proc, sys_root=sysr,
        statvfs_fn=lambda _m: FakeVfs(1000, 150, 100),
        hostname="pi-test", throttled_reader=lambda: 0x50005,
    )
    assert v["hostname"] == "pi-test"
    assert v["cpu_cores"] == 4
    assert v["load_per_core"] == {"1m": 0.5, "5m": 1.0, "15m": 1.7}
    assert v["mem_pct"] == 75.0
    assert v["temp_c"] == 61.2
    assert v["uptime_s"] == 12345
    assert v["throttled"] is True and v["throttled_raw"] == "0x50005"
    # df-style: used / (used + avail) = 850 / 950
    assert v["disk_pct"] == 89.5


def test_collect_vitals_missing_sources(tmp_path):
    (tmp_path / "proc").mkdir(); (tmp_path / "sys").mkdir()
    v = hb.collect_vitals(proc_root=tmp_path / "proc", sys_root=tmp_path / "sys",
                          statvfs_fn=lambda _m: FakeVfs(10, 5, 5), throttled_reader=lambda: None)
    assert v["temp_c"] is None and v["throttled"] is None and v["mem_pct"] is None


def test_heartbeat_includes_vitals(tmp_path):
    doc = hb.collect_heartbeat(tmp_path, watchlist=[], vitals={"hostname": "x", "disk_pct": 1})
    assert doc["vitals"]["hostname"] == "x"


def test_file_check_freshness(tmp_path):
    f = tmp_path / "MANIFEST.json"
    f.write_text(json.dumps({"ok": True}))
    spec = {"name": "b2-puller", "file": str(f), "max_age_s": 100, "json_ok_key": "ok"}
    assert hb.collect_file_check(spec, now=os.path.getmtime(f) + 10)["health_ok"] is True
    assert hb.collect_file_check(spec, now=os.path.getmtime(f) + 1000)["health_ok"] is False
    f.write_text(json.dumps({"ok": False}))
    assert hb.collect_file_check(spec, now=os.path.getmtime(f) + 1)["health_ok"] is False


def test_watchlist_from_json(tmp_path, monkeypatch):
    p = tmp_path / "wl.json"
    p.write_text(json.dumps({"services": [{"name": "b2", "unit": "b2.service", "severity": "red"}]}))
    monkeypatch.setenv("HEARTBEAT_WATCHLIST", str(p))
    assert hb.load_watchlist()[0]["name"] == "b2"


def _doc(**v):
    base = {"disk_pct": 50, "disk_mount": "/", "load_per_core": {"15m": 0.3}, "temp_c": 50}
    base.update(v)
    return {"host": "pi", "vitals": base, "services": [], "degraded": []}


def test_thresholds_env_override():
    th = ha.load_thresholds({"HEARTBEAT_DISK_WARN_PCT": "70", "HEARTBEAT_UNIT_DOWN_CHECKS": "3"})
    assert th["disk_warn_pct"] == 70 and th["unit_down_checks"] == 3 and th["temp_c"] == 75


def test_evaluate_vitals_levels():
    th = ha.load_thresholds({})
    a, _ = ha.evaluate(_doc(disk_pct=85), {}, th)
    assert a["disk"]["level"] == "amber"
    a, _ = ha.evaluate(_doc(disk_pct=91, temp_c=76, load_per_core={"15m": 1.6}), {}, th)
    assert a["disk"]["level"] == "red" and "temp" in a and "load" in a
    a, _ = ha.evaluate(_doc(load_per_core={"15m": 1.5}), {}, th)
    assert "load" not in a  # strictly greater than 1.5


def test_unit_down_needs_two_consecutive():
    th = ha.load_thresholds({})
    doc = _doc()
    doc["services"] = [{"name": "b2", "active": False, "health_ok": None}]
    doc["degraded"] = [{"service": "b2", "severity": "red"}]
    a, s = ha.evaluate(doc, {}, th)
    assert "svc:b2" not in a
    a, s = ha.evaluate(doc, s, th)
    assert a["svc:b2"]["level"] == "red"
    doc["services"][0]["active"] = True
    a, s = ha.evaluate(doc, s, th)
    assert "svc:b2" not in a and s["down_counts"]["b2"] == 0


def test_posts_only_on_state_change_with_recovery(tmp_path):
    state = tmp_path / "alerts_state.json"
    posts = []
    post = lambda t, x: posts.append((t, x)) or {"posted": True}  # noqa: E731
    th = ha.load_thresholds({})
    r = ha.run_alerts(_doc(), state, thresholds=th, post=post)
    assert r["status"] == "green" and not posts
    ha.run_alerts(_doc(disk_pct=85), state, thresholds=th, post=post)
    ha.run_alerts(_doc(disk_pct=86), state, thresholds=th, post=post)  # same level: silent
    assert len(posts) == 1 and "amber" in posts[0][0] and "NEW" in posts[0][1]
    ha.run_alerts(_doc(disk_pct=92), state, thresholds=th, post=post)
    assert len(posts) == 2 and "CHANGED" in posts[1][1]
    d = _doc()
    ha.run_alerts(d, state, thresholds=th, post=post)
    assert len(posts) == 3 and "recovered" in posts[2][0] and "RECOVERED" in posts[2][1]
    assert d["status"] == "green" and d["alerts"] == []


def test_failed_post_retries(tmp_path):
    state = tmp_path / "s.json"
    calls = []
    bad = lambda t, x: calls.append(1) or {"posted": False, "error": "URLError"}  # noqa: E731
    th = ha.load_thresholds({})
    ha.run_alerts(_doc(temp_c=80), state, thresholds=th, post=bad)
    ha.run_alerts(_doc(temp_c=80), state, thresholds=th, post=bad)
    assert len(calls) == 2


def test_comment_body_silent_and_no_token(monkeypatch):
    body = ha.build_ops_comment_body("t", "x")
    assert "@" not in body and "#704" in body
    for k in ("GITHUB_TOKEN", "GH_TOKEN", "BUZZ_BOARD_GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert ha.post_ops_comment("t", "x")["skipped"] == "no-github-token"
