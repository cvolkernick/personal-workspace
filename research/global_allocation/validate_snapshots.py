"""Validate snapshot JSON files.

Prints the file name and the schema errors. It does not print prices, keys,
or document bodies.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from research.global_allocation.schema import validate

PACKAGE = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]) if argv else PACKAGE / "snapshots"
    paths = sorted(path for path in root.glob("*.json") if path.is_file())
    if not paths:
        print("no snapshot files")
        return 1
    failed = 0
    for path in paths:
        found = validate(json.loads(path.read_text(encoding="utf-8")))
        if found:
            print(f"{path.name}: {'; '.join(found)}")
            failed += 1
    if failed:
        return 1
    print(f"validated {len(paths)} snapshots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
