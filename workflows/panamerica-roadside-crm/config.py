"""Env-driven config. Secrets never belong in the repo, chat, or logs."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from models import COPY_VERSION, VOICE_DELAY_MAX_DAYS, VOICE_DELAY_MIN_DAYS


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Config:
    dry_run: bool = True
    copy_approved: bool = False
    store_path: Path = Path.home() / ".local/share/panamerica-roadside-crm/store.json"
    drive_folder_id: str = "1QS6rqyApNDCrsJ90mp83rnbEthlzznxy"
    drive_folder_name: str = "Panamerica - Roadside Leads"
    copy_version: str = COPY_VERSION
    daily_sms_cap: int = 20
    voice_delay_min_days: int = VOICE_DELAY_MIN_DAYS
    voice_delay_max_days: int = VOICE_DELAY_MAX_DAYS
    sender_name: str = "Alexandra"
    bland_api_key: str = ""
    bland_agent_id: str = ""
    bland_from_number: str = ""
    google_drive_token: str = ""
    alert_webhook: str = ""
    webhook_secret: str = ""
    webhook_public_url: str = ""
    fixture_path: Path | None = None
    simulate_replies: bool = False
    simulate_interest: bool = False
    now: Optional[Callable[[], datetime]] = None

    def clock(self) -> datetime:
        if self.now is not None:
            return self.now()
        return datetime.now(timezone.utc)

    @classmethod
    def from_env(cls, **overrides: object) -> "Config":
        store = _env("PANAMERICA_ROADSIDE_STORE")
        fixture = _env("PANAMERICA_ROADSIDE_FIXTURE")
        cfg = cls(
            dry_run=not _flag("PANAMERICA_ROADSIDE_LIVE"),
            copy_approved=_flag("PANAMERICA_ROADSIDE_COPY_APPROVED"),
            store_path=Path(store).expanduser()
            if store
            else Path.home() / ".local/share/panamerica-roadside-crm/store.json",
            drive_folder_id=_env(
                "PANAMERICA_ROADSIDE_DRIVE_FOLDER_ID",
                "1QS6rqyApNDCrsJ90mp83rnbEthlzznxy",
            ),
            daily_sms_cap=int(_env("PANAMERICA_ROADSIDE_DAILY_SMS_CAP", "20") or "20"),
            voice_delay_min_days=int(
                _env("PANAMERICA_ROADSIDE_VOICE_DELAY_MIN_DAYS", str(VOICE_DELAY_MIN_DAYS))
                or str(VOICE_DELAY_MIN_DAYS)
            ),
            voice_delay_max_days=int(
                _env("PANAMERICA_ROADSIDE_VOICE_DELAY_MAX_DAYS", str(VOICE_DELAY_MAX_DAYS))
                or str(VOICE_DELAY_MAX_DAYS)
            ),
            bland_api_key=_env("BLAND_API_KEY"),
            bland_agent_id=_env("BLAND_AGENT_ID"),
            bland_from_number=_env("BLAND_FROM_NUMBER"),
            google_drive_token=_env("GOOGLE_DRIVE_ACCESS_TOKEN") or _env("GOOGLE_OAUTH_TOKEN"),
            alert_webhook=_env("PANAMERICA_ROADSIDE_ALERT_WEBHOOK"),
            webhook_secret=_env("PANAMERICA_ROADSIDE_WEBHOOK_SECRET"),
            webhook_public_url=_env("PANAMERICA_ROADSIDE_WEBHOOK_URL"),
            fixture_path=Path(fixture).expanduser() if fixture else None,
        )
        return replace(cfg, **overrides) if overrides else cfg

    def require_live_sends(self) -> None:
        """Live SMS/voice stays blocked until the first-send human gate (#855)."""
        if self.dry_run:
            return
        if not self.copy_approved:
            raise LiveBlocked(
                "live outreach blocked: first-send human gate "
                "(set PANAMERICA_ROADSIDE_COPY_APPROVED=1)"
            )

    def redacted(self) -> dict[str, object]:
        return {
            "dry_run": self.dry_run,
            "copy_approved": self.copy_approved,
            "store_path": str(self.store_path),
            "drive_folder_id": self.drive_folder_id,
            "drive_folder_name": self.drive_folder_name,
            "copy_version": self.copy_version,
            "daily_sms_cap": self.daily_sms_cap,
            "voice_delay_min_days": self.voice_delay_min_days,
            "voice_delay_max_days": self.voice_delay_max_days,
            "sender_name": self.sender_name,
            "bland_key_set": bool(self.bland_api_key),
            "bland_agent_set": bool(self.bland_agent_id),
            "drive_token_set": bool(self.google_drive_token),
            "alert_webhook_set": bool(self.alert_webhook),
            "webhook_secret_set": bool(self.webhook_secret),
            "canonical_store": "file",
        }


class LiveBlocked(RuntimeError):
    pass
