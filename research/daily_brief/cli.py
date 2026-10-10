"""Daily Brief publish CLI.

Grok's morning/evening routines call it over SSH on prism. The legacy path
still works (``financial-command/brief.py`` is a thin shim onto this module)::

  python3 ~/personal-workspace/financial-command/brief.py publish -  < edition.json
  python3 -m research.daily_brief publish <file.json|->     # same, new home
  python3 -m research.daily_brief validate <file.json|->
  python3 -m research.daily_brief list
  python3 -m research.daily_brief path
  python3 -m research.daily_brief mac-tasks <page.md|->     # Notion page body -> mac_tasks JSON
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import prism_public  # noqa: E402
from research.daily_brief.store import (  # noqa: E402
    MAC_TASKS_URL,
    BriefError,
    drop_bad_images,
    edition_url,
    list_editions,
    load_json_arg,
    mac_tasks_from_markdown,
    normalize,
    parse_filename,
    publish_report,
    store_dir,
    validate,
)


def public_base() -> str | None:
    """Validated public prefix, or None when publish should print a relative path.

    Does not exit under systemd. Publish still succeeds; the warning is on stderr.
    """
    return prism_public.daily_brief_public_base(fatal_under_systemd=False)


def public_url(path: str) -> str:
    rel = path if path.startswith("/") else "/" + path
    base = public_base()
    if not base:
        return rel
    return base + rel


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="daily-brief", description="Daily Brief edition store (#1091)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_pub = sub.add_parser("publish", help="validate + write an edition (re-publish overwrites)")
    p_pub.add_argument("file", help="edition JSON path, or - for stdin")
    p_val = sub.add_parser("validate", help="validate only, write nothing")
    p_val.add_argument("file")
    sub.add_parser("list", help="list editions, newest first")
    sub.add_parser("path", help="print the edition store directory")
    p_mac = sub.add_parser("mac-tasks", help="build the mac_tasks block from a Notion page body (Markdown)")
    p_mac.add_argument("file", nargs="?", default="-", help="notion-fetch page text/Markdown, or - for stdin")
    p_mac.add_argument("--source-url", default=MAC_TASKS_URL)
    p_mac.add_argument("--fetched-at", default=None, help="ISO timestamp (default: now ET)")
    p_mac.add_argument("--missing", action="store_true", help="page not found: emit page_found=false")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "publish":
            dest, warnings = publish_report(load_json_arg(args.file))
            for w in warnings:
                print(f"daily-brief: warning: {w}", file=sys.stderr)
            d, e = parse_filename(dest.name) or ("", "")
            url = edition_url((d, e))
            print(json.dumps({"ok": True, "path": str(dest), "url": url, "public_url": public_url(url),
                              "date": d, "edition": e, "warnings": warnings}))
            return 0
        if args.cmd == "validate":
            ed, warnings = drop_bad_images(normalize(load_json_arg(args.file)))
            problems = validate(ed)
            print(json.dumps({"ok": not problems, "problems": problems, "warnings": warnings,
                              "date": ed.get("date"), "edition": ed.get("edition")}))
            return 0 if not problems else 2
        if args.cmd == "list":
            for d, e in list_editions():
                print(f"{d} {e} {edition_url((d, e))}")
            return 0
        if args.cmd == "path":
            print(store_dir())
            return 0
        if args.cmd == "mac-tasks":
            md = None
            if not args.missing:
                try:
                    md = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
                except OSError as e:
                    raise BriefError(f"cannot read {args.file}: {e}") from e
            block = mac_tasks_from_markdown(md, source_url=args.source_url, fetched_at=args.fetched_at)
            print(json.dumps(block, ensure_ascii=False, indent=2))
            return 0
    except BriefError as e:
        print(json.dumps({"ok": False, "problems": e.problems}), file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
