#!/usr/bin/env python3
"""Restore prism *books/state* from the last finley pull + git.

Keys are a kill-switch re-issue — they are not in the snapshot and this
script refuses to copy them. The puller cannot restore itself (off-site
bucket, store not chosen).

Usage (operator on LAN, after prism is replaced / rebuilt):
  # 1. git clone + checkout the last known good master on new prism
  # 2. from finley (or a copy of ~/b2-pulls/prism):
  python3 deploy/b2-puller/restore_prism.py \\
    --from /home/finley-agent/b2-pulls/prism \\
    --to /home/prism-agent \\
    --dry-run
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from paths import (  # noqa: E402
    PULL_RELATIVE,
    classify_dest,
    dest_for_source,
    is_refused_name,
)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Restore prism books from last finley pull")
    p.add_argument("--from", dest="src", required=True, help="finley pull dest (last pull)")
    p.add_argument("--to", dest="dest", required=True, help="prism $HOME to restore into")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    src_root = Path(args.src).expanduser()
    dest_home = Path(args.dest).expanduser()
    dc = classify_dest(dest_home)
    if not dc.allowed:
        print(f"REFUSE: {dc.reason} ({dest_home})", file=sys.stderr)
        return 2

    wrote = []
    skipped = []
    for rel in PULL_RELATIVE:
        pulled = dest_for_source(rel, src_root)
        target = dest_home / rel
        if is_refused_name(rel) or is_refused_name(target):
            print(f"REFUSE: key path {rel}", file=sys.stderr)
            return 2
        if not pulled.exists():
            skipped.append(rel)
            continue
        if args.dry_run:
            wrote.append(rel)
            continue
        if pulled.is_dir():
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(pulled, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(pulled, target)
        wrote.append(rel)

    print(
        json.dumps(
            {
                "ok": True,
                "wrote": wrote,
                "skipped_missing": skipped,
                "keys": "not restored — re-issue (kill-switch)",
                "git": "checkout last known master separately",
                "puller_self": "not restorable from this snapshot (off-site bucket, out of scope)",
                "dry_run": args.dry_run,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
