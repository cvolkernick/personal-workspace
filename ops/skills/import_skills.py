#!/usr/bin/env python3
"""Copy portable skill surfaces into ops/skills/, refusing secrets (#580 E7)."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import importlib.util

EX_PATH = Path(__file__).resolve().parents[1] / "context" / "exclude.py"


def _load_ex():
    spec = importlib.util.spec_from_file_location("ctx_exclude", EX_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ctx_exclude"] = mod
    spec.loader.exec_module(mod)
    return mod


ex = _load_ex()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from", dest="src", required=True, type=Path)
    p.add_argument("--to", dest="dest", required=True, type=Path)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    src = args.src.expanduser().resolve()
    dest = args.dest.expanduser().resolve()
    if not src.exists():
        print(f"missing source: {src}", file=sys.stderr)
        return 1
    n = 0
    skipped = 0
    for path, c in ex.walk_allowed(src):
        rel = path.relative_to(src)
        if not c.allowed:
            skipped += 1
            print(f"skip {rel} ({c.reason})")
            continue
        print(f"{'copy' if args.apply else 'would copy'} {rel}")
        if args.apply:
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        n += 1
    print(f"{n} files · skipped {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
