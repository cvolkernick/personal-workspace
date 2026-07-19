#!/usr/bin/env python3
"""CLI entry for local workflow scheduler (cron-friendly).

Usage:
  python3 projects-dashboard/run_scheduler.py tick
  python3 projects-dashboard/run_scheduler.py tick --force
  python3 projects-dashboard/run_scheduler.py status
  python3 projects-dashboard/run_scheduler.py install-cron
  python3 projects-dashboard/run_scheduler.py uninstall-cron
  python3 projects-dashboard/run_scheduler.py enable
  python3 projects-dashboard/run_scheduler.py disable
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WS = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(WS) not in sys.path:
    sys.path.insert(0, str(WS))

from scheduler import (  # noqa: E402
    install_cron,
    load_config,
    save_config,
    scheduler_payload,
    tick,
    uninstall_cron,
)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    cmd = argv[0] if argv else "status"
    if cmd == "tick":
        force = "--force" in argv
        print(json.dumps(tick(force=force), indent=2))
        return 0
    if cmd == "status":
        print(json.dumps(scheduler_payload(), indent=2))
        return 0
    if cmd == "install-cron":
        print(json.dumps(install_cron(), indent=2))
        return 0
    if cmd == "uninstall-cron":
        print(json.dumps(uninstall_cron(), indent=2))
        return 0
    if cmd == "enable":
        cfg = load_config()
        cfg["enabled"] = True
        print(json.dumps(save_config(cfg), indent=2))
        return 0
    if cmd == "disable":
        cfg = load_config()
        cfg["enabled"] = False
        print(json.dumps(save_config(cfg), indent=2))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
