"""CLI: print the Roadside brief, or ingest one JSON lead from stdin.

No Messenger or SMS is sent.
"""

from __future__ import annotations

import json
import sys

from workflows.marketplace_leads.actions import ActionError, apply_action
from workflows.marketplace_leads.api import public_lead
from workflows.marketplace_leads.brief import morning_brief_from_store
from workflows.marketplace_leads.ingest import ingest
from workflows.marketplace_leads.store import open_store


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "brief"
    store = open_store()
    if cmd == "brief":
        sys.stdout.write(morning_brief_from_store(store))
        return 0
    if cmd == "ingest":
        payload = json.loads(sys.stdin.read() or "{}")
        result = ingest(store, payload)
        json.dump(
            {
                "ok": True,
                "created": result.created,
                "duplicate": result.duplicate,
                "lead": public_lead(result.lead),
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        return 0
    if cmd == "action":
        if len(args) < 3:
            sys.stderr.write("usage: action <lead-id> <contacted|disqualified|converted|sms_eligible>\n")
            return 1
        try:
            lead = apply_action(store, args[1], args[2])
        except ActionError as exc:
            json.dump({"ok": False, "error": str(exc)}, sys.stdout)
            sys.stdout.write("\n")
            return 1
        json.dump({"ok": True, "lead": public_lead(lead)}, sys.stdout)
        sys.stdout.write("\n")
        return 0
    sys.stderr.write("usage: brief | ingest | action <id> <action>\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
