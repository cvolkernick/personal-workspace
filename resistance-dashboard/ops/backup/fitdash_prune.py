#!/usr/bin/env python3
"""Retention prune for FitDash backup releases.

Keep 14 daily, 8 weekly (Sunday), 12 monthly (the 1st), and one snapshot
per year after that. Never delete the newest 3 releases or a release marked
last-verified-good (release name/body, or a tag in the state file).

Dry-run is the default. ``--apply`` deletes. Unknown tags are left alone.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Protocol

_TAG = re.compile(r"^bk-(\d{4})(\d{2})(\d{2})(?:-(\d{2})(\d{2})(\d{2})?)?$")
VERIFIED_MARK = "last-verified-good"


@dataclass(frozen=True)
class Release:
    tag: str
    day: date
    when: datetime
    release_id: int
    name: str = ""
    body: str = ""
    draft: bool = False

    @property
    def verified_in_text(self) -> bool:
        return VERIFIED_MARK in f"{self.name}\n{self.body}"


class ReleaseAPI(Protocol):
    def list_releases(self) -> list[dict]: ...

    def delete_release(self, release_id: int, tag: str) -> None: ...


@dataclass(frozen=True)
class PrunePlan:
    keep: tuple[str, ...]
    delete: tuple[str, ...]
    applied: bool


def parse_tag(tag: str) -> Optional[tuple[date, Optional[datetime]]]:
    match = _TAG.fullmatch(tag or "")
    if not match:
        return None
    year, month, day, hour, minute, second = match.groups()
    try:
        calendar_day = date(int(year), int(month), int(day))
    except ValueError:
        return None
    if hour is None:
        return calendar_day, None
    try:
        stamp = datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second or "0"),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None
    return calendar_day, stamp


def release_from_tag(
    tag: str,
    *,
    release_id: int = 0,
    published_at: Optional[datetime] = None,
    name: str = "",
    body: str = "",
    draft: bool = False,
) -> Optional[Release]:
    parsed = parse_tag(tag)
    if parsed is None:
        return None
    calendar_day, stamped = parsed
    when = stamped or published_at
    if when is None:
        when = datetime(
            calendar_day.year,
            calendar_day.month,
            calendar_day.day,
            tzinfo=timezone.utc,
        )
    return Release(
        tag=tag,
        day=calendar_day,
        when=when.astimezone(timezone.utc),
        release_id=release_id,
        name=name,
        body=body,
        draft=draft,
    )


def select_keep(
    releases: Iterable[Release],
    *,
    daily: int = 14,
    weekly: int = 8,
    monthly: int = 12,
    newest: int = 3,
    verified_tags: Iterable[str] = (),
) -> set[str]:
    """Tags to keep. Calendar slots use the tag date. Ordering uses ``when``."""
    items = [item for item in releases if not item.draft]
    if not items:
        return set()
    ordered = sorted(items, key=lambda item: (item.when, item.tag), reverse=True)
    keep: set[str] = set()
    for item in ordered[: max(0, newest)]:
        keep.add(item.tag)

    newest_on_day: dict[date, Release] = {}
    for item in items:
        current = newest_on_day.get(item.day)
        if current is None or (item.when, item.tag) > (current.when, current.tag):
            newest_on_day[item.day] = item
    days = sorted(newest_on_day, reverse=True)
    for day in days[: max(0, daily)]:
        keep.add(newest_on_day[day].tag)
    sundays = [day for day in days if day.weekday() == 6]
    for day in sundays[: max(0, weekly)]:
        keep.add(newest_on_day[day].tag)
    firsts = [day for day in days if day.day == 1]
    for day in firsts[: max(0, monthly)]:
        keep.add(newest_on_day[day].tag)

    by_year: dict[int, list[Release]] = {}
    for day, item in newest_on_day.items():
        by_year.setdefault(day.year, []).append(item)
    for year_items in by_year.values():
        january = [item for item in year_items if item.day.month == 1 and item.day.day == 1]
        if january:
            chosen = max(january, key=lambda item: (item.when, item.tag))
        else:
            chosen = min(year_items, key=lambda item: (item.when, item.tag))
        keep.add(chosen.tag)

    verified = set(verified_tags)
    for item in items:
        if item.tag in verified or item.verified_in_text:
            keep.add(item.tag)
    return keep


def releases_to_delete(releases: Iterable[Release], keep: set[str]) -> list[Release]:
    parsed = [item for item in releases if not item.draft]
    deleting = [item for item in parsed if item.tag not in keep]
    remaining = len(parsed) - len(deleting)
    floor = min(3, len(parsed))
    if remaining < floor:
        raise RuntimeError("prune would drop below the newest-3 floor; refusing")
    return sorted(deleting, key=lambda item: (item.when, item.tag))


def read_verified_tags(path: Optional[Path]) -> set[str]:
    if path is None or not path.is_file():
        return set()
    tags: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tags.add(stripped.split()[0])
    return tags


def plan_from_api_payloads(
    payloads: list[dict], verified_tags: Iterable[str] = ()
) -> tuple[list[Release], set[str], list[Release]]:
    from gh_releases import parse_github_time

    releases: list[Release] = []
    for payload in payloads:
        if payload.get("draft"):
            continue
        tag = str(payload.get("tag_name") or "")
        item = release_from_tag(
            tag,
            release_id=int(payload.get("id") or 0),
            published_at=parse_github_time(payload.get("published_at")),
            name=str(payload.get("name") or ""),
            body=str(payload.get("body") or ""),
        )
        if item is not None:
            releases.append(item)
    keep = select_keep(releases, verified_tags=verified_tags)
    return releases, keep, releases_to_delete(releases, keep)


def prune(
    client: ReleaseAPI,
    *,
    apply: bool = False,
    verified_file: Optional[Path] = None,
) -> PrunePlan:
    _releases, keep, deleting = plan_from_api_payloads(
        client.list_releases(), read_verified_tags(verified_file)
    )
    if apply:
        for item in deleting:
            client.delete_release(item.release_id, item.tag)
    return PrunePlan(
        keep=tuple(sorted(keep)),
        delete=tuple(item.tag for item in deleting),
        applied=apply,
    )


def render_plan(plan: PrunePlan) -> str:
    mode = "applied" if plan.applied else "dry-run"
    lines = [
        f"prune {mode}",
        f"keep ({len(plan.keep)}): {', '.join(plan.keep) or '(none)'}",
        f"delete ({len(plan.delete)}): {', '.join(plan.delete) or '(none)'}",
    ]
    return "\n".join(lines)


def default_verified_file() -> Path:
    override = (os.environ.get("FITDASH_BACKUP_STATE_DIR") or "").strip()
    base = Path(override).expanduser() if override else Path.home() / ".local" / "state" / "fitdash-backup"
    return base / "last-verified-good"


def main(argv: Optional[list[str]] = None) -> int:
    from gh_releases import client_from_env

    parser = argparse.ArgumentParser(description="Prune FitDash backup releases")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="delete releases outside the retention set (default is dry-run)",
    )
    parser.add_argument("--repo", default=None, help="owner/name (default cvolkernick/fitdash-backups)")
    parser.add_argument(
        "--verified-file",
        default=None,
        help="file of tags that must be kept (default last-verified-good in the state dir)",
    )
    args = parser.parse_args(argv)
    verified = Path(args.verified_file).expanduser() if args.verified_file else default_verified_file()
    try:
        client = client_from_env(args.repo)
        plan = prune(client, apply=args.apply, verified_file=verified)
    except Exception as exc:  # noqa: BLE001 — operator-facing CLI
        print(f"fitdash_prune: {exc}", file=sys.stderr)
        return 1
    print(render_plan(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
