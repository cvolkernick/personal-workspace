"""Daily Brief publish CLI.

Grok's morning/evening routines call it over SSH on prism. The legacy path
still works (``financial-command/brief.py`` is a thin shim onto this module)::

  python3 ~/personal-workspace/financial-command/brief.py publish -  < edition.json
  python3 -m research.daily_brief publish <file.json|->     # same, new home
  python3 -m research.daily_brief validate <file.json|->
  python3 -m research.daily_brief list
  python3 -m research.daily_brief path
  python3 -m research.daily_brief mac-tasks <page.md|->     # Notion page body -> mac_tasks JSON
  python3 -m research.daily_brief publish - --with-fund     # also snapshot the Fund watch block (reads the repo checkout)
  python3 -m research.daily_brief fund-build [--date D]     # print the fund block JSON, write nothing
  python3 -m research.daily_brief fund-check <notes.md>     # check Naka's notes file against FORMAT.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import prism_public  # noqa: E402
from research.daily_brief.store import (  # noqa: E402
    MAC_TASKS_URL,
    BriefError,
    eastern_date_for,
    edition_url,
    list_editions,
    load_json_arg,
    mac_tasks_from_markdown,
    normalize,
    parse_filename,
    publish,
    store_dir,
    validate,
)


def build_fund_block(
    root: Path | str | None = None,
    *,
    date_s: str | None = None,
    now: datetime | None = None,
    macro_path: str | None = None,
    network: bool = True,
    issue: dict | None = None,
) -> dict:
    """Assemble the Fund watch block from the repo checkout (see fund.py). Never raises on missing inputs."""
    from research.daily_brief import fund, fund_notes, fund_status

    root = Path(root) if root else _REPO_ROOT
    date_s = date_s or eastern_date_for(now)
    cs = fund.load_consider_share(root / "investment" / "consider_share.json")
    policy = fund._load_json(root / "investment" / "fund_manager.json") or {}
    snapshot = fund._load_json(root / "treasury" / "snapshots" / "robinhood_latest.json")
    if issue is None and network:
        issue = fund_status.gather_issue()
    macro_file = macro_path or os.environ.get("DAILY_BRIEF_FUND_MACRO") or None
    if not macro_file and (store_dir() / fund.MACRO_FILENAME).is_file():
        macro_file = str(store_dir() / fund.MACRO_FILENAME)
    return fund.build_fund(
        cs,
        notes=fund_notes.load_notes(root, date_s),
        macro=fund.load_macro(macro_file),
        holdings=fund_status.holdings_block(snapshot, policy, cs, now),
        status=fund_status.status_block(policy, issue),
        naka=fund_status.parse_naka_actions(_read_text(root / "investment" / "naka-actions.md")),
        now=now,
    )


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


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
    p_pub.add_argument("--with-fund", action="store_true", help="snapshot the Fund watch block into the edition (unless it already has one)")
    p_fb = sub.add_parser("fund-build", help="print the Fund watch block JSON for a date (writes nothing)")
    p_fb.add_argument("--date", default=None, help="Eastern date YYYY-MM-DD (default: today ET)")
    p_fb.add_argument("--root", default=None, help="repo checkout (default: this checkout)")
    p_fb.add_argument("--macro", default=None, help="Meridian macro file (default: env/store/fixture)")
    p_fb.add_argument("--no-network", action="store_true", help="skip the #1093 lookup (shows no data)")
    p_fc = sub.add_parser("fund-check", help="parse a notes file and report problems (exit 2 if any)")
    p_fc.add_argument("file")
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
        if args.cmd == "fund-build":
            block = build_fund_block(args.root, date_s=args.date, macro_path=args.macro, network=not args.no_network)
            print(json.dumps(block, ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "fund-check":
            from research.daily_brief.fund_notes import parse_notes

            res = parse_notes(_read_text(Path(args.file)) or "")
            print(json.dumps({k: res[k] for k in ("state", "date", "filed_at", "missing_sections", "unparsed", "problems")}, indent=2))
            return 0 if not res["problems"] else 2
        if args.cmd == "publish":
            edition = load_json_arg(args.file)
            if args.with_fund:
                ed = normalize(edition)
                if ed.get("fund") is None:
                    edition = {**edition, "fund": build_fund_block(date_s=ed.get("date"))}
            dest = publish(edition)
            d, e = parse_filename(dest.name) or ("", "")
            url = edition_url((d, e))
            print(json.dumps({"ok": True, "path": str(dest), "url": url, "public_url": public_url(url),
                              "date": d, "edition": e}))
            return 0
        if args.cmd == "validate":
            ed = normalize(load_json_arg(args.file))
            problems = validate(ed)
            print(json.dumps({"ok": not problems, "problems": problems, "date": ed.get("date"), "edition": ed.get("edition")}))
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
