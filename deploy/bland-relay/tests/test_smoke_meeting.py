"""smoke.py --check meeting-check. Mocked HTTP and a local relay with no calendar keys: no network, no real calendar."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import bland_relay
import meeting_check
import smoke

TOKEN = "meeting-check-smoke-token"
LEAK = "unique-meeting-time-text-4c1d"
BAD_BODY = "not-json-LEAKED-BODY"


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
        if self.path != "/bland/meeting-check":
            return self._send(404, {"ok": False})
        if auth != "Bearer " + TOKEN:
            return self._send(401, {"ok": False, "error": "uniq-401-marker"})
        if Handler.mode == "slow":
            time.sleep(0.02)
        if Handler.mode == "malformed":
            return self._send(200, raw=BAD_BODY.encode())
        if Handler.mode == "match":
            return self._send(200, {"match": True, "meeting_time": LEAK})
        if Handler.mode == "extra":
            return self._send(200, {"match": False, "meeting_time": LEAK})
        if Handler.mode == "error500":
            return self._send(500, {"ok": False})
        return self._send(200, {"match": False})


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
    return smoke.run_meeting_check(base, TOKEN, **kw)


def names(report):
    return [row["name"] for row in report["cases"]]


def test_pass_case_and_case_names(http):
    report = report_of(http)
    assert report["check"] == "meeting-check"
    assert report["pass"] is True
    assert names(report) == ["no_token", "wrong_token", "non_match", "garbage_body", "empty_body", "latency"]
    assert report["cases"][-1]["n"] == 4 and report["cases"][-1]["ok"] == 4
    text = json.dumps(report)
    assert TOKEN not in text
    assert "uniq-401-marker" not in text


def test_401_without_and_with_wrong_token(http):
    by = {row["name"]: row for row in report_of(http)["cases"]}
    assert by["no_token"]["status"] == 401 and by["no_token"]["pass"] is True
    assert by["wrong_token"]["status"] == 401 and by["wrong_token"]["pass"] is True
    auths = [a for _p, a, _b in Handler.seen[:2]]
    assert auths[0] == "" and auths[1] != "Bearer " + TOKEN and auths[1].startswith("Bearer ")


def test_probe_is_non_matching_unique_and_synthetic(http):
    report_of(http, n=6)
    bodies = [json.loads(b) for _p, _a, b in Handler.seen if b.startswith(b"{")]
    assert len(bodies) == 2 + 1 + 6  # two 401 probes, one non-match probe, six latency probes
    ids = [b["call_id"] for b in bodies]
    assert len(set(ids)) == len(ids)
    for b in bodies:
        assert b["meeting_date"] == "2030-01-01"
        assert b["phone_number"].startswith("+1000555")
        assert b["caller_name"] == "Smoke Probe"
        assert set(b) == {"call_id", "phone_number", "caller_name", "meeting_date", "meeting_time", "meeting_tz"}
    raws = [b for _p, _a, b in Handler.seen]
    assert b"not json" in raws and b"" in raws


def test_requests_stay_under_the_hourly_cap(http):
    report_of(http, n=smoke.MEETING_N_MAX)
    valid = [1 for _p, a, _b in Handler.seen if a == "Bearer " + TOKEN]
    assert len(valid) == 3 + smoke.MEETING_N_MAX
    assert len(valid) <= 40


@pytest.mark.parametrize("mode", ["match", "extra", "malformed", "error500"])
def test_unexpected_answers_fail_without_echoing_the_body(http, mode):
    Handler.mode = mode
    report = report_of(http)
    assert report["pass"] is False
    text = json.dumps(report)
    assert LEAK not in text and BAD_BODY not in text and TOKEN not in text


def test_slow_server_fails_p95(http):
    Handler.mode = "slow"
    report = report_of(http, n=3, limit_ms=1)
    latency = report["cases"][-1]
    assert latency["pass"] is False and latency["p95_ms"] >= 1
    assert report["pass"] is False


def test_wrong_status_for_no_token_fails(http):
    class Open(Handler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            self._send(200, {"match": False})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Open)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        host, port = srv.server_address
        report = report_of("http://%s:%d" % (host, port))
    finally:
        srv.shutdown()
        srv.server_close()
    by = {row["name"]: row for row in report["cases"]}
    assert by["no_token"]["pass"] is False and by["wrong_token"]["pass"] is False
    assert report["pass"] is False


def test_delete_and_get_stay_refused_or_unused(http):
    report_of(http)
    client = smoke.Client()
    with pytest.raises(ValueError, match="read-only"):
        client.exchange("DELETE", http + "/bland/meeting-check", {})
    assert all(path == "/bland/meeting-check" for path, _a, _b in Handler.seen)


# ---------------------------------------------------------------------- cli

def test_cli_token_unset_fails_clearly_without_a_request(capsys, monkeypatch, http):
    monkeypatch.delenv("MEETING_CHECK_TOKEN", raising=False)
    monkeypatch.setenv("CALLER_CONTEXT_TOKEN", "caller-token-should-not-be-used")
    code = smoke.main(["--check", "meeting-check", "--base", http])
    assert code == 2
    out = json.loads(capsys.readouterr().out)
    assert out == {"check": "meeting-check", "error": "token_unset", "pass": False}
    assert Handler.seen == []


def test_cli_reads_the_token_from_env_and_defaults_to_30(capsys, monkeypatch):
    monkeypatch.setenv("MEETING_CHECK_TOKEN", TOKEN)
    monkeypatch.setenv("RELAY_PUBLIC_BASE", "http://127.0.0.1:9")
    seen = {}

    def fake(base, token, n, limit_ms, client=None):
        seen.update(base=base, token=token, n=n)
        return {"check": "meeting-check", "pass": True, "cases": []}

    monkeypatch.setattr(smoke, "run_meeting_check", fake)
    assert smoke.main(["--check", "meeting-check"]) == 0
    assert seen == {"base": "http://127.0.0.1:9", "token": TOKEN, "n": 30}
    out = capsys.readouterr().out
    assert TOKEN not in out and json.loads(out)["pass"] is True
    assert smoke.main(["--check", "meeting-check", "--n", "12"]) == 0
    assert seen["n"] == 12


def test_cli_caller_context_default_is_still_50(monkeypatch):
    monkeypatch.setenv("CALLER_CONTEXT_TOKEN", TOKEN)
    monkeypatch.setenv("RELAY_PUBLIC_BASE", "http://127.0.0.1:9")
    seen = {}
    monkeypatch.setattr(smoke, "run_caller_context", lambda base, token, n, limit_ms, include_rate_limit=True, client=None:
                        seen.update(n=n) or {"check": "caller-context", "pass": True, "cases": []})
    assert smoke.main(["--check", "caller-context"]) == 0
    assert seen["n"] == 50


@pytest.mark.parametrize("n", ["0", "36", "500"])
def test_cli_rejects_n_beyond_the_hourly_cap(capsys, monkeypatch, http, n):
    monkeypatch.setenv("MEETING_CHECK_TOKEN", TOKEN)
    assert smoke.main(["--check", "meeting-check", "--base", http, "--n", n]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "bad_args"
    assert Handler.seen == []


def test_cli_rejects_a_base_that_carries_the_token(capsys, monkeypatch):
    monkeypatch.setenv("MEETING_CHECK_TOKEN", TOKEN)
    assert smoke.main(["--check", "meeting-check", "--base", "https://user:%s@funnel.example" % TOKEN]) == 2
    out = capsys.readouterr().out
    assert TOKEN not in out and json.loads(out)["error"] == "base_rejected"


def test_cli_token_env_override(capsys, monkeypatch):
    monkeypatch.delenv("MEETING_CHECK_TOKEN", raising=False)
    monkeypatch.setenv("OTHER_SMOKE_TOKEN", TOKEN)
    seen = {}
    monkeypatch.setattr(smoke, "run_meeting_check", lambda base, token, n, limit_ms, client=None:
                        seen.update(token=token) or {"check": "meeting-check", "pass": True, "cases": []})
    assert smoke.main(["--check", "meeting-check", "--base", "http://127.0.0.1:9", "--token-env", "OTHER_SMOKE_TOKEN"]) == 0
    assert seen["token"] == TOKEN


def test_report_is_withheld_if_it_would_carry_the_token(capsys, monkeypatch):
    monkeypatch.setenv("MEETING_CHECK_TOKEN", TOKEN)
    monkeypatch.setattr(smoke, "run_meeting_check", lambda *a, **k: {"check": "meeting-check", "pass": True, "note": TOKEN})
    assert smoke.main(["--check", "meeting-check", "--base", "http://127.0.0.1:9"]) == 2
    assert TOKEN not in capsys.readouterr().out


# -------------------------------------------- against the relay, no calendar keys

def test_against_a_real_relay_handler_with_calendar_keys_unset(monkeypatch, tmp_path, capsys):
    for key in ("MEETING_GCAL_CLIENT_ID", "MEETING_GCAL_CLIENT_SECRET", "MEETING_GCAL_REFRESH_TOKEN",
                "MEETING_GCAL_CALENDAR_ID", "MEETING_CHECK_DISABLED"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BLAND_RELAY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MEETING_CHECK_TOKEN", TOKEN)
    meeting_check.reset()
    srv = bland_relay.ThreadingHTTPServer(("127.0.0.1", 0), bland_relay.H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address
    try:
        code = smoke.main(["--check", "meeting-check", "--base", "http://%s:%d" % (host, port), "--n", "5"])
    finally:
        srv.shutdown()
        srv.server_close()
        checker_client = meeting_check.get_checker().client
        meeting_check.reset()
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    assert out["pass"] is True
    assert names(out) == ["no_token", "wrong_token", "non_match", "garbage_body", "empty_body", "latency"]
    assert checker_client is None  # no calendar client existed, so no calendar call was possible
    assert TOKEN not in json.dumps(out)
