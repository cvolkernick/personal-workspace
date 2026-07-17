"""CLI for the time allocator MVP.

Usage examples:
  python3 -m holistic.time_allocator seed
  python3 -m holistic.time_allocator list
  python3 -m holistic.time_allocator add "Write design doc" --kind task --priority 5
  python3 -m holistic.time_allocator remove seed-admin
  python3 -m holistic.time_allocator allocate 480
  python3 -m holistic.time_allocator set seed-fitness --priority 5 --minutes 60
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Allow `python3 holistic/time_allocator/cli.py` from repo root.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from holistic.time_allocator.domain import (  # noqa: E402
    add_item,
    allocate_total,
    apply_plan,
    build_rolling_plan,
    list_items,
    list_targets,
    remove_item,
    seed_starter,
    set_minutes,
    set_priority,
)
from holistic.time_allocator.store import load_state, resolve_data_path, save_state  # noqa: E402


def format_table(items: list[dict[str, Any]]) -> str:
    if not items:
        return "(no tasks/goals — run: seed  or  add \"…\")"
    headers = ("id", "kind", "pri", "min", "title")
    rows: list[tuple[str, str, str, str, str]] = []
    for it in items:
        rows.append(
            (
                str(it.get("id") or ""),
                str(it.get("kind") or ""),
                str(int(it.get("priority") or 0)),
                str(int(it.get("minutes") or 0)),
                str(it.get("title") or ""),
            )
        )
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = [
        "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)),
        "  ".join("-" * widths[i] for i in range(len(headers))),
    ]
    for row in rows:
        lines.append("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))
    total_min = sum(int(it.get("minutes") or 0) for it in items)
    lines.append("")
    lines.append(f"items: {len(items)}  allocated_minutes: {total_min}")
    return "\n".join(lines)


def _cmd_list(args: argparse.Namespace) -> int:
    state = load_state(args.data)
    items = list_items(state)
    targets = list_targets(state)
    if args.json:
        print(
            json.dumps(
                {
                    "items": items,
                    "targets": targets,
                    "plan": state.get("plan"),
                    "path": str(resolve_data_path(args.data)),
                },
                indent=2,
            )
        )
    else:
        path = resolve_data_path(args.data)
        print(f"data: {path}")
        print("## Targets")
        if not targets:
            print("(none)")
        else:
            for t in targets:
                print(
                    f"  [{t.get('kind')}] pri={t.get('priority')}  {t.get('id')}  {t.get('title')}"
                )
        print("## Ad-hoc")
        print(format_table(items))
    return 0


def _cmd_seed(args: argparse.Namespace) -> int:
    state = seed_starter(load_state(args.data), personal=not args.generic)
    state = apply_plan(state)
    path = save_state(state, args.data)
    print(f"seeded {'generic items' if args.generic else 'personal targets'} → {path}")
    plan = state.get("plan") or {}
    print(f"plan blocks: {len(plan.get('blocks') or [])}  active={plan.get('active_minutes')}")
    for b in plan.get("blocks") or []:
        print(f"  {b.get('minutes'):4d}m  [{b.get('role')}] {b.get('title')}")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    state = load_state(args.data)
    plan = build_rolling_plan(state)
    if args.apply:
        state = apply_plan(state, plan)
        save_state(state, args.data)
    if args.json:
        print(json.dumps(plan, indent=2))
    else:
        print(f"window: {plan.get('window_start')} → {plan.get('window_end')}")
        print(
            f"24h={plan.get('window_minutes')}  sleep={plan.get('sleep_reserve_minutes')}  "
            f"active={plan.get('active_minutes')}"
        )
        for b in plan.get("blocks") or []:
            print(f"  {int(b.get('minutes') or 0):4d}m  [{b.get('role')}] {b.get('title')}")
        for n in plan.get("notes") or []:
            print(f"  note: {n}")
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    state = load_state(args.data)
    try:
        state = add_item(
            state,
            args.title,
            kind=args.kind,
            priority=args.priority,
            minutes=args.minutes,
            item_id=args.id,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    path = save_state(state, args.data)
    print(f"added → {path}")
    print(format_table(list_items(state)))
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    state = load_state(args.data)
    try:
        state = remove_item(state, args.key)
    except (KeyError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    path = save_state(state, args.data)
    print(f"removed → {path}")
    print(format_table(list_items(state)))
    return 0


def _cmd_allocate(args: argparse.Namespace) -> int:
    state = load_state(args.data)
    state = allocate_total(state, args.total)
    path = save_state(state, args.data)
    print(f"allocated {int(args.total)} minutes by priority → {path}")
    print(format_table(list_items(state)))
    return 0


def _cmd_set(args: argparse.Namespace) -> int:
    state = load_state(args.data)
    try:
        if args.priority is not None:
            state = set_priority(state, args.key, args.priority)
        if args.minutes is not None:
            state = set_minutes(state, args.key, args.minutes)
        if args.priority is None and args.minutes is None:
            print("error: provide --priority and/or --minutes", file=sys.stderr)
            return 2
    except (KeyError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    path = save_state(state, args.data)
    print(f"updated → {path}")
    print(format_table(list_items(state)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="time-allocator",
        description="Allocate time across tasks/goals (holistic MVP)",
    )
    p.add_argument(
        "--data",
        type=Path,
        default=None,
        help="Path to tasks JSON (default: holistic/data/tasks.json or TIME_ALLOCATOR_DATA)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("list", help="List tasks/goals with priority and minutes")
    sp.add_argument("--json", action="store_true", help="Machine-readable output")
    sp.set_defaults(func=_cmd_list)

    sp = sub.add_parser(
        "seed",
        help="Load personal targets (sleep/workout/Duchess/Lyft) + build 24h plan",
    )
    sp.add_argument(
        "--generic",
        action="store_true",
        help="Also load legacy generic ad-hoc starter items",
    )
    sp.set_defaults(func=_cmd_seed)

    sp = sub.add_parser("plan", help="Show / apply rolling 24h plan")
    sp.add_argument("--apply", action="store_true", help="Persist plan on the store")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=_cmd_plan)

    sp = sub.add_parser("add", help="Add a task or goal")
    sp.add_argument("title", help="Title of the task/goal")
    sp.add_argument("--kind", choices=("task", "goal"), default="task")
    sp.add_argument("--priority", type=int, default=1, help="Higher = more important")
    sp.add_argument("--minutes", type=int, default=0)
    sp.add_argument("--id", default=None, help="Optional stable id")
    sp.set_defaults(func=_cmd_add)

    sp = sub.add_parser("remove", help="Remove by id or exact title")
    sp.add_argument("key", help="Item id or title")
    sp.set_defaults(func=_cmd_remove)

    sp = sub.add_parser("allocate", help="Distribute total minutes by priority weight")
    sp.add_argument("total", type=int, help="Total minutes in the day budget")
    sp.set_defaults(func=_cmd_allocate)

    sp = sub.add_parser("set", help="Update priority and/or minutes for one item")
    sp.add_argument("key", help="Item id or title")
    sp.add_argument("--priority", type=int, default=None)
    sp.add_argument("--minutes", type=int, default=None)
    sp.set_defaults(func=_cmd_set)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
