"""Mocked-HTTP tests for scripts/x-analytics/twitter_metrics.py (no network)."""

from __future__ import annotations

import json
import logging
import re
import stat
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest
import requests

import twitter_metrics as tm

ROOT = Path(__file__).resolve().parents[3]

# Obvious fakes, distinctive enough to grep for in any output.
FAKE = {
    "X_CONSUMER_KEY": "FAKE-ck-9f3a1b7c2d",
    "X_CONSUMER_SECRET": "FAKE-cs-5e8d4c6a0b",
    "X_ACCESS_TOKEN": "FAKE-at-1234567890-AbCdEfGhIj",
    "X_ACCESS_TOKEN_SECRET": "FAKE-ats-zYxWvUtSrQ",
}
NOW = datetime(2026, 10, 19, 10, 0, 0)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


USER = {
    "data": {
        "id": "42",
        "username": "ChrisVolkernick",
        "verified": True,
        "public_metrics": {
            "followers_count": 1500,
            "following_count": 300,
            "tweet_count": 4200,
            "listed_count": 12,
        },
    }
}
TWEETS = {
    "data": [
        {"id": "1", "text": "low", "public_metrics": {"like_count": 1, "impression_count": 1000}},
        {
            "id": "2",
            "text": "x" * 120,
            "public_metrics": {"like_count": 50, "retweet_count": 5, "reply_count": 2, "impression_count": 500},
        },
    ]
}
FOLLOWERS = {
    "data": [
        {"id": "7", "name": "Verified Vera", "username": "vera", "verified": True},
        {"id": "8", "name": "Plain Pat", "username": "pat", "verified": False},
    ]
}


def router(user=None, tweets=None, followers=None):
    """Return a fake requests.get that answers by URL."""

    def fake_get(url, **kwargs):
        assert kwargs.get("timeout"), "every request must have a timeout"
        if "/users/by/username/" in url:
            return user or FakeResponse(200, USER)
        if url.endswith("/tweets"):
            return tweets or FakeResponse(200, TWEETS)
        if url.endswith("/followers"):
            return followers or FakeResponse(200, FOLLOWERS)
        raise AssertionError(f"unexpected URL {url}")

    return fake_get


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for key in FAKE:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(tm.OUTPUT_DIR_ENV, raising=False)
    monkeypatch.setattr(tm, "_SECRETS", ())
    # Hard guarantee: nothing in this module may reach the network.
    def no_network(*a, **k):
        raise AssertionError("unmocked network call")

    monkeypatch.setattr(requests.sessions.Session, "request", no_network)
    monkeypatch.setattr(tm, "X_ENV_FILE", str(tmp_path / "does-not-exist.env"))
    return tmp_path


def run_main(argv, get, monkeypatch):
    for key, value in FAKE.items():
        monkeypatch.setenv(key, value)
    with mock.patch.object(tm.requests, "get", side_effect=get):
        return tm.main(argv)


# ---------- credentials ----------

def test_load_creds_from_env_file(tmp_path):
    env = tmp_path / "x-api.env"
    env.write_text(
        "# comment\nX_CONSUMER_KEY=ck\nexport X_CONSUMER_SECRET='cs'\nX_ACCESS_TOKEN=\"at\"\nX_ACCESS_TOKEN_SECRET=ats\n"
    )
    assert tm.load_x_creds(str(env)) == {
        "X_CONSUMER_KEY": "ck",
        "X_CONSUMER_SECRET": "cs",
        "X_ACCESS_TOKEN": "at",
        "X_ACCESS_TOKEN_SECRET": "ats",
    }


def test_env_vars_win_over_file(tmp_path, monkeypatch):
    env = tmp_path / "x-api.env"
    env.write_text("X_CONSUMER_KEY=from-file\nX_CONSUMER_SECRET=cs\nX_ACCESS_TOKEN=at\nX_ACCESS_TOKEN_SECRET=ats\n")
    monkeypatch.setenv("X_CONSUMER_KEY", "from-env")
    assert tm.load_x_creds(str(env))["X_CONSUMER_KEY"] == "from-env"


def test_missing_creds_exit_1_names_keys_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("X_CONSUMER_KEY", FAKE["X_CONSUMER_KEY"])
    with mock.patch.object(tm.requests, "get") as get:
        assert tm.main(["--output-dir", str(tmp_path / "out")]) == 1
        get.assert_not_called()
    err = capsys.readouterr().err
    assert "X_CONSUMER_SECRET" in err and "X_ACCESS_TOKEN_SECRET" in err
    assert FAKE["X_CONSUMER_KEY"] not in err


def test_check_config_no_network_no_values(monkeypatch, capsys):
    for key, value in FAKE.items():
        monkeypatch.setenv(key, value)
    with mock.patch.object(tm.requests, "get") as get:
        assert tm.main(["--check-config"]) == 0
        get.assert_not_called()
    out = capsys.readouterr().out
    assert "True" in out
    assert not any(v in out for v in FAKE.values())


# ---------- success ----------

def test_success_report_snapshot_and_exit_0(tmp_path, monkeypatch, capsys):
    out_dir = tmp_path / "out"
    assert run_main(["--output-dir", str(out_dir)], router(), monkeypatch) == 0
    out = capsys.readouterr().out
    assert "=== X Account Analysis for @ChrisVolkernick ===" in out
    assert "Followers: 1,500" in out
    assert "TOP PERFORMING RECENT TWEETS" in out
    # the 11.4%-engagement tweet ranks first and its long text is cut at 80 chars
    assert "1. " + "x" * 80 + "..." in out and "2. low" in out
    assert "Verified followers (sample of 100): 1" in out
    assert "Sample verified followers: Verified Vera" in out
    assert "No previous snapshot found." in out
    last = out.strip().splitlines()[-1]
    parsed = json.loads(last)
    assert parsed["metrics"]["followers_count"] == 1500 and parsed["verified_sample_count"] == 1
    assert "errors" not in parsed

    files = {p.name for p in out_dir.iterdir()}
    today = datetime.now().strftime("%Y-%m-%d")
    assert files == {f"{today}.json", f"{today}.txt", "latest.json"}
    assert json.loads((out_dir / "latest.json").read_text())["metrics"]["followers_count"] == 1500
    assert stat.S_IMODE((out_dir / "latest.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(out_dir.stat().st_mode) == 0o700


def test_delta_section_content(tmp_path, monkeypatch, capsys):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "2026-10-12.json").write_text(
        json.dumps({"metrics": {"followers_count": 1400, "tweet_count": 4190}, "verified_sample_count": 0})
    )
    (out_dir / "2026-10-05.json").write_text(json.dumps({"metrics": {"followers_count": 1}}))
    for key, value in FAKE.items():
        monkeypatch.setenv(key, value)
    with mock.patch.object(tm.requests, "get", side_effect=router()):
        assert tm.run(out_dir, dict(FAKE), now=NOW) == 0
    out = capsys.readouterr().out
    assert "CHANGE VS PREVIOUS SNAPSHOT (2026-10-12)" in out
    assert "Followers: +100 (1,400 -> 1,500)" in out
    assert "Tweets: +10 (4,190 -> 4,200)" in out
    assert "Verified followers in sample: +1" in out


def test_output_dir_precedence(tmp_path, monkeypatch):
    assert tm.resolve_output_dir("/a/b") == Path("/a/b")
    monkeypatch.setenv(tm.OUTPUT_DIR_ENV, str(tmp_path / "env"))
    assert tm.resolve_output_dir() == tmp_path / "env"
    assert tm.resolve_output_dir("/cli") == Path("/cli")
    monkeypatch.delenv(tm.OUTPUT_DIR_ENV)
    assert tm.resolve_output_dir() == Path.home() / "x-analytics-output"


# ---------- failures ----------

def test_401_exits_nonzero_status_and_title_only(tmp_path, monkeypatch, capsys):
    body = {"title": "Unauthorized", "detail": "Unauthorized", "type": "about:blank", "status": 401}
    rc = run_main(["--output-dir", str(tmp_path / "out")], router(user=FakeResponse(401, body)), monkeypatch)
    assert rc == 1
    cap = capsys.readouterr()
    assert "HTTP 401 - Unauthorized" in cap.err
    assert "Failed to fetch user data" in cap.err
    assert not (tmp_path / "out").exists(), "no snapshot on fatal failure"
    assert "type" not in cap.err and "about:blank" not in cap.err


def test_429_exits_nonzero_and_does_not_retry(tmp_path, monkeypatch, capsys):
    calls = []
    inner = router(user=FakeResponse(429, {"title": "Too Many Requests", "detail": "Too Many Requests"}))

    def counting(url, **kw):
        calls.append(url)
        return inner(url, **kw)

    rc = run_main(["--output-dir", str(tmp_path / "out")], counting, monkeypatch)
    assert rc == 1
    assert len(calls) == 1
    assert "HTTP 429 - Too Many Requests" in capsys.readouterr().err


def test_partial_failure_writes_snapshot_with_errors_and_exits_2(tmp_path, monkeypatch, capsys):
    followers = FakeResponse(403, {"title": "Forbidden", "detail": "x"})
    out_dir = tmp_path / "out"
    rc = run_main(["--output-dir", str(out_dir)], router(followers=followers), monkeypatch)
    assert rc == 2
    cap = capsys.readouterr()
    assert "followers: HTTP 403 - Forbidden" in cap.err
    snap = json.loads((out_dir / "latest.json").read_text())
    assert snap["errors"] == ["followers: HTTP 403 Forbidden"]
    assert snap["metrics"]["followers_count"] == 1500


def test_network_exception_and_non_json_error_body(tmp_path, monkeypatch, capsys):
    def boom(url, **kw):
        raise requests.ConnectionError(f"dns failure for {FAKE['X_ACCESS_TOKEN']}")

    assert run_main(["--output-dir", str(tmp_path / "o1")], boom, monkeypatch) == 1
    err = capsys.readouterr().err
    assert "ConnectionError" in err and FAKE["X_ACCESS_TOKEN"] not in err

    html = FakeResponse(502, None, text="<html>bad gateway</html>")
    assert run_main(["--output-dir", str(tmp_path / "o2")], router(user=html), monkeypatch) == 1
    err = capsys.readouterr().err
    assert "HTTP 502 - no title" in err and "<html>" not in err


# ---------- credentials never leak ----------

@pytest.mark.parametrize("status", [401, 429, 403, 500])
def test_no_credential_values_in_stdout_stderr_log_or_files_on_failure(
    status, tmp_path, monkeypatch, capsys, caplog
):
    """The API body and title deliberately echo every credential value."""
    echo = " ".join(FAKE.values())
    body = {"title": f"Unauthorized {echo}", "detail": echo, "errors": [{"message": echo}]}
    out_dir = tmp_path / "out"
    caplog.set_level(logging.DEBUG)
    rc = run_main(["--output-dir", str(out_dir)], router(user=FakeResponse(status, body, text=echo)), monkeypatch)
    assert rc == 1
    cap = capsys.readouterr()
    blob = cap.out + cap.err + caplog.text
    if out_dir.exists():
        blob += "".join(p.read_text() for p in out_dir.iterdir())
    for name, value in FAKE.items():
        assert value not in blob, f"{name} value leaked"
    assert "FAKE-" not in blob
    assert f"HTTP {status}" in cap.err  # we did report the failure, just not the body


def test_no_credential_values_leak_on_partial_failure_or_success(tmp_path, monkeypatch, capsys, caplog):
    echo = " ".join(FAKE.values())
    tweets = FakeResponse(401, {"title": f"Unauthorized {echo}"})
    out_dir = tmp_path / "out"
    caplog.set_level(logging.DEBUG)
    assert run_main(["--output-dir", str(out_dir)], router(tweets=tweets), monkeypatch) == 2
    cap = capsys.readouterr()
    blob = cap.out + cap.err + caplog.text + "".join(p.read_text() for p in out_dir.iterdir())
    assert "FAKE-" not in blob


def test_scrub_replaces_longest_first():
    tm.configure_secrets({"a": "abc", "b": "abcdef"})
    assert tm.scrub("x abcdef y abc") == "x [redacted] y [redacted]"


# ---------- repo hygiene ----------

def test_script_has_no_hardcoded_credentials_or_body_prints():
    src = (Path(tm.__file__)).read_text(encoding="utf-8")
    assert not re.search(r"response\.text", src), "never print API response bodies"
    assert not re.search(r"AAAA[A-Za-z0-9%]{20,}", src)  # bearer-token shape
    assert not re.search(r"['\"][A-Za-z0-9]{25,}['\"]", src)


def test_systemd_units_match_schedule_and_have_no_gateway_or_secrets():
    svc = (ROOT / "deploy/units/x-weekly-analytics.service").read_text()
    timer = (ROOT / "deploy/units/x-weekly-analytics.timer").read_text()
    assert "OnCalendar=Mon *-*-* 10:00:00 America/New_York" in timer
    assert "Persistent=true" in timer
    assert "Unit=x-weekly-analytics.service" in timer
    assert "ExecStart=/usr/bin/python3 %h/.local/lib/x-analytics/twitter_metrics.py" in svc
    assert "Type=oneshot" in svc
    combined = (svc + timer).lower()
    assert ".openclaw" not in combined and "gateway" not in combined.replace("prism-gateway", "")
    assert "x_consumer" not in combined and "environmentfile" not in combined
