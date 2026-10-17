"""Monthly refresh: golden snapshot, degradation, dry-run, and exit codes."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from research.global_allocation.import_seed import run as import_seed
from research.global_allocation.refresh import main

PACKAGE = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "monthly"
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden_monthly.json"


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


def test_golden_monthly_and_idempotent_rerun(tmp_path, capsys):
    root = _root_with_scaled_anchor(tmp_path)
    code = main(
        [
            "--kind",
            "monthly",
            "--date",
            "2026-10-17",
            "--offline-fixtures",
            str(FIXTURES),
            "--root",
            str(root),
        ]
    )
    assert code == 0
    path = root / "snapshots" / "2026-10-17_monthly.json"
    assert path.read_text(encoding="utf-8") == GOLDEN.read_text(encoding="utf-8")
    doc = json.loads(path.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in doc["classes"]}
    assert doc["inputs"]["btc_height"] == 971907
    assert doc["inputs"]["btc_supply"] == 20099709.375
    assert by_id["gold"]["value_usd_tn"] == 28.6
    assert by_id["gold"]["value_kind"] == "computed_price_x_qty"
    assert by_id["listed_equities"]["value_usd_tn"] == 173.6
    assert by_id["listed_equities"]["value_kind"] == "scaled_anchor"
    assert by_id["real_estate"]["stale"] is True
    assert by_id["real_estate"]["stale_days"] == 200
    assert by_id["art_collectibles"]["stale"] is True
    assert by_id["art_collectibles"]["stale_days"] == 290
    assert doc["fx_basis"]["as_of"] == "2026-10-16"
    assert "gross, overlapping, mixed dates" in doc["caveats"]
    out = capsys.readouterr().out
    assert "anchor review:" in out
    assert "real_estate" in out
    file_bytes = path.read_bytes()
    index_bytes = (root / "snapshots" / "index.jsonl").read_bytes()
    assert main(["--kind", "monthly", "--date", "2026-10-17", "--root", str(root)]) == 0
    assert path.read_bytes() == file_bytes
    assert (root / "snapshots" / "index.jsonl").read_bytes() == index_bytes
    assert "exists" in capsys.readouterr().out


def test_revise_keeps_original_and_appends_index(tmp_path):
    root = _root_with_scaled_anchor(tmp_path)
    assert main(["--kind", "monthly", "--date", "2026-10-17", "--offline-fixtures", str(FIXTURES), "--root", str(root)]) == 0
    original = root / "snapshots" / "2026-10-17_monthly.json"
    before_file = original.read_bytes()
    before_index = (root / "snapshots" / "index.jsonl").read_bytes()
    code = main(
        [
            "--kind",
            "monthly",
            "--date",
            "2026-10-17",
            "--offline-fixtures",
            str(FIXTURES),
            "--revise",
            "correct a price",
            "--root",
            str(root),
        ]
    )
    assert code == 0
    assert original.read_bytes() == before_file
    after_index = (root / "snapshots" / "index.jsonl").read_bytes()
    assert after_index.startswith(before_index)
    assert after_index.count(b"\n") == before_index.count(b"\n") + 1
    revised = json.loads((root / "snapshots" / "2026-10-17_monthly_r1.json").read_text(encoding="utf-8"))
    assert revised["supersedes"] == "2026-10-17_monthly"
    assert revised["revision_reason"] == "correct a price"


def test_source_failure_carries_that_class_forward(tmp_path, capsys):
    root = _root_with_scaled_anchor(tmp_path)
    assert import_seed(root) == 0
    seed_index = (root / "snapshots" / "index.jsonl").read_bytes()
    missing = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, missing)
    (missing / "coinbase_btc.json").unlink()
    code = main(
        ["--kind", "monthly", "--date", "2026-10-17", "--offline-fixtures", str(missing), "--root", str(root)]
    )
    assert code == 0
    doc = json.loads((root / "snapshots" / "2026-10-17_monthly.json").read_text(encoding="utf-8"))
    bitcoin = next(item for item in doc["classes"] if item["id"] == "bitcoin")
    assert bitcoin["value_usd_tn"] == 1.64
    assert bitcoin["value_kind"] == "carried_forward"
    assert bitcoin["stale"] is True
    assert any("Coinbase" in line for line in doc["caveats"])
    assert (root / "snapshots" / "index.jsonl").read_bytes().startswith(seed_index)
    assert "anchor review:" in capsys.readouterr().out


def test_every_class_failed_exits_2_and_writes_nothing(tmp_path):
    root = tmp_path / "empty"
    (root / "inputs").mkdir(parents=True)
    (root / "inputs" / "anchors.json").write_text('{"entries": []}\n', encoding="utf-8")
    (root / "inputs" / "quantities.json").write_text('{"entries": []}\n', encoding="utf-8")
    fixtures = tmp_path / "no-fixtures"
    fixtures.mkdir()
    code = main(["--kind", "monthly", "--date", "2026-10-17", "--offline-fixtures", str(fixtures), "--root", str(root)])
    assert code == 2
    assert list((root / "snapshots").glob("*.json")) == [] if (root / "snapshots").is_dir() else True
    assert not (root / "snapshots" / "index.jsonl").exists()


def test_validation_failure_writes_nothing(tmp_path, monkeypatch):
    root = _root_with_scaled_anchor(tmp_path)

    def bad(**_kwargs):
        return {"schema_version": 1}, [], []

    monkeypatch.setattr("research.global_allocation.refresh.assemble", bad)
    code = main(
        ["--kind", "monthly", "--date", "2026-10-17", "--offline-fixtures", str(FIXTURES), "--root", str(root)]
    )
    assert code == 1
    assert not (root / "snapshots").exists()


def test_dry_run_writes_nothing(tmp_path):
    root = _root_with_scaled_anchor(tmp_path)
    code = main(
        [
            "--kind",
            "monthly",
            "--date",
            "2026-10-17",
            "--offline-fixtures",
            str(FIXTURES),
            "--dry-run",
            "--root",
            str(root),
        ]
    )
    assert code == 0
    assert not (root / "snapshots").exists()


def test_weekly_is_out_of_slice(tmp_path):
    assert main(["--kind", "weekly", "--root", str(tmp_path)]) == 1
