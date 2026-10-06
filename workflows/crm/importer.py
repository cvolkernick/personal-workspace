"""Import roadside JSON, marketplace JSON, outreach_log.jsonl, and vendors.json.

Parity is source count versus rows present after the import. A mismatch exits non-zero.
Re-runs are idempotent. --dry-run imports into a throwaway database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

from workflows.crm.auth import CrmError
from workflows.crm.core import CrmDB
from workflows.crm.schema import default_db_path


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _section(source: int, imported: int) -> dict[str, Any]:
    return {"source": source, "imported": imported, "ok": source == imported}


def _digest(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def backup_file(path: Path) -> Optional[Path]:
    """Copy once to `<name>.bak-p1`. Later writes must not touch that path."""
    path = Path(path)
    if not path.is_file():
        return None
    dest = path.with_name(path.name + ".bak-p1")
    if not dest.exists():
        shutil.copy2(path, dest)
    return dest


def import_all(
    db: CrmDB,
    *,
    roadside: Optional[Path] = None,
    marketplace: Optional[Path] = None,
    outreach_log: Optional[Path] = None,
    vendors: Optional[Path] = None,
) -> dict[str, Any]:
    actor = db.actor_for_seat("grok")
    report: dict[str, Any] = {}
    with db.batch():
        if roadside is not None:
            doc = _load_json(Path(roadside))
            if not isinstance(doc, dict):
                raise ValueError("roadside store must be a JSON object")
            report["roadside_leads"] = _import_leads(db, actor, doc)
            report["suppression"] = _import_suppression(db, actor, doc)
            report["outbox"] = _import_outbox(db, actor, doc)
            report["processed_folders"] = _import_folders(db, doc)
            _import_alerts(db, doc)
            _import_sms_days(db, doc)
        if marketplace is not None:
            doc = _load_json(Path(marketplace))
            report["marketplace_leads"] = _import_marketplace(db, actor, doc)
        if outreach_log is not None:
            report["outreach_log"] = _import_outreach_log(db, actor, Path(outreach_log))
        if vendors is not None:
            report["vendors"] = _import_vendors(db, actor, Path(vendors))
    report["ok"] = all(bool(section.get("ok")) for section in report.values() if isinstance(section, dict))
    return report


def _import_leads(db: CrmDB, actor: Any, doc: dict[str, Any]) -> dict[str, Any]:
    leads = doc.get("leads") if isinstance(doc.get("leads"), dict) else {}
    for lead_id, lead in leads.items():
        if not isinstance(lead, dict):
            continue
        row = dict(lead)
        row.setdefault("id", str(lead_id))
        db.save_roadside_lead(actor, row)
    present = 0
    for lead_id in leads:
        try:
            party = db.get_party(actor, str(lead_id))
        except CrmError:
            party = None
        if party is not None and party.get("type") == "lead":
            present += 1
    return _section(len(leads), present)


def _import_suppression(db: CrmDB, actor: Any, doc: dict[str, Any]) -> dict[str, Any]:
    rows = doc.get("suppression") if isinstance(doc.get("suppression"), list) else []
    keys: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            key = str(row.get("key") or "")
            reason = str(row.get("reason") or "import")
        else:
            key = str(row)
            reason = "import"
        if not key:
            continue
        keys.append(key)
        db.suppress(actor, [key], reason)
    present = sum(1 for key in keys if db.is_suppressed(key))
    return _section(len(rows), present)


def _import_outbox(db: CrmDB, actor: Any, doc: dict[str, Any]) -> dict[str, Any]:
    rows = doc.get("outbox") if isinstance(doc.get("outbox"), list) else []
    existing_keys = {
        str(row.get("import_key") or "")
        for row in db.outbox()
        if row.get("import_key")
    }
    msg_ids: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        msg_id = str(
            row.get("provider_msg_id")
            or row.get("conversation_id")
            or row.get("call_id")
            or f"outbox:{_digest(row)}"
        )
        msg_ids.append(msg_id)
        payload = dict(row)
        payload["import_key"] = msg_id
        if msg_id not in existing_keys:
            db.record_outbox(payload)
            existing_keys.add(msg_id)
        lead_id = str(row.get("lead_id") or "")
        if not lead_id:
            continue
        channel = "call" if str(row.get("channel") or "") in {"voice", "call"} else "sms"
        db.add_interaction(
            actor,
            {
                "party_id": lead_id,
                "channel": channel,
                "direction": "out",
                "provider": "bland",
                "provider_msg_id": msg_id,
                "to": row.get("to") or "",
                "body": row.get("body") or row.get("task") or "",
                "outcome": "sent",
                "occurred_at": row.get("at") or "",
                "logged_by": "importer",
            },
        )
    present = _count_msg_ids(db, msg_ids)
    return _section(len(rows), present)


def _import_folders(db: CrmDB, doc: dict[str, Any]) -> dict[str, Any]:
    folders = doc.get("processed_folders") if isinstance(doc.get("processed_folders"), dict) else {}
    for folder_id, row in folders.items():
        info = row if isinstance(row, dict) else {}
        db.mark_folder(str(folder_id), str(info.get("lead_id") or ""), str(info.get("reason") or ""))
    present = sum(1 for folder_id in folders if db.folder_processed(str(folder_id)))
    return _section(len(folders), present)


def _import_alerts(db: CrmDB, doc: dict[str, Any]) -> None:
    rows = doc.get("alerts") if isinstance(doc.get("alerts"), list) else []
    existing = {str(row.get("import_key") or "") for row in db.alerts()}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = "alert:" + _digest(row)
        if key in existing:
            continue
        payload = dict(row)
        payload["import_key"] = key
        db.record_alert(payload)


def _import_sms_days(db: CrmDB, doc: dict[str, Any]) -> None:
    days = doc.get("sms_by_date") if isinstance(doc.get("sms_by_date"), dict) else {}
    for day, count in days.items():
        target = int(count or 0)
        while db.sms_sent_on(str(day)) < target:
            db.increment_sms(str(day))


def _import_marketplace(db: CrmDB, actor: Any, doc: Any) -> dict[str, Any]:
    leads = doc.get("leads") if isinstance(doc, dict) else None
    if not isinstance(leads, dict):
        leads = {}
    ids: list[str] = []
    for lead_id, lead in leads.items():
        if not isinstance(lead, dict):
            continue
        party_id = str(lead.get("id") or lead_id)
        ids.append(party_id)
        db.create_party(
            actor,
            {
                "id": party_id,
                "type": "lead",
                "source": "marketplace",
                "display_name": lead.get("seller_name") or lead.get("display_name") or "",
                "status": lead.get("status") or "surface_to_chairman",
                "location": lead.get("location") or "",
                "phone": lead.get("phone") or "",
                "listing_id": lead.get("listing_id") or "",
                "vehicle": {
                    "year": lead.get("year") or "",
                    "make": lead.get("make") or "",
                    "model": lead.get("model") or "",
                    "asking_price": lead.get("price") or "",
                    "mileage": lead.get("mileage") or "",
                    "location": lead.get("location") or "",
                },
            },
        )
    present = 0
    for party_id in ids:
        try:
            party = db.get_party(actor, party_id)
        except CrmError:
            party = None
        if party is not None and party.get("source") == "marketplace":
            present += 1
        elif party is not None and party.get("type") == "lead":
            # Dedup returned an existing lead that already owns the listing.
            present += 1
    return _section(len(leads), present)


def _import_outreach_log(db: CrmDB, actor: Any, path: Path) -> dict[str, Any]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    msg_ids: list[str] = []
    for line in lines:
        row = json.loads(line)
        if not isinstance(row, dict):
            continue
        msg_id = str(row.get("provider_msg_id") or row.get("msg_id") or row.get("id") or "")
        if not msg_id:
            msg_id = "outreach:" + _digest(row)
        msg_ids.append(msg_id)
        phone = str(row.get("phone") or "")
        listing = str(row.get("listing_id") or "")
        found = None
        if phone:
            found = db.by_identity(actor, kind="phone", value=phone)
        if found is None and listing:
            found = db.by_identity(actor, kind="listing_id", value=listing)
        if found is None:
            found, _created = db.create_party(
                actor,
                {
                    "id": "outreach-" + msg_id.replace("-", "")[:20],
                    "type": "lead",
                    "source": "marketplace",
                    "display_name": row.get("name") or row.get("display_name") or "",
                    "status": "new",
                    "phone": phone,
                    "listing_id": listing,
                },
            )
        outcome = str(row.get("outcome") or row.get("status") or "")
        db.add_interaction(
            actor,
            {
                "party_id": found["id"],
                "channel": row.get("channel") or "sms",
                "direction": row.get("direction") or "out",
                "provider": row.get("provider") or "bland",
                "provider_msg_id": msg_id,
                "to": row.get("to") or phone,
                "from": row.get("from") or "",
                "body": row.get("body") or "",
                "outcome": outcome,
                "error_code": str(row.get("error_code") or ""),
                "occurred_at": row.get("occurred_at") or row.get("at") or "",
                "logged_by": "importer",
            },
        )
    return _section(len(lines), _count_msg_ids(db, msg_ids))


def _import_vendors(db: CrmDB, actor: Any, path: Path) -> dict[str, Any]:
    doc = _load_json(path)
    if isinstance(doc, dict):
        rows = doc.get("vendors") or doc.get("entries") or []
    elif isinstance(doc, list):
        rows = doc
    else:
        rows = []
    ids: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        party_id = str(row.get("id") or f"vendor-{index + 1}")
        ids.append(party_id)
        tags = row.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if row.get("tag") and row.get("tag") not in tags:
            tags = [*tags, row.get("tag")]
        db.create_party(
            actor,
            {
                "id": party_id,
                "type": row.get("type") or "vendor",
                "source": "vendors_json",
                "display_name": row.get("display_name") or row.get("name") or "",
                "org_name": row.get("org_name") or "",
                "location": row.get("location") or "",
                "status": row.get("status") or "new",
                "tags": tags,
                "phone": row.get("phone") or "",
                "email": row.get("email") or "",
            },
        )
    present = 0
    for party_id in ids:
        try:
            party = db.get_party(actor, party_id)
        except CrmError:
            party = None
        if party is not None and party.get("type") == "vendor":
            present += 1
    return _section(len(rows), present)


def _count_msg_ids(db: CrmDB, msg_ids: list[str]) -> int:
    if not msg_ids:
        return 0
    found = 0
    for msg_id in msg_ids:
        row = db.conn.execute(
            "SELECT 1 FROM interaction WHERE provider_msg_id = ?",
            (msg_id,),
        ).fetchone()
        if row is not None:
            found += 1
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import legacy CRM files into crm.db")
    parser.add_argument("--db", default=str(default_db_path()))
    parser.add_argument("--roadside", default="")
    parser.add_argument("--marketplace", default="")
    parser.add_argument("--outreach-log", default="")
    parser.add_argument("--vendors", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backup", action="store_true", help="Copy roadside JSON to .bak-p1 once")
    args = parser.parse_args(argv)
    roadside = Path(args.roadside) if args.roadside else None
    if args.backup and roadside is not None:
        backup_file(roadside)
    db_path = Path(args.db)
    cleanup: Optional[Path] = None
    if args.dry_run:
        cleanup = Path(tempfile.mkdtemp(prefix="crm-import-"))
        db_path = cleanup / "dry.db"
    db = CrmDB(db_path)
    try:
        report = import_all(
            db,
            roadside=roadside,
            marketplace=Path(args.marketplace) if args.marketplace else None,
            outreach_log=Path(args.outreach_log) if args.outreach_log else None,
            vendors=Path(args.vendors) if args.vendors else None,
        )
    finally:
        db.close()
        if cleanup is not None:
            shutil.rmtree(cleanup, ignore_errors=True)
    report["dry_run"] = bool(args.dry_run)
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
