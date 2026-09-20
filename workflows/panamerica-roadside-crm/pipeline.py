"""Daily intake + two-phase outreach state machine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from adapters import Adapters, ExternalSendError, build_adapters
from config import Config, LiveBlocked
from outreach_copy import parse_inbound, render_sms, render_voice_task
from intake import harvest_set_text, lead_from_set
from models import CALL_ELIGIBLE_STATES, NEVER_RECONTACT_STATES, SMS_ELIGIBLE_STATES, Lead
from store import FileStore, utc_now


class PipelineError(RuntimeError):
    pass


class Pipeline:
    def __init__(self, cfg: Config, store: FileStore, adapters: Adapters) -> None:
        self.cfg = cfg
        self.store = store
        self.adapters = adapters

    def _now(self) -> datetime:
        stamp = self.cfg.clock()
        if stamp.tzinfo is None:
            return stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc)

    def _iso(self, when: Optional[datetime] = None) -> str:
        return (when or self._now()).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- daily intake -------------------------------------------------------

    def daily_pass(self) -> dict[str, Any]:
        organized: list[dict[str, Any]] = []
        organize = getattr(self.adapters.drive, "organize_root", None)
        if callable(organize):
            organized = list(organize(geocoder=getattr(self.adapters, "geocoder", None)) or [])
        created: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        needs_info: list[dict[str, Any]] = []
        for photo_set in self.adapters.drive.list_photo_sets():
            if not photo_set.id or photo_set.name.upper().startswith("HOW TO"):
                continue
            if self.store.folder_processed(photo_set.id) or self.store.find_by_folder(photo_set.id):
                skipped.append({"folder_id": photo_set.id, "reason": "already processed"})
                continue
            text, vehicle_text = harvest_set_text(photo_set, self.adapters.ocr)
            lead = lead_from_set(photo_set, text, vehicle_text=vehicle_text)
            lead.created_at = self._iso()
            if lead.phone:
                existing = self.store.find_by_phone(lead.phone)
                if existing is not None:
                    self.store.mark_folder_processed(photo_set.id, existing.id, "duplicate-phone")
                    existing.events.append(
                        {
                            "at": self._iso(),
                            "op": "duplicate_skip",
                            "folder_id": photo_set.id,
                            "folder_name": photo_set.name,
                        }
                    )
                    if photo_set.photos:
                        existing.photos.extend(lead.photos)
                    self.store.put(existing)
                    skipped.append(
                        {
                            "folder_id": photo_set.id,
                            "reason": "duplicate-phone",
                            "phone": lead.phone,
                            "existing_id": existing.id,
                        }
                    )
                    continue
                if self.store.is_suppressed("phone:" + lead.phone):
                    self.store.mark_folder_processed(photo_set.id, "", "suppressed-phone")
                    skipped.append(
                        {"folder_id": photo_set.id, "reason": "suppressed-phone", "phone": lead.phone}
                    )
                    continue
            lead.events.append({"at": self._iso(), "op": "intake", "state": lead.state})
            self.store.put(lead)
            self.store.mark_folder_processed(photo_set.id, lead.id, lead.state)
            row = {"lead_id": lead.id, "state": lead.state, "phone": lead.phone, "folder_id": photo_set.id}
            if lead.state == "needs-info":
                needs_info.append(row)
            else:
                created.append(row)
        return {
            "created": created,
            "skipped": skipped,
            "needs_info": needs_info,
            "organized": organized,
            "counts": self.store.counts(),
        }

    def weekly_pass(self) -> dict[str, Any]:
        """Alias kept so existing harness calls and tests keep working."""
        return self.daily_pass()

    # --- phase 1 SMS --------------------------------------------------------

    def send_sms(self, lead: Lead) -> dict[str, Any]:
        self.cfg.require_live_sends()
        if lead.state in NEVER_RECONTACT_STATES:
            return {"lead_id": lead.id, "skipped": True, "state": lead.state, "reason": f"state={lead.state}"}
        if lead.state not in SMS_ELIGIBLE_STATES:
            return {"lead_id": lead.id, "skipped": True, "state": lead.state, "reason": "not sms-eligible"}
        if not lead.phone:
            return {"lead_id": lead.id, "skipped": True, "reason": "no phone"}
        if self.store.is_suppressed(*self.store.suppression_keys_for(lead)):
            return self._kill(lead, "declined", "suppression list")
        if self.store.sms_sent_today() >= self.cfg.daily_sms_cap:
            raise PipelineError("SMS refused: daily cap")
        body = render_sms(lead)
        sent = self.adapters.bland.send_sms(to=lead.phone, body=body, lead_id=lead.id)
        self.store.record_outbox(sent)
        if not self.cfg.dry_run:
            self.store.increment_sms()
        lead.state = "sms_sent"
        lead.sms_sent_at = self._iso()
        lead.last_contact_at = lead.sms_sent_at
        lead.events.append({"at": lead.sms_sent_at, "op": "sms_sent", "dry_run": self.cfg.dry_run})
        self.store.put(lead)
        return {"lead_id": lead.id, "state": lead.state, "sms": sent}

    def sms_batch(self) -> list[dict[str, Any]]:
        return [self.send_sms(lead) for lead in self.store.by_state("new")]

    # --- phase 2 voice ------------------------------------------------------

    def _sms_age_days(self, lead: Lead) -> Optional[float]:
        stamp = lead.sms_sent_at or lead.last_contact_at
        if not stamp:
            return None
        try:
            when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        return (self._now() - when).total_seconds() / 86400.0

    def due_for_call(self, lead: Lead) -> bool:
        if lead.state not in CALL_ELIGIBLE_STATES:
            return False
        if lead.state in NEVER_RECONTACT_STATES or not lead.phone:
            return False
        if self.store.is_suppressed(*self.store.suppression_keys_for(lead)):
            return False
        age = self._sms_age_days(lead)
        if age is None:
            return False
        return age >= float(self.cfg.voice_delay_min_days)

    def start_call(self, lead: Lead) -> dict[str, Any]:
        self.cfg.require_live_sends()
        if lead.state in NEVER_RECONTACT_STATES:
            return {"lead_id": lead.id, "skipped": True, "state": lead.state, "reason": f"state={lead.state}"}
        if not self.due_for_call(lead):
            return {"lead_id": lead.id, "skipped": True, "state": lead.state, "reason": "not due for call"}
        if lead.call_attempted_at:
            return {"lead_id": lead.id, "skipped": True, "reason": "already called"}
        task = render_voice_task(lead)
        sent = self.adapters.bland.start_call(to=lead.phone, task=task, lead_id=lead.id)
        self.store.record_outbox(sent)
        lead.state = "call_attempted"
        lead.call_attempted_at = self._iso()
        lead.last_contact_at = lead.call_attempted_at
        lead.events.append({"at": lead.call_attempted_at, "op": "call_attempted", "dry_run": self.cfg.dry_run})
        self.store.put(lead)
        return {"lead_id": lead.id, "state": lead.state, "call": sent}

    def call_batch(self) -> list[dict[str, Any]]:
        results = []
        for lead in self.store.all_leads():
            if self.due_for_call(lead):
                results.append(self.start_call(lead))
        return results

    # --- inbound / outcomes -------------------------------------------------

    def ingest_text(self, text: str, *, lead: Optional[Lead] = None, phone: str = "") -> dict[str, Any]:
        if lead is None and phone:
            lead = self.store.find_by_phone(phone)
        if lead is None:
            return {"ignored": True, "reason": "unknown lead"}
        parsed = parse_inbound(text)
        if parsed["opt_out"] or parsed["declined"]:
            reason = "opt-out" if parsed["opt_out"] else "declined"
            return self._kill(lead, "declined", reason)
        lead.responded_at = self._iso()
        lead.events.append({"at": lead.responded_at, "op": "responded", "text": str(parsed["text"])[:400]})
        if parsed["interested"]:
            return self.mark_interested(lead, note=str(parsed["text"]))
        if lead.state in NEVER_RECONTACT_STATES:
            self.store.put(lead)
            return {"lead_id": lead.id, "state": lead.state}
        lead.state = "responded"
        self.store.put(lead)
        return {"lead_id": lead.id, "state": lead.state, "interested": False}

    def mark_interested(self, lead: Lead, *, note: str = "", callback_at: str = "") -> dict[str, Any]:
        if lead.state in NEVER_RECONTACT_STATES:
            return {"lead_id": lead.id, "skipped": True, "state": lead.state, "reason": "terminal"}
        lead.state = "interested"
        lead.callback = {
            "status": "scheduled" if callback_at else "requested",
            "with": "Chris",
            "at": callback_at or "",
            "note": note[:400],
            "requested_at": self._iso(),
        }
        lead.events.append({"at": self._iso(), "op": "interested", "callback": dict(lead.callback)})
        self.store.put(lead)
        alert = self.adapters.alerter.send(
            title=f"ROADSIDE INTEREST: {lead.car_label()} {lead.phone}",
            body=(
                f"{lead.contact_name or 'Owner'} of {lead.car_label()} spotted on "
                f"{lead.location or 'roadside'} wants a callback with Chris.\n"
                f"Phone {lead.phone}. Note: {note or 'expressed interest'}"
            ),
            payload={
                "lead_id": lead.id,
                "phone": lead.phone,
                "car": lead.car_label(),
                "location": lead.location,
                "callback": lead.callback,
            },
        )
        self.store.record_alert(alert)
        return {
            "lead_id": lead.id,
            "state": lead.state,
            "callback": lead.callback,
            "alert": {"title": alert["title"], "dry_run": alert.get("dry_run"), "sent": alert.get("sent")},
        }

    def _kill(self, lead: Lead, state: str, reason: str) -> dict[str, Any]:
        lead.state = state
        lead.suppression_reason = reason
        lead.events.append({"at": self._iso(), "op": state, "reason": reason})
        self.store.suppress(self.store.suppression_keys_for(lead), reason)
        self.store.put(lead)
        return {"lead_id": lead.id, "state": state, "reason": reason}

    # --- e2e ----------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        intake = self.daily_pass()
        sms = self.sms_batch()
        calls: list[dict[str, Any]] = []
        interested: list[dict[str, Any]] = []
        if self.cfg.simulate_replies:
            for lead in list(self.store.by_state("sms_sent")):
                body = "yes let's talk" if self.cfg.simulate_interest else "ok"
                result = self.ingest_text(body, lead=lead)
                if result.get("state") == "interested":
                    interested.append(result)
        else:
            # Advance due calls without inventing replies.
            # Tests freeze clock past the 5–7 day window when they want Phase 2.
            calls = self.call_batch()
        return {
            "dry_run": self.cfg.dry_run,
            "intake": intake,
            "sms": sms,
            "calls": calls,
            "interested": interested,
            "counts": self.store.counts(),
            "outbox": len(self.store.outbox()),
            "alerts": len(self.store.alerts()),
        }


def make_pipeline(
    cfg: Config,
    *,
    drive: Any = None,
    ocr: Any = None,
    bland: Any = None,
    geocoder: Any = None,
) -> Pipeline:
    store = FileStore(cfg.store_path)
    adapters = build_adapters(cfg, drive=drive, ocr=ocr, bland=bland, geocoder=geocoder)
    return Pipeline(cfg, store, adapters)


def public_error(exc: BaseException) -> dict[str, str]:
    if isinstance(exc, (PipelineError, LiveBlocked, ExternalSendError)):
        return {"error": str(exc)}
    return {"error": exc.__class__.__name__}


def shift_clock(base: datetime, days: float) -> datetime:
    return base + timedelta(days=days)
