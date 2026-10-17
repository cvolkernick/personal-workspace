"""Write-once snapshots, revisions, and idempotent reruns."""

from __future__ import annotations

import json
import os
from pathlib import Path

from research.global_allocation import store as store_mod
from research.global_allocation.import_seed import run as import_seed
from research.global_allocation.schema import seal
from research.global_allocation.store import EXISTS, WRITTEN, Store

PACKAGE = Path(__file__).resolve().parents[1]


def test_store_source_never_uses_write_modes():
    text = Path(store_mod.__file__).read_text(encoding="utf-8")
    for banned in ("write_text", "write_bytes", '"w"', "'w'", '"a"', "'a'", '"r+"', '"w+"', '"a+"'):
        assert banned not in text
    assert "O_EXCL" in text


def test_existing_snapshot_is_not_opened(tmp_path, monkeypatch):
    assert import_seed(tmp_path) == 0
    path = tmp_path / "snapshots" / "2026-10-17_seed.json"
    before = path.read_bytes()
    calls = []
    real_open = os.open

    def wrapped(filename, flags, *args, **kwargs):
        calls.append(str(filename))
        return real_open(filename, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", wrapped)
    store = Store(tmp_path)
    doc = json.loads(before)
    assert store.write_new(path, doc) == EXISTS
    assert calls == []
    assert path.read_bytes() == before


def test_revise_appends_and_leaves_original_bytes(tmp_path):
    assert import_seed(tmp_path) == 0
    store = Store(tmp_path)
    original = store.path_for("2026-10-17", "seed")
    before_file = original.read_bytes()
    before_index = store.index_bytes()
    doc = json.loads(before_file)
    revised = dict(doc)
    revised["snapshot_id"] = "2026-10-17_seed_r1"
    revised["supersedes"] = doc["snapshot_id"]
    revised["revision_reason"] = "correct a label"
    revised.pop("sha256", None)
    sealed = seal(revised)
    dest = store.path_for("2026-10-17", "seed", 1)
    assert store.write_new(dest, sealed) == WRITTEN
    assert original.read_bytes() == before_file
    after_index = store.index_bytes()
    assert after_index.startswith(before_index)
    assert after_index.count(b"\n") == before_index.count(b"\n") + 1
    loaded = json.loads(dest.read_text(encoding="utf-8"))
    assert loaded["supersedes"] == "2026-10-17_seed"
    assert loaded["revision_reason"] == "correct a label"
    latest = store.latest_document()
    assert latest["snapshot_id"] == "2026-10-17_seed_r1"


def test_idempotent_seed_rerun(tmp_path):
    assert import_seed(tmp_path) == 0
    path = tmp_path / "snapshots" / "2026-10-17_seed.json"
    index = tmp_path / "snapshots" / "index.jsonl"
    file_bytes = path.read_bytes()
    index_bytes = index.read_bytes()
    assert import_seed(tmp_path) == 0
    assert path.read_bytes() == file_bytes
    assert index.read_bytes() == index_bytes
