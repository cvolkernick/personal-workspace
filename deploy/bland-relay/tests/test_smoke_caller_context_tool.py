"""smoke.py --check caller-context-tool. Fixture server and a local relay: no Bland, no prism."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import bland_relay
import caller_context
import smoke

TOKEN = "caller-context-smoke-token"
LEAK = "unique-caller-prose-9f3a"
EMPTY = {
    "known": False,
    "caller_context": "No previous contact on file.",
    "last_contact_at": "",
    "open_followups": [],
    "caller_type": "unknown",
    "backend": "index",
}


class Handler(BaseHTTPRequestHandler):
    mode = "ok"
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

    def do_GET(self):
        self._send(404, {"ok": False})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        auth = self.headers.get("Authorization") or ""
        Handler.seen.append((self.path, auth, body))
        if self.path != "/bland/caller-context":
            return self._send(404, {"ok": False})
        if auth != "Bearer " + TOKEN:
            return self._send(401, {"ok": False, "error": "uniq-401-marker"})
        if Handler.mode == "slow":
            time.sleep(0.02)
        if Handler.mode == "malformed":
            return self._send(200, raw=b"not-json-LEAKED-BODY")
        if Handler.mode == "leak":
            return self._send(200, dict(EMPTY, known=True, caller_context=LEAK))
        return self._send(200, EMPTY)


@pytest.fixture
def http():
    Handler.mode = "ok"
    Handler.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address
    try:
        yield "http://%s:%d" % (host, port)
    finally:
        srv.shutdown()
        srv.server_close()


def report_of(base, **kw):
    kw.setdefault("n", 4)
    return smoke.run_caller_context_tool(base, TOKEN, **kw)


def names(report):
    return [row["name"] for row in report["cases"]]


def test_pass_case_names_and_no_token_in_the_report(http):
    report = report_of(http)
    assert report["check"] == "caller-context-tool"
    assert report["pass"] is True
    assert names(report) == [
        "no_token", "wrong_token", "unknown_number", "garbage_body", "empty_body", "latency",
    ]
    assert report["cases"][-1]["n"] == 4 and report["cases"][-1]["ok"] == 4
    text = json.dumps(report)
    assert TOKEN not in text
    assert LEAK not in text
    assert "No previous contact" not in text
    assert "uniq-401-marker" not in text


def test_401_and_probe_shape(http):
    report = report_of(http, n=3)
    by = {row["name"]: row for row in report["cases"]}
    assert by["no_token"]["status"] == 401 and by["no_token"]["pass"] is True
    assert by["wrong_token"]["status"] == 401 and by["wrong_token"]["pass"] is True
    bodies = [json.loads(b) for _p, _a, b in Handler.seen if b.startswith(b"{")]
    assert bodies
    for body in bodies:
        assert set(body) == {"call_id", "phone_number"}
        assert body["call_id"].startswith("smoke-ctx-")
        assert body["phone_number"].startswith("+1207555")
    assert b"not json" in [b for _p, _a, b in Handler.seen]
    assert b"" in [b for _p, _a, b in Handler.seen]


def test_leak_and_malformed_fail_without_echoing_the_body(http):
    for mode in ("leak", "malformed"):
        Handler.mode = mode
        Handler.seen = []
        report = report_of(http)
        assert report["pass"] is False
        text = json.dumps(report)
        assert LEAK not in text
        assert "not-json-LEAKED-BODY" not in text
        assert TOKEN not in text


def test_slow_server_fails_p95(http):
    Handler.mode = "slow"
    report = report_of(http, n=3, limit_ms=1)
    latency = report["cases"][-1]
    assert latency["pass"] is False and latency["p95_ms"] >= 1
    assert report["pass"] is False


def test_cli_unset_token_sends_nothing(capsys, monkeypatch, http):
    monkeypatch.delenv("CALLER_CONTEXT_TOKEN", raising=False)
    monkeypatch.setenv("MEETING_CHECK_TOKEN", "meeting-token-should-not-be-used")
    code = smoke.main(["--check", "caller-context-tool", "--base", http])
    assert code == 2
    out = json.loads(capsys.readouterr().out)
    assert out == {"check": "caller-context-tool", "error": "token_unset", "pass": False}
    assert Handler.seen == []


def test_cli_default_n_and_token_env(capsys, monkeypatch):
    monkeypatch.setenv("CALLER_CONTEXT_TOKEN", TOKEN)
    monkeypatch.setenv("RELAY_PUBLIC_BASE", "http://127.0.0.1:9")
    seen = {}

    def fake(base, token, n, limit_ms, client=None):
        seen.update(base=base, token=token, n=n)
        return {"check": "caller-context-tool", "pass": True, "cases": []}

    monkeypatch.setattr(smoke, "run_caller_context_tool", fake)
    assert smoke.main(["--check", "caller-context-tool"]) == 0
    assert seen == {"base": "http://127.0.0.1:9", "token": TOKEN, "n": smoke.TOOL_N_DEFAULT}
    assert TOKEN not in capsys.readouterr().out
    assert smoke.main(["--check", "caller-context-tool", "--base", "http://127.0.0.1:9", "--n", "41"]) == 2


def test_against_the_local_relay(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("CALLER_CONTEXT_TOKEN", TOKEN)
    monkeypatch.delenv("CRM_API_BASE", raising=False)
    monkeypatch.delenv("CRM_API_TOKEN", raising=False)
    caller_context.reset_limits()
    srv = bland_relay.ThreadingHTTPServer(("127.0.0.1", 0), bland_relay.H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address
    try:
        code = smoke.main([
            "--check", "caller-context-tool",
            "--base", "http://%s:%d" % (host, port),
            "--n", "4",
        ])
    finally:
        srv.shutdown()
        srv.server_close()
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["pass"] is True
    assert names(out) == [
        "no_token", "wrong_token", "unknown_number", "garbage_body", "empty_body", "latency",
    ]
    assert TOKEN not in json.dumps(out)
    assert "No previous contact" not in json.dumps(out)
