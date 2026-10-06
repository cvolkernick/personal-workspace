"""FileStore-shaped adapter over workflows.crm. Roadside leads only.

Vendors and other party types stay in the database and are invisible to
daily-pass, sms-batch, and call-batch. SMS selection also drops landline tags.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterable, Optional

PKG = Path(__file__).resolve().parent
ROOT = PKG.parents[1]
for entry in (str(PKG), str(ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from models import Lead  # noqa: E402
from phones import normalize_phone  # noqa: E402
from store import today_utc, utc_now  # noqa: E402

from workflows.crm.core import CrmDB  # noqa: E402
from workflows.crm.export import build_doc  # noqa: E402


def resolve_db_path(store_path: Path, crm_db_path: Optional[Path] = None) -> Path:
    """Canonical DB for the canonical store. Any other store gets a sibling crm.db."""
    if crm_db_path is not None:
        return Path(crm_db_path)
    default_store = Path.home() / ".local" / "share" / "panamerica-roadside-crm" / "store.json"
    if Path(store_path) == default_store:
        return Path.home() / ".local" / "share" / "panamerica-crm" / "crm.db"
    return Path(store_path).parent / "crm.db"


class CrmStore:
    def __init__(self, db_path: Path, export_path: Path) -> None:
        self.path = Path(export_path)
        self.db = CrmDB(Path(db_path))
        self._actor = self.db.actor_for_seat("roadside-pipeline")
        # An existing JSON store is imported before the export path is armed,
        # so a failed import cannot overwrite it.
        if self.path.is_file() and not self.db.roadside_leads():
            from workflows.crm.importer import import_all

            report = import_all(self.db, roadside=self.path)
            if not report.get("ok"):
                raise RuntimeError(f"crm import parity failed: {report}")
        self.db.set_export_path(self.path)

    @property
    def _doc(self) -> dict[str, Any]:
        return build_doc(self.db.conn)

    def get(self, lead_id: str) -> Optional[Lead]:
        snap = self._snap(lead_id)
        return Lead.from_dict(snap) if snap else None

    def put(self, lead: Lead) -> Lead:
        lead.updated_at = utc_now()
        if not lead.created_at:
            lead.created_at = lead.updated_at
        self.db.save_roadside_lead(self._actor, lead.to_dict())
        return lead

    def all_leads(self) -> list[Lead]:
        return [Lead.from_dict(row) for row in self.db.roadside_leads()]

    def by_state(self, state: str) -> list[Lead]:
        leads = [lead for lead in self.all_leads() if lead.state == state]
        if state == "new":
            leads = [lead for lead in leads if not self.db.phone_has_tag(lead.id, "landline")]
        return leads

    def find_by_phone(self, phone: str) -> Optional[Lead]:
        needle = normalize_phone(phone)
        if not needle:
            return None
        for lead in self.all_leads():
            if normalize_phone(lead.phone) == needle:
                return lead
        return None

    def find_by_folder(self, folder_id: str) -> Optional[Lead]:
        if not folder_id:
            return None
        for lead in self.all_leads():
            if lead.folder_id == folder_id or lead.id == folder_id:
                return lead
        return None

    def mark_folder_processed(self, folder_id: str, lead_id: str, reason: str) -> None:
        self.db.mark_folder(folder_id, lead_id, reason)

    def folder_processed(self, folder_id: str) -> bool:
        return self.db.folder_processed(folder_id)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for lead in self.all_leads():
            out[lead.state] = out.get(lead.state, 0) + 1
        return out

    def suppress(self, keys: Iterable[str], reason: str) -> None:
        self.db.suppress(self._actor, list(keys), reason)

    def is_suppressed(self, *keys: str) -> bool:
        return self.db.is_suppressed(*keys)

    def suppression_keys_for(self, lead: Lead) -> list[str]:
        keys = [f"folder:{lead.folder_id}"]
        if lead.phone:
            keys.append("phone:" + normalize_phone(lead.phone))
        return keys

    def phone_already_assigned(self, lead: Lead) -> bool:
        needle = normalize_phone(lead.phone)
        if not needle:
            return False
        for other in self.all_leads():
            if other.id == lead.id:
                continue
            if normalize_phone(other.phone) != needle:
                continue
            if str(other.sms_variant_id or "").strip() or other.sms_sent_at:
                return True
        return False

    def record_outbox(self, item: dict[str, Any]) -> dict[str, Any]:
        return self.db.record_outbox(item)

    def record_alert(self, item: dict[str, Any]) -> dict[str, Any]:
        return self.db.record_alert(item)

    def sms_sent_today(self) -> int:
        return self.db.sms_sent_on(today_utc())

    def increment_sms(self) -> None:
        self.db.increment_sms(today_utc())

    def outbox(self) -> list[dict[str, Any]]:
        return self.db.outbox()

    def alerts(self) -> list[dict[str, Any]]:
        return self.db.alerts()

    def _snap(self, lead_id: str) -> Optional[dict[str, Any]]:
        for row in self.db.roadside_leads():
            if str(row.get("id") or "") == lead_id:
                return row
        return None
