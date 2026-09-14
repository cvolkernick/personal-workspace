"""Invoice-ready rows in Turso. Source is Panamerica Turo mail, not Google Tasks.

Schema is the owner-locked minimum: id, subject, received_at, source_email_ref,
status. Notes is optional (email snippet) so car-match can see a trip #.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping, Optional, Sequence

try:
    from . import envfile, turso_http, turo_inbox
except ImportError:  # script / unittest path
    import envfile  # type: ignore
    import turso_http  # type: ignore
    import turo_inbox  # type: ignore

TABLE = "fleet_invoice_ready"
LIST_ID = "turso"
LIST_TITLE = "invoice-ready"
STATUS_OPEN = "open"
STATUS_COMPLETED = "completed"

ENSURE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id TEXT PRIMARY KEY,
  subject TEXT NOT NULL DEFAULT '',
  received_at TEXT,
  source_email_ref TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""

LIST_OPEN_SQL = f"""
SELECT id, subject, received_at, source_email_ref, status, notes, updated_at
FROM {TABLE}
WHERE status = ?
ORDER BY received_at DESC, id ASC
"""

UPSERT_SQL = f"""
INSERT INTO {TABLE}(
  id, subject, received_at, source_email_ref, status, notes, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
  subject = excluded.subject,
  received_at = excluded.received_at,
  source_email_ref = excluded.source_email_ref,
  notes = excluded.notes,
  updated_at = excluded.updated_at
"""

COMPLETE_SQL = f"""
UPDATE {TABLE}
SET status = ?, updated_at = ?
WHERE id = ?
"""

GET_SQL = f"""
SELECT id, subject, received_at, source_email_ref, status, notes, updated_at
FROM {TABLE}
WHERE id = ?
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_env(
    *,
    env: Mapping[str, str] | None = None,
    env_file: Optional[Any] = None,
) -> dict[str, str]:
    file_env = envfile.load_env_file(env_file)
    return envfile.merge_env(file_env, env if env is not None else os.environ)


def configured(
    *,
    env: Mapping[str, str] | None = None,
    env_file: Optional[Any] = None,
) -> bool:
    return turso_http.turso_enabled(load_env(env=env, env_file=env_file))


def connect(
    *,
    env: Mapping[str, str] | None = None,
    env_file: Optional[Any] = None,
):
    return turso_http.connect(load_env(env=env, env_file=env_file))


def ensure_table(conn) -> None:
    conn.execute(ENSURE_SQL)


def _parse_received(raw: Any) -> Optional[str]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        if text.endswith("Z") or "+" in text[10:] or text[:4].isdigit():
            return text
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def is_invoice_ready_message(msg: Mapping[str, Any]) -> bool:
    """True when the Panamerica Turo mail is an invoice-ready notice."""
    subject = str(msg.get("subject") or "")
    kind = turo_inbox.classify_subject(subject)
    if kind == "invoice_ready":
        return True
    if kind in ("payout", "booked", "canceled", "modified"):
        return False
    blob = " ".join(
        str(msg.get(k) or "") for k in ("subject", "snippet", "body")
    ).lower()
    return any(
        w in blob
        for w in (
            "time to invoice",
            "invoice your guest",
            "don't forget to invoice",
            "do not forget to invoice",
            "add extra charges",
            "extra charges",
            "charge your guest",
        )
    )


def message_row(msg: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    if not is_invoice_ready_message(msg):
        return None
    mid = str(msg.get("id") or msg.get("message_id") or "").strip()
    if not mid:
        return None
    subject = str(msg.get("subject") or "").strip()
    snippet = str(msg.get("snippet") or "").strip()
    return {
        "id": mid,
        "subject": subject,
        "received_at": _parse_received(msg.get("date") or msg.get("received_at")),
        "source_email_ref": mid,
        "status": STATUS_OPEN,
        "notes": snippet,
    }


class MemoryStore:
    """In-process stand-in for Turso. Tests only."""

    def __init__(self, rows: Sequence[Mapping[str, Any]] | None = None) -> None:
        self.rows: list[dict[str, Any]] = [dict(r) for r in (rows or [])]

    def list_open(self) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.rows
            if (r.get("status") or STATUS_OPEN) in (STATUS_OPEN, "needsAction")
        ]

    def upsert(self, row: Mapping[str, Any]) -> dict[str, Any]:
        rec = dict(row)
        rec.setdefault("status", STATUS_OPEN)
        rec.setdefault("updated_at", _now())
        rec.setdefault("created_at", rec["updated_at"])
        for i, existing in enumerate(self.rows):
            if existing.get("id") == rec.get("id"):
                rec["status"] = existing.get("status") or STATUS_OPEN
                rec["created_at"] = existing.get("created_at") or rec["created_at"]
                merged = dict(existing)
                merged.update(
                    {
                        "subject": rec.get("subject") or existing.get("subject") or "",
                        "received_at": rec.get("received_at") or existing.get("received_at"),
                        "source_email_ref": rec.get("source_email_ref")
                        or existing.get("source_email_ref"),
                        "notes": rec.get("notes")
                        if rec.get("notes") is not None
                        else existing.get("notes") or "",
                        "updated_at": rec["updated_at"],
                    }
                )
                self.rows[i] = merged
                return dict(merged)
        self.rows.append(rec)
        return dict(rec)

    def complete(self, item_id: str) -> dict[str, Any]:
        tid = (item_id or "").strip()
        now = _now()
        for row in self.rows:
            if row.get("id") == tid:
                row["status"] = STATUS_COMPLETED
                row["updated_at"] = now
                return {"ok": True, "item": dict(row)}
        return {"ok": False, "error": "not found"}


def _row_dict(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": raw.get("id"),
        "subject": raw.get("subject") or "",
        "received_at": raw.get("received_at"),
        "source_email_ref": raw.get("source_email_ref") or raw.get("id"),
        "status": raw.get("status") or STATUS_OPEN,
        "notes": raw.get("notes") or "",
        "updated_at": raw.get("updated_at"),
    }


def list_open(
    *,
    store: Any | None = None,
    env: Mapping[str, str] | None = None,
    env_file: Optional[Any] = None,
) -> dict[str, Any]:
    """Open invoice-ready rows. Unconfigured → empty, not a Google Tasks error."""
    if isinstance(store, MemoryStore):
        items = [_row_dict(r) for r in store.list_open()]
        return {
            "ok": True,
            "source": "turso",
            "configured": True,
            "list_id": LIST_ID,
            "list_title": LIST_TITLE,
            "items": items,
            "count": len(items),
        }
    if not configured(env=env, env_file=env_file):
        return {
            "ok": True,
            "source": "turso",
            "configured": False,
            "list_id": LIST_ID,
            "list_title": LIST_TITLE,
            "items": [],
            "count": 0,
        }
    try:
        conn = connect(env=env, env_file=env_file)
        ensure_table(conn)
        cur = conn.execute(LIST_OPEN_SQL, (STATUS_OPEN,))
        items = [_row_dict(r) for r in cur.fetchall()]
        return {
            "ok": True,
            "source": "turso",
            "configured": True,
            "list_id": LIST_ID,
            "list_title": LIST_TITLE,
            "items": items,
            "count": len(items),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "source": "turso",
            "configured": True,
            "list_id": LIST_ID,
            "list_title": LIST_TITLE,
            "error": str(exc) or "Turso error",
            "items": [],
            "count": 0,
        }


def upsert_from_messages(
    messages: Sequence[Mapping[str, Any]],
    *,
    store: Any | None = None,
    env: Mapping[str, str] | None = None,
    env_file: Optional[Any] = None,
) -> dict[str, Any]:
    """Insert/update invoice-ready mail. Does not reopen completed rows."""
    rows = [r for r in (message_row(m) for m in messages) if r]
    if isinstance(store, MemoryStore):
        written = [store.upsert(r) for r in rows]
        return {"ok": True, "source": "turso", "upserted": len(written), "items": written}
    if not configured(env=env, env_file=env_file):
        return {
            "ok": True,
            "source": "turso",
            "configured": False,
            "upserted": 0,
            "items": [],
        }
    try:
        conn = connect(env=env, env_file=env_file)
        ensure_table(conn)
        now = _now()
        written = 0
        for row in rows:
            conn.execute(
                UPSERT_SQL,
                (
                    row["id"],
                    row["subject"],
                    row.get("received_at"),
                    row.get("source_email_ref") or row["id"],
                    STATUS_OPEN,
                    row.get("notes") or "",
                    now,
                    now,
                ),
            )
            written += 1
        conn.commit()
        return {
            "ok": True,
            "source": "turso",
            "configured": True,
            "upserted": written,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "source": "turso",
            "error": str(exc) or "Turso error",
            "upserted": 0,
        }


def complete(
    item_id: str,
    *,
    store: Any | None = None,
    env: Mapping[str, str] | None = None,
    env_file: Optional[Any] = None,
) -> dict[str, Any]:
    tid = (item_id or "").strip()
    if not tid:
        return {"ok": False, "error": "task_id required"}
    if isinstance(store, MemoryStore):
        result = store.complete(tid)
        if not result.get("ok"):
            return result
        return {
            "ok": True,
            "source": "turso",
            "list_id": LIST_ID,
            "task_id": tid,
            "item": result.get("item"),
        }
    if not configured(env=env, env_file=env_file):
        return {"ok": False, "error": "Turso not configured"}
    try:
        conn = connect(env=env, env_file=env_file)
        ensure_table(conn)
        conn.execute(COMPLETE_SQL, (STATUS_COMPLETED, _now(), tid))
        conn.commit()
        cur = conn.execute(GET_SQL, (tid,))
        row = cur.fetchone()
        if not row:
            return {"ok": False, "error": "not found"}
        rec = _row_dict(row)
        if rec.get("status") != STATUS_COMPLETED:
            return {"ok": False, "error": "not found"}
        return {
            "ok": True,
            "source": "turso",
            "list_id": LIST_ID,
            "task_id": tid,
            "item": rec,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc) or "Turso error"}
