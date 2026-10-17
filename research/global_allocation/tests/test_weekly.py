"""Weekly price refresh: golden file, carry-forward, and feed failures."""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from research.global_allocation.import_seed import run as import_seed
from research.global_allocation.refresh import main
from research.global_allocation.sources import WEEKLY_SOURCE_IDS, collect_weekly
from research.global_allocation.sources.base import unavailable
from research.global_allocation.sources.equity_proxy import fetch as fetch_equity
from research.global_allocation.validate_snapshots import main as validate_main

PACKAGE = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "monthly"
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden_weekly.json"
HELD = (
    "real_estate",
    "debt_securities",
    "broad_money",
    "private_markets",
    "art_collectibles",
    "other_crypto",
)


def _root_with_scaled_anchor(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    inputs = root / "inputs"
    inputs.mkdir(parents=True)
    anchors = json.loads((PACKAGE / "inputs" / "anchors.json").read_text(encoding="utf-8"))
    for entry in anchors["entries"]:
        if entry["class_id"] == "listed_equities":
            entry["proxy_price_at_anchor"] = 100
    (inputs / "anchors.json").write_text(json.dumps(anchors, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    shutil.copy(PACKAGE / "inputs" / "quantities.json", inputs / "quantities.json")
    return root


def _root_with_package_anchors(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    inputs = root / "inputs"
    inputs.mkdir(parents=True)
    shutil.copy(PACKAGE / "inputs" / "anchors.json", inputs / "anchors.json")
    shutil.copy(PACKAGE / "inputs" / "quantities.json", inputs / "quantities.json")
    return root


def _monthly(root: Path) -> Path:
    assert main(["--kind", "monthly", "--date", "2026-10-17", "--offline-fixtures", str(FIXTURES), "--root", str(root)]) == 0
    return root / "snapshots" / "2026-10-17_monthly.json"


def _weekly(root: Path, fixtures: Path | None = None) -> int:
    args = ["--kind", "weekly", "--date", "2026-10-17", "--root", str(root)]
    if fixtures is not None:
        args.extend(["--offline-fixtures", str(fixtures)])
    return main(args)


def _by_id(doc: dict) -> dict:
    return {item["id"]: item for item in doc["classes"]}


def test_golden_weekly_and_idempotent_rerun(tmp_path, monkeypatch):
    root = _root_with_scaled_anchor(tmp_path)
    monthly_path = _monthly(root)
    monthly_bytes = monthly_path.read_bytes()
    index_after_monthly = (root / "snapshots" / "index.jsonl").read_bytes()
    monthly = json.loads(monthly_bytes)
    assert _weekly(root, FIXTURES) == 0
    path = root / "snapshots" / "2026-10-17_weekly.json"
    assert path.read_text(encoding="utf-8") == GOLDEN.read_text(encoding="utf-8")
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["kind"] == "weekly"
    assert doc["stats_eligible"] is False
    assert "gross, overlapping, mixed dates" in doc["caveats"]
    by_id = _by_id(doc)
    monthly_by_id = _by_id(monthly)
    assert by_id["gold"]["value_usd_tn"] == 28.6
    assert by_id["gold"]["price_check"] == "ok"
    assert by_id["gold"]["value_kind"] == "computed_price_x_qty"
    assert by_id["bitcoin"]["value_usd_tn"] == 1.6
    assert doc["inputs"]["btc_height"] == 971907
    assert doc["inputs"]["btc_supply"] == 20099709.375
    assert by_id["listed_equities"]["value_usd_tn"] == 173.6
    assert by_id["listed_equities"]["value_kind"] == "scaled_anchor"
    assert by_id["listed_equities"]["price_stale"] is False
    for class_id in HELD:
        assert by_id[class_id]["value_usd_tn"] == monthly_by_id[class_id]["value_usd_tn"]
        assert by_id[class_id]["as_of"] == monthly_by_id[class_id]["as_of"]
        assert by_id[class_id]["value_kind"] == "carried_forward"
    assert monthly_path.read_bytes() == monthly_bytes
    index = (root / "snapshots" / "index.jsonl").read_bytes()
    assert index.startswith(index_after_monthly)
    assert index.count(b"\n") == index_after_monthly.count(b"\n") + 1

    def boom(*_args, **_kwargs):
        raise AssertionError("weekly rerun fetched")

    monkeypatch.setattr("research.global_allocation.refresh.collect_weekly", boom)
    assert _weekly(root, FIXTURES) == 0
    assert path.read_bytes() == GOLDEN.read_bytes()
    assert (root / "snapshots" / "index.jsonl").read_bytes() == index


def test_weekly_from_seed_when_no_monthly_exists(tmp_path):
    root = _root_with_package_anchors(tmp_path)
    assert import_seed(root) == 0
    seed = json.loads((root / "snapshots" / "2026-10-17_seed.json").read_text(encoding="utf-8"))
    assert _weekly(root, FIXTURES) == 0
    doc = json.loads((root / "snapshots" / "2026-10-17_weekly.json").read_text(encoding="utf-8"))
    assert not (root / "snapshots" / "2026-10-17_monthly.json").exists()
    assert doc["stats_eligible"] is False
    carried = _by_id(doc)["real_estate"]
    source = _by_id(seed)["real_estate"]
    assert carried["value_usd_tn"] == source["value_usd_tn"]
    assert carried["as_of"] == source["as_of"]
    assert carried["value_kind"] == "carried_forward"
    equities = _by_id(doc)["listed_equities"]
    assert equities["price_stale"] is True
    assert equities["value_usd_tn"] == 157.8
    assert any("proxy_price_at_anchor" in line for line in doc["caveats"])


def test_missing_equity_key_carries_and_exits_0(tmp_path, monkeypatch):
    root = _root_with_scaled_anchor(tmp_path)
    _monthly(root)
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)

    def wrapped(offline):
        records = collect_weekly(offline)
        records["equity_proxy"] = fetch_equity()
        return records

    monkeypatch.setattr("research.global_allocation.refresh.collect_weekly", wrapped)
    assert _weekly(root, FIXTURES) == 0
    doc = json.loads((root / "snapshots" / "2026-10-17_weekly.json").read_text(encoding="utf-8"))
    equities = _by_id(doc)["listed_equities"]
    assert equities["price_stale"] is True
    assert equities["value_kind"] == "carried_forward"
    assert equities["value_usd_tn"] == 173.6
    assert equities["as_of"] == "2025"
    assert doc["stats_eligible"] is False
    assert any("ALPHAVANTAGE_API_KEY" in line for line in doc["caveats"])


@pytest.mark.parametrize("reason", ["HTTP 429", "TimeoutError", "parse JSONDecodeError"])
def test_equity_feed_failure_carries(tmp_path, monkeypatch, reason):
    root = _root_with_scaled_anchor(tmp_path)
    _monthly(root)

    def wrapped(offline):
        records = collect_weekly(offline)
        records["equity_proxy"] = unavailable(
            "equity_proxy",
            "Alpha Vantage",
            "https://www.alphavantage.co/query",
            reason,
        )
        return records

    monkeypatch.setattr("research.global_allocation.refresh.collect_weekly", wrapped)
    assert _weekly(root, FIXTURES) == 0
    doc = json.loads((root / "snapshots" / "2026-10-17_weekly.json").read_text(encoding="utf-8"))
    equities = _by_id(doc)["listed_equities"]
    assert equities["price_stale"] is True
    assert equities["value_usd_tn"] == 173.6
    assert any(reason in line for line in doc["caveats"])


def _paxg_fixtures(tmp_path: Path, amount: str) -> Path:
    copied = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, copied)
    payload = json.loads((copied / "coinbase_paxg.json").read_text(encoding="utf-8"))
    payload["data"]["amount"] = amount
    (copied / "coinbase_paxg.json").write_text(json.dumps(payload), encoding="utf-8")
    return copied


def test_gold_divergence_keeps_primary(tmp_path):
    root = _root_with_scaled_anchor(tmp_path)
    monthly = json.loads(_monthly(root).read_text(encoding="utf-8"))
    fixtures = _paxg_fixtures(tmp_path, "5000")
    assert _weekly(root, fixtures) == 0
    doc = json.loads((root / "snapshots" / "2026-10-17_weekly.json").read_text(encoding="utf-8"))
    gold = _by_id(doc)["gold"]
    assert gold["price_check"] == "diverged"
    assert gold["value_usd_tn"] == 28.6
    assert gold["value_kind"] == "computed_price_x_qty"
    for class_id in HELD:
        assert _by_id(doc)[class_id]["value_usd_tn"] == _by_id(monthly)[class_id]["value_usd_tn"]
        assert _by_id(doc)[class_id]["as_of"] == _by_id(monthly)[class_id]["as_of"]


def test_gold_gap_of_exactly_two_percent_is_ok(tmp_path):
    root = _root_with_scaled_anchor(tmp_path)
    _monthly(root)
    # Spot fixture is 4000. 4080 is exactly 2 percent, and the rule is more than 2 percent.
    assert Decimal("80") / Decimal("4000") == Decimal("0.02")
    fixtures = _paxg_fixtures(tmp_path, "4080")
    assert _weekly(root, fixtures) == 0
    doc = json.loads((root / "snapshots" / "2026-10-17_weekly.json").read_text(encoding="utf-8"))
    assert _by_id(doc)["gold"]["price_check"] == "ok"
    assert _by_id(doc)["gold"]["value_usd_tn"] == 28.6


def test_weekly_offline_collect_does_not_call_http(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("http fetch")

    monkeypatch.setattr("research.global_allocation.sources.http.fetch", boom)
    monkeypatch.setattr("research.global_allocation.sources.coingecko.fetch", boom)
    records = collect_weekly(FIXTURES)
    assert set(records) == set(WEEKLY_SOURCE_IDS)
    assert all(record.ok for record in records.values())


def test_committed_snapshots_still_validate():
    assert validate_main() == 0
