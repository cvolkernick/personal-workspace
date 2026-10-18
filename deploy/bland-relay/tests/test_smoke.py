"""Public caller-context smoke (#1229). Fixture server only: no network, no token in the report."""
import ast
import inspect
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import bland_relay
import caller_context
import smoke

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "caller-context-test-token"
LEAK = "unique-caller-prose-9f3a"
BAD_BODY = "not-json-LEAKED-BODY"
EMPTY = {
    "known": False,
    "caller_context": "No previous contact on file.",
    "last_contact_at": "",
    "open_followups": [],
    "caller_type": "unknown",
    "backend": "empty",
}


class Handler(BaseHTTPRequestHandler):
    mode = "ok"
    count = 0
    seen = []

    def log_message(self, fmt, *args):
        return

    def _send(self, code, obj=None, raw=None):
        if raw is None:
            raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        Handler.seen.append((self.command, n, body))
        self._send(200, {"ok": True})

    def do_GET(self):
        Handler.count += 1
        if Handler.mode == "limit" and Handler.count >= 61:
            return self._send(429, {"ok": False, "error": "rate_limited"})
        if Handler.mode == "slow":
            import time
            time.sleep(0.02)
        auth = self.headers.get("Authorization") or ""
        if not auth:
            return self._send(401, {"ok": False, "error": "uniq-missing-marker"})
        if auth != "Bearer " + TOKEN:
            return self._send(401, {"ok": False, "error": "uniq-wrong-marker"})
        if Handler.mode == "malformed":
            return self._send(200, raw=BAD_BODY.encode())
        if Handler.mode == "leak" and "pn=%2B10000000000" in self.path:
            body = dict(EMPTY)
            body["caller_context"] = LEAK
            return self._send(200, body)
        return self._send(200, EMPTY)


@pytest.fixture
def http():
    Handler.mode = "ok"
    Handler.count = 0
    Handler.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    host, port = srv.server_address
    try:
        yield "http://%s:%d" % (host, port)
    finally:
        srv.shutdown()
        srv.server_close()


def _report(base, **kwargs):
    kwargs.setdefault("include_rate_limit", False)
    kwargs.setdefault("n", 4)
    return smoke.run_caller_context(base, TOKEN, **kwargs)


def _text(report):
    return json.dumps(report)


def test_smoke_module_is_stdlib_only():
    tree = ast.parse((ROOT / "smoke.py").read_text())
    allowed = {"argparse", "json", "os", "sys", "time", "urllib"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] in allowed


def test_pass_case_hides_caller_text(http):
    report = _report(http)
    assert report["pass"] is True
    names = [row["name"] for row in report["cases"]]
    assert names == ["no_token", "wrong_token", "unknown_e164", "garbage_pn", "empty_pn", "latency"]
    text = _text(report)
    assert TOKEN not in text
    assert "No previous contact on file." not in text
    assert "uniq-missing-marker" not in text
    latency = report["cases"][-1]
    assert latency["n"] == 4
    assert latency["ok"] == 4
    assert latency["p95_ms"] < 1000


def test_401_case(http):
    report = _report(http)
    by_name = {row["name"]: row for row in report["cases"]}
    assert by_name["no_token"]["status"] == 401
    assert by_name["wrong_token"]["status"] == 401
    assert by_name["no_token"]["pass"] is True
    assert by_name["wrong_token"]["pass"] is True
    assert "uniq-wrong-marker" not in _text(report)


def test_slow_server_fails_p95(http):
    Handler.mode = "slow"
    report = _report(http, n=3, limit_ms=1)
    latency = [row for row in report["cases"] if row["name"] == "latency"][0]
    assert latency["pass"] is False
    assert latency["p95_ms"] >= 1
    assert report["pass"] is False


def test_malformed_json_fails_without_echoing_the_body(http):
    Handler.mode = "malformed"
    report = _report(http)
    unknown = [row for row in report["cases"] if row["name"] == "unknown_e164"][0]
    assert unknown["pass"] is False
    assert unknown["shape"] is False
    assert report["pass"] is False
    assert BAD_BODY not in _text(report)


def test_response_body_is_not_in_the_report(http):
    Handler.mode = "leak"
    report = _report(http)
    unknown = [row for row in report["cases"] if row["name"] == "unknown_e164"][0]
    assert unknown["pass"] is True
    assert unknown["known"] is False
    assert LEAK not in _text(report)
    assert TOKEN not in _text(report)


def test_rate_limit_61st_after_the_window_wait(http):
    Handler.mode = "limit"
    slept = []

    def _sleep(delay):
        slept.append(delay)
        Handler.count = 0

    client = smoke.Client(sleep=_sleep, now=lambda: 1000.0)
    report = smoke.run_caller_context(
        http, TOKEN, n=2, include_rate_limit=True, client=client,
    )
    assert slept and slept[0] >= 60
    rate = [row for row in report["cases"] if row["name"] == "rate_limit"][0]
    assert rate["status"] == 429
    assert rate["sent"] == 61
    assert rate["pass"] is True
    assert report["pass"] is True
    assert TOKEN not in _text(report)
    assert "rate_limited" not in _text(report)


def test_post_is_empty_and_delete_is_refused(http):
    client = smoke.Client()
    status, doc, kind, _elapsed = client.exchange("POST", http + "/bland/caller-context", {})
    assert status == 200
    assert kind == "json"
    assert doc == {"ok": True}
    assert Handler.seen == [("POST", 0, b"")]
    with pytest.raises(ValueError, match="read-only"):
        client.exchange("DELETE", http + "/bland/caller-context", {})


def test_cli_rejects_a_base_that_carries_the_token(capsys, monkeypatch):
    monkeypatch.setenv(smoke.TOKEN_ENV, TOKEN)
    code = smoke.main([
        "--check", "caller-context",
        "--base", "https://user:%s@funnel.example" % TOKEN,
    ])
    assert code == 2
    out = capsys.readouterr().out
    assert TOKEN not in out
    assert json.loads(out)["error"] == "base_rejected"


def test_cli_unknown_check_and_missing_token(capsys, monkeypatch):
    monkeypatch.delenv(smoke.TOKEN_ENV, raising=False)
    code = smoke.main(["--check", "no-such-check", "--base", "http://127.0.0.1:9"])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "unknown_check"
    code = smoke.main(["--check", "caller-context", "--base", "http://127.0.0.1:9"])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "token_unset"


def test_cli_uses_env_base_and_hides_the_token(capsys, monkeypatch):
    monkeypatch.setenv(smoke.TOKEN_ENV, TOKEN)
    monkeypatch.setenv(smoke.BASE_ENV, "http://127.0.0.1:9")
    seen = {}

    def fake(base, token, n, limit_ms, include_rate_limit=True, client=None):
        seen["base"] = base
        seen["token"] = token
        seen["n"] = n
        seen["rate"] = include_rate_limit
        return {"check": "caller-context", "pass": True, "cases": []}

    monkeypatch.setattr(smoke, "run_caller_context", fake)
    code = smoke.main(["--check", "caller-context", "--n", "7"])
    assert code == 0
    assert seen == {
        "base": "http://127.0.0.1:9",
        "token": TOKEN,
        "n": 7,
        "rate": True,
    }
    out = capsys.readouterr().out
    assert TOKEN not in out
    assert json.loads(out)["pass"] is True


def test_startup_line_has_no_token_number_or_path(monkeypatch, tmp_path):
    token = "super-secret-token"
    phone = "+12075550100"
    index_path = tmp_path / "caller_index.sqlite"
    monkeypatch.setenv("CALLER_CONTEXT_TOKEN", token)
    monkeypatch.setenv("CALLER_FORGET_TOKEN", "forget-secret-token")
    monkeypatch.setenv("CRM_API_TOKEN", "crm-secret-token")
    monkeypatch.setenv("CRM_API_BASE", "http://evil.example/hidden-path")
    monkeypatch.setenv("CALLER_INDEX_PATH", str(index_path))
    store = caller_context.CallerIndex(index_path)
    store.upsert((
        "bland", "id-1", phone, "call", "in", "completed",
        "secret-purpose", "", "unknown", 0, "2026-10-09T14:00:00Z",
    ))
    store.close()
    line = bland_relay.caller_context_startup_line()
    assert re.fullmatch(
        r"caller-context: token=set backend=index index_rows=1",
        line,
    )
    assert token not in line
    assert phone not in line
    assert "secret-purpose" not in line
    assert "evil.example" not in line
    assert "hidden-path" not in line
    assert str(index_path) not in line
    assert "sqlite" not in line
    src = inspect.getsource(bland_relay.main)
    assert src.index("caller_context_startup_line") < src.index("serve_forever")


def test_startup_line_crm_and_unreadable_index(monkeypatch, tmp_path):
    monkeypatch.delenv("CALLER_CONTEXT_TOKEN", raising=False)
    monkeypatch.setenv("CRM_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("CRM_API_TOKEN", "crm-secret-token")
    monkeypatch.setenv("CALLER_INDEX_PATH", str(tmp_path))
    line = bland_relay.caller_context_startup_line()
    assert line == "caller-context: token=unset backend=crm index_rows=0"
    assert "crm-secret-token" not in line
    assert str(tmp_path) not in line
    assert "/" not in line
