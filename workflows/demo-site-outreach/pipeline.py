"""Per-lead state machine and harness-facing pipeline steps."""

from __future__ import annotations

from typing import Any, Optional

from adapters import Adapters, ExternalSendError, build_adapters
from config import Config, LiveBlocked
from discovery import (
    apply_discovery,
    parse_inbound,
    render_bow_out_sms,
    render_demo_sms,
    render_discovery_email,
    render_followup_email,
    simulated_interest_reply,
    simulated_reply,
)
from models import TERMINAL_STATES, Lead
from sitegen import generate_site
from sourcing import source_leads
from store import FileStore, utc_now


class PipelineError(RuntimeError):
    pass


class Pipeline:
    def __init__(self, cfg: Config, store: FileStore, adapters: Adapters) -> None:
        self.cfg = cfg
        self.store = store
        self.adapters = adapters

    # --- source -------------------------------------------------------------

    def source(self) -> list[Lead]:
        return source_leads(
            self.store,
            self.adapters.places,
            geo=self.cfg.geo,
            category=self.cfg.category,
            limit=self.cfg.batch_size,
        )

    # --- outreach (email, Touch 1) -----------------------------------------

    def outreach_lead(self, lead: Lead) -> dict[str, Any]:
        self.cfg.require_live_sends()
        if lead.state in TERMINAL_STATES:
            return {
                "lead_id": lead.id,
                "skipped": True,
                "state": lead.state,
                "reason": f"state={lead.state}",
            }
        if self.store.is_suppressed(*self.store.suppression_keys_for(lead)):
            return self._kill(lead, "suppressed", "suppression list")
        website = self.adapters.places.website_for(lead.place_id)
        if website:
            lead.website = website
            return self._kill(lead, "dead", "website appeared before outreach")
        if not lead.email:
            return {"lead_id": lead.id, "skipped": True, "reason": "no email on listing"}
        email = render_discovery_email(
            lead,
            physical_address=self.cfg.physical_address,
            sender_name=self.cfg.sender_name,
        )
        sent = self.adapters.emailer.send(
            to=email["to"], subject=email["subject"], body=email["body"], lead_id=lead.id
        )
        self.store.record_outbox(sent)
        lead.state = "contacted"
        lead.last_contact_at = utc_now()
        lead.events.append({"at": lead.last_contact_at, "op": "email_discovery", "dry_run": self.cfg.dry_run})
        self.store.put(lead)
        return {"lead_id": lead.id, "state": lead.state, "email": sent}

    def outreach_batch(self) -> list[dict[str, Any]]:
        results = []
        for lead in self.store.by_state("sourced"):
            results.append(self.outreach_lead(lead))
        return results

    # --- inbound ------------------------------------------------------------

    def ingest_text(self, text: str, *, lead: Optional[Lead] = None, phone: str = "") -> dict[str, Any]:
        if lead is None and phone:
            lead = self.store.find_by_phone(phone)
        if lead is None:
            return {"ignored": True, "reason": "unknown lead"}
        parsed = parse_inbound(text)
        if parsed["opt_out"] or parsed["not_interested"]:
            reason = "opt-out" if parsed["opt_out"] else "not-interested"
            return self._kill(lead, "suppressed", reason)
        apply_discovery(lead, parsed)

        if lead.state in {"contacted", "discovering"}:
            lead.state = "discovering"
            lead.events.append({"at": utc_now(), "op": "discovery_reply"})
            self.store.put(lead)
            if lead.discovery_complete() and lead.sms_consent:
                return self.build_and_deliver(lead)
            return {"lead_id": lead.id, "state": lead.state, "discovery": dict(lead.discovery), "sms_consent": lead.sms_consent}

        if lead.state in {"demo_sent", "qualifying"}:
            return self._qualify(lead, parsed, text)

        self.store.put(lead)
        return {"lead_id": lead.id, "state": lead.state}

    # --- build + SMS deliver ------------------------------------------------

    def build_and_deliver(self, lead: Lead) -> dict[str, Any]:
        self.cfg.require_live_sends()
        if not lead.discovery_complete():
            raise PipelineError("discovery incomplete")
        lead.state = "building"
        lead.events.append({"at": utc_now(), "op": "building", "template": lead.template_version})
        self.store.put(lead)
        files = generate_site(lead)
        deployed = self.adapters.vercel.deploy(lead=lead, files=files)
        lead.demo_url = deployed["url"]
        lead.vercel_deployment_id = deployed.get("deployment_id") or ""
        sms = self._send_sms(lead, render_demo_sms(lead))
        lead.state = "demo_sent"
        lead.events.append({"at": utc_now(), "op": "demo_sent", "url": lead.demo_url})
        self.store.put(lead)
        return {
            "lead_id": lead.id,
            "state": lead.state,
            "demo_url": lead.demo_url,
            "sms": sms,
            "files": sorted(files),
        }

    def _send_sms(self, lead: Lead, body: str) -> dict[str, Any]:
        if not lead.sms_consent or not lead.sms_number:
            raise PipelineError("SMS refused: no stored consent/number")
        if self.store.is_suppressed(*self.store.suppression_keys_for(lead)):
            raise PipelineError("SMS refused: suppressed")
        if self.store.sms_sent_today() >= self.cfg.daily_sms_cap:
            raise PipelineError("SMS refused: daily cap")
        sent = self.adapters.bland.send_sms(
            to=lead.sms_number, body=body, lead_id=lead.id, consent=lead.sms_consent
        )
        self.store.record_outbox(sent)
        if not self.cfg.dry_run:
            self.store.increment_sms()
        lead.last_contact_at = utc_now()
        return sent

    # --- qualify / handoff --------------------------------------------------

    def _qualify(self, lead: Lead, parsed: dict[str, Any], text: str) -> dict[str, Any]:
        lead.state = "qualifying"
        lead.asked_for = parsed.get("asked_for") or text.strip()[:400]
        self.store.put(lead)
        if not parsed.get("interest"):
            return {"lead_id": lead.id, "state": lead.state, "interest": False}
        return self.handoff(lead)

    def handoff(self, lead: Lead) -> dict[str, Any]:
        self.cfg.require_live_sends()
        bow = render_bow_out_sms(lead)
        sms = None
        if lead.sms_consent and lead.sms_number:
            sms = self._send_sms(lead, bow)
        package = {
            "who": lead.name,
            "business": lead.name,
            "phone": lead.sms_number or lead.phone,
            "email": lead.email,
            "demo_url": lead.demo_url,
            "discovery_summary": lead.two_line_summary(),
            "asked_for": lead.asked_for or "expressed interest",
            "lead_id": lead.id,
        }
        lead.handoff = package
        alert = self.adapters.alerter.send(
            title=f"HOT LEAD: {lead.name}",
            body=(
                f"{lead.name} is interested. Demo: {lead.demo_url}\n"
                f"{package['discovery_summary']}\nAsked: {package['asked_for']}\n"
                f"Phone {package['phone']} · Email {package['email']}"
            ),
            handoff=package,
        )
        self.store.record_alert(alert)
        lead.state = "handed_off"
        lead.events.append({"at": utc_now(), "op": "handed_off"})
        self.store.put(lead)
        return {
            "lead_id": lead.id,
            "state": lead.state,
            "bow_out": bow,
            "sms": sms,
            "handoff": package,
            "alert": {"title": alert["title"], "dry_run": alert.get("dry_run"), "sent": alert.get("sent")},
        }

    # --- negative paths -----------------------------------------------------

    def follow_up_batch(self) -> list[dict[str, Any]]:
        self.cfg.require_live_sends()
        results = []
        for lead in self.store.all_leads():
            if lead.state not in {"contacted", "discovering"}:
                continue
            if self.store.is_suppressed(*self.store.suppression_keys_for(lead)):
                results.append(self._kill(lead, "suppressed", "suppression list"))
                continue
            if lead.follow_ups >= self.cfg.max_follow_ups:
                results.append(self._kill(lead, "dead", "max follow-ups"))
                continue
            n = lead.follow_ups + 1
            if n > self.cfg.max_follow_ups:
                results.append(self._kill(lead, "dead", "max follow-ups"))
                continue
            if not lead.email:
                results.append(self._kill(lead, "dead", "no email for follow-up"))
                continue
            email = render_followup_email(lead, n=n, sender_name=self.cfg.sender_name)
            sent = self.adapters.emailer.send(
                to=email["to"], subject=email["subject"], body=email["body"], lead_id=lead.id
            )
            self.store.record_outbox(sent)
            lead.follow_ups = n
            lead.last_contact_at = utc_now()
            lead.events.append({"at": lead.last_contact_at, "op": "follow_up", "n": n})
            if lead.follow_ups >= self.cfg.max_follow_ups:
                # This was the last allowed follow-up; a later tick marks dead if still silent.
                pass
            self.store.put(lead)
            results.append({"lead_id": lead.id, "follow_ups": lead.follow_ups, "state": lead.state})
        return results

    def _kill(self, lead: Lead, state: str, reason: str) -> dict[str, Any]:
        lead.state = state
        lead.suppression_reason = reason
        lead.events.append({"at": utc_now(), "op": state, "reason": reason})
        if state == "suppressed":
            self.store.suppress(self.store.suppression_keys_for(lead), reason)
        self.store.put(lead)
        return {"lead_id": lead.id, "state": state, "reason": reason}

    # --- e2e ----------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        sourced = self.source()
        outreach = self.outreach_batch()
        built: list[dict[str, Any]] = []
        handed: list[dict[str, Any]] = []
        if self.cfg.simulate_replies:
            ids = [lead.id for lead in sourced]
            if not ids:
                ids = [
                    lead.id
                    for lead in self.store.all_leads()
                    if lead.state in {"sourced", "contacted", "discovering"}
                ]
            for lead_id in ids:
                fresh = self.store.get(lead_id)
                if fresh is None:
                    continue
                result = self.ingest_text(simulated_reply(fresh), lead=fresh)
                built.append(result)
                if self.cfg.simulate_interest and result.get("state") == "demo_sent":
                    fresh = self.store.get(lead_id)
                    if fresh:
                        handed.append(self.ingest_text(simulated_interest_reply(fresh), lead=fresh))
        return {
            "dry_run": self.cfg.dry_run,
            "sourced": [lead.id for lead in sourced],
            "outreach": outreach,
            "built": built,
            "handed_off": handed,
            "counts": self.store.counts(),
            "outbox": len(self.store.outbox()),
            "alerts": len(self.store.alerts()),
        }

    def teardown_expired(self) -> list[dict[str, Any]]:
        # Retention is recorded per lead; actual delete is a live Vercel call.
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.cfg.demo_retention_days)
        results = []
        for lead in self.store.all_leads():
            if not lead.vercel_deployment_id or not lead.demo_url:
                continue
            stamped = None
            for event in reversed(lead.events):
                if event.get("op") == "demo_sent":
                    stamped = event.get("at")
                    break
            if not stamped:
                continue
            try:
                when = datetime.strptime(stamped, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if when > cutoff:
                continue
            deleted = self.adapters.vercel.delete(lead.vercel_deployment_id)
            lead.events.append({"at": utc_now(), "op": "teardown", "dry_run": self.cfg.dry_run})
            self.store.put(lead)
            results.append({"lead_id": lead.id, "teardown": deleted})
        return results


def make_pipeline(cfg: Config, *, places: Any = None) -> Pipeline:
    store = FileStore(cfg.store_path)
    adapters = build_adapters(cfg, places=places)
    return Pipeline(cfg, store, adapters)


def public_error(exc: BaseException) -> dict[str, str]:
    if isinstance(exc, (PipelineError, LiveBlocked, ExternalSendError)):
        return {"error": str(exc)}
    return {"error": exc.__class__.__name__}
