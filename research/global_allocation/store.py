"""Write-once snapshot files and an append-only index.

A snapshot path is created with O_CREAT|O_EXCL|O_WRONLY. If the path already
exists the call returns ``exists`` and does not open it. Nothing in this
module opens an existing snapshot for writing. The index is a different file
and is only appended.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from research.global_allocation.schema import pretty_bytes

# Snapshot create flags. Exclusive so an existing file is never opened for write.
_SNAPSHOT_CREATE_FLAGS = os.O_CREAT | os.O_EXCL | os.O_WRONLY
# Index only. Not used on snapshot JSON.
_INDEX_APPEND_FLAGS = os.O_CREAT | os.O_WRONLY | os.O_APPEND

EXISTS = "exists"
WRITTEN = "written"


def file_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def snapshot_name(run_date: str, kind: str, revision: int | None = None) -> str:
    if revision is None:
        return f"{run_date}_{kind}.json"
    return f"{run_date}_{kind}_r{revision}.json"


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.snap_dir = self.root / "snapshots"
        self.index_path = self.snap_dir / "index.jsonl"

    def path_for(self, run_date: str, kind: str, revision: int | None = None) -> Path:
        return self.snap_dir / snapshot_name(run_date, kind, revision)

    def next_revision(self, run_date: str, kind: str) -> int:
        n = 1
        while self.path_for(run_date, kind, n).is_file():
            n += 1
        return n

    def write_new(self, path: Path, doc: dict[str, Any]) -> str:
        """Create ``path`` or return ``exists``. Does not open an existing snapshot."""
        if path.is_file():
            return EXISTS
        self.snap_dir.mkdir(parents=True, exist_ok=True)
        payload = pretty_bytes(doc)
        if not self._create_snapshot(path, payload):
            return EXISTS
        self._append_index(_index_line(path.name, payload, doc))
        return WRITTEN

    def read_snapshot(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def index_bytes(self) -> bytes:
        if not self.index_path.is_file():
            return b""
        return self.index_path.read_bytes()

    def index_lines(self) -> list[dict[str, Any]]:
        raw = self.index_bytes()
        lines = []
        for line in raw.decode("utf-8").splitlines():
            if line:
                lines.append(json.loads(line))
        return lines

    def latest_document(self) -> dict[str, Any] | None:
        lines = self.index_lines()
        superseded = {ln.get("supersedes") for ln in lines if ln.get("supersedes")}
        alive = [ln for ln in lines if ln.get("snapshot_id") not in superseded]
        if not alive:
            return None
        return self.read_snapshot(self.snap_dir / alive[-1]["file"])

    def _create_snapshot(self, path: Path, payload: bytes) -> bool:
        try:
            fd = os.open(path, _SNAPSHOT_CREATE_FLAGS, 0o644)
        except FileExistsError:
            return False
        try:
            view = memoryview(payload)
            while view:
                n = os.write(fd, view)
                view = view[n:]
        finally:
            os.close(fd)
        return True

    def _append_index(self, line: bytes) -> None:
        fd = os.open(self.index_path, _INDEX_APPEND_FLAGS, 0o644)
        try:
            view = memoryview(line)
            while view:
                n = os.write(fd, view)
                view = view[n:]
        finally:
            os.close(fd)


def _index_line(filename: str, payload: bytes, doc: dict[str, Any]) -> bytes:
    record = {
        "body_sha256": doc["sha256"],
        "file": filename,
        "file_sha256": file_sha256(payload),
        "kind": doc["kind"],
        "run_date_et": doc["run_date_et"],
        "snapshot_id": doc["snapshot_id"],
        "supersedes": doc.get("supersedes"),
    }
    return (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
