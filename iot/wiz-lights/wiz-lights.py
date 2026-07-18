#!/usr/bin/env python3
"""Wiz lights CLI — thin wrapper over iot.wiz_adapter.

Run from monorepo root:
  python3 iot/wiz-lights/wiz-lights.py all cyan
  python3 iot/wiz-lights/wiz-lights.py entryway1 red
  python3 iot/wiz-lights/wiz-lights.py entryway1 off
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from iot.wiz_adapter import execute_control, run_async  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print("Usage: wiz-lights.py <light-name|all> <color|off> [brightness]")
        print("Example: wiz-lights.py all cyan")
        print("Presets: white red green blue cyan magenta yellow orange purple warm off")
        return 1
    light = args[0]
    color = args[1]
    brightness = int(args[2]) if len(args) > 2 else 200
    result = run_async(execute_control(light, color, brightness))
    for r in result.get("results") or []:
        status = "ok" if r.get("ok") else f"FAIL {r.get('error')}"
        print(f"{r.get('name') or r.get('ip')} -> {color} [{status}]")
    if not result.get("ok"):
        if result.get("error"):
            print(result["error"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
