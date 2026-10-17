"""Snapshot refresh.

``--kind monthly`` rebuilds every class. ``--kind weekly`` recomputes gold,
bitcoin, and listed equities from the latest monthly snapshot, or from the
seed when no monthly file exists, and carries the other classes forward.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from research.global_allocation.inputs import resolve
from research.global_allocation.model import CLASS_IDS, anchor_review, assemble, assemble_weekly
from research.global_allocation.schema import SchemaError, seal
from research.global_allocation.sources import LIVE, WEEKLY_SOURCE_IDS, collect, collect_weekly
from research.global_allocation.store import EXISTS, Store

PACKAGE_ROOT = Path(__file__).resolve().parent


def run_date_default() -> str:
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def run_at_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_sources(records, order=None) -> str:
    lines = ["sources:"]
    if order is None:
        order = [source_id for source_id, _fetch in LIVE]
    for source_id in order:
        rec = records.get(source_id)
        if rec is None:
            lines.append(f"- {source_id}: missing")
        elif rec.unavailable:
            lines.append(f"- {source_id}: unavailable: {rec.unavailable}")
        else:
            lines.append(f"- {source_id}: ok as_of={rec.as_of}")
    return "\n".join(lines)


def format_review(items: list[dict]) -> str:
    if not items:
        return "anchor review: none past window"
    lines = ["anchor review:"]
    for item in items:
        lines.append(
            f"- {item['class_id']} as_of {item['as_of']} past {item['window']} by {item['stale_days']} days"
        )
    return "\n".join(lines)


def _latest_id(store: Store, run_date: str, kind: str) -> str | None:
    superseded = set()
    matches = []
    for line in store.index_lines():
        if line.get("supersedes"):
            superseded.add(line["supersedes"])
        if line.get("run_date_et") == run_date and line.get("kind") == kind:
            matches.append(line)
    alive = [line for line in matches if line.get("snapshot_id") not in superseded]
    if not alive:
        return None
    return alive[-1]["snapshot_id"]


def execute(
    *,
    root: Path,
    kind: str,
    run_date: str,
    dry_run: bool,
    revise: str | None,
    offline_fixtures: Path | None,
) -> int:
    if kind == "weekly":
        return _execute_weekly(
            root=root,
            run_date=run_date,
            dry_run=dry_run,
            revise=revise,
            offline_fixtures=offline_fixtures,
        )
    if kind != "monthly":
        print(f"unknown kind {kind}")
        return 1
    store = Store(root)
    base = store.path_for(run_date, kind)
    if revise:
        prior_id = _latest_id(store, run_date, kind)
        if prior_id is None and not base.is_file():
            print("revise refused: no snapshot to supersede")
            return 1
        if prior_id is None:
            prior_id = f"{run_date}_{kind}"
        revision = store.next_revision(run_date, kind)
        snapshot_id = f"{run_date}_{kind}_r{revision}"
        dest = store.path_for(run_date, kind, revision)
        supersedes: str | None = prior_id
        reason = revise
    else:
        if base.is_file():
            print(f"exists {base.name}")
            return 0
        snapshot_id = f"{run_date}_{kind}"
        dest = base
        supersedes = None
        reason = None

    anchors, quantities = resolve(root)
    records = collect(offline_fixtures)
    print(format_sources(records))
    review = anchor_review(anchors, quantities, run_date)
    print(format_review(review))
    run_at = f"{run_date}T16:00:00Z" if offline_fixtures is not None else run_at_now()
    prior = store.latest_document()
    doc, failures, _caveats = assemble(
        run_date=run_date,
        run_at_utc=run_at,
        kind=kind,
        snapshot_id=snapshot_id,
        supersedes=supersedes,
        revision_reason=reason,
        anchors=anchors,
        quantities=quantities,
        sources=records,
        prior=prior,
    )
    if doc is None:
        if len(failures) >= len(CLASS_IDS):
            print("every class failed")
            return 2
        print(f"incomplete snapshot, unfilled: {', '.join(failures)}")
        return 1
    try:
        sealed = seal(doc)
    except SchemaError as exc:
        print(f"validation failed: {exc}")
        return 1
    if dry_run:
        print("dry-run: not written")
        return 0
    result = store.write_new(dest, sealed)
    if result == EXISTS:
        print(f"exists {dest.name}")
        return 0
    print(f"wrote {dest.name}")
    return 0


def _plan_write(store: Store, run_date: str, kind: str, revise: str | None):
    """Return ``(dest, snapshot_id, supersedes, reason)`` or ``None`` when the file exists."""
    base = store.path_for(run_date, kind)
    if revise:
        prior_id = _latest_id(store, run_date, kind)
        if prior_id is None and not base.is_file():
            print("revise refused: no snapshot to supersede")
            return "refused"
        if prior_id is None:
            prior_id = f"{run_date}_{kind}"
        revision = store.next_revision(run_date, kind)
        return store.path_for(run_date, kind, revision), f"{run_date}_{kind}_r{revision}", prior_id, revise
    if base.is_file():
        print(f"exists {base.name}")
        return None
    return base, f"{run_date}_{kind}", None, None


def _execute_weekly(
    *,
    root: Path,
    run_date: str,
    dry_run: bool,
    revise: str | None,
    offline_fixtures: Path | None,
) -> int:
    store = Store(root)
    planned = _plan_write(store, run_date, "weekly", revise)
    if planned is None:
        return 0
    if planned == "refused":
        return 1
    dest, snapshot_id, supersedes, reason = planned
    basis = store.latest_basis()
    if basis is None:
        print("weekly refresh needs a monthly or seed snapshot")
        return 1
    anchors, quantities = resolve(root)
    records = collect_weekly(offline_fixtures)
    print(format_sources(records, WEEKLY_SOURCE_IDS))
    review = anchor_review(anchors, quantities, run_date)
    print(format_review(review))
    run_at = f"{run_date}T16:00:00Z" if offline_fixtures is not None else run_at_now()
    doc, failures, _caveats = assemble_weekly(
        run_date=run_date,
        run_at_utc=run_at,
        kind="weekly",
        snapshot_id=snapshot_id,
        supersedes=supersedes,
        revision_reason=reason,
        anchors=anchors,
        quantities=quantities,
        sources=records,
        prior=basis,
    )
    if doc is None:
        if len(failures) >= len(CLASS_IDS):
            print("every class failed")
            return 2
        print(f"incomplete snapshot, unfilled: {', '.join(failures)}")
        return 1
    try:
        sealed = seal(doc)
    except SchemaError as exc:
        print(f"validation failed: {exc}")
        return 1
    if dry_run:
        print("dry-run: not written")
        return 0
    result = store.write_new(dest, sealed)
    if result == EXISTS:
        print(f"exists {dest.name}")
        return 0
    print(f"wrote {dest.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build one global-allocation snapshot")
    parser.add_argument("--kind", required=True, choices=["monthly", "weekly"])
    parser.add_argument("--date", help="run_date_et YYYY-MM-DD (default: today America/New_York)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--revise", help="write a new revision with this reason")
    parser.add_argument("--offline-fixtures", help="directory of saved source payloads; no network")
    parser.add_argument("--root", default=str(PACKAGE_ROOT))
    args = parser.parse_args(argv)
    offline = Path(args.offline_fixtures) if args.offline_fixtures else None
    return execute(
        root=Path(args.root),
        kind=args.kind,
        run_date=args.date or run_date_default(),
        dry_run=args.dry_run,
        revise=args.revise,
        offline_fixtures=offline,
    )


if __name__ == "__main__":
    raise SystemExit(main())
