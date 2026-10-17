"""snapshot_v1 accepts snapshot 0 and a monthly fixture, and rejects the named cases."""

from __future__ import annotations

import json
from pathlib import Path

from research.global_allocation.schema import validate

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PACKAGE = Path(__file__).resolve().parents[1]


def _seed_doc(tmp_path):
    from research.global_allocation.import_seed import run

    assert run(tmp_path) == 0
    return json.loads((tmp_path / "snapshots" / "2026-10-17_seed.json").read_text(encoding="utf-8"))


def test_schema_accepts_snapshot_0(tmp_path):
    doc = _seed_doc(tmp_path)
    assert validate(doc) == []
    assert "gross, overlapping, mixed dates" in doc["caveats"]
    for item in doc["classes"]:
        for field in (
            "as_of",
            "source_name",
            "source_url",
            "method_note",
            "method_version",
            "overlap_flag",
            "overlap_with",
            "value_kind",
            "confidence",
        ):
            assert field in item


def test_schema_accepts_synthetic_monthly():
    doc = json.loads((FIXTURES / "golden_monthly.json").read_text(encoding="utf-8"))
    assert doc["kind"] == "monthly"
    assert validate(doc) == []


def _reject(doc, needle):
    errors = validate(doc)
    assert errors, "expected rejection"
    assert any(needle in error for error in errors), errors


def test_schema_rejects_missing_as_of(tmp_path):
    doc = _seed_doc(tmp_path)
    del doc["classes"][0]["as_of"]
    _reject(doc, "as_of")


def test_schema_rejects_missing_source_url(tmp_path):
    doc = _seed_doc(tmp_path)
    del doc["classes"][0]["source_url"]
    _reject(doc, "source_url")


def test_schema_rejects_unknown_class_id(tmp_path):
    doc = _seed_doc(tmp_path)
    doc["classes"][0]["id"] = "silver"
    _reject(doc, "silver")


def test_schema_rejects_share_sum(tmp_path):
    doc = _seed_doc(tmp_path)
    for item in doc["classes"]:
        item["share_pct"] = 0
    _reject(doc, "shares sum")


def test_schema_rejects_missing_overlap_flag(tmp_path):
    doc = _seed_doc(tmp_path)
    del doc["classes"][0]["overlap_flag"]
    _reject(doc, "overlap_flag")
