"""forward(): rate-limit responses retry in the background with backoff."""
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import bland_relay

RL_BODY = "Automation run rate limit exceeded: limit 10000 per minute Bearer leaky.token"
BODY = json.dumps({"call_id": "call-abc"}).encode()


@pytest.fixture
def mock_alex(monkeypatch):
    script, auths = [], []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            auths.append(self.headers.get("Authorization"))
            code, text = script.pop(0) if script else (200, "{}")
            self.send_response(code)
            self.end_headers()
            self.wfile.write(text.encode())

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("ALEXANDRA_ALERT_URL", f"http://127.0.0.1:{srv.server_port}/hook")
    monkeypatch.setenv("ALEXANDRA_ALERT_AUTH", "Bearer test-secret-xyz")
    spawned, alerts, waits = [], [], []
    monkeypatch.setattr(bland_relay, "_spawn", lambda t, a=(): spawned.append((t, a)))
    monkeypatch.setattr(bland_relay, "alert_failure", lambda *a, **k: alerts.append(a))
    monkeypatch.setattr(bland_relay.time, "sleep", waits.append)
    yield script, auths, spawned, alerts, waits
    srv.shutdown()


def test_rate_limit_returns_fast_then_retries_to_success(mock_alex, caplog):
    script, auths, spawned, alerts, waits = mock_alex
    script[:] = [(400, RL_BODY), (400, RL_BODY), (429, "slow down"), (200, "{}")]
    caplog.set_level(logging.INFO)
    assert bland_relay.forward(BODY) == (202, "", 1)
    assert len(spawned) == 1 and waits == []
    target, args = spawned.pop()
    assert target(*args) is True
    assert waits == [15, 30, 60]
    assert not alerts
    text = caplog.text
    assert "RATE_LIMIT call_id=call-abc attempt=1 status=400" in text
    assert "RATE_LIMIT call_id=call-abc attempt=3 status=429" in text
    assert "RATE_LIMIT_FINAL call_id=call-abc result=success attempts=4" in text
    assert "test-secret-xyz" not in text and "leaky.token" not in text


def test_rate_limit_exhausted_alerts_once(mock_alex, caplog):
    script, _a, spawned, alerts, waits = mock_alex
    script[:] = [(400, RL_BODY)] * 5
    bland_relay.forward(BODY)
    target, args = spawned.pop()
    assert target(*args) is False
    assert waits == [15, 30, 60, 120] and sum(waits) < 240
    assert len(alerts) == 1 and alerts[0][0] == "call-abc"
    assert "RATE_LIMIT_FINAL call_id=call-abc result=failed attempts=5" in caplog.text


def test_other_errors_keep_two_inline_attempts(mock_alex):
    script, auths, spawned, _al, _w = mock_alex
    script[:] = [(500, "boom"), (500, "boom")]
    status, err, attempts = bland_relay.forward(BODY)
    assert status is None and attempts == 2 and err.startswith("HTTP 500")
    assert spawned == [] and len(auths) == 2


def test_success_first_try(mock_alex):
    assert bland_relay.forward(BODY) == (200, "", 1)
