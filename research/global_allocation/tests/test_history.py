"""Committed snapshots validate, and the index hashes match the files."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from research.global_allocation.import_seed import run as import_seed
from research.global_allocation.schema import validate

PACKAGE = Path(__file__).resolve().parents[1]
SNAP = PACKAGE / "snapshots"


def test_committed_seed_matches_a_fresh_import(tmp_path):
    assert import_seed(tmp_path) == 0
    fresh = (tmp_path / "snapshots" / "2026-10-17_seed.json").read_bytes()
    committed = (SNAP / "2026-10-17_seed.json").read_bytes()
    assert fresh == committed


def test_committed_snapshots_match_index_and_schema():
    files = sorted(path for path in SNAP.glob("*.json"))
    assert files, "snapshot 0 must be committed"
    index_lines = [
        json.loads(line)
        for line in (SNAP / "index.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    by_file = {line["file"]: line for line in index_lines}
    assert set(by_file) == {path.name for path in files}
    for path in files:
        payload = path.read_bytes()
        doc = json.loads(payload)
        assert validate(doc) == []
        line = by_file[path.name]
        assert line["file_sha256"] == hashlib.sha256(payload).hexdigest()
        assert line["body_sha256"] == doc["sha256"]
        assert line["snapshot_id"] == doc["snapshot_id"]
