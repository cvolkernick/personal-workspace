#!/usr/bin/env python3
"""Grok Bot harness entry for the demo-site outreach workflow.

    python3 workflows/demo-site-outreach/run.py run \\
        --geo "Austin, TX" --category "" --batch-size 10 --dry-run \\
        --fixture workflows/demo-site-outreach/tests/fixtures/places_listings.json \\
        --simulate-replies

Args: --geo, --category, --batch-size, --dry-run / --live.
Live sends are blocked until DEMO_SITE_OUTREACH_TEMPLATE_APPROVED=1.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from config import Config, LiveBlocked  # noqa: E402
from pipeline import Pipeline, make_pipeline, public_error  # noqa: E402
from webhook import check_secret, handle_payload, serve  # noqa: E402


def _json(obj: object) -> int:
    print(json.dumps(obj, indent=2, default=str))
    return 0


def _cfg(args: argparse.Namespace) -> Config:
    overrides: dict[str, object] = {
        "dry_run": not bool(args.live),
        "geo": args.geo,
        "category": args.category or "",
        "batch_size": int(args.batch_size),
        "simulate_replies": bool(getattr(args, "simulate_replies", False)),
        "simulate_interest": bool(getattr(args, "simulate_interest", False)),
    }
    if args.store:
        overrides["store_path"] = Path(args.store).expanduser()
    if getattr(args, "fixture", None):
        overrides["fixture_path"] = Path(args.fixture).expanduser()
    return Config.from_env(**overrides)


def _pipe(args: argparse.Namespace) -> Pipeline:
    return make_pipeline(_cfg(args))


def cmd_run(args: argparse.Namespace) -> int:
    return _json(_pipe(args).run())


def cmd_source(args: argparse.Namespace) -> int:
    leads = _pipe(args).source()
    return _json(
        {
            "count": len(leads),
            "leads": [
                {
                    "id": lead.id,
                    "name": lead.name,
                    "address": lead.address,
                    "phone": lead.phone,
                    "hours": lead.hours,
                    "category": lead.category,
                    "website": lead.website,
                    "state": lead.state,
                }
                for lead in leads
            ],
        }
    )


def cmd_outreach(args: argparse.Namespace) -> int:
    return _json({"results": _pipe(args).outreach_batch()})


def cmd_ingest(args: argparse.Namespace) -> int:
    pipe = _pipe(args)
    lead = pipe.store.get(args.lead) if args.lead else None
    body = args.body or sys.stdin.read()
    return _json(pipe.ingest_text(body, lead=lead, phone=args.phone or ""))


def cmd_build(args: argparse.Namespace) -> int:
    pipe = _pipe(args)
    lead = pipe.store.get(args.lead)
    if lead is None:
        return _json({"error": "unknown lead"})
    return _json(pipe.build_and_deliver(lead))


def cmd_follow_up(args: argparse.Namespace) -> int:
    return _json({"results": _pipe(args).follow_up_batch()})


def cmd_webhook(args: argparse.Namespace) -> int:
    pipe = _pipe(args)
    if args.payload_file:
        payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    else:
        payload = json.loads(sys.stdin.read() or "{}")
    if not isinstance(payload, dict):
        return _json({"error": "payload must be a JSON object"})
    provided = args.secret or ""
    if not check_secret(provided, pipe.cfg.webhook_secret):
        return _json({"error": "unauthorized"})
    return _json(handle_payload(pipe, payload))


def cmd_serve(args: argparse.Namespace) -> int:
    pipe = _pipe(args)
    httpd = serve(pipe, args.host, int(args.port))
    print(
        json.dumps({"listening": f"http://{args.host}:{args.port}", "dry_run": pipe.cfg.dry_run}),
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    pipe = _pipe(args)
    return _json(
        {
            "config": pipe.cfg.redacted(),
            "counts": pipe.store.counts(),
            "suppression": len(pipe.store._doc["suppression"]),
            "outbox": len(pipe.store.outbox()),
            "alerts": len(pipe.store.alerts()),
        }
    )


def cmd_teardown(args: argparse.Namespace) -> int:
    return _json({"results": _pipe(args).teardown_expired()})


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Demo-site outreach workflow (Alexandra / Bland / Grok Bot harness)."
    )
    p.add_argument("--geo", default="US", help="Places geo (city, state, or US)")
    p.add_argument("--category", default="", help="Optional Places category filter (empty = none)")
    p.add_argument("--batch-size", type=int, default=10, help="Max new leads per source run")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="live",
        action="store_false",
        help="Full pipeline, no external sends (default)",
    )
    mode.add_argument(
        "--live",
        dest="live",
        action="store_true",
        help="Real email/SMS/Vercel/alert (blocked until template approved)",
    )
    p.set_defaults(live=False)
    p.add_argument("--store", default="", help="JSON store path (canonical file store)")
    p.add_argument("--fixture", default="", help="Places listings JSON for dry-run/source")
    p.add_argument("--simulate-replies", action="store_true", help="Dry-run: invent discovery replies")
    p.add_argument("--simulate-interest", action="store_true", help="Dry-run: invent interest after demo")

    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="Source (+ optional simulate) end-to-end").set_defaults(func=cmd_run)
    sub.add_parser("source", help="Stage 1: source N no-website leads").set_defaults(func=cmd_source)
    sub.add_parser("outreach", help="Stage 2: discovery emails for sourced leads").set_defaults(func=cmd_outreach)
    ing = sub.add_parser("ingest-reply", help="Parse a discovery/feedback reply")
    ing.add_argument("--lead", default="")
    ing.add_argument("--phone", default="")
    ing.add_argument("--body", default="")
    ing.set_defaults(func=cmd_ingest)
    bld = sub.add_parser("build", help="Stage 3–4: generate site + SMS the link")
    bld.add_argument("--lead", required=True)
    bld.set_defaults(func=cmd_build)
    sub.add_parser("follow-up", help="Stage 6: max 2 follow-ups then dead").set_defaults(func=cmd_follow_up)
    wh = sub.add_parser("webhook", help="Apply one inbound Bland JSON payload")
    wh.add_argument("--payload-file", default="")
    wh.add_argument("--secret", default="")
    wh.set_defaults(func=cmd_webhook)
    srv = sub.add_parser("serve-webhook", help="HTTP webhook server for Bland inbound SMS")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8788)
    srv.set_defaults(func=cmd_serve)
    sub.add_parser("status", help="Counts + redacted config").set_defaults(func=cmd_status)
    sub.add_parser("teardown-expired", help="Delete demos older than retention days").set_defaults(
        func=cmd_teardown
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (LiveBlocked, RuntimeError) as exc:
        _json(public_error(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
