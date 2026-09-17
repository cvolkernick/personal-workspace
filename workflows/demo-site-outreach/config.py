"""Env-driven config. Secrets never belong in the repo, chat, or logs."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from models import MAX_FOLLOW_UPS, TEMPLATE_VERSION


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Config:
    dry_run: bool = True
    template_approved: bool = False
    store_path: Path = Path.home() / ".local/share/demo-site-outreach/store.json"
    geo: str = "US"
    category: str = ""
    batch_size: int = 10
    template_version: str = TEMPLATE_VERSION
    max_follow_ups: int = MAX_FOLLOW_UPS
    daily_sms_cap: int = 20
    demo_retention_days: int = 30
    from_email: str = ""
    physical_address: str = ""
    sender_name: str = "Alexandra"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    places_api_key: str = ""
    bland_api_key: str = ""
    bland_agent_id: str = ""
    bland_from_number: str = ""
    vercel_token: str = ""
    vercel_team_id: str = ""
    alert_webhook: str = ""
    webhook_secret: str = ""
    webhook_public_url: str = ""
    fixture_path: Path | None = None
    simulate_replies: bool = False
    simulate_interest: bool = False

    @classmethod
    def from_env(cls, **overrides: object) -> "Config":
        store = _env("DEMO_SITE_OUTREACH_STORE")
        fixture = _env("DEMO_SITE_OUTREACH_FIXTURE")
        cfg = cls(
            dry_run=not _flag("DEMO_SITE_OUTREACH_LIVE"),
            template_approved=_flag("DEMO_SITE_OUTREACH_TEMPLATE_APPROVED"),
            store_path=Path(store).expanduser()
            if store
            else Path.home() / ".local/share/demo-site-outreach/store.json",
            geo=_env("DEMO_SITE_OUTREACH_GEO", "US"),
            category=_env("DEMO_SITE_OUTREACH_CATEGORY"),
            batch_size=int(_env("DEMO_SITE_OUTREACH_BATCH_SIZE", "10") or "10"),
            daily_sms_cap=int(_env("DEMO_SITE_OUTREACH_DAILY_SMS_CAP", "20") or "20"),
            demo_retention_days=int(_env("DEMO_SITE_OUTREACH_RETENTION_DAYS", "30") or "30"),
            from_email=_env("DEMO_SITE_OUTREACH_FROM_EMAIL"),
            physical_address=_env("DEMO_SITE_OUTREACH_PHYSICAL_ADDRESS"),
            smtp_host=_env("DEMO_SITE_OUTREACH_SMTP_HOST"),
            smtp_port=int(_env("DEMO_SITE_OUTREACH_SMTP_PORT", "587") or "587"),
            smtp_user=_env("DEMO_SITE_OUTREACH_SMTP_USER"),
            smtp_pass=_env("DEMO_SITE_OUTREACH_SMTP_PASS"),
            places_api_key=_env("GOOGLE_PLACES_API_KEY") or _env("PLACES_API_KEY"),
            bland_api_key=_env("BLAND_API_KEY"),
            bland_agent_id=_env("BLAND_AGENT_ID"),
            bland_from_number=_env("BLAND_FROM_NUMBER"),
            vercel_token=_env("VERCEL_TOKEN"),
            vercel_team_id=_env("VERCEL_TEAM_ID") or _env("VERCEL_ORG_ID"),
            alert_webhook=_env("DEMO_SITE_OUTREACH_ALERT_WEBHOOK"),
            webhook_secret=_env("DEMO_SITE_OUTREACH_WEBHOOK_SECRET"),
            webhook_public_url=_env("DEMO_SITE_OUTREACH_WEBHOOK_URL"),
            fixture_path=Path(fixture).expanduser() if fixture else None,
        )
        return replace(cfg, **overrides) if overrides else cfg

    def require_live_sends(self) -> None:
        """Live email/SMS/deploy is blocked until the Chairman approves the template."""
        if self.dry_run:
            return
        if not self.template_approved:
            raise LiveBlocked(
                "live outreach blocked: site template is not Chairman-approved "
                "(set DEMO_SITE_OUTREACH_TEMPLATE_APPROVED=1 after sign-off)"
            )
        if not self.physical_address:
            raise LiveBlocked("live outreach blocked: DEMO_SITE_OUTREACH_PHYSICAL_ADDRESS required (CAN-SPAM)")
        if not self.from_email:
            raise LiveBlocked("live outreach blocked: DEMO_SITE_OUTREACH_FROM_EMAIL required")

    def redacted(self) -> dict[str, object]:
        return {
            "dry_run": self.dry_run,
            "template_approved": self.template_approved,
            "store_path": str(self.store_path),
            "geo": self.geo,
            "category": self.category,
            "batch_size": self.batch_size,
            "template_version": self.template_version,
            "max_follow_ups": self.max_follow_ups,
            "daily_sms_cap": self.daily_sms_cap,
            "demo_retention_days": self.demo_retention_days,
            "sender_name": self.sender_name,
            "from_email": self.from_email,
            "physical_address_set": bool(self.physical_address),
            "places_key_set": bool(self.places_api_key),
            "bland_key_set": bool(self.bland_api_key),
            "bland_agent_set": bool(self.bland_agent_id),
            "vercel_token_set": bool(self.vercel_token),
            "alert_webhook_set": bool(self.alert_webhook),
            "webhook_secret_set": bool(self.webhook_secret),
            "canonical_store": "file",
        }


class LiveBlocked(RuntimeError):
    pass
