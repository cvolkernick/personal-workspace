"""Adapters: parsers, missing key, and the CoinGecko demotion (no socket)."""

from __future__ import annotations

from research.global_allocation.sources import coingecko, equity_proxy


def test_coingecko_fetch_does_not_use_http(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("http fetch")

    monkeypatch.setattr("research.global_allocation.sources.http.fetch", boom)
    record = coingecko.fetch()
    assert record.ok is False
    assert "robots.txt" in record.unavailable
    assert "/api/v3" in record.unavailable


def test_coingecko_parser_reads_a_permitted_payload():
    body = (
        b'{"data": {"total_market_cap": {"usd": 2743000000000}, '
        b'"market_cap_percentage": {"btc": 59.4}}}'
    )
    record = coingecko.parse(body)
    assert record.ok
    assert record.payload["btc_dominance_pct"] == 59.4


def test_equity_proxy_missing_key_makes_no_request(monkeypatch):
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)

    def boom(*_args, **_kwargs):
        raise AssertionError("http fetch")

    monkeypatch.setattr("research.global_allocation.sources.http.fetch", boom)
    record = equity_proxy.fetch()
    assert "ALPHAVANTAGE_API_KEY" in record.unavailable


def test_call_reports_429_timeout_and_malformed_without_the_query(monkeypatch):
    from research.global_allocation.sources.equity_proxy import parse
    from research.global_allocation.sources.http import call

    url = "https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&apikey=SECRET"

    monkeypatch.setattr("research.global_allocation.sources.http.fetch", lambda *_args, **_kwargs: (429, b""))
    limited = call("equity_proxy", "Alpha Vantage", url, parse)
    assert limited.unavailable == "HTTP 429"
    assert "SECRET" not in limited.source_url
    assert "SECRET" not in (limited.unavailable or "")

    def time_out(*_args, **_kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr("research.global_allocation.sources.http.fetch", time_out)
    timed = call("equity_proxy", "Alpha Vantage", url, parse)
    assert timed.unavailable == "TimeoutError"
    assert "SECRET" not in timed.source_url

    monkeypatch.setattr("research.global_allocation.sources.http.fetch", lambda *_args, **_kwargs: (200, b"{"))
    broken = call("equity_proxy", "Alpha Vantage", url, parse)
    assert broken.unavailable.startswith("parse ")
    assert "SECRET" not in broken.source_url
    assert "SECRET" not in broken.unavailable


def test_twelve_data_is_not_called():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "sources"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert "twelvedata" not in text.lower()
