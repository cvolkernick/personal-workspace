"""Allowlisted SMS/RCS contact → B2 capture (incremental, OTP-redacted).

Config (not in git): ~/B2/inbox/meta/sms_contacts.json
State:              ~/B2/inbox/meta/sms_ingest_state.json
Token:              env SMS_INGEST_TOKEN or ~/B2/inbox/meta/sms_ingest_token
Captures:           inbox/captures/sms/<contact_id>.md

CLI:
  python3 -m b2_kb.sms_contact status
  python3 -m b2_kb.sms_contact ingest --file messages.json
  python3 -m b2_kb.sms_contact init-token
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .vault import resolve_vault_path

META_REL = Path("inbox") / "meta"
CAPTURES_REL = Path("inbox") / "captures" / "sms"
CONTACTS_FILE = "sms_contacts.json"
STATE_FILE = "sms_ingest_state.json"
TOKEN_FILE = "sms_ingest_token"

# E.164-ish digits only for matching
_NON_DIGIT = re.compile(r"\D+")
# OTP / verification heuristics (conservative)
_OTP_LINE = re.compile(
    r"(?i)\b("
    r"(?:your\s+)?(?:code|otp|passcode|verification(?:\s+code)?|2fa|one[-\s]?time)"
    r".{0,40}\b\d{4,8}\b"
    r"|\b\d{4,8}\b\s*(?:is\s+your|is\s+the)\s+(?:code|otp|passcode)"
    r")\b"
)
_BARE_CODE = re.compile(r"(?<!\d)(\d{6,8})(?!\d)")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_address(raw: str) -> str:
    """Normalize phone-ish addresses to comparable form (+digits when possible)."""
    s = (raw or "").strip()
    if not s:
        return ""
    # keep shortcodes (e.g. 12345) as-is digits
    digits = _NON_DIGIT.sub("", s)
    if not digits:
        return s.lower()
    if s.startswith("+") or len(digits) >= 10:
        # US default: 10 digits → +1
        if len(digits) == 10:
            return "+1" + digits
        if len(digits) == 11 and digits.startswith("1"):
            return "+" + digits
        return "+" + digits if not s.startswith("+") else "+" + digits
    return digits


def addresses_match(a: str, b: str) -> bool:
    na, nb = normalize_address(a), normalize_address(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # suffix match for national vs +E.164
    return na.endswith(nb) or nb.endswith(na)


def redact_body(text: str, enabled: bool = True) -> str:
    if not enabled or not text:
        return text or ""
    out = _OTP_LINE.sub("[OTP redacted]", text)
    # only bare-code redact when line looks auth-related
    lines = []
    for line in out.splitlines():
        if re.search(r"(?i)\b(code|otp|verify|verification|2fa|login|password)\b", line):
            line = _BARE_CODE.sub("[OTP redacted]", line)
        lines.append(line)
    return "\n".join(lines)


def message_id(address: str, ts: str, body: str, direction: str) -> str:
    raw = f"{normalize_address(address)}|{ts}|{direction}|{body}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def meta_dir(vault: Path) -> Path:
    d = vault / META_REL
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@dataclass
class Contact:
    id: str
    display_name: str
    addresses: List[str]
    enabled: bool = True
    redact_otp: bool = True
    max_body_chars: int = 4000

    def matches(self, address: str) -> bool:
        return any(addresses_match(address, a) for a in self.addresses)


def load_contacts(vault: Path) -> List[Contact]:
    raw = load_json(meta_dir(vault) / CONTACTS_FILE, {"version": 1, "contacts": []})
    out: List[Contact] = []
    for c in raw.get("contacts") or []:
        if not isinstance(c, dict):
            continue
        addrs = c.get("addresses") or []
        if isinstance(addrs, str):
            addrs = [addrs]
        out.append(
            Contact(
                id=str(c.get("id") or "").strip() or "unknown",
                display_name=str(c.get("display_name") or c.get("id") or "Unknown").strip(),
                addresses=[str(a) for a in addrs if str(a).strip()],
                enabled=bool(c.get("enabled", True)),
                redact_otp=bool(c.get("redact_otp", True)),
                max_body_chars=int(c.get("max_body_chars") or 4000),
            )
        )
    return out


def find_contact(contacts: Sequence[Contact], address: str) -> Optional[Contact]:
    for c in contacts:
        if c.enabled and c.matches(address):
            return c
    return None


def load_state(vault: Path) -> Dict[str, Any]:
    return load_json(meta_dir(vault) / STATE_FILE, {"version": 1, "contacts": {}})


def save_state(vault: Path, state: Dict[str, Any]) -> None:
    save_json(meta_dir(vault) / STATE_FILE, state)


def get_or_create_token(vault: Path) -> str:
    env = (os.environ.get("SMS_INGEST_TOKEN") or "").strip()
    if env:
        return env
    path = meta_dir(vault) / TOKEN_FILE
    if path.is_file():
        t = path.read_text(encoding="utf-8").strip()
        if t:
            return t
    t = secrets.token_urlsafe(32)
    path.write_text(t + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return t


def verify_token(vault: Path, provided: Optional[str]) -> bool:
    if not provided:
        return False
    expected = get_or_create_token(vault)
    # constant-time-ish compare
    return secrets.compare_digest(provided.strip(), expected)


def capture_path(vault: Path, contact_id: str) -> Path:
    d = vault / CAPTURES_REL
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", contact_id).strip("-") or "contact"
    return d / f"{safe}.md"


def ensure_capture_header(path: Path, contact: Contact) -> None:
    if path.is_file():
        return
    addrs = ", ".join(f"`{a}`" for a in contact.addresses)
    header = (
        f"---\n"
        f'title: "SMS — {contact.display_name}"\n'
        f"tags: [sms, contact, b2-capture]\n"
        f"source_type: sms_thread\n"
        f"contact_id: {contact.id}\n"
        f"addresses: {json.dumps(contact.addresses)}\n"
        f"as_of: {utc_now_iso()}\n"
        f"---\n\n"
        f"# SMS — {contact.display_name}\n\n"
        f"Allowlisted addresses: {addrs}\n\n"
        f"Autonomous ingest from Android bridge. OTP lines redacted.\n\n"
    )
    path.write_text(header, encoding="utf-8")


def format_block(ts: str, direction: str, body: str) -> str:
    arrow = "→ me" if direction in ("out", "outbound", "sent") else "← them"
    if direction in ("out", "outbound", "sent"):
        arrow = "→ me"
    elif direction in ("in", "inbound", "received"):
        arrow = "← them"
    else:
        arrow = direction or "?"
    return f"### {ts} {arrow}\n{body.strip()}\n\n"


def _parse_messages(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [m for m in payload if isinstance(m, dict)]
    if isinstance(payload, dict):
        msgs = payload.get("messages") or payload.get("items") or []
        if isinstance(msgs, list):
            return [m for m in msgs if isinstance(m, dict)]
        # single message object
        if payload.get("body") or payload.get("text"):
            return [payload]
    return []


def ingest_messages(
    payload: Any,
    vault_path: Optional[os.PathLike | str] = None,
    *,
    notify_summary: bool = False,
) -> Dict[str, Any]:
    """Ingest a batch of messages. Rejects non-allowlisted addresses.

    Message fields (flexible):
      address / from / number / phone
      body / text / message
      ts / timestamp / date / date_sent  (ISO or ms epoch)
      direction / type  (in|out|inbound|outbound|1|2)
    """
    vault = resolve_vault_path(vault_path)
    contacts = load_contacts(vault)
    if not contacts:
        return {
            "ok": False,
            "error": "no contacts configured",
            "hint": f"create {META_REL / CONTACTS_FILE}",
            "accepted": 0,
            "rejected": 0,
            "duplicates": 0,
        }

    state = load_state(vault)
    cstate: Dict[str, Any] = state.setdefault("contacts", {})
    accepted = 0
    rejected = 0
    duplicates = 0
    by_contact: Dict[str, int] = {}
    errors: List[str] = []

    for raw in _parse_messages(payload):
        address = str(
            raw.get("address")
            or raw.get("from")
            or raw.get("number")
            or raw.get("phone")
            or raw.get("address_normalized")
            or ""
        ).strip()
        body = str(raw.get("body") or raw.get("text") or raw.get("message") or "")
        direction = str(raw.get("direction") or raw.get("type") or "in").lower()
        if direction in ("1", "received", "inbox"):
            direction = "in"
        elif direction in ("2", "sent", "outbox"):
            direction = "out"

        ts_raw = raw.get("ts") or raw.get("timestamp") or raw.get("date") or raw.get("date_sent")
        ts = _normalize_ts(ts_raw)

        contact = find_contact(contacts, address)
        if not contact:
            rejected += 1
            continue

        if contact.redact_otp:
            body = redact_body(body, True)
        if contact.max_body_chars and len(body) > contact.max_body_chars:
            body = body[: contact.max_body_chars] + "…"

        mid = str(raw.get("id") or message_id(address, ts, body, direction))
        st = cstate.setdefault(
            contact.id,
            {"seen_ids": [], "last_message_ts": None, "message_count": 0, "last_ingest_at": None},
        )
        seen: List[str] = list(st.get("seen_ids") or [])
        if mid in seen:
            duplicates += 1
            continue

        path = capture_path(vault, contact.id)
        ensure_capture_header(path, contact)
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(format_block(ts, direction, body))
            # bump as_of in frontmatter lightly: rewrite first lines if present
            _touch_as_of(path)
        except OSError as e:
            errors.append(f"{contact.id}: {e}")
            continue

        seen.append(mid)
        # cap seen list
        if len(seen) > 5000:
            seen = seen[-3000:]
        st["seen_ids"] = seen
        st["last_message_ts"] = ts
        st["message_count"] = int(st.get("message_count") or 0) + 1
        st["last_ingest_at"] = utc_now_iso()
        accepted += 1
        by_contact[contact.id] = by_contact.get(contact.id, 0) + 1

    save_state(vault, state)
    result = {
        "ok": True,
        "accepted": accepted,
        "rejected": rejected,
        "duplicates": duplicates,
        "by_contact": by_contact,
        "errors": errors,
        "as_of": utc_now_iso(),
    }
    if notify_summary:
        result["channel_summary"] = format_channel_summary(result, contacts)
    return result


def format_channel_summary(result: Dict[str, Any], contacts: Sequence[Contact]) -> str:
    if not result.get("accepted"):
        return ""
    names = {c.id: c.display_name for c in contacts}
    parts = [
        f"{names.get(cid, cid)} +{n}" for cid, n in (result.get("by_contact") or {}).items()
    ]
    return (
        f"**SMS → B2** promoted {result['accepted']} message(s) "
        f"({', '.join(parts)}). Dupes={result.get('duplicates', 0)} "
        f"rejected={result.get('rejected', 0)}."
    )


def _normalize_ts(ts_raw: Any) -> str:
    if ts_raw is None or ts_raw == "":
        return utc_now_iso()
    if isinstance(ts_raw, (int, float)):
        # ms vs s
        v = float(ts_raw)
        if v > 1e12:
            v = v / 1000.0
        return datetime.fromtimestamp(v, tz=timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        )
    s = str(ts_raw).strip()
    # pure digits epoch
    if re.fullmatch(r"\d{10,13}", s):
        return _normalize_ts(int(s))
    return s


def _touch_as_of(path: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    if not text.startswith("---"):
        return
    parts = text.split("---", 2)
    if len(parts) < 3:
        return
    fm = parts[1]
    body = parts[2]
    if re.search(r"(?m)^as_of:", fm):
        fm = re.sub(r"(?m)^as_of:.*$", f"as_of: {utc_now_iso()}", fm)
    else:
        fm = fm.rstrip() + f"\nas_of: {utc_now_iso()}\n"
    path.write_text(f"---{fm}---{body}", encoding="utf-8")


def status(vault_path: Optional[os.PathLike | str] = None) -> Dict[str, Any]:
    vault = resolve_vault_path(vault_path)
    contacts = load_contacts(vault)
    state = load_state(vault)
    token_path = meta_dir(vault) / TOKEN_FILE
    return {
        "vault": str(vault),
        "contacts_file": str(meta_dir(vault) / CONTACTS_FILE),
        "contacts": [
            {
                "id": c.id,
                "display_name": c.display_name,
                "addresses": c.addresses,
                "enabled": c.enabled,
                "capture": str(CAPTURES_REL / f"{c.id}.md"),
                "state": {
                    k: v
                    for k, v in ((state.get("contacts") or {}).get(c.id) or {}).items()
                    if k != "seen_ids"
                },
            }
            for c in contacts
        ],
        "token_configured": bool(
            (os.environ.get("SMS_INGEST_TOKEN") or "").strip() or token_path.is_file()
        ),
        "cadence_recommendation": "push on receive + 15m catch-up poll",
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SMS allowlisted contact → B2 ingest")
    parser.add_argument("--vault", default=None, help="Vault path (default ~/B2)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show contacts + ingest state")
    p_init = sub.add_parser("init-token", help="Create ingest bearer token if missing")
    p_ing = sub.add_parser("ingest", help="Ingest messages from JSON file or stdin")
    p_ing.add_argument("--file", "-f", help="JSON file; omit or - for stdin")
    p_ing.add_argument(
        "--format",
        choices=("json", "channel"),
        default="json",
        help="json result or channel markdown summary",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)
    vault = resolve_vault_path(args.vault)

    if args.cmd == "status":
        print(json.dumps(status(vault), indent=2))
        return 0

    if args.cmd == "init-token":
        t = get_or_create_token(vault)
        print(json.dumps({"ok": True, "token_path": str(meta_dir(vault) / TOKEN_FILE), "token_len": len(t)}))
        return 0

    if args.cmd == "ingest":
        if args.file and args.file != "-":
            raw = Path(args.file).read_text(encoding="utf-8")
        else:
            raw = sys.stdin.read()
        try:
            payload = json.loads(raw) if raw.strip() else {"messages": []}
        except json.JSONDecodeError as e:
            print(json.dumps({"ok": False, "error": f"invalid JSON: {e}"}))
            return 1
        result = ingest_messages(payload, vault, notify_summary=True)
        if args.format == "channel":
            summary = result.get("channel_summary") or json.dumps(result)
            print(summary)
        else:
            print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
