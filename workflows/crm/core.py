"""CRM write path. The API and the roadside adapter both go through here."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from workflows.crm.auth import SEAT_SCOPES, Actor, CrmError, require
from workflows.crm.ids import ulid
from workflows.crm.normalize import (
    email_norm,
    folder_id_norm,
    is_stop_text,
    listing_id_norm,
    nanp_digits,
    phone_e164,
    phone_key,
    vin_norm,
)
from workflows.crm.schema import connect, utc_now

PARTY_TYPES = ("lead", "vendor", "supplier", "customer", "partner", "other")
_EVENT_INTERACTION = {
    "sms_sent": ("sms", "out", "sent"),
    "call_attempted": ("call", "out", "connected"),
    "responded": ("sms", "in", "replied"),
    "interested": ("sms", "in", "replied"),
    "declined": ("sms", "in", "replied"),
}
_SNAPSHOT_NOTE_AUTHOR = "roadside-snapshot"


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)


def _loads(value: object, fallback: Any) -> Any:
    if not isinstance(value, str) or not value:
        return fallback
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return fallback
    return parsed


def _holds(actor: Actor | None, scope: str) -> bool:
    if actor is None:
        return False
    try:
        require(actor, scope)
    except CrmError:
        return False
    return True


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _event_msg_id(party_id: str, index: int, event: dict[str, Any]) -> str:
    raw = _dumps({"lead": party_id, "i": index, "op": event.get("op"), "at": event.get("at")})
    return "roadside-event:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class CrmDB:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.conn = connect(self.path)
        self._lock = threading.RLock()
        self._tx_depth = 0
        self._export_pause = 0

    def close(self) -> None:
        self.conn.close()

    def actor_for_seat(self, seat: str) -> Actor:
        scopes = SEAT_SCOPES.get(seat)
        if scopes is None:
            raise CrmError(400, "unknown seat")
        return Actor(seat=seat, cred_id="in-process", scopes=frozenset(scopes))

    def issue_token(self, seat: str, scopes: Optional[list[str]] = None) -> dict[str, Any]:
        chosen = tuple(scopes) if scopes is not None else SEAT_SCOPES.get(seat)
        if not chosen:
            raise CrmError(400, "unknown seat")
        token = "crm_" + secrets.token_urlsafe(32)
        cred_id = ulid()
        with self._tx():
            self.conn.execute(
                """
                INSERT INTO credential(id, seat, token_hash, scopes, created_at, revoked)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (cred_id, seat, token_hash(token), json.dumps(list(chosen)), utc_now()),
            )
        return {"token": token, "cred_id": cred_id, "seat": seat, "scopes": list(chosen)}

    def authenticate(self, token: str) -> Optional[Actor]:
        if not token:
            return None
        row = self.conn.execute(
            """
            SELECT id, seat, scopes, revoked FROM credential WHERE token_hash = ?
            """,
            (token_hash(token),),
        ).fetchone()
        if row is None or int(row["revoked"]):
            return None
        scopes = _loads(row["scopes"], [])
        if not isinstance(scopes, list):
            return None
        return Actor(seat=row["seat"], cred_id=row["id"], scopes=frozenset(str(s) for s in scopes))

    def set_export_path(self, path: Path | None) -> None:
        with self._tx(export=False):
            if path is None:
                self.conn.execute("DELETE FROM schema_meta WHERE key = 'export_path'")
            else:
                self._meta_set("export_path", str(path))

    def set_export_until(self, stamp: str) -> None:
        with self._tx(export=False):
            self._meta_set("export_until", stamp)

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Hold the JSON export until the batch finishes."""
        self._export_pause += 1
        try:
            yield
        finally:
            self._export_pause -= 1
            if self._export_pause == 0:
                self.maybe_export()

    def create_party(
        self, actor: Actor, payload: dict[str, Any], request_id: str = ""
    ) -> tuple[dict[str, Any], bool]:
        require(actor, "write:party")
        party_type = str(payload.get("type") or "lead").strip()
        if party_type not in PARTY_TYPES:
            raise CrmError(400, "bad type")
        specs = self._identity_specs(payload)
        with self._tx():
            for spec in specs:
                owner = self._party_for_identity(spec["kind"], spec["value_norm"])
                if owner:
                    return self._party_doc(owner), False
            explicit = str(payload.get("id") or "").strip()
            if explicit and self._party_row(explicit) is not None:
                return self._party_doc(explicit), False
            party_id = explicit or ulid()
            now = utc_now()
            ext = payload.get("ext") if isinstance(payload.get("ext"), dict) else {}
            self.conn.execute(
                """
                INSERT INTO party(
                  id, type, display_name, org_name, status, source, owner_seat, location,
                  ext_json, archived, created_at, created_by, updated_at, updated_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    party_id,
                    party_type,
                    str(payload.get("display_name") or ""),
                    str(payload.get("org_name") or ""),
                    str(payload.get("status") or "new"),
                    str(payload.get("source") or "manual"),
                    str(payload.get("owner_seat") or actor.seat),
                    str(payload.get("location") or ""),
                    _dumps(ext),
                    now,
                    actor.seat,
                    now,
                    actor.seat,
                ),
            )
            self._replace_tags(party_id, payload.get("tags") or [])
            for spec in specs:
                self._insert_identity(party_id, spec)
            self._insert_vehicles(party_id, payload.get("vehicle"), payload.get("vehicles"))
            for body in payload.get("notes") or []:
                if str(body).strip():
                    self._insert_note(party_id, str(body), _SNAPSHOT_NOTE_AUTHOR, now)
            self._reindex(party_id)
            doc = self._party_doc(party_id)
            self._audit(actor, "party", party_id, "create", None, doc, request_id)
            return doc, True

    def get_party(self, actor: Actor, party_id: str) -> dict[str, Any]:
        require(actor, "read:party")
        if self._party_row(party_id) is None:
            raise CrmError(404, "not found")
        return self._party_doc(party_id)

    def update_party(
        self, actor: Actor, party_id: str, patch: dict[str, Any], request_id: str = ""
    ) -> dict[str, Any]:
        with self._tx():
            row = self._party_row(party_id)
            if row is None:
                raise CrmError(404, "not found")
            self._require_update(actor, row["type"], patch)
            before = self._party_doc(party_id)
            fields: dict[str, Any] = {}
            for key in ("display_name", "org_name", "status", "location", "owner_seat", "source"):
                if key in patch:
                    fields[key] = str(patch[key] or "")
            if "archived" in patch:
                fields["archived"] = 1 if patch["archived"] else 0
            ext = _loads(row["ext_json"], {})
            if not isinstance(ext, dict):
                ext = {}
            if isinstance(patch.get("ext"), dict):
                ext.update(patch["ext"])
            snap = ext.get("roadside_lead")
            if isinstance(snap, dict):
                if "status" in patch:
                    snap["state"] = str(patch["status"] or "")
                if "display_name" in patch:
                    snap["contact_name"] = str(patch["display_name"] or "")
                if "location" in patch:
                    snap["location"] = str(patch["location"] or "")
                ext["roadside_lead"] = snap
            now = utc_now()
            assignments = ", ".join(f"{key} = ?" for key in fields)
            params: list[Any] = list(fields.values())
            sql = "UPDATE party SET ext_json = ?, updated_at = ?, updated_by = ?"
            params_head: list[Any] = [_dumps(ext), now, actor.seat]
            if assignments:
                sql += ", " + assignments
            sql += " WHERE id = ?"
            self.conn.execute(sql, [*params_head, *params, party_id])
            if "tags" in patch:
                self._replace_tags(party_id, patch.get("tags") or [])
            if "vehicle" in patch or "vehicles" in patch:
                self.conn.execute("DELETE FROM vehicle WHERE party_id = ?", (party_id,))
                self._insert_vehicles(party_id, patch.get("vehicle"), patch.get("vehicles"))
            self._reindex(party_id)
            after = self._party_doc(party_id)
            self._audit(actor, "party", party_id, "update", before, after, request_id)
            return after

    def delete_party(self, actor: Actor, party_id: str, request_id: str = "") -> None:
        require(actor, "delete")
        with self._tx():
            if self._party_row(party_id) is None:
                raise CrmError(404, "not found")
            before = self._party_doc(party_id)
            self.conn.execute("DELETE FROM party_fts WHERE party_id = ?", (party_id,))
            self.conn.execute("DELETE FROM party WHERE id = ?", (party_id,))
            self._audit(actor, "party", party_id, "delete", before, None, request_id)

    def merge_parties(
        self, actor: Actor, survivor_id: str, loser_id: str, request_id: str = ""
    ) -> dict[str, Any]:
        require(actor, "merge")
        if survivor_id == loser_id:
            raise CrmError(400, "merge needs two parties")
        with self._tx():
            if self._party_row(survivor_id) is None or self._party_row(loser_id) is None:
                raise CrmError(404, "not found")
            before_s = self._party_doc(survivor_id)
            before_l = self._party_doc(loser_id)
            for table in ("note", "task", "interaction", "vehicle"):
                self.conn.execute(
                    f"UPDATE {table} SET party_id = ? WHERE party_id = ?",
                    (survivor_id, loser_id),
                )
            for row in self.conn.execute(
                "SELECT kind, value_norm, value_raw FROM identity WHERE party_id = ?",
                (loser_id,),
            ):
                owner = self._party_for_identity(row["kind"], row["value_norm"])
                if owner and owner != survivor_id:
                    continue
                if owner == survivor_id:
                    continue
                self._insert_identity(
                    survivor_id,
                    {
                        "kind": row["kind"],
                        "value_norm": row["value_norm"],
                        "value_raw": row["value_raw"],
                    },
                )
            for row in self.conn.execute(
                "SELECT tag FROM party_tag WHERE party_id = ?", (loser_id,)
            ):
                self.conn.execute(
                    "INSERT OR IGNORE INTO party_tag(party_id, tag) VALUES (?, ?)",
                    (survivor_id, row["tag"]),
                )
            self.conn.execute(
                "UPDATE party SET archived = 1, status = 'merged', updated_at = ?, updated_by = ? WHERE id = ?",
                (utc_now(), actor.seat, loser_id),
            )
            self._reindex(survivor_id)
            self._reindex(loser_id)
            after_s = self._party_doc(survivor_id)
            after_l = self._party_doc(loser_id)
            self._audit(actor, "party", survivor_id, "merge", before_s, after_s, request_id)
            self._audit(actor, "party", loser_id, "merge", before_l, after_l, request_id)
            return after_s

    def add_interaction(
        self, actor: Actor, payload: dict[str, Any], request_id: str = ""
    ) -> tuple[dict[str, Any], bool]:
        require(actor, "write:interaction")
        party_id = str(payload.get("party_id") or "").strip()
        if not party_id or self._party_row(party_id) is None:
            raise CrmError(404, "not found")
        msg_id = str(payload.get("provider_msg_id") or "").strip() or None
        with self._tx():
            if msg_id:
                existing = self._interaction_by_msg(msg_id)
                if existing is not None:
                    changed = self._touch_interaction(existing, payload)
                    row = self._interaction_doc(existing["id"])
                    if changed:
                        self._audit(
                            actor, "interaction", row["id"], "update", existing, row, request_id
                        )
                    self._apply_interaction_effects(actor, party_id, row, request_id)
                    return row, False
            now = utc_now()
            interaction_id = str(payload.get("id") or "").strip() or ulid()
            self.conn.execute(
                """
                INSERT INTO interaction(
                  id, party_id, channel, direction, provider, provider_msg_id,
                  from_addr, to_addr, body, summary, transcript_ref, outcome,
                  error_code, copy_version, variant_id, occurred_at, logged_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    interaction_id,
                    party_id,
                    str(payload.get("channel") or "sms"),
                    str(payload.get("direction") or "out"),
                    str(payload.get("provider") or ""),
                    msg_id,
                    str(payload.get("from") or payload.get("from_addr") or ""),
                    str(payload.get("to") or payload.get("to_addr") or ""),
                    str(payload.get("body") or ""),
                    str(payload.get("summary") or ""),
                    str(payload.get("transcript_ref") or ""),
                    str(payload.get("outcome") or ""),
                    str(payload.get("error_code") or ""),
                    str(payload.get("copy_version") or ""),
                    str(payload.get("variant_id") or ""),
                    str(payload.get("occurred_at") or now),
                    str(payload.get("logged_by") or actor.seat),
                ),
            )
            row = self._interaction_doc(interaction_id)
            self._audit(actor, "interaction", interaction_id, "create", None, row, request_id)
            self._apply_interaction_effects(actor, party_id, row, request_id)
            self._reindex(party_id)
            return row, True

    def timeline(self, actor: Actor, party_id: str) -> list[dict[str, Any]]:
        require(actor, "read:party")
        if self._party_row(party_id) is None:
            raise CrmError(404, "not found")
        rows = self.conn.execute(
            """
            SELECT * FROM interaction WHERE party_id = ?
            ORDER BY occurred_at, id
            """,
            (party_id,),
        ).fetchall()
        return [self._interaction_from_row(row) for row in rows]

    def add_note(
        self, actor: Actor, payload: dict[str, Any], request_id: str = ""
    ) -> dict[str, Any]:
        require(actor, "write:note")
        party_id = str(payload.get("party_id") or "").strip()
        if self._party_row(party_id) is None:
            raise CrmError(404, "not found")
        body = str(payload.get("body") or "").strip()
        if not body:
            raise CrmError(400, "note body required")
        with self._tx():
            note_id = self._insert_note(party_id, body, actor.seat, utc_now())
            self._reindex(party_id)
            doc = self._note_doc(note_id)
            self._audit(actor, "note", note_id, "create", None, doc, request_id)
            return doc

    def update_note(
        self, actor: Actor, note_id: str, patch: dict[str, Any], request_id: str = ""
    ) -> dict[str, Any]:
        require(actor, "write:note")
        with self._tx():
            row = self.conn.execute("SELECT * FROM note WHERE id = ?", (note_id,)).fetchone()
            if row is None:
                raise CrmError(404, "not found")
            before = self._note_doc(note_id)
            body = str(patch["body"]) if "body" in patch else row["body"]
            archived = int(row["archived"])
            if "archived" in patch:
                archived = 1 if patch["archived"] else 0
            self.conn.execute(
                "UPDATE note SET body = ?, archived = ? WHERE id = ?",
                (body, archived, note_id),
            )
            self._reindex(row["party_id"])
            after = self._note_doc(note_id)
            self._audit(actor, "note", note_id, "update", before, after, request_id)
            return after

    def get_note(self, actor: Actor, note_id: str) -> dict[str, Any]:
        require(actor, "read:party")
        return self._note_doc(note_id)

    def list_notes(
        self, actor: Actor, party_id: str, *, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        require(actor, "read:party")
        sql = "SELECT id FROM note WHERE party_id = ?"
        if not include_archived:
            sql += " AND archived = 0"
        sql += " ORDER BY created_at, id"
        return [self._note_doc(row["id"]) for row in self.conn.execute(sql, (party_id,))]

    def add_task(
        self, actor: Actor, payload: dict[str, Any], request_id: str = ""
    ) -> dict[str, Any]:
        require(actor, "write:task")
        party_id = str(payload.get("party_id") or "").strip() or None
        if party_id and self._party_row(party_id) is None:
            raise CrmError(404, "not found")
        title = str(payload.get("title") or "").strip()
        if not title:
            raise CrmError(400, "task title required")
        with self._tx():
            task_id = ulid()
            now = utc_now()
            self.conn.execute(
                """
                INSERT INTO task(
                  id, party_id, title, due_at, status, assignee_seat, created_by,
                  completed_by, completed_at, created_at
                ) VALUES (?, ?, ?, ?, 'open', ?, ?, '', '', ?)
                """,
                (
                    task_id,
                    party_id,
                    title,
                    str(payload.get("due_at") or ""),
                    str(payload.get("assignee_seat") or actor.seat),
                    actor.seat,
                    now,
                ),
            )
            doc = self._task_doc(task_id)
            self._audit(actor, "task", task_id, "create", None, doc, request_id)
            return doc

    def update_task(
        self, actor: Actor, task_id: str, patch: dict[str, Any], request_id: str = ""
    ) -> dict[str, Any]:
        require(actor, "write:task")
        with self._tx():
            row = self.conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise CrmError(404, "not found")
            before = self._task_doc(task_id)
            status = str(patch.get("status") or row["status"])
            completed_by = row["completed_by"]
            completed_at = row["completed_at"]
            if status == "done" and row["status"] != "done":
                completed_by = actor.seat
                completed_at = utc_now()
            if status != "done":
                completed_by = ""
                completed_at = ""
            due_at = str(patch["due_at"]) if "due_at" in patch else row["due_at"]
            title = str(patch["title"]) if "title" in patch else row["title"]
            assignee = (
                str(patch["assignee_seat"]) if "assignee_seat" in patch else row["assignee_seat"]
            )
            self.conn.execute(
                """
                UPDATE task
                SET title = ?, due_at = ?, status = ?, assignee_seat = ?,
                    completed_by = ?, completed_at = ?
                WHERE id = ?
                """,
                (title, due_at, status, assignee, completed_by, completed_at, task_id),
            )
            after = self._task_doc(task_id)
            self._audit(actor, "task", task_id, "update", before, after, request_id)
            return after

    def list_tasks(
        self,
        actor: Actor,
        *,
        assignee: str = "",
        due_before: str = "",
    ) -> list[dict[str, Any]]:
        require(actor, "read:party")
        sql = "SELECT id, due_at, assignee_seat FROM task WHERE 1 = 1"
        params: list[Any] = []
        if assignee:
            sql += " AND assignee_seat = ?"
            params.append(assignee)
        rows = []
        for row in self.conn.execute(sql, params):
            if due_before and str(row["due_at"] or "")[:10] >= due_before[:10]:
                continue
            rows.append(self._task_doc(row["id"]))
        return rows

    def search(
        self,
        actor: Actor,
        *,
        q: str = "",
        party_type: str = "",
        tag: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        require(actor, "read:party")
        tokens = [tok for tok in _fts_tokens(q)]
        params: list[Any] = []
        if tokens:
            match = " AND ".join(f'"{tok}"' for tok in tokens)
            sql = """
                SELECT p.id AS id
                FROM party_fts
                JOIN party p ON p.id = party_fts.party_id
                WHERE party_fts MATCH ?
                  AND p.archived = 0
            """
            params.append(match)
        else:
            sql = "SELECT p.id AS id FROM party p WHERE p.archived = 0"
        if party_type:
            sql += " AND p.type = ?"
            params.append(party_type)
        if status:
            sql += " AND p.status = ?"
            params.append(status)
        if tag:
            sql += " AND EXISTS (SELECT 1 FROM party_tag t WHERE t.party_id = p.id AND t.tag = ?)"
            params.append(tag)
        sql += " ORDER BY p.updated_at DESC LIMIT ?"
        params.append(int(limit))
        return [self._party_doc(row["id"]) for row in self.conn.execute(sql, params)]

    def by_identity(self, actor: Actor, *, kind: str, value: str) -> Optional[dict[str, Any]]:
        require(actor, "read:party")
        spec = self._one_identity(kind, value)
        if spec is None:
            return None
        party_id = self._party_for_identity(spec["kind"], spec["value_norm"])
        if not party_id:
            return None
        return self._party_doc(party_id)

    def list_audit(self, actor: Actor, entity_id: str) -> list[dict[str, Any]]:
        require(actor, "read:party")
        rows = self.conn.execute(
            """
            SELECT id, at, actor_seat, actor_cred_id, entity, entity_id, op,
                   before_json, after_json, request_id
            FROM audit WHERE entity_id = ? ORDER BY id
            """,
            (entity_id,),
        ).fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "id": row["id"],
                    "at": row["at"],
                    "actor_seat": row["actor_seat"],
                    "actor_cred_id": row["actor_cred_id"],
                    "entity": row["entity"],
                    "entity_id": row["entity_id"],
                    "op": row["op"],
                    "before": _loads(row["before_json"], None),
                    "after": _loads(row["after_json"], None),
                    "request_id": row["request_id"],
                }
            )
        return out

    def suppress(self, actor: Actor, keys: list[str], reason: str, request_id: str = "") -> None:
        require(actor, "write:suppression")
        with self._tx():
            at = utc_now()
            for key in keys:
                norm = self._suppression_key(key)
                if not norm:
                    continue
                current = self.conn.execute(
                    "SELECT key_norm FROM suppression WHERE key_norm = ?", (norm,)
                ).fetchone()
                if current is not None:
                    continue
                self.conn.execute(
                    "INSERT INTO suppression(key_norm, reason, at, by_seat) VALUES (?, ?, ?, ?)",
                    (norm, reason, at, actor.seat),
                )
                self._audit(
                    actor,
                    "suppression",
                    norm,
                    "create",
                    None,
                    {"key": norm, "reason": reason, "at": at},
                    request_id,
                )

    def is_suppressed(self, *keys: str) -> bool:
        banned = {row["key_norm"] for row in self.conn.execute("SELECT key_norm FROM suppression")}
        for key in keys:
            if not key:
                continue
            candidates = {str(key)}
            norm = self._suppression_key(key)
            if norm:
                digits = norm.split(":", 1)[-1]
                candidates.add(norm)
                candidates.add(digits)
                candidates.add("phone:" + digits)
                candidates.add("+1" + digits)
            if candidates & banned:
                return True
        return False

    def count_parties(self, party_type: str = "") -> int:
        if party_type:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM party WHERE type = ?", (party_type,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM party").fetchone()
        return int(row["n"])

    def count_interactions(self, party_id: str = "") -> int:
        if party_id:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM interaction WHERE party_id = ?", (party_id,)
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM interaction").fetchone()
        return int(row["n"])

    def outreach_eligible_ids(self) -> list[str]:
        return [
            row["id"]
            for row in self.conn.execute("SELECT id FROM outreach_eligible ORDER BY id")
        ]

    def phone_has_tag(self, party_id: str, tag: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM identity i
            JOIN identity_tag t ON t.identity_id = i.id
            WHERE i.party_id = ? AND i.kind = 'phone' AND t.tag = ?
            """,
            (party_id, tag),
        ).fetchone()
        return row is not None

    def list_parties(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT id FROM party"
        if not include_archived:
            sql += " WHERE archived = 0"
        sql += " ORDER BY type, display_name, id"
        return [self._party_doc(row["id"]) for row in self.conn.execute(sql)]

    def save_roadside_lead(self, actor: Actor, lead: dict[str, Any]) -> None:
        """Upsert one roadside lead. A second lead may share a phone; the first keeps the identity."""
        require(actor, "write:party")
        party_id = str(lead.get("id") or "").strip()
        if not party_id:
            raise CrmError(400, "lead id required")
        with self._tx():
            existing = self._party_row(party_id)
            before = self._party_doc(party_id) if existing else None
            ext: dict[str, Any] = {}
            if existing is not None:
                loaded = _loads(existing["ext_json"], {})
                if isinstance(loaded, dict):
                    ext = loaded
            ext["roadside_lead"] = lead
            for key in (
                "sms_variant_id",
                "sms_variant_assigned_at",
                "reply_quality",
                "callback",
                "copy_version",
            ):
                if key in lead:
                    ext[key] = lead.get(key)
            now = utc_now()
            if existing is None:
                self.conn.execute(
                    """
                    INSERT INTO party(
                      id, type, display_name, org_name, status, source, owner_seat, location,
                      ext_json, archived, created_at, created_by, updated_at, updated_by
                    ) VALUES (?, 'lead', ?, '', ?, 'roadside', ?, ?, ?, 0, ?, ?, ?, ?)
                    """,
                    (
                        party_id,
                        str(lead.get("contact_name") or ""),
                        str(lead.get("state") or "new"),
                        actor.seat,
                        str(lead.get("location") or ""),
                        _dumps(ext),
                        str(lead.get("created_at") or now),
                        actor.seat,
                        now,
                        actor.seat,
                    ),
                )
            else:
                self.conn.execute(
                    """
                    UPDATE party
                    SET display_name = ?, status = ?, location = ?, ext_json = ?,
                        source = 'roadside', type = 'lead', updated_at = ?, updated_by = ?
                    WHERE id = ?
                    """,
                    (
                        str(lead.get("contact_name") or ""),
                        str(lead.get("state") or "new"),
                        str(lead.get("location") or ""),
                        _dumps(ext),
                        now,
                        actor.seat,
                        party_id,
                    ),
                )
            phone = phone_e164(lead.get("phone") or "")
            if phone:
                self._claim_identity(party_id, "phone", phone, str(lead.get("phone") or ""))
            folder = folder_id_norm(lead.get("folder_id") or "")
            if folder:
                self._claim_identity(party_id, "drive_folder_id", folder, folder)
            self.conn.execute("DELETE FROM vehicle WHERE party_id = ?", (party_id,))
            self._insert_vehicle_row(
                party_id,
                {
                    "year": lead.get("year") or "",
                    "make": lead.get("make") or "",
                    "model": lead.get("model") or "",
                    "asking_price": lead.get("asking_price") or "",
                    "location": lead.get("location") or "",
                    "gps": lead.get("gps") or "",
                    "photos": lead.get("photos") or [],
                    "spotted_at": lead.get("spotted_at") or "",
                    "date_source": lead.get("date_source") or "",
                    "location_source": lead.get("location_source") or "",
                },
            )
            self.conn.execute(
                "DELETE FROM note WHERE party_id = ? AND author_seat = ?",
                (party_id, _SNAPSHOT_NOTE_AUTHOR),
            )
            for body in lead.get("notes") or []:
                text = str(body).strip()
                if text:
                    self._insert_note(party_id, text, _SNAPSHOT_NOTE_AUTHOR, now)
            self._sync_lead_events(actor, party_id, lead.get("events") or [])
            self._reindex(party_id)
            after = self._party_doc(party_id)
            self._audit(
                actor,
                "party",
                party_id,
                "create" if before is None else "update",
                before,
                after,
                "",
            )

    def mark_folder(self, folder_id: str, lead_id: str, reason: str) -> None:
        with self._tx():
            self.conn.execute(
                """
                INSERT INTO intake_folder(folder_id, lead_id, reason, at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(folder_id) DO UPDATE SET
                  lead_id = excluded.lead_id,
                  reason = excluded.reason,
                  at = excluded.at
                """,
                (folder_id, lead_id, reason, utc_now()),
            )

    def folder_processed(self, folder_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM intake_folder WHERE folder_id = ?", (folder_id,)
        ).fetchone()
        return row is not None

    def record_outbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        stored = dict(payload)
        stored.setdefault("at", utc_now())
        with self._tx():
            self.conn.execute(
                "INSERT INTO outbox(payload_json, at) VALUES (?, ?)",
                (_dumps(stored), stored["at"]),
            )
        return stored

    def record_alert(self, payload: dict[str, Any]) -> dict[str, Any]:
        stored = dict(payload)
        stored.setdefault("at", utc_now())
        with self._tx():
            self.conn.execute(
                "INSERT INTO alert(payload_json, at) VALUES (?, ?)",
                (_dumps(stored), stored["at"]),
            )
        return stored

    def outbox(self) -> list[dict[str, Any]]:
        out = []
        for row in self.conn.execute("SELECT payload_json FROM outbox ORDER BY id"):
            payload = _loads(row["payload_json"], None)
            if isinstance(payload, dict):
                out.append(payload)
        return out

    def alerts(self) -> list[dict[str, Any]]:
        out = []
        for row in self.conn.execute("SELECT payload_json FROM alert ORDER BY id"):
            payload = _loads(row["payload_json"], None)
            if isinstance(payload, dict):
                out.append(payload)
        return out

    def sms_sent_on(self, day: str) -> int:
        row = self.conn.execute("SELECT n FROM sms_day WHERE day = ?", (day,)).fetchone()
        return int(row["n"]) if row else 0

    def increment_sms(self, day: str) -> None:
        with self._tx():
            self.conn.execute(
                """
                INSERT INTO sms_day(day, n) VALUES (?, 1)
                ON CONFLICT(day) DO UPDATE SET n = n + 1
                """,
                (day,),
            )

    def roadside_leads(self) -> list[dict[str, Any]]:
        leads = []
        for row in self.conn.execute(
            """
            SELECT ext_json FROM party
            WHERE type = 'lead' AND source = 'roadside' AND archived = 0
            ORDER BY created_at, id
            """
        ):
            ext = _loads(row["ext_json"], {})
            snap = ext.get("roadside_lead") if isinstance(ext, dict) else None
            if isinstance(snap, dict):
                leads.append(snap)
        return leads

    def maybe_export(self) -> None:
        if self._export_pause or self._tx_depth:
            return
        path = self._meta("export_path")
        until = self._meta("export_until")
        if not path or not until or utc_now() > until:
            return
        from workflows.crm.export import write_export

        write_export(self.conn, Path(path))

    @contextmanager
    def _tx(self, export: bool = True) -> Iterator[None]:
        with self._lock:
            outer = self._tx_depth == 0
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            self._tx_depth += 1
            try:
                yield
            except Exception:
                self._tx_depth -= 1
                if outer:
                    self.conn.execute("ROLLBACK")
                raise
            else:
                self._tx_depth -= 1
                if outer:
                    self.conn.execute("COMMIT")
                    if export:
                        self.maybe_export()

    def _meta(self, key: str) -> str:
        row = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?", (key,)
        ).fetchone()
        return str(row["value"]) if row else ""

    def _meta_set(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO schema_meta(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def _party_row(self, party_id: str) -> Any:
        return self.conn.execute("SELECT * FROM party WHERE id = ?", (party_id,)).fetchone()

    def _party_for_identity(self, kind: str, value_norm: str) -> str:
        if not kind or not value_norm:
            return ""
        row = self.conn.execute(
            "SELECT party_id FROM identity WHERE kind = ? AND value_norm = ?",
            (kind, value_norm),
        ).fetchone()
        return str(row["party_id"]) if row else ""

    def _require_update(self, actor: Actor, party_type: str, patch: dict[str, Any]) -> None:
        keys = set(patch)
        status_only = keys <= {"status"} and "status" in keys
        if status_only and party_type == "lead" and (
            _holds(actor, "write:lead-status") or _holds(actor, "write:status")
        ):
            return
        if status_only and _holds(actor, "write:status"):
            return
        require(actor, "write:party")

    def _identity_specs(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        specs: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def add(kind: str, value: object) -> None:
            spec = self._one_identity(kind, value)
            if spec is None:
                return
            key = (spec["kind"], spec["value_norm"])
            if key in seen:
                return
            seen.add(key)
            specs.append(spec)

        for kind, raw in (
            ("phone", payload.get("phone")),
            ("email", payload.get("email")),
            ("vin", payload.get("vin")),
            ("listing_id", payload.get("listing_id")),
            ("drive_folder_id", payload.get("drive_folder_id")),
        ):
            add(kind, raw)
        for item in payload.get("identities") or []:
            if isinstance(item, dict):
                add(str(item.get("kind") or ""), item.get("value") or item.get("value_norm"))
        return specs

    def _one_identity(self, kind: str, value: object) -> Optional[dict[str, str]]:
        kind = kind.strip()
        raw = str(value or "").strip()
        if kind == "phone":
            norm = phone_e164(raw)
        elif kind == "email":
            norm = email_norm(raw)
        elif kind == "vin":
            norm = vin_norm(raw)
        elif kind == "listing_id":
            norm = listing_id_norm(raw)
        elif kind == "drive_folder_id":
            norm = folder_id_norm(raw)
        elif kind in {"turo_guest_id"}:
            norm = raw
        else:
            return None
        if not norm:
            return None
        return {"kind": kind, "value_norm": norm, "value_raw": raw}

    def _insert_identity(self, party_id: str, spec: dict[str, str]) -> str:
        identity_id = ulid()
        self.conn.execute(
            """
            INSERT INTO identity(id, party_id, kind, value_norm, value_raw)
            VALUES (?, ?, ?, ?, ?)
            """,
            (identity_id, party_id, spec["kind"], spec["value_norm"], spec["value_raw"]),
        )
        return identity_id

    def _claim_identity(self, party_id: str, kind: str, value_norm: str, value_raw: str) -> None:
        if not value_norm:
            return
        row = self.conn.execute(
            "SELECT id, party_id FROM identity WHERE kind = ? AND value_norm = ?",
            (kind, value_norm),
        ).fetchone()
        if row is None:
            self._insert_identity(
                party_id, {"kind": kind, "value_norm": value_norm, "value_raw": value_raw}
            )
            return
        if row["party_id"] == party_id:
            self.conn.execute(
                "UPDATE identity SET value_raw = ? WHERE id = ?",
                (value_raw, row["id"]),
            )

    def _replace_tags(self, party_id: str, tags: object) -> None:
        self.conn.execute("DELETE FROM party_tag WHERE party_id = ?", (party_id,))
        seen: set[str] = set()
        for tag in tags or []:
            text = str(tag).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            self.conn.execute(
                "INSERT INTO party_tag(party_id, tag) VALUES (?, ?)",
                (party_id, text),
            )

    def _insert_vehicles(self, party_id: str, vehicle: object, vehicles: object) -> None:
        rows: list[Any] = []
        if isinstance(vehicle, dict):
            rows.append(vehicle)
        if isinstance(vehicles, list):
            rows.extend(item for item in vehicles if isinstance(item, dict))
        for item in rows:
            self._insert_vehicle_row(party_id, item)

    def _insert_vehicle_row(self, party_id: str, item: dict[str, Any]) -> None:
        photos = item.get("photos") if item.get("photos") is not None else item.get("photos_json")
        if isinstance(photos, str):
            photos_json = photos
        else:
            photos_json = _dumps(photos or [])
        self.conn.execute(
            """
            INSERT INTO vehicle(
              id, party_id, year, make, model, vin, plate, asking_price, mileage,
              location, gps, photos_json, spotted_at, date_source, location_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(item.get("id") or ulid()),
                party_id,
                str(item.get("year") or ""),
                str(item.get("make") or ""),
                str(item.get("model") or ""),
                str(item.get("vin") or ""),
                str(item.get("plate") or ""),
                str(item.get("asking_price") or item.get("price") or ""),
                str(item.get("mileage") or ""),
                str(item.get("location") or ""),
                str(item.get("gps") or ""),
                photos_json,
                str(item.get("spotted_at") or ""),
                str(item.get("date_source") or ""),
                str(item.get("location_source") or ""),
            ),
        )

    def _insert_note(self, party_id: str, body: str, author: str, at: str) -> str:
        note_id = ulid()
        self.conn.execute(
            """
            INSERT INTO note(id, party_id, body, author_seat, created_at, archived)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (note_id, party_id, body, author, at),
        )
        return note_id

    def _sync_lead_events(self, actor: Actor, party_id: str, events: object) -> None:
        if not isinstance(events, list):
            return
        for index, event in enumerate(events):
            if not isinstance(event, dict):
                continue
            op = str(event.get("op") or "")
            request_id = _event_msg_id(party_id, index, event)
            mapped = _EVENT_INTERACTION.get(op)
            if mapped is None:
                if op not in {"intake", "duplicate_skip", "sms_variant_assigned"}:
                    continue
                exists = self.conn.execute(
                    "SELECT 1 FROM audit WHERE request_id = ?", (request_id,)
                ).fetchone()
                if exists is None:
                    self._audit(actor, "lead_event", party_id, "create", None, event, request_id)
                continue
            if self._interaction_by_msg(request_id) is not None:
                continue
            channel, direction, outcome = mapped
            self.conn.execute(
                """
                INSERT INTO interaction(
                  id, party_id, channel, direction, provider, provider_msg_id,
                  from_addr, to_addr, body, summary, transcript_ref, outcome,
                  error_code, copy_version, variant_id, occurred_at, logged_by
                ) VALUES (?, ?, ?, ?, 'bland', ?, '', '', ?, ?, '', ?, '', ?, ?, ?, 'roadside-event')
                """,
                (
                    ulid(),
                    party_id,
                    channel,
                    direction,
                    request_id,
                    str(event.get("text") or ""),
                    op,
                    outcome,
                    str(event.get("copy_version") or ""),
                    str(event.get("variant") or event.get("sms_variant_id") or ""),
                    str(event.get("at") or utc_now()),
                ),
            )

    def _interaction_by_msg(self, msg_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM interaction WHERE provider_msg_id = ?", (msg_id,)
        ).fetchone()
        if row is None:
            return None
        return self._interaction_from_row(row)

    def _touch_interaction(self, existing: dict[str, Any], payload: dict[str, Any]) -> bool:
        outcome = str(payload.get("outcome") or "")
        error = str(payload.get("error_code") or "")
        if not outcome and not error:
            return False
        if outcome == existing.get("outcome") and error == existing.get("error_code"):
            return False
        self.conn.execute(
            "UPDATE interaction SET outcome = ?, error_code = ? WHERE id = ?",
            (outcome or existing.get("outcome") or "", error or existing.get("error_code") or "", existing["id"]),
        )
        return True

    def _apply_interaction_effects(
        self, actor: Actor, party_id: str, row: dict[str, Any], request_id: str
    ) -> None:
        if str(row.get("outcome")) == "undelivered" and str(row.get("error_code")) == "30006":
            self._tag_phones(party_id, "landline")
        if str(row.get("direction")) == "in" and is_stop_text(row.get("body")):
            party = self._party_row(party_id)
            phones = self.conn.execute(
                "SELECT value_norm, value_raw FROM identity WHERE party_id = ? AND kind = 'phone'",
                (party_id,),
            ).fetchall()
            keys = []
            for phone in phones:
                key = phone_key(phone["value_norm"]) or phone_key(phone["value_raw"])
                if key:
                    keys.append(key)
            if keys and _holds(actor, "write:suppression"):
                self.suppress(actor, keys, "opt-out", request_id)
            elif keys:
                # Buzz can log the inbound STOP. Suppression is a pipeline write.
                # Record it as the roadside seat when the caller cannot.
                self.suppress(self.actor_for_seat("roadside-pipeline"), keys, "opt-out", request_id)
            if party is not None and party["type"] == "lead" and party["status"] != "declined":
                self.update_party(
                    self.actor_for_seat("roadside-pipeline")
                    if not _holds(actor, "write:status") and not _holds(actor, "write:party")
                    else actor,
                    party_id,
                    {"status": "declined"},
                    request_id,
                )

    def _tag_phones(self, party_id: str, tag: str) -> None:
        rows = self.conn.execute(
            "SELECT id FROM identity WHERE party_id = ? AND kind = 'phone'",
            (party_id,),
        ).fetchall()
        for row in rows:
            self.conn.execute(
                "INSERT OR IGNORE INTO identity_tag(identity_id, tag) VALUES (?, ?)",
                (row["id"], tag),
            )

    def _suppression_key(self, key: str) -> str:
        raw = str(key or "").strip()
        if not raw:
            return ""
        if raw.lower().startswith("phone:") or nanp_digits(raw):
            return phone_key(raw)
        if raw.lower().startswith("folder:"):
            folder = raw.split(":", 1)[1].strip()
            return "folder:" + folder if folder else ""
        return raw

    def _reindex(self, party_id: str) -> None:
        row = self._party_row(party_id)
        if row is None:
            self.conn.execute("DELETE FROM party_fts WHERE party_id = ?", (party_id,))
            return
        tags = " ".join(
            r["tag"]
            for r in self.conn.execute(
                "SELECT tag FROM party_tag WHERE party_id = ? ORDER BY tag", (party_id,)
            )
        )
        notes = " ".join(
            r["body"]
            for r in self.conn.execute(
                "SELECT body FROM note WHERE party_id = ? AND archived = 0", (party_id,)
            )
        )
        vehicles = []
        for vehicle in self.conn.execute(
            "SELECT year, make, model, vin FROM vehicle WHERE party_id = ?", (party_id,)
        ):
            vehicles.append(" ".join(str(vehicle[key] or "") for key in ("year", "make", "model", "vin")))
        identities = []
        for ident in self.conn.execute(
            "SELECT value_norm, value_raw FROM identity WHERE party_id = ?", (party_id,)
        ):
            identities.append(str(ident["value_norm"] or ""))
            identities.append(str(ident["value_raw"] or ""))
        self.conn.execute("DELETE FROM party_fts WHERE party_id = ?", (party_id,))
        self.conn.execute(
            """
            INSERT INTO party_fts(party_id, display_name, org_name, tags, notes, vehicle, identities)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                party_id,
                row["display_name"],
                row["org_name"],
                tags,
                notes,
                " ".join(vehicles),
                " ".join(identities),
            ),
        )

    def _audit(
        self,
        actor: Actor,
        entity: str,
        entity_id: str,
        op: str,
        before: Any,
        after: Any,
        request_id: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO audit(
              at, actor_seat, actor_cred_id, entity, entity_id, op,
              before_json, after_json, request_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now(),
                actor.seat,
                actor.cred_id,
                entity,
                entity_id,
                op,
                None if before is None else _dumps(before),
                None if after is None else _dumps(after),
                request_id,
            ),
        )

    def _party_doc(self, party_id: str) -> dict[str, Any]:
        row = self._party_row(party_id)
        if row is None:
            raise CrmError(404, "not found")
        tags = [
            r["tag"]
            for r in self.conn.execute(
                "SELECT tag FROM party_tag WHERE party_id = ? ORDER BY tag", (party_id,)
            )
        ]
        identities = []
        for ident in self.conn.execute(
            "SELECT * FROM identity WHERE party_id = ? ORDER BY kind, value_norm",
            (party_id,),
        ):
            id_tags = [
                t["tag"]
                for t in self.conn.execute(
                    "SELECT tag FROM identity_tag WHERE identity_id = ? ORDER BY tag",
                    (ident["id"],),
                )
            ]
            identities.append(
                {
                    "id": ident["id"],
                    "kind": ident["kind"],
                    "value": ident["value_norm"],
                    "value_raw": ident["value_raw"],
                    "tags": id_tags,
                }
            )
        vehicles = []
        for vehicle in self.conn.execute(
            "SELECT * FROM vehicle WHERE party_id = ? ORDER BY id", (party_id,)
        ):
            vehicles.append(
                {
                    "id": vehicle["id"],
                    "year": vehicle["year"],
                    "make": vehicle["make"],
                    "model": vehicle["model"],
                    "vin": vehicle["vin"],
                    "plate": vehicle["plate"],
                    "asking_price": vehicle["asking_price"],
                    "mileage": vehicle["mileage"],
                    "location": vehicle["location"],
                    "gps": vehicle["gps"],
                    "photos": _loads(vehicle["photos_json"], []),
                    "spotted_at": vehicle["spotted_at"],
                    "date_source": vehicle["date_source"],
                    "location_source": vehicle["location_source"],
                }
            )
        return {
            "id": row["id"],
            "type": row["type"],
            "display_name": row["display_name"],
            "org_name": row["org_name"],
            "tags": tags,
            "status": row["status"],
            "source": row["source"],
            "owner_seat": row["owner_seat"],
            "location": row["location"],
            "archived": bool(row["archived"]),
            "ext": _loads(row["ext_json"], {}),
            "identities": identities,
            "vehicles": vehicles,
            "created_at": row["created_at"],
            "created_by": row["created_by"],
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
        }

    def _note_doc(self, note_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM note WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            raise CrmError(404, "not found")
        return {
            "id": row["id"],
            "party_id": row["party_id"],
            "body": row["body"],
            "author_seat": row["author_seat"],
            "created_at": row["created_at"],
            "archived": bool(row["archived"]),
        }

    def _task_doc(self, task_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise CrmError(404, "not found")
        return {
            "id": row["id"],
            "party_id": row["party_id"] or "",
            "title": row["title"],
            "due_at": row["due_at"],
            "status": row["status"],
            "assignee_seat": row["assignee_seat"],
            "created_by": row["created_by"],
            "completed_by": row["completed_by"],
            "completed_at": row["completed_at"],
            "created_at": row["created_at"],
        }

    def _interaction_doc(self, interaction_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM interaction WHERE id = ?", (interaction_id,)
        ).fetchone()
        if row is None:
            raise CrmError(404, "not found")
        return self._interaction_from_row(row)

    def _interaction_from_row(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "party_id": row["party_id"],
            "channel": row["channel"],
            "direction": row["direction"],
            "provider": row["provider"],
            "provider_msg_id": row["provider_msg_id"] or "",
            "from": row["from_addr"],
            "to": row["to_addr"],
            "body": row["body"],
            "summary": row["summary"],
            "transcript_ref": row["transcript_ref"],
            "outcome": row["outcome"],
            "error_code": row["error_code"],
            "copy_version": row["copy_version"],
            "variant_id": row["variant_id"],
            "occurred_at": row["occurred_at"],
            "logged_by": row["logged_by"],
        }


def _fts_tokens(q: str) -> list[str]:
    out = []
    token = []
    for ch in str(q or ""):
        if ch.isalnum():
            token.append(ch)
        elif token:
            out.append("".join(token))
            token = []
    if token:
        out.append("".join(token))
    return out
