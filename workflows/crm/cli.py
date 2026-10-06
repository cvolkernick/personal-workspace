"""Thin `crm` client. Local DB for tests and the roadside seat; HTTP when CRM_API_URL is set."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from workflows.crm.core import CrmDB
from workflows.crm.schema import default_db_path


def badge_line(party: dict[str, Any]) -> str:
    kind = str(party.get("type") or "").upper()
    name = party.get("display_name") or party.get("org_name") or party.get("id") or ""
    return f"[{kind}] {name}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crm", description="Panamerica CRM client")
    parser.add_argument("--db", default=os.environ.get("PANAMERICA_CRM_DB") or str(default_db_path()))
    parser.add_argument("--api", default=os.environ.get("CRM_API_URL") or "")
    parser.add_argument("--token", default=os.environ.get("CRM_TOKEN") or "")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="Print each party with a TYPE badge")
    show = sub.add_parser("get", help="Print one party badge and JSON")
    show.add_argument("party_id")
    search = sub.add_parser("search")
    search.add_argument("--q", default="")
    search.add_argument("--type", default="")
    search.add_argument("--tag", default="")
    search.add_argument("--status", default="")
    token = sub.add_parser("token", help="Issue a seat token (prints the secret once)")
    token.add_argument("seat")

    args = parser.parse_args(argv)
    if args.cmd == "token":
        db = CrmDB(Path(args.db))
        issued = db.issue_token(args.seat)
        print(issued["token"])
        return 0
    if args.api:
        return _http(args)
    db = CrmDB(Path(args.db))
    if args.cmd == "list":
        for party in db.list_parties():
            print(badge_line(party))
        return 0
    if args.cmd == "get":
        from workflows.crm.auth import CrmError

        try:
            party = db.get_party(db.actor_for_seat("grok"), args.party_id)
        except CrmError as exc:
            print(exc.message, file=sys.stderr)
            return 1
        print(badge_line(party))
        print(json.dumps(party, indent=2))
        return 0
    if args.cmd == "search":
        parties = db.search(
            db.actor_for_seat("grok"),
            q=args.q,
            party_type=args.type,
            tag=args.tag,
            status=args.status,
        )
        for party in parties:
            print(badge_line(party))
        return 0
    return 2


def _http(args: argparse.Namespace) -> int:
    base = args.api.rstrip("/")
    if args.cmd == "list":
        # The HTTP API has no unscoped list. Search with an empty query.
        payload = _request(base, "GET", "/v1/search", args.token)
        for party in payload.get("results") or []:
            print(badge_line(party))
        return 0
    if args.cmd == "get":
        payload = _request(base, "GET", f"/v1/parties/{args.party_id}", args.token)
        print(badge_line(payload))
        print(json.dumps(payload, indent=2))
        return 0
    if args.cmd == "search":
        query = urllib_query(
            {"q": args.q, "type": args.type, "tag": args.tag, "status": args.status}
        )
        payload = _request(base, "GET", "/v1/search" + query, args.token)
        for party in payload.get("results") or []:
            print(badge_line(party))
        return 0
    return 2


def urllib_query(params: dict[str, str]) -> str:
    from urllib.parse import urlencode

    kept = {key: value for key, value in params.items() if value}
    return ("?" + urlencode(kept)) if kept else ""


def _request(base: str, method: str, path: str, token: str) -> dict[str, Any]:
    req = urllib.request.Request(base + path, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(detail or exc.reason, file=sys.stderr)
        raise SystemExit(1) from exc
    parsed = json.loads(raw or "{}")
    return parsed if isinstance(parsed, dict) else {"data": parsed}


if __name__ == "__main__":
    raise SystemExit(main())
