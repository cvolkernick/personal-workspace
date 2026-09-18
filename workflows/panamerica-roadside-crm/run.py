#!/usr/bin/env python3
"""Grok Bot harness entry for the Panamerica roadside CRM.

    python3 workflows/panamerica-roadside-crm/run.py daily-pass \\
        --dry-run --fixture workflows/panamerica-roadside-crm/tests/fixtures/photo_sets.json

Live SMS/voice is blocked until PANAMERICA_ROADSIDE_COPY_APPROVED=1.
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
        "simulate_replies": bool(getattr(args, "simulate_replies", False)),
        "simulate_interest": bool(getattr(args, "simulate_interest", False)),
    }
    if args.store:
        overrides["store_path"] = Path(args.store).expanduser()
    if getattr(args, "fixture", None):
        overrides["fixture_path"] = Path(args.fixture).expanduser()
    if getattr(args, "drive_folder", None):
        overrides["drive_folder_id"] = args.drive_folder
    return Config.from_env(**overrides)


def _pipe(args: argparse.Namespace) -> Pipeline:
    return make_pipeline(_cfg(args))


def cmd_run(args: argparse.Namespace) -> int:
    return _json(_pipe(args).run())


def cmd_daily(args: argparse.Namespace) -> int:
    return _json(_pipe(args).daily_pass())


def cmd_sms(args: argparse.Namespace) -> int:
    return _json({"results": _pipe(args).sms_batch()})


def cmd_calls(args: argparse.Namespace) -> int:
    return _json({"results": _pipe(args).call_batch()})


def cmd_ingest(args: argparse.Namespace) -> int:
    pipe = _pipe(args)
    lead = pipe.store.get(args.lead) if args.lead else None
    body = args.body or sys.stdin.read()
    return _json(pipe.ingest_text(body, lead=lead, phone=args.phone or ""))


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
            "needs_info": [
                {"id": lead.id, "folder_name": lead.folder_name, "location": lead.location}
                for lead in pipe.store.by_state("needs-info")
            ],
        }
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Panamerica roadside owner-outreach CRM (Drive intake + Bland SMS/voice)."
    )
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
        help="Real SMS/voice (blocked until copy approved)",
    )
    p.set_defaults(live=False)
    p.add_argument("--store", default="", help="JSON store path (canonical file store)")
    p.add_argument("--fixture", default="", help="Photo-set JSON for dry-run/intake")
    p.add_argument("--drive-folder", default="", help="Override Drive folder id")
    p.add_argument("--simulate-replies", action="store_true", help="Dry-run: invent SMS replies")
    p.add_argument("--simulate-interest", action="store_true", help="Dry-run: invent interest")

    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="Daily pass + Phase 1 SMS (+ optional simulate)").set_defaults(func=cmd_run)
    sub.add_parser("daily-pass", help="Organize root photos from EXIF and pull new sets into the CRM").set_defaults(
        func=cmd_daily
    )
    sub.add_parser("weekly-pass", help="Alias for daily-pass").set_defaults(func=cmd_daily)
    sub.add_parser("sms-batch", help="Phase 1: SMS new leads with approved copy").set_defaults(func=cmd_sms)
    sub.add_parser("call-batch", help="Phase 2: Bland voice to SMS non-responders after 5–7 days").set_defaults(
        func=cmd_calls
    )
    ing = sub.add_parser("ingest-reply", help="Parse an inbound SMS/call transcript")
    ing.add_argument("--lead", default="")
    ing.add_argument("--phone", default="")
    ing.add_argument("--body", default="")
    ing.set_defaults(func=cmd_ingest)
    wh = sub.add_parser("webhook", help="Apply one inbound Bland JSON payload")
    wh.add_argument("--payload-file", default="")
    wh.add_argument("--secret", default="")
    wh.set_defaults(func=cmd_webhook)
    srv = sub.add_parser("serve-webhook", help="HTTP webhook server for Bland inbound")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8789)
    srv.set_defaults(func=cmd_serve)
    sub.add_parser("status", help="Counts + redacted config + needs-info queue").set_defaults(func=cmd_status)
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
