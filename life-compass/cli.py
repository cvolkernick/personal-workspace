#!/usr/bin/env python3
"""Life Compass CLI — sweep, one-thing, missing, pings, constraint.

  python3 life-compass/cli.py sweep
  python3 life-compass/cli.py one-thing
  python3 life-compass/cli.py missing
  python3 life-compass/cli.py pings [--consume]
  python3 life-compass/cli.py constraint
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domain import iso, local_hour, missing_anything, one_thing  # noqa: E402
from engine import build_dashboard, consume_pings, empty_state, run_sweep  # noqa: E402
from sensors import collect_snapshot  # noqa: E402
from store import load_state, resolve_state_path, save_state  # noqa: E402


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _print(payload: Any) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")


def cmd_sweep(args: argparse.Namespace) -> int:
    now = _now()
    state_path = resolve_state_path(Path(args.state) if args.state else None)
    state = load_state(state_path)
    if args.snapshot:
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    else:
        snapshot = collect_snapshot(
            now=now,
            workspace=ROOT,
            payoff_history=(state.get("payoff_history") or {}),
            prepped_event_ids={str(x) for x in (state.get("prepped_event_ids") or [])},
        )
    state, pings, dashboard = run_sweep(
        state, snapshot, now=now, hour=args.hour
    )
    save_state(state, state_path)
    _print(
        {
            "ok": True,
            "as_of": iso(now),
            "pings": pings,
            "ping_count": len(pings),
            "one_thing": dashboard.get("one_thing"),
            "quiet": dashboard.get("briefing", {}).get("quiet"),
            "state_path": str(state_path),
        }
    )
    return 0


def cmd_one_thing(args: argparse.Namespace) -> int:
    now = _now()
    state = load_state(Path(args.state) if args.state else None)
    watch = state.get("watchlist") or []
    hour = args.hour if args.hour is not None else local_hour(now)
    _print(one_thing(watch, now=now, hour=hour))
    return 0


def cmd_missing(args: argparse.Namespace) -> int:
    now = _now()
    state = load_state(Path(args.state) if args.state else None)
    _print(missing_anything(state.get("watchlist") or [], now=now))
    return 0


def cmd_pings(args: argparse.Namespace) -> int:
    now = _now()
    path = Path(args.state) if args.state else None
    state = load_state(path)
    if args.consume:
        state, pings = consume_pings(state, now=now)
        save_state(state, path)
    else:
        pings = list(state.get("pending_pings") or [])
    _print({"ok": True, "pings": pings, "count": len(pings)})
    return 0


def cmd_constraint(args: argparse.Namespace) -> int:
    state = load_state(Path(args.state) if args.state else None)
    _print(state.get("weekly_constraint") or {"kind": "unknown", "text": "No sweep yet."})
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    now = _now()
    state = load_state(Path(args.state) if args.state else None)
    if not state.get("last_sweep_at"):
        state = empty_state()
    _print(build_dashboard(state, now=now, hour=args.hour))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Life Compass v1")
    parser.add_argument("--state", default=None, help="Path to state JSON")
    parser.add_argument("--hour", type=int, default=None, help="Override local hour 0-23")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sweep = sub.add_parser("sweep", help="Evaluate triggers; emit pings if needed")
    p_sweep.add_argument("--snapshot", default=None, help="Fixture snapshot JSON (skip live sensors)")
    p_sweep.set_defaults(func=cmd_sweep)

    sub.add_parser("one-thing", help="What is my one thing right now?").set_defaults(
        func=cmd_one_thing
    )
    sub.add_parser("missing", help="Am I missing anything?").set_defaults(func=cmd_missing)
    p_pings = sub.add_parser("pings", help="Pending attention pings (empty = stay silent)")
    p_pings.add_argument("--consume", action="store_true")
    p_pings.set_defaults(func=cmd_pings)
    sub.add_parser("constraint", help="Weekly constraint").set_defaults(func=cmd_constraint)
    sub.add_parser("dashboard", help="Dump dashboard JSON").set_defaults(func=cmd_dashboard)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
