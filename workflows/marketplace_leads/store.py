"""JSON file store. One file is the CRM source of truth."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from workflows.marketplace_leads.models import Lead, clean_listing_id, dedup_key

STORE_VERSION = 1
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_store_path() -> Path:
    env = (os.environ.get("MARKETPLACE_LEAD_STORE") or "").strip()
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "marketplace-leads" / "store.json"


def _lock_for(path: Path) -> threading.Lock:
    key = os.path.normpath(str(path))
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def _empty_doc() -> dict:
    return {"version": STORE_VERSION, "leads": {}, "updated_at": utc_now()}


class FileStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._doc = _empty_doc()
        self._reload()

    def _reload(self) -> None:
        if not self.path.is_file():
            self._doc = _empty_doc()
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = _empty_doc()
        if not isinstance(loaded, dict):
            loaded = _empty_doc()
        loaded.setdefault("leads", {})
        loaded.setdefault("version", STORE_VERSION)
        self._doc = loaded

    def _save(self) -> None:
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
        with _lock_for(self.path):
            self._reload()
            raw = self._doc["leads"].get(lead_id)
            return Lead.from_dict(raw) if isinstance(raw, dict) else None

    def all_leads(self) -> list[Lead]:
        with _lock_for(self.path):
            self._reload()
            return self._all_unlocked()

    def _all_unlocked(self) -> list[Lead]:
        return [
            Lead.from_dict(v)
            for v in self._doc["leads"].values()
            if isinstance(v, dict)
        ]

    def open_queue(self) -> list[Lead]:
        rows = [lead for lead in self.all_leads() if lead.on_queue()]
        rows.sort(key=lambda lead: (lead.created_at, lead.listing_id))
        return rows

    def find_by_listing_id(self, listing_id: str) -> Optional[Lead]:
        needle = clean_listing_id(listing_id)
        if not needle:
            return None
        for lead in self.all_leads():
            if clean_listing_id(lead.listing_id) == needle:
                return lead
        return None

    def find_by_dedup(self, listing_id: str, phone: str) -> Optional[Lead]:
        needle = dedup_key(listing_id, phone)
        if not needle[0]:
            return None
        for lead in self.all_leads():
            if dedup_key(lead.listing_id, lead.phone) == needle:
                return lead
        return None

    def insert_if_new(self, lead: Lead) -> tuple[Lead, bool]:
        """Insert unless listing_id or listing_id+phone is already in the CRM.

        A duplicate is returned unchanged. Status is not reset, so a lead
        that already left the queue is never re-queued.
        """
        with _lock_for(self.path):
            self._reload()
            existing = self._match_unlocked(lead.listing_id, lead.phone)
            if existing is not None:
                return existing, False
            now = utc_now()
            lead.updated_at = now
            if not lead.created_at:
                lead.created_at = now
            self._doc["leads"][lead.id] = lead.to_dict()
            self._save()
            return lead, True

    def _match_unlocked(self, listing_id: str, phone: str) -> Optional[Lead]:
        needle_id = clean_listing_id(listing_id)
        needle_key = dedup_key(listing_id, phone)
        for lead in self._all_unlocked():
            if needle_id and clean_listing_id(lead.listing_id) == needle_id:
                return lead
            if needle_key[0] and dedup_key(lead.listing_id, lead.phone) == needle_key:
                return lead
        return None

    def update(self, lead: Lead) -> Lead:
        with _lock_for(self.path):
            self._reload()
            if lead.id not in self._doc["leads"]:
                raise KeyError(lead.id)
            lead.updated_at = utc_now()
            self._doc["leads"][lead.id] = lead.to_dict()
            self._save()
            return lead


def open_store(path: Path | None = None) -> FileStore:
    return FileStore(path if path is not None else default_store_path())
