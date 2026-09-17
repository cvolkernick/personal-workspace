"""Canonical JSON-file store for leads, suppression, outbox, and alerts."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from models import CANONICAL_STORE, Lead

STORE_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _empty_doc() -> dict[str, Any]:
    return {
        "version": STORE_VERSION,
        "canonical": CANONICAL_STORE,
        "leads": {},
        "suppression": [],
        "outbox": [],
        "alerts": [],
        "sms_by_date": {},
        "updated_at": utc_now(),
    }


class FileStore:
    """Single canonical store. Checked before every send. No exceptions."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._doc = _empty_doc()
        if self.path.is_file():
            self._doc = json.loads(self.path.read_text(encoding="utf-8"))
            self._doc.setdefault("leads", {})
            self._doc.setdefault("suppression", [])
            self._doc.setdefault("outbox", [])
            self._doc.setdefault("alerts", [])
            self._doc.setdefault("sms_by_date", {})

    def save(self) -> None:
        self._doc["updated_at"] = utc_now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._doc, indent=2, ensure_ascii=False) + "\n"
        fd, tmp = tempfile.mkstemp(prefix=".store.", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get(self, lead_id: str) -> Optional[Lead]:
        raw = self._doc["leads"].get(lead_id)
        return Lead.from_dict(raw) if isinstance(raw, dict) else None

    def put(self, lead: Lead) -> Lead:
        lead.updated_at = utc_now()
        if not lead.created_at:
            lead.created_at = lead.updated_at
        self._doc["leads"][lead.id] = lead.to_dict()
        self.save()
        return lead

    def all_leads(self) -> list[Lead]:
        return [Lead.from_dict(v) for v in self._doc["leads"].values() if isinstance(v, dict)]

    def by_state(self, state: str) -> list[Lead]:
        return [lead for lead in self.all_leads() if lead.state == state]

    def find_by_phone(self, phone: str) -> Optional[Lead]:
        needle = normalize_contact(phone)
        if not needle:
            return None
        for lead in self.all_leads():
            if needle in {normalize_contact(lead.phone), normalize_contact(lead.sms_number)}:
                return lead
        return None

    def find_by_place(self, place_id: str) -> Optional[Lead]:
        for lead in self.all_leads():
            if lead.place_id == place_id or lead.id == place_id:
                return lead
        return None

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for lead in self.all_leads():
            out[lead.state] = out.get(lead.state, 0) + 1
        return out

    def suppress(self, keys: Iterable[str], reason: str) -> None:
        at = utc_now()
        existing = {row.get("key") for row in self._doc["suppression"]}
        for key in keys:
            norm = normalize_contact(key) if ":" not in key else key
            if not norm or norm in existing:
                continue
            self._doc["suppression"].append({"key": norm, "reason": reason, "at": at})
            existing.add(norm)
        self.save()

    def is_suppressed(self, *keys: str) -> bool:
        banned = {row.get("key") for row in self._doc["suppression"]}
        for key in keys:
            if not key:
                continue
            candidates = {key, normalize_contact(key)}
            if key.startswith("place:"):
                candidates.add(key)
            else:
                candidates.add("place:" + key)
                candidates.add("phone:" + normalize_contact(key))
                candidates.add("email:" + normalize_contact(key))
            if candidates & banned:
                return True
        return False

    def suppression_keys_for(self, lead: Lead) -> list[str]:
        keys = [f"place:{lead.place_id}"]
        if lead.phone:
            keys.append("phone:" + normalize_contact(lead.phone))
        if lead.sms_number:
            keys.append("phone:" + normalize_contact(lead.sms_number))
        if lead.email:
            keys.append("email:" + normalize_contact(lead.email))
        return keys

    def record_outbox(self, item: dict[str, Any]) -> dict[str, Any]:
        row = dict(item)
        row.setdefault("at", utc_now())
        self._doc["outbox"].append(row)
        self.save()
        return row

    def record_alert(self, item: dict[str, Any]) -> dict[str, Any]:
        row = dict(item)
        row.setdefault("at", utc_now())
        self._doc["alerts"].append(row)
        self.save()
        return row

    def sms_sent_today(self) -> int:
        return int(self._doc["sms_by_date"].get(today_utc(), 0))

    def increment_sms(self) -> None:
        day = today_utc()
        self._doc["sms_by_date"][day] = int(self._doc["sms_by_date"].get(day, 0)) + 1
        self.save()

    def outbox(self) -> list[dict[str, Any]]:
        return list(self._doc["outbox"])

    def alerts(self) -> list[dict[str, Any]]:
        return list(self._doc["alerts"])


def normalize_contact(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if "@" in raw:
        return raw
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits
