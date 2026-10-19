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


def test_server_registers_six_readonly_tools():
    pytest.importorskip("mcp")
    import asyncio
    tools = asyncio.run(fm.build_server().list_tools())
    assert sorted(t.name for t in tools) == sorted(f"fitdash_{s}" for s in fm.SECTIONS)
