"""Daily intake + two-phase outreach state machine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from adapters import Adapters, ExternalSendError, build_adapters
from config import Config, LiveBlocked
from outreach_copy import (
    ab_mode,
    assign_sms_variant,
    parse_inbound,
    render_sms,
    render_voice_task,
    variant_b_approved,
    variant_of,
    variant_sendable,
)
from intake import harvest_set_text, lead_from_set
from models import (
    CALL_ELIGIBLE_STATES,
    COPY_VERSION,
    NEVER_RECONTACT_STATES,
    REPLY_WINDOW_HOURS,
    SMS_ELIGIBLE_STATES,
    SMS_VARIANT_IDS,
    Lead,
)
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
        report = self.variant_report()
        return {
            "created": created,
            "skipped": skipped,
            "needs_info": needs_info,
            "organized": organized,
            "counts": self.store.counts(),
            "variant_report": report,
        }

    def weekly_pass(self) -> dict[str, Any]:
        """Alias kept so existing harness calls and tests keep working."""
        return self.daily_pass()

    # --- phase 1 SMS --------------------------------------------------------

    def send_sms(self, lead: Lead) -> dict[str, Any]:
        self.cfg.require_live_sends()
        stored = self.store.get(lead.id)
        if stored is not None:
            lead = stored
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
        if self.store.phone_already_assigned(lead):
            return {
                "lead_id": lead.id,
                "skipped": True,
                "state": lead.state,
                "reason": "duplicate-phone",
                "sms_variant_id": variant_of(lead),
                "ab_mode": ab_mode(),
            }
        mode = self._ensure_sms_variant(lead)
        variant = variant_of(lead)
        if not variant_sendable(variant):
            return {
                "lead_id": lead.id,
                "skipped": True,
                "state": lead.state,
                "reason": "variant_not_approved",
                "sms_variant_id": variant,
                "ab_mode": mode,
            }
        body = render_sms(lead)
        sent = dict(self.adapters.bland.send_sms(to=lead.phone, body=body, lead_id=lead.id))
        sent["sms_variant_id"] = variant
        sent["ab_mode"] = mode
        self.store.record_outbox(sent)
        if not self.cfg.dry_run:
            self.store.increment_sms()
        lead.state = "sms_sent"
        lead.sms_sent_at = self._iso()
        lead.last_contact_at = lead.sms_sent_at
        lead.events.append(
            {
                "at": lead.sms_sent_at,
                "op": "sms_sent",
                "dry_run": self.cfg.dry_run,
                "variant": variant,
                "ab_mode": mode,
            }
        )
        self.store.put(lead)
        return {
            "lead_id": lead.id,
            "state": lead.state,
            "sms": sent,
            "sms_variant_id": variant,
            "ab_mode": mode,
        }

    def _ensure_sms_variant(self, lead: Lead) -> str:
        """Assign an arm once. An existing id is left untouched."""
        if variant_of(lead):
            return ab_mode()
        variant, mode = assign_sms_variant(lead.id)
        lead.sms_variant_id = variant
        lead.sms_variant_assigned_at = self._iso()
        lead.events.append(
            {
                "at": lead.sms_variant_assigned_at,
                "op": "sms_variant_assigned",
                "variant": variant,
                "ab_mode": mode,
                "copy_version": lead.copy_version,
            }
        )
        self.store.put(lead)
        return mode

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
        stored = self.store.get(lead.id)
        if stored is not None:
            lead = stored
        parsed = parse_inbound(text)
        if parsed["opt_out"]:
            return self._kill(lead, "declined", "opt-out", reply_quality="stop_angry")
        if parsed["declined"]:
            return self._kill(lead, "declined", "declined", reply_quality="not_now")
        quality = "interested" if parsed["interested"] else "other"
        lead.responded_at = self._iso()
        lead.reply_quality = quality
        lead.events.append(
            {
                "at": lead.responded_at,
                "op": "responded",
                "text": str(parsed["text"])[:400],
                "reply_quality": quality,
                "sms_variant_id": variant_of(lead),
            }
        )
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
        lead.reply_quality = "interested"
        lead.callback = {
            "status": "scheduled" if callback_at else "requested",
            "with": "Chris",
            "at": callback_at or "",
            "note": note[:400],
            "requested_at": self._iso(),
        }
        lead.events.append(
            {
                "at": self._iso(),
                "op": "interested",
                "reply_quality": "interested",
                "sms_variant_id": variant_of(lead),
                "callback": dict(lead.callback),
            }
        )
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

    def _kill(self, lead: Lead, state: str, reason: str, *, reply_quality: str = "") -> dict[str, Any]:
        lead.state = state
        lead.suppression_reason = reason
        event: dict[str, Any] = {"at": self._iso(), "op": state, "reason": reason}
        if reply_quality:
            lead.reply_quality = reply_quality
            event["reply_quality"] = reply_quality
            event["sms_variant_id"] = variant_of(lead)
        lead.events.append(event)
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
        report = self.variant_report()
        intake["variant_report"] = report
        return {
            "dry_run": self.cfg.dry_run,
            "intake": intake,
            "sms": sms,
            "calls": calls,
            "interested": interested,
            "counts": self.store.counts(),
            "outbox": len(self.store.outbox()),
            "alerts": len(self.store.alerts()),
            "variant_report": report,
        }

    def variant_report(self) -> dict[str, Any]:
        """Per-arm counters for the #718 daily / sms-batch report.

        Sends are leads with sms_sent_at. Replies are inbound outcomes
        (interested, not_now, stop_angry, other). no_reply is derived
        for sends whose 72h window has closed with no reply — state is
        not changed. reply_rate_72h is replies inside the window divided
        by matured sends, or null when no send has matured. Both arms
        are always present.
        """
        now = self._now()
        today = now.strftime("%Y-%m-%d")
        window = timedelta(hours=REPLY_WINDOW_HOURS)
        sends_today = {arm: 0 for arm in SMS_VARIANT_IDS}
        cumulative: dict[str, dict[str, Any]] = {}
        for arm in SMS_VARIANT_IDS:
            cumulative[arm] = {
                "sends": 0,
                "replies": 0,
                "interested": 0,
                "not_now": 0,
                "stop_angry": 0,
                "no_reply": 0,
                "matured": 0,
                "replies_within_72h": 0,
                "reply_rate_72h": None,
            }
        for lead in self.store.all_leads():
            variant = variant_of(lead)
            if variant not in cumulative or not lead.sms_sent_at:
                continue
            bucket = cumulative[variant]
            bucket["sends"] += 1
            if str(lead.sms_sent_at)[:10] == today:
                sends_today[variant] += 1
            sent_at = _parse_stamp(lead.sms_sent_at)
            reply_at = _reply_at(lead)
            quality = _reply_quality(lead)
            if quality in {"interested", "not_now", "stop_angry", "other"}:
                bucket["replies"] += 1
                if quality in {"interested", "not_now", "stop_angry"}:
                    bucket[quality] += 1
            in_window = False
            if (
                quality
                and reply_at is not None
                and sent_at is not None
                and reply_at <= sent_at + window
            ):
                in_window = True
                bucket["replies_within_72h"] += 1
            matured = sent_at is not None and sent_at + window <= now
            if matured:
                bucket["matured"] += 1
                if not in_window and not quality:
                    bucket["no_reply"] += 1
        for bucket in cumulative.values():
            matured = int(bucket["matured"])
            if matured:
                bucket["reply_rate_72h"] = round(bucket["replies_within_72h"] / matured, 4)
        return {
            "ab_mode": ab_mode(),
            "variant_b_approved": variant_b_approved(),
            "reply_window_hours": REPLY_WINDOW_HOURS,
            "copy_version": COPY_VERSION,
            "sends_today": sends_today,
            "cumulative": cumulative,
        }


_REPLY_QUALITIES = frozenset({"interested", "not_now", "stop_angry", "other"})


def _parse_stamp(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _is_reply_event(event: object) -> bool:
    if not isinstance(event, dict):
        return False
    if str(event.get("reply_quality") or "") in _REPLY_QUALITIES:
        return True
    op = event.get("op")
    if op in {"responded", "interested"}:
        return True
    return op == "declined" and event.get("reason") in {"opt-out", "declined"}


def _reply_at(lead: Lead) -> Optional[datetime]:
    earliest: Optional[datetime] = None
    for event in lead.events:
        if not _is_reply_event(event):
            continue
        stamp = _parse_stamp(event.get("at"))
        if stamp is not None and (earliest is None or stamp < earliest):
            earliest = stamp
    return earliest


def _reply_quality(lead: Lead) -> str:
    stored = str(lead.reply_quality or "")
    if stored in _REPLY_QUALITIES:
        return stored
    found = ""
    for event in lead.events:
        if not isinstance(event, dict):
            continue
        quality = str(event.get("reply_quality") or "")
        if quality in _REPLY_QUALITIES:
            found = quality
            continue
        op = event.get("op")
        if op == "interested":
            found = "interested"
        elif op == "declined" and event.get("reason") == "opt-out":
            found = "stop_angry"
        elif op == "declined" and event.get("reason") == "declined":
            found = "not_now"
        elif op == "responded" and not found:
            found = "other"
    return found


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
