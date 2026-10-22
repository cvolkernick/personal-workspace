"""GET /v1/contacts/by-phone (#1097 slice 1). Read-only, no outbound network.

The caller-context seat (bland-relay, #1096) holds only ``read:caller-context``:
it can call this lookup and nothing else, and it gets identity kinds without
values. Grok, Buzz and Chris (``read:*``) also get identity values.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from workflows.crm.auth import Actor, CrmError, require
from workflows.crm.normalize import phone_e164
from workflows.crm.pii import scrub

ET = ZoneInfo("America/New_York")
SCOPE = "read:caller-context"
DEFAULT_LIMIT = 5
MAX_LIMIT = 20
TURO_TAGS = frozenset({"turo-guest", "turo"})
# Identities the lookup may return. VIN, listing and Drive ids stay internal.
_IDENTITY_KINDS = ("phone", "email", "turo_guest_id")


def _limit(raw: object) -> int:
    try:
        value = int(str(raw).strip()) if str(raw or "").strip() else DEFAULT_LIMIT
    except ValueError as exc:
        raise CrmError(400, "invalid limit") from exc
    return max(1, min(MAX_LIMIT, value))


def _parse(stamp: str) -> Optional[datetime]:
    text = (stamp or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_et(stamp: str) -> tuple[str, str]:
    """(ISO in ET with offset, short ET text) or ("", "") when unparseable."""
    parsed = _parse(stamp)
    if parsed is None:
        return "", ""
    local = parsed.astimezone(ET)
    hour = local.strftime("%I").lstrip("0") or "12"
    text = f"{local.strftime('%a %b')} {local.day} {hour}:{local.strftime('%M %p')} ET"
    return local.isoformat(timespec="seconds"), text


def _ext_turo(ext_json: str) -> bool:
    import json

    try:
        ext = json.loads(ext_json or "{}")
    except ValueError:
        return False
    return isinstance(ext, dict) and bool(ext.get("turo_booking"))


def contact_by_phone(db: Any, actor: Optional[Actor], pn: str, limit: object = "") -> Optional[dict[str, Any]]:
    require(actor, SCOPE)
    count = _limit(limit)
    phone = phone_e164(pn)
    if not phone:
        raise CrmError(400, "invalid phone")
    conn = db.conn
    row = conn.execute(
        """
        SELECT p.* FROM identity i JOIN party p ON p.id = i.party_id
        WHERE i.kind = 'phone' AND i.value_norm = ? AND p.archived = 0
        """,
        (phone,),
    ).fetchone()
    if row is None:
        return None
    party_id = row["id"]
    tags = [r["tag"] for r in conn.execute("SELECT tag FROM party_tag WHERE party_id = ? ORDER BY tag", (party_id,))]
    full = "read:*" in actor.scopes or "read:party" in actor.scopes
    identities = []
    turo = _ext_turo(row["ext_json"]) or bool(TURO_TAGS.intersection(t.lower() for t in tags))
    for ident in conn.execute(
        "SELECT kind, value_norm FROM identity WHERE party_id = ? ORDER BY kind, value_norm",
        (party_id,),
    ):
        kind = ident["kind"]
        if kind == "turo_guest_id":
            turo = True
        if kind not in _IDENTITY_KINDS:
            continue
        item = {"kind": kind}
        if full:
            item["value"] = ident["value_norm"]
        identities.append(item)
    followups = []
    for task in conn.execute(
        """
        SELECT id, title, due_at, assignee_seat FROM task
        WHERE party_id = ? AND status = 'open'
        ORDER BY CASE WHEN due_at = '' THEN 1 ELSE 0 END, due_at, created_at
        LIMIT ?
        """,
        (party_id, MAX_LIMIT),
    ):
        due_iso, due_text = to_et(task["due_at"])
        followups.append(
            {
                "id": task["id"],
                "title": scrub(task["title"]),
                "due_at": due_iso,
                "due_text": due_text,
                "assignee_seat": task["assignee_seat"],
            }
        )
    interactions = []
    for item in conn.execute(
        """
        SELECT id, channel, direction, outcome, summary, occurred_at FROM interaction
        WHERE party_id = ? ORDER BY occurred_at DESC, id DESC LIMIT ?
        """,
        (party_id, count),
    ):
        at_iso, at_text = to_et(item["occurred_at"])
        interactions.append(
            {
                "id": item["id"],
                "channel": item["channel"],
                "direction": item["direction"],
                "outcome": item["outcome"],
                "summary": scrub(item["summary"]),
                "occurred_at": at_iso,
                "occurred_text": at_text,
            }
        )
    return {
        "party": {
            "id": party_id,
            "type": row["type"],
            "status": row["status"],
            "display_name": scrub(row["display_name"], 80),
            "tags": tags,
        },
        "type": row["type"],
        "tags": tags,
        "identities": identities,
        "turo_booking": turo,
        "open_followups": [f["title"] for f in followups],
        "followups": followups,
        "interactions": interactions,
        "tz": "America/New_York",
    }
