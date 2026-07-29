#!/usr/bin/env python3
"""Financial Command Center entrypoint (monorepo).

Canonical FCC lives on the ``work/treasury`` worktree:

  ~/personal-workspace-worktrees/treasury/financial-command/server.py

This monorepo copy is a **safe launcher**: it always re-execs into that
worktree when present, so Morpho settings, Braiins, and X Money cannot
silently attach to a stale main-tree config.

Override (not recommended):
  FCC_ALLOW_MAIN_TREE=1  — run this tree's code instead (none; fails closed)
  FCC_WORKTREE_ROOT=/path/to/treasury-worktree
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _worktree_server() -> Path | None:
    env = (os.environ.get("FCC_WORKTREE_ROOT") or "").strip()
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env).expanduser().resolve())
    base = Path(
        os.environ.get(
            "PERSONAL_WORKSPACE_WORKTREES",
            str(Path.home() / "personal-workspace-worktrees"),
        )
    ).expanduser()
    candidates.append((base / "treasury").resolve())
    here = Path(__file__).resolve()
    # Already inside a treasury worktree → use this file only if it is the full server
    # (this monorepo file is intentionally a shim).
    for root in candidates:
        script = root / "financial-command" / "server.py"
        if script.is_file() and script.resolve() != here:
            # Prefer worktree that has braiins attach / coach (larger full server)
            try:
                text = script.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "def _braiins_live" in text or "/api/braiins" in text:
                return script
            if script.stat().st_size > 20_000:
                return script
    return None


def main() -> int:
    if os.environ.get("FCC_ALLOW_MAIN_TREE") == "1":
        print(
            "[fcc] FCC_ALLOW_MAIN_TREE=1 but monorepo financial-command/server.py "
            "is a shim only — set FCC_WORKTREE_ROOT or use the treasury worktree.",
            file=sys.stderr,
        )
        return 2

    target = _worktree_server()
    if not target:
        print(
            "[fcc] ERROR: treasury worktree FCC not found.\n"
            "  Expected: ~/personal-workspace-worktrees/treasury/financial-command/server.py\n"
            "  Create it: python3 projects-dashboard/worktrees.py ensure\n"
            "  Or set FCC_WORKTREE_ROOT to the worktree root.",
            file=sys.stderr,
        )
        return 1

    print(f"[fcc] re-exec → {target}", file=sys.stderr)
    os.execv(sys.executable, [sys.executable, str(target), *sys.argv[1:]])
    return 1  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
