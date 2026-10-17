"""Snapshot 0 matches the seed table exactly."""

from __future__ import annotations

import json
from pathlib import Path

from research.global_allocation.import_seed import class_id_for, parse_section1, run

PACKAGE = Path(__file__).resolve().parents[1]
SEED = PACKAGE / "seed" / "global_asset_allocation.md"
EXPECTED = {
    "real_estate": 393.3,
    "debt_securities": 160.7,
    "listed_equities": 157.8,
    "broad_money": 144.4,
    "gold": 29.9,
    "private_markets": 20.3,
    "art_collectibles": 2.86,
    "bitcoin": 1.64,
    "other_crypto": 1.09,
}


def test_seed_values_as_of_and_total(tmp_path):
    rows, total = parse_section1(SEED.read_text(encoding="utf-8"))
    assert run(tmp_path) == 0
    doc = json.loads((tmp_path / "snapshots" / "2026-10-17_seed.json").read_text(encoding="utf-8"))
    assert doc["stats_eligible"] is False
    assert doc["totals"]["gross_usd_tn"] == 912
    assert float(total) == 912
    by_id = {item["id"]: item for item in doc["classes"]}
    table = {class_id_for(row["label"]): row for row in rows}
    assert set(by_id) == set(EXPECTED)
    for class_id, value in EXPECTED.items():
        assert by_id[class_id]["value_usd_tn"] == value
        assert by_id[class_id]["as_of"] == table[class_id]["as_of"]
    assert "gross, overlapping, mixed dates" in doc["caveats"]
