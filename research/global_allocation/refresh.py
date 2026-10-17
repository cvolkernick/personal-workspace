"""Monthly snapshot refresh.

Weekly mode is #1251. This command rejects ``--kind weekly``.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from research.global_allocation.inputs import resolve
from research.global_allocation.model import CLASS_IDS, anchor_review, assemble
from research.global_allocation.schema import SchemaError, seal
from research.global_allocation.sources import LIVE, collect
from research.global_allocation.store import EXISTS, Store

PACKAGE_ROOT = Path(__file__).resolve().parent


def run_date_default() -> str:
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def run_at_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_sources(records) -> str:
    lines = ["sources:"]
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
    if kind != "monthly":
        print("weekly refresh is #1251 and is not in this slice")
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
        stats_eligible=True,
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
