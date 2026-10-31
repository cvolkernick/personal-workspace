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
    expect = sorted([
        *(f"fitdash_{s}" for s in fm.SECTIONS),
        "get_coaching_targets",
        "set_coaching_target",
        "get_pantry",
        "update_pantry",
    ])
    assert names == expect


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


def test_coaching_target_post_redacts_token():
    t = httpx.MockTransport(lambda r: httpx.Response(400, text="nope Bearer " + TOKEN))
    with pytest.raises(fm.FitDashError) as ei:
        fm.post_coaching_target("steps", 9000, transport=t)
    assert "400" in str(ei.value) and TOKEN not in str(ei.value)


# ------------------------------------------------------------ pantry tools
WTOKEN = "test-write-token-abc"
INV = {"ok": True, "generated_at": "g", "as_of": "a", "data": {
    "count": 3, "in_stock_count": 2, "out_of_stock_count": 1, "note": "n", "items": [
        {"id": "oats", "name": "Oats", "stock": "in", "in_stock": True, "per_serving": {"protein_g": 5.0}},
        {"id": "rice", "name": "Jasmine Rice", "stock": "low", "in_stock": True, "per_serving": None},
        {"id": "tilapia", "name": "Tilapia", "stock": "out", "in_stock": False, "per_serving": None},
    ]}}


@pytest.fixture
def wtok(monkeypatch):
    monkeypatch.setenv("FITDASH_INVENTORY_AGENT_TOKEN", WTOKEN)


def test_get_pantry_uses_read_token_and_filters():
    seen = []
    out = fm.get_pantry(transport=_transport(body=INV, seen=seen))
    assert seen[0].method == "GET" and str(seen[0].url).endswith("/api/agent/inventory")
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert out["returned"] == 3 and out["out_of_stock_count"] == 1
    assert out["items"][0]["per_serving"] == {"protein_g": 5.0}
    assert [i["id"] for i in fm.get_pantry("out", transport=_transport(body=INV))["items"]] == ["tilapia"]
    assert [i["id"] for i in fm.get_pantry("low", transport=_transport(body=INV))["items"]] == ["rice"]
    assert [i["id"] for i in fm.get_pantry(query="RICE", transport=_transport(body=INV))["items"]] == ["rice"]
    with pytest.raises(fm.FitDashError):
        fm.get_pantry("maybe", transport=_transport(body=INV))


def test_update_pantry_routes_and_uses_write_token(wtok):
    seen = []
    t = _transport(body={"ok": True, "as_of": "x", "items": [{"id": "z"}]}, seen=seen)
    out = fm.update_pantry(add=[{"name": "Skyr", "serving_g": 150}], restock=[{"id": "oats", "qty": 3}, "rice"],
                           out_of_stock=["tilapia"], updated_by="nourish", transport=t)
    assert [str(r.url) for r in seen] == ["https://fitdash.test:10001/api/agent/inventory/items",
                                          "https://fitdash.test:10001/api/agent/inventory/stock"]
    assert all(r.method == "POST" and r.headers["authorization"] == f"Bearer {WTOKEN}" for r in seen)
    import json as _j
    assert _j.loads(seen[0].content) == {"items": [{"name": "Skyr", "serving_g": 150}], "updated_by": "nourish"}
    assert _j.loads(seen[1].content)["items"] == [
        {"id": "oats", "in_stock": True, "qty": 3}, {"id": "rice", "in_stock": True},
        {"id": "tilapia", "in_stock": False, "qty": 0}]
    assert out["added"] and out["stock_updated"]


def test_update_pantry_stock_only_skips_create(wtok):
    seen = []
    fm.update_pantry(out_of_stock=["oats"], transport=_transport(body={"items": []}, seen=seen))
    assert len(seen) == 1 and str(seen[0].url).endswith("/inventory/stock")


def test_update_pantry_local_validation(wtok):
    t = _transport()
    for kw in ({}, {"restock": [{"qty": 1}]}, {"restock": [{"id": "a", "stock": "low"}]},
               {"out_of_stock": [""]}, {"restock": ["a"], "out_of_stock": ["A"]},
               {"out_of_stock": [f"i{n}" for n in range(101)]}):
        with pytest.raises(fm.FitDashError):
            fm.update_pantry(transport=t, **kw)


def test_update_pantry_never_uses_read_token(monkeypatch, tmp_path):
    monkeypatch.delenv("FITDASH_INVENTORY_AGENT_TOKEN", raising=False)
    monkeypatch.setenv("FITDASH_INVENTORY_AGENT_TOKEN_FILE", str(tmp_path / "nope"))
    with pytest.raises(fm.FitDashError, match="inventory agent token"):
        fm.update_pantry(out_of_stock=["oats"], transport=_transport())


def test_update_pantry_errors_redact_token(wtok):
    t = httpx.MockTransport(lambda r: httpx.Response(409, text="conflict " + r.headers["authorization"]))
    with pytest.raises(fm.FitDashError) as ei:
        fm.update_pantry(add=[{"name": "Oats"}], transport=t)
    assert "409" in str(ei.value) and WTOKEN not in str(ei.value)
    with pytest.raises(fm.FitDashError, match="401"):
        fm.update_pantry(out_of_stock=["x"], transport=_transport(401))


def test_post_rejects_non_pantry_paths(wtok):
    with pytest.raises(fm.FitDashError):
        fm.post("generate-plan", {}, transport=_transport())
