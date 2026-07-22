#!/usr/bin/env python3
"""Turn freeform capture text into a structured initiative markdown file.

High-leverage friction killer for the creative/wealth loop:
scattered notes (chat paste, voice dump, memo) → durable initiative under
``initiatives/`` with title, description, next action, and progress stubs.

Usage:
  python3 tools/capture_to_initiative.py "Ship weekly market memo"
  python3 tools/capture_to_initiative.py --title "X" --body-file notes.txt
  echo "raw notes..." | python3 tools/capture_to_initiative.py --stdin --title "Y"
  python3 tools/capture_to_initiative.py --title "X" --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = WORKSPACE_ROOT / "initiatives"


def slugify(title: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (title or "initiative").strip().lower()).strip("-")
    return (s[:60] or "initiative")


def extract_title(body: str, explicit: Optional[str] = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    for line in (body or "").splitlines():
        line = line.strip()
        if not line:
            continue
        # drop common list markers
        line = re.sub(r"^[-*#]+\s*", "", line)
        return line[:120]
    return "Untitled capture"


def extract_next_action(body: str) -> str:
    """Heuristic: first line starting with TODO/Next/Action or first non-title line."""
    lines = [ln.strip() for ln in (body or "").splitlines() if ln.strip()]
    for ln in lines[1:]:
        if re.match(r"^(TODO|Next|Action|NA)\b[:\s-]*", ln, re.I):
            return re.sub(r"^(TODO|Next|Action|NA)\b[:\s-]*", "", ln, flags=re.I).strip()
    if len(lines) > 1:
        return lines[1][:200]
    return "Define the smallest next shippable step."


def build_initiative_markdown(
    *,
    title: str,
    body: str,
    next_action: str,
    created_at: Optional[str] = None,
) -> str:
    created = created_at or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    body = (body or "").strip() or "_No additional capture body._"
    return f"""# {title}

## Description
{body}

## Current Next Action
{next_action}

## Progress / Wins
- [ ] Captured from freeform notes via `tools/capture_to_initiative.py` on {created}

## Notes
- Edit this file freely; keep next action concrete and shippable in hours.
- Link from `strategy/today.md` when it becomes the day's focus.

See `strategy/command-center-requirements.md` and `strategy/bets.md` for context.
"""


def write_initiative(
    *,
    title: str,
    body: str,
    out_dir: Path = DEFAULT_OUT_DIR,
    dry_run: bool = False,
) -> dict:
    title = extract_title(body, title)
    next_action = extract_next_action(body if body.strip() else title)
    # If body was only the title, still produce a useful description stub
    desc_body = body.strip()
    if desc_body == title:
        desc_body = f"Initiative captured from freeform notes.\n\nOriginal capture:\n{title}"
    md = build_initiative_markdown(title=title, body=desc_body, next_action=next_action)
    slug = slugify(title)
    path = out_dir / f"{slug}.md"
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        # avoid clobber: suffix if exists
        if path.is_file():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            path = out_dir / f"{slug}-{stamp}.md"
        path.write_text(md, encoding="utf-8")
    abs_path = path if path.is_absolute() else (out_dir / path.name)
    try:
        rel = str(path.resolve().relative_to(WORKSPACE_ROOT.resolve()))
    except ValueError:
        rel = str(path)
    return {
        "ok": True,
        "title": title,
        "path": rel,
        "absolute_path": str(path.resolve()) if not dry_run else str((out_dir / f"{slug}.md").resolve()),
        "slug": slug,
        "next_action": next_action,
        "markdown": md,
        "dry_run": dry_run,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Capture freeform notes → initiative MD")
    p.add_argument("title_or_body", nargs="?", help="Title or short capture text")
    p.add_argument("--title", help="Explicit title")
    p.add_argument("--body-file", type=Path, help="Read body from file")
    p.add_argument("--stdin", action="store_true", help="Read body from stdin")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    body = ""
    if args.body_file:
        body = args.body_file.read_text(encoding="utf-8")
    elif args.stdin:
        body = sys.stdin.read()
    elif args.title_or_body:
        body = args.title_or_body

    title = args.title
    if not body and not title:
        p.error("provide capture text, --title, --body-file, or --stdin")

    if not body and title:
        body = title

    result = write_initiative(
        title=title or "",
        body=body,
        out_dir=args.out_dir,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        sys.stdout.write(result["markdown"])
    else:
        print(result["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
