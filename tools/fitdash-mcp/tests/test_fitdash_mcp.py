import importlib.util
from pathlib import Path

import pytest

try:
    import httpx
except ImportError:  # CI installs it via requirements-ci.txt
    httpx = None

pytestmark = pytest.mark.skipif(httpx is None, reason="httpx not installed")

_spec = importlib.util.spec_from_file_location(
    "fitdash_mcp", Path(__file__).resolve().parents[1] / "fitdash_mcp.py")
fm = importlib.util.module_from_spec(_spec)
if httpx is not None:
    _spec.loader.exec_module(fm)
else:
    fm.SECTIONS = ()

TOKEN = "test-secret-token-xyz"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("FITDASH_AGENT_TOKEN", TOKEN)
    monkeypatch.setenv("FITDASH_API_BASE", "https://fitdash.test:10001/")


def _transport(status=200, body=None, seen=None):
    def handler(req):
        if seen is not None:
            seen.append(req)
        return httpx.Response(status, json=body if body is not None else {})
    return httpx.MockTransport(handler)


@pytest.mark.parametrize("section", fm.SECTIONS)
def test_returns_json_as_is(section):
    body = {"generated_at": "2026-10-19T21:00:00Z", "as_of": "2026-10-19", "x": [1]}
    seen = []
    assert fm.fetch(section, transport=_transport(body=body, seen=seen)) == body
    req = seen[0]
    assert req.method == "GET"
    assert str(req.url) == f"https://fitdash.test:10001/api/agent/{section}"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"


def test_401_clear_error_without_token():
    with pytest.raises(fm.FitDashError) as ei:
        fm.fetch("targets", transport=_transport(401, {"error": "unauthorized"}))
    msg = str(ei.value)
    assert "401" in msg and "token" in msg
    assert TOKEN not in msg


def test_unreachable_host_clear_error():
    def boom(req):
        raise httpx.ConnectError("conn refused " + req.headers["authorization"])
    with pytest.raises(fm.FitDashError) as ei:
        fm.fetch("plan", transport=httpx.MockTransport(boom))
    assert "unreachable" in str(ei.value)
    assert TOKEN not in str(ei.value)


def test_other_http_error_redacts_token():
    t = httpx.MockTransport(lambda r: httpx.Response(500, text="echo Bearer " + TOKEN))
    with pytest.raises(fm.FitDashError) as ei:
        fm.fetch("plan", transport=t)
    assert "500" in str(ei.value) and TOKEN not in str(ei.value)


def test_logs_params_and_validation():
    seen = []
    fm.fetch_logs("2026-10-01", "2026-10-19", transport=_transport(seen=seen))
    assert seen[0].url.params["from"] == "2026-10-01"
    assert seen[0].url.params["to"] == "2026-10-19"
    seen.clear()
    fm.fetch_logs(transport=_transport(seen=seen))
    assert not seen[0].url.params
    with pytest.raises(fm.FitDashError):
        fm.fetch_logs("2026-01-01", "2026-10-19", transport=_transport())
    with pytest.raises(fm.FitDashError):
        fm.fetch_logs("10/01/2026", None, transport=_transport())
    with pytest.raises(fm.FitDashError):
        fm.fetch_logs("2026-10-19", "2026-10-01", transport=_transport())


def test_missing_token(monkeypatch, tmp_path):
    monkeypatch.delenv("FITDASH_AGENT_TOKEN")
    monkeypatch.setenv("FITDASH_AGENT_TOKEN_FILE", str(tmp_path / "nope"))
    with pytest.raises(fm.FitDashError, match="No FitDash agent token"):
        fm.fetch("targets", transport=_transport())


def test_proxy_socks5h_normalized(monkeypatch):
    monkeypatch.setenv("FITDASH_PROXY", "socks5h://127.0.0.1:1055")
    assert fm._proxy() == "socks5://127.0.0.1:1055"


def test_server_registers_section_tools_and_coaching_targets():
    pytest.importorskip("mcp")
    import asyncio
    tools = asyncio.run(fm.build_server().list_tools())
    names = sorted(t.name for t in tools)
    expect = sorted(tool["name"] for tool in fm.TOOL_CATALOG)
    assert names == expect
    assert "get_phase" in names
    assert "get_coach_card" in names


def test_coaching_targets_get_and_set_use_the_agent_token():
    seen = []
    body = {"ok": True, "as_of": "2026-11-04", "targets": {"steps": {"effective": 9000}}}
    got = fm.fetch_coaching_targets("2026-11-04", transport=_transport(body=body, seen=seen))
    assert got == body
    assert seen[0].method == "GET"
    assert str(seen[0].url).startswith("https://fitdash.test:10001/api/agent/coaching_targets")
    assert seen[0].url.params["as_of"] == "2026-11-04"
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    seen.clear()
    fm.post_coaching_target("steps", 9000, transport=_transport(body=body, seen=seen))
    assert seen[0].method == "POST"
    assert str(seen[0].url) == "https://fitdash.test:10001/api/agent/coaching_targets"
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    import json
    assert json.loads(seen[0].content) == {"target": "steps", "value": 9000}


def test_coaching_target_bounds_do_not_call_the_api():
    seen = []
    transport = _transport(seen=seen)
    for target, value in (
        ("steps", 999),
        ("steps", 30001),
        ("steps", True),
        ("kcal", 1199),
        ("kcal", 6001),
        ("hydration_ml", 499),
        ("hydration_ml", 8001),
        ("fiber", 35),
    ):
        with pytest.raises(fm.FitDashError):
            fm.post_coaching_target(target, value, transport=transport)
    assert seen == []
    fm.validate_coaching_target("steps", 1000)
    fm.validate_coaching_target("steps", 30000)
    fm.validate_coaching_target("kcal", 1200)
    fm.validate_coaching_target("kcal", 6000)
    fm.validate_coaching_target("hydration_ml", 500)
    fm.validate_coaching_target("hydration_ml", 8000)
    assert fm.validate_coaching_target("steps", None) is None


def test_phase_and_coach_card_use_the_agent_token():
    seen = []
    body = {
        "ok": True,
        "phase": "cut",
        "median_7d": 10.35,
        "hits": 0,
        "hits_needed": 12,
        "counted": 4,
        "counted_needed": 14,
        "median_14d": None,
        "thin": False,
        "paused": False,
        "flip_applied": False,
        "flip_date": None,
        "targets": {"calories": 2000, "protein_g": 170, "carbs_g": 195, "fat_g": 60, "phase": "cut"},
    }
    got = fm.fetch_phase("2026-11-10", transport=_transport(body=body, seen=seen))
    assert got["phase"] == "cut"
    assert got["median_7d"] == 10.35
    assert seen[0].method == "GET"
    assert str(seen[0].url).startswith("https://fitdash.test:10001/api/agent/phase")
    assert seen[0].url.params["as_of"] == "2026-11-10"
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    seen.clear()
    card = {"ok": True, "phase_report": body, "coach_card": {"phase_report": body}}
    got = fm.fetch_coach_card(transport=_transport(body=card, seen=seen))
    assert got["phase_report"]["hits"] == 0
    assert got["coach_card"]["phase_report"]["phase"] == "cut"
    assert str(seen[0].url) == "https://fitdash.test:10001/api/agent/coach_card"
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"


def test_coaching_target_post_redacts_token():
    t = httpx.MockTransport(lambda r: httpx.Response(400, text="nope Bearer " + TOKEN))
    with pytest.raises(fm.FitDashError) as ei:
        fm.post_coaching_target("steps", 9000, transport=t)
    assert "400" in str(ei.value) and TOKEN not in str(ei.value)
