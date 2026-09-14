#!/usr/bin/env python3
"""YNAB Chris's Plan: monthly funding targets + month assignment (#734).

Does not edit transactions, accounts, or scheduled transfers.
Credit Card Payments / Coinbase One Card is never targeted or assigned.

Usage:
  python3 treasury/ynab_plan.py status
  python3 treasury/ynab_plan.py apply            # dry-run
  python3 treasury/ynab_plan.py apply --write    # live PATCH
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.ynab_sync import (  # noqa: E402
    YNAB_API,
    load_ynab_token,
    milli_to_units,
    ynab_get,
)

TARGETS_PATH = Path(__file__).resolve().parent / "ynab_plan_targets.json"
DEFAULT_SLEEP_S = 0.25


def dollars_to_milli(usd: Any) -> int:
    return int(round(float(usd) * 1000))


def load_targets(path: Path = TARGETS_PATH) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def cover_milli(activity_milli: int) -> int:
    """Amount to assign so available reaches 0 when currently unassigned."""
    if activity_milli < 0:
        return -int(activity_milli)
    return 0


def remaining_to_target_milli(target_milli: int, covered: int) -> int:
    return max(0, int(target_milli) - int(covered))


def assignment_need(spec: Dict[str, Any], activity_milli: int) -> Tuple[int, int]:
    """Return (cover, remaining_to_target) milliunits for a spend category."""
    role = spec.get("role")
    assign = spec.get("assign")
    if role != "spend" or assign in ("skip", "zero"):
        return 0, 0
    covered = cover_milli(activity_milli)
    target = dollars_to_milli(spec.get("target_usd") or 0)
    return covered, remaining_to_target_milli(target, covered)


def plan_assignments(
    specs: List[Dict[str, Any]],
    activity_by_id: Dict[str, int],
    tbb_milli: int,
    buffer_milli: int,
) -> List[Dict[str, Any]]:
    """Waterfall: cover overspend first (by priority), then remaining-to-target.

    Never assigns more than max(0, tbb - buffer). Returns rows with
    `assign_milli` to PATCH as the month's `budgeted` (from $0 assigned).
    """
    spendable = [
        s
        for s in specs
        if s.get("role") == "spend" and s.get("assign") == "waterfall"
    ]
    rows: List[Dict[str, Any]] = []
    remaining = max(0, int(tbb_milli) - int(buffer_milli))

    def take(want: int) -> int:
        nonlocal remaining
        give = min(max(0, int(want)), remaining)
        remaining -= give
        return give

    # Pass 1: cover already-spent so categories are not red.
    cover_got: Dict[str, int] = {}
    for spec in sorted(spendable, key=lambda s: int(s.get("priority") or 99)):
        cid = spec["id"]
        cover, _rem = assignment_need(spec, int(activity_by_id.get(cid) or 0))
        got = take(cover)
        cover_got[cid] = got
        rows.append(
            {
                "id": cid,
                "name": spec["name"],
                "group": spec["group"],
                "phase": "cover",
                "want_milli": cover,
                "assign_milli": got,
                "priority": spec.get("priority"),
            }
        )

    # Pass 2: remaining-month toward target.
    rem_got: Dict[str, int] = {}
    for spec in sorted(spendable, key=lambda s: int(s.get("priority") or 99)):
        cid = spec["id"]
        _cover, rem = assignment_need(spec, int(activity_by_id.get(cid) or 0))
        got = take(rem)
        rem_got[cid] = got
        rows.append(
            {
                "id": cid,
                "name": spec["name"],
                "group": spec["group"],
                "phase": "remaining",
                "want_milli": rem,
                "assign_milli": got,
                "priority": spec.get("priority"),
            }
        )

    totals: Dict[str, Dict[str, Any]] = {}
    for spec in spendable:
        cid = spec["id"]
        totals[cid] = {
            "id": cid,
            "name": spec["name"],
            "group": spec["group"],
            "priority": spec.get("priority"),
            "target_milli": dollars_to_milli(spec.get("target_usd") or 0),
            "activity_milli": int(activity_by_id.get(cid) or 0),
            "assign_milli": int(cover_got.get(cid) or 0) + int(rem_got.get(cid) or 0),
            "cover_milli": int(cover_got.get(cid) or 0),
            "remaining_milli": int(rem_got.get(cid) or 0),
        }
    leftover = remaining
    return [
        {
            "tbb_milli": int(tbb_milli),
            "buffer_milli": int(buffer_milli),
            "assignable_milli": max(0, int(tbb_milli) - int(buffer_milli)),
            "leftover_after_plan_milli": leftover,
            "phases": rows,
            "totals": list(totals.values()),
        }
    ]


def collapse_plan(plan_wrap: List[Dict[str, Any]]) -> Dict[str, Any]:
    return plan_wrap[0]


def goal_payload(spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """PATCH body for category goal. None = do not touch goal."""
    role = spec.get("role")
    if role in ("income", "internal", "cc_payment"):
        return None
    target = dollars_to_milli(spec.get("target_usd") or 0)
    if target <= 0:
        return {"goal_target": None}
    body: Dict[str, Any] = {
        "goal_target": target,
        "goal_frequency": "monthly",
    }
    nwa = spec.get("needs_whole_amount")
    if nwa is True or nwa is False:
        body["goal_needs_whole_amount"] = bool(nwa)
    return body


def ynab_patch(
    path: str,
    token: str,
    payload: Dict[str, Any],
    *,
    timeout: int = 45,
) -> Dict[str, Any]:
    url = YNAB_API + path
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "personal-workspace-fcc/1.0",
        },
        method="PATCH",
    )
    waits = [0, 30, 60, 120]
    last_err: Optional[Exception] = None
    for wait in waits:
        if wait:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")[:800]
            if e.code == 429:
                last_err = RuntimeError(f"YNAB HTTP 429: {err_body}")
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "User-Agent": "personal-workspace-fcc/1.0",
                    },
                    method="PATCH",
                )
                continue
            raise RuntimeError(f"YNAB HTTP {e.code}: {err_body}") from e
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    raise RuntimeError(f"YNAB PATCH failed: {last_err}")


def month_index(month: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for c in month.get("categories") or []:
        if c.get("deleted"):
            continue
        out[c["id"]] = c
    return out


def status_payload(token: str, targets: Dict[str, Any], month: str) -> Dict[str, Any]:
    bid = targets["budget_id"]
    m = ynab_get(f"/budgets/{bid}/months/{month}", token)["data"]["month"]
    by_id = month_index(m)
    rows = []
    for spec in targets["categories"]:
        live = by_id.get(spec["id"]) or {}
        rows.append(
            {
                "group": spec["group"],
                "name": spec["name"],
                "role": spec.get("role"),
                "target_usd": spec.get("target_usd"),
                "assign": spec.get("assign"),
                "budgeted": milli_to_units(live.get("budgeted")),
                "activity": milli_to_units(live.get("activity")),
                "available": milli_to_units(live.get("balance")),
                "goal_type": live.get("goal_type"),
                "goal_target_usd": milli_to_units(live.get("goal_target") or 0),
            }
        )
    return {
        "month": month,
        "income_usd": milli_to_units(m.get("income")),
        "budgeted_usd": milli_to_units(m.get("budgeted")),
        "activity_usd": milli_to_units(m.get("activity")),
        "tbb_usd": milli_to_units(m.get("to_be_budgeted")),
        "categories": rows,
    }


def build_apply_plan(token: str, targets: Dict[str, Any], month: str) -> Dict[str, Any]:
    bid = targets["budget_id"]
    m = ynab_get(f"/budgets/{bid}/months/{month}", token)["data"]["month"]
    by_id = month_index(m)
    activity = {cid: int(c.get("activity") or 0) for cid, c in by_id.items()}
    unpark: List[Dict[str, Any]] = []
    for spec in targets["categories"]:
        if not spec.get("unpark"):
            continue
        live = by_id.get(spec["id"]) or {}
        available = int(live.get("balance") or 0)
        currently_budgeted = int(live.get("budgeted") or 0)
        if available <= 0:
            continue
        # Move available to TBB: new budgeted = current budgeted - available.
        unpark.append(
            {
                "id": spec["id"],
                "name": spec["name"],
                "group": spec["group"],
                "available_milli": available,
                "current_budgeted_milli": currently_budgeted,
                "new_budgeted_milli": currently_budgeted - available,
            }
        )
    unpark_sum = sum(u["available_milli"] for u in unpark)
    tbb_now = int(m.get("to_be_budgeted") or 0)
    tbb_after_unpark = tbb_now + unpark_sum
    buffer = dollars_to_milli(targets.get("buffer_usd") or 0)
    assign_plan = collapse_plan(
        plan_assignments(targets["categories"], activity, tbb_after_unpark, buffer)
    )
    goals = []
    for spec in targets["categories"]:
        payload = goal_payload(spec)
        if payload is None:
            continue
        goals.append(
            {
                "id": spec["id"],
                "name": spec["name"],
                "group": spec["group"],
                "payload": payload,
            }
        )
    return {
        "month": month,
        "tbb_now_milli": tbb_now,
        "unpark": unpark,
        "unpark_sum_milli": unpark_sum,
        "tbb_after_unpark_milli": tbb_after_unpark,
        "goals": goals,
        "assign": assign_plan,
        "skip": [
            {
                "id": s["id"],
                "name": s["name"],
                "role": s.get("role"),
                "assign": s.get("assign"),
                "reason": (
                    "cc_payment"
                    if s.get("role") == "cc_payment"
                    else "off_book_or_zero"
                    if s.get("assign") == "zero"
                    else s.get("role")
                ),
            }
            for s in targets["categories"]
            if s.get("role") in ("cc_payment", "income") or s.get("assign") == "zero"
        ],
    }


def _fmt(milli: int) -> str:
    return f"${milli_to_units(milli):,.2f}"


def print_plan(plan: Dict[str, Any]) -> None:
    print(f"month {plan['month']}")
    print(f"TBB now           {_fmt(plan['tbb_now_milli'])}")
    print(f"unpark income     {_fmt(plan['unpark_sum_milli'])}")
    print(f"TBB after unpark  {_fmt(plan['tbb_after_unpark_milli'])}")
    a = plan["assign"]
    print(
        f"buffer            {_fmt(a['buffer_milli'])}  leftover after plan {_fmt(a['leftover_after_plan_milli'])}"
    )
    print("\nUnpark:")
    for u in plan["unpark"]:
        print(
            f"  {u['group']}/{u['name']}: available {_fmt(u['available_milli'])} -> budgeted {_fmt(u['new_budgeted_milli'])}"
        )
    print("\nGoals:")
    for g in plan["goals"]:
        print(f"  {g['group']}/{g['name']}: {g['payload']}")
    print("\nSeptember assignment (from $0):")
    for t in sorted(a["totals"], key=lambda r: int(r.get("priority") or 99)):
        if t["assign_milli"] == 0 and t["cover_milli"] == 0 and t["remaining_milli"] == 0:
            # still show if target > 0 so underfunded is visible
            if t["target_milli"] <= 0:
                continue
        print(
            f"  {t['group']}/{t['name']}: assign {_fmt(t['assign_milli'])} "
            f"(cover {_fmt(t['cover_milli'])} + rem {_fmt(t['remaining_milli'])}) "
            f"target {_fmt(t['target_milli'])} activity {_fmt(t['activity_milli'])}"
        )


def apply_plan(
    token: str,
    targets: Dict[str, Any],
    plan: Dict[str, Any],
    *,
    write: bool,
    sleep_s: float = DEFAULT_SLEEP_S,
) -> Dict[str, Any]:
    bid = targets["budget_id"]
    month = plan["month"]
    results: Dict[str, Any] = {"write": write, "goals": [], "unpark": [], "assign": []}
    if not write:
        results["dry_run"] = True
        return results

    for g in plan["goals"]:
        path = f"/budgets/{bid}/categories/{g['id']}"
        payload = dict(g["payload"])
        try:
            ynab_patch(path, token, {"category": payload})
            results["goals"].append({"id": g["id"], "name": g["name"], "ok": True, "payload": payload})
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            stripped = {
                k: v
                for k, v in payload.items()
                if k in ("goal_target",) or (k == "goal_needs_whole_amount" and "goal_needs_whole_amount" not in msg)
            }
            if stripped != payload:
                try:
                    ynab_patch(path, token, {"category": stripped})
                    results["goals"].append(
                        {
                            "id": g["id"],
                            "name": g["name"],
                            "ok": True,
                            "payload": stripped,
                            "fallback": msg[:200],
                        }
                    )
                    time.sleep(sleep_s)
                    continue
                except Exception as e2:  # noqa: BLE001
                    msg = str(e2)
            results["goals"].append(
                {"id": g["id"], "name": g["name"], "ok": False, "error": msg[:400]}
            )
        time.sleep(sleep_s)

    for u in plan["unpark"]:
        path = f"/budgets/{bid}/months/{month}/categories/{u['id']}"
        try:
            ynab_patch(
                path, token, {"category": {"budgeted": u["new_budgeted_milli"]}}
            )
            results["unpark"].append({"id": u["id"], "name": u["name"], "ok": True})
        except Exception as e:  # noqa: BLE001
            results["unpark"].append(
                {"id": u["id"], "name": u["name"], "ok": False, "error": str(e)[:400]}
            )
        time.sleep(sleep_s)

    for t in plan["assign"]["totals"]:
        if t["assign_milli"] <= 0:
            continue
        path = f"/budgets/{bid}/months/{month}/categories/{t['id']}"
        try:
            ynab_patch(
                path, token, {"category": {"budgeted": t["assign_milli"]}}
            )
            results["assign"].append(
                {
                    "id": t["id"],
                    "name": t["name"],
                    "ok": True,
                    "budgeted_milli": t["assign_milli"],
                }
            )
        except Exception as e:  # noqa: BLE001
            results["assign"].append(
                {
                    "id": t["id"],
                    "name": t["name"],
                    "ok": False,
                    "error": str(e)[:400],
                }
            )
        time.sleep(sleep_s)
    return results


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status", help="Print assigned vs activity vs target")
    st.add_argument("--month", default=None)
    ap = sub.add_parser("apply", help="Set goals and assign the month (dry-run default)")
    ap.add_argument("--month", default=None)
    ap.add_argument("--write", action="store_true", help="Actually PATCH YNAB")
    ap.add_argument("--json-out", default=None, help="Write plan+result JSON path")
    args = p.parse_args(argv)

    targets = load_targets()
    month = args.month or targets.get("month") or "current"
    token, src = load_ynab_token()
    if not token:
        print("No YNAB token (~/.config/ynab/token or YNAB_TOKEN)", file=sys.stderr)
        return 2
    print(f"token_src {src}", file=sys.stderr)

    if args.cmd == "status":
        payload = status_payload(token, targets, month)
        print(json.dumps(payload, indent=2))
        return 0

    plan = build_apply_plan(token, targets, month)
    print_plan(plan)
    result = apply_plan(token, targets, plan, write=bool(args.write))
    if args.write:
        print(json.dumps({"result": result}, indent=2))
        # Re-read TBB after writes.
        after = status_payload(token, targets, month)
        print(
            json.dumps(
                {
                    "tbb_usd": after["tbb_usd"],
                    "budgeted_usd": after["budgeted_usd"],
                    "income_usd": after["income_usd"],
                },
                indent=2,
            )
        )
        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps({"plan": plan, "result": result, "after": after}, indent=2)
                + "\n",
                encoding="utf-8",
            )
    elif args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"plan": plan, "result": result}, indent=2) + "\n",
            encoding="utf-8",
        )
    failed = [
        r
        for key in ("goals", "unpark", "assign")
        for r in result.get(key) or []
        if r.get("ok") is False
    ]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
