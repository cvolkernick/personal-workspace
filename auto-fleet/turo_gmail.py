"""Write a Turo inbox dump the dashboard can parse. No live Gmail call.

The :8796 process never talks to Gmail. Refresh from an agent session
(Gmail MCP) or pipe JSON into this writer:

  python3 auto-fleet/turo_gmail.py --from-json dump.json
  python3 auto-fleet/turo_gmail.py --from-json -

Default output: ~/.config/auto-fleet/turo_inbox.json (mode 600, not git).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from . import turo_inbox
except ImportError:  # script path
    import turo_inbox  # type: ignore

DEFAULT_OUT = turo_inbox.CONFIG_INBOX
GMAIL_QUERY = turo_inbox.GMAIL_QUERY
GMAIL_INBOX_ADDR = turo_inbox.GMAIL_INBOX_ADDR


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_messages(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [m for m in raw if isinstance(m, dict)]
    if isinstance(raw, dict):
        msgs = raw.get("messages")
        if isinstance(msgs, list):
            return [m for m in msgs if isinstance(m, dict)]
        if any(k in raw for k in ("subject", "body", "from")):
            return [raw]
    raise ValueError("expected a list of messages or an object with messages[]")


def write_dump(
    messages: Sequence[Mapping[str, Any]],
    path: Path | None = None,
    *,
    inbox: str = GMAIL_INBOX_ADDR,
    query: str = GMAIL_QUERY,
    source: str = "gmail_dump",
) -> Path:
    dest = Path(path) if path is not None else DEFAULT_OUT
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "as_of": _now(),
        "source": source,
        "inbox": inbox,
        "query": query,
        "messages": [dict(m) for m in messages],
    }
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    return dest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-json",
        required=True,
        help="Path to a JSON list/object, or '-' for stdin",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Dump path (default {DEFAULT_OUT})",
    )
    parser.add_argument("--inbox", default=GMAIL_INBOX_ADDR)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.from_json == "-":
        raw = json.load(sys.stdin)
    else:
        raw = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
    messages = normalize_messages(raw)
    dest = write_dump(messages, args.out, inbox=args.inbox)
    print(f"wrote {len(messages)} message(s) to {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
