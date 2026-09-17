"""External I/O adapters. Dry-run uses recording fakes — no email, SMS, deploy, or alert HTTP."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from config import Config
from models import Lead

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_GET_URL = "https://places.googleapis.com/v1/places/{place_id}"
BLAND_SMS_URL = "https://api.bland.ai/v1/sms/send"
VERCEL_DEPLOY_URL = "https://api.vercel.com/v13/deployments"
VERCEL_DELETE_URL = "https://api.vercel.com/v13/deployments/{id}"
PLACES_FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.nationalPhoneNumber",
        "places.internationalPhoneNumber",
        "places.websiteUri",
        "places.regularOpeningHours",
        "places.types",
        "places.rating",
        "places.reviews",
        "places.location",
        "places.photos",
    ]
)


class ExternalSendError(RuntimeError):
    pass


def _http_json(
    url: str,
    payload: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    method: str = "POST",
    timeout: int = 30,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            data = json.loads(raw)
            return data if isinstance(data, dict) else {"data": data}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise ExternalSendError(f"HTTP {exc.code} {url.split('?', 1)[0]}: {detail}") from exc


# --- Places -----------------------------------------------------------------


def listing_from_place(place: dict[str, Any], geo: str = "US") -> dict[str, Any]:
    name_obj = place.get("displayName") or {}
    hours_obj = place.get("regularOpeningHours") or {}
    weekday = hours_obj.get("weekdayDescriptions") or []
    reviews = []
    for row in place.get("reviews") or []:
        text = (row.get("text") or {}).get("text") or row.get("originalText", {}).get("text") or ""
        if text:
            reviews.append(str(text).strip()[:400])
    types = [t.replace("_", " ") for t in (place.get("types") or []) if t not in {"point_of_interest", "establishment"}]
    photos = []
    for photo in (place.get("photos") or [])[:4]:
        name = photo.get("name") or ""
        if name:
            photos.append(name)
    address = place.get("formattedAddress") or ""
    neighborhood = ""
    if address:
        parts = [p.strip() for p in address.split(",") if p.strip()]
        if len(parts) >= 2:
            neighborhood = parts[-3] if len(parts) >= 3 else parts[0]
    return {
        "place_id": place.get("id") or "",
        "name": name_obj.get("text") or place.get("name") or "",
        "address": address,
        "phone": place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber") or "",
        "hours": "; ".join(weekday),
        "category": types[0] if types else "",
        "rating": place.get("rating"),
        "reviews": reviews,
        "photos": photos,
        "neighborhood": neighborhood,
        "website": (place.get("websiteUri") or "").strip(),
        "geo": geo,
        "email": (place.get("email") or "").strip(),
    }


class FakePlaces:
    def __init__(self, listings: list[dict[str, Any]] | None = None) -> None:
        self.listings = list(listings or [])
        self.calls: list[dict[str, Any]] = []

    def search(self, geo: str, category: str, limit: int) -> list[dict[str, Any]]:
        self.calls.append({"op": "search", "geo": geo, "category": category, "limit": limit})
        out = []
        for row in self.listings:
            if category:
                blob = " ".join(
                    [
                        str(row.get("category") or ""),
                        str(row.get("name") or ""),
                        " ".join(row.get("types") or []),
                    ]
                ).lower()
                if category.lower() not in blob:
                    continue
            if geo and geo.upper() != "US":
                hay = f"{row.get('address') or ''} {row.get('geo') or ''} {row.get('neighborhood') or ''}"
                if geo.lower() not in hay.lower() and row.get("geo", "US").upper() != geo.upper():
                    continue
            out.append(dict(row))
            if len(out) >= limit:
                break
        return out

    def website_for(self, place_id: str) -> str:
        self.calls.append({"op": "website_for", "place_id": place_id})
        for row in self.listings:
            if row.get("place_id") == place_id:
                return str(row.get("website") or "").strip()
        return ""


class LivePlaces:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ExternalSendError("GOOGLE_PLACES_API_KEY is not set")
        self.api_key = api_key

    def search(self, geo: str, category: str, limit: int) -> list[dict[str, Any]]:
        query = " ".join(p for p in [category or "small business", "in", geo or "US"] if p)
        payload = {
            "textQuery": query,
            "maxResultCount": max(1, min(int(limit), 20)),
            "regionCode": "US",
        }
        data = _http_json(
            PLACES_SEARCH_URL,
            payload,
            headers={
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": PLACES_FIELD_MASK,
            },
        )
        places = data.get("places") or []
        return [listing_from_place(p, geo=geo) for p in places if isinstance(p, dict)]

    def website_for(self, place_id: str) -> str:
        url = PLACES_GET_URL.format(place_id=urllib.parse.quote(place_id, safe=""))
        data = _http_json(
            url,
            None,
            headers={
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": "websiteUri",
            },
            method="GET",
        )
        return str(data.get("websiteUri") or "").strip()


# --- Email / Bland / Vercel / Alert ----------------------------------------


@dataclass
class RecordingEmailer:
    sends: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = True

    def send(self, *, to: str, subject: str, body: str, lead_id: str) -> dict[str, Any]:
        row = {
            "channel": "email",
            "to": to,
            "subject": subject,
            "body": body,
            "lead_id": lead_id,
            "dry_run": self.dry_run,
            "sent": not self.dry_run,
        }
        self.sends.append(row)
        return row


class LiveEmailer:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.sends: list[dict[str, Any]] = []

    def send(self, *, to: str, subject: str, body: str, lead_id: str) -> dict[str, Any]:
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["From"] = f"{self.cfg.sender_name} <{self.cfg.from_email}>"
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            if self.cfg.smtp_user:
                smtp.login(self.cfg.smtp_user, self.cfg.smtp_pass)
            smtp.send_message(msg)
        row = {
            "channel": "email",
            "to": to,
            "subject": subject,
            "body": body,
            "lead_id": lead_id,
            "dry_run": False,
            "sent": True,
        }
        self.sends.append(row)
        return row


@dataclass
class RecordingBland:
    sends: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = True

    def send_sms(self, *, to: str, body: str, lead_id: str, consent: bool) -> dict[str, Any]:
        if not consent:
            raise ExternalSendError("refusing SMS: no stored consent")
        row = {
            "channel": "sms",
            "to": to,
            "body": body,
            "lead_id": lead_id,
            "dry_run": self.dry_run,
            "sent": not self.dry_run,
            # never include agent id
        }
        self.sends.append(row)
        return row


class LiveBland:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.sends: list[dict[str, Any]] = []

    def send_sms(self, *, to: str, body: str, lead_id: str, consent: bool) -> dict[str, Any]:
        if not consent:
            raise ExternalSendError("refusing SMS: no stored consent")
        if not self.cfg.bland_api_key:
            raise ExternalSendError("BLAND_API_KEY is not set")
        payload: dict[str, Any] = {
            "user_number": to,
            "agent_message": body,
            "channel": "sms",
            "metadata": {"lead_id": lead_id, "workflow": "demo-site-outreach"},
        }
        if self.cfg.bland_from_number:
            payload["agent_number"] = self.cfg.bland_from_number
        if self.cfg.bland_agent_id:
            payload["persona_id"] = self.cfg.bland_agent_id
        if self.cfg.webhook_public_url:
            payload["webhook"] = self.cfg.webhook_public_url
        data = _http_json(
            BLAND_SMS_URL,
            payload,
            headers={"authorization": self.cfg.bland_api_key},
        )
        row = {
            "channel": "sms",
            "to": to,
            "body": body,
            "lead_id": lead_id,
            "dry_run": False,
            "sent": True,
            "conversation_id": ((data.get("data") or {}).get("conversation_id")),
        }
        self.sends.append(row)
        return row


@dataclass
class RecordingVercel:
    deploys: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = True

    def deploy(self, *, lead: Lead, files: dict[str, str]) -> dict[str, str]:
        url = f"https://dry-run.invalid/{lead.id}"
        row = {
            "lead_id": lead.id,
            "url": url,
            "deployment_id": f"dryrun_{lead.id}",
            "files": sorted(files),
            "dry_run": self.dry_run,
            "sent": False,
        }
        self.deploys.append(row)
        return {"url": url, "deployment_id": row["deployment_id"]}

    def delete(self, deployment_id: str) -> dict[str, Any]:
        row = {"op": "delete", "deployment_id": deployment_id, "dry_run": self.dry_run}
        self.deploys.append(row)
        return row


class LiveVercel:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.deploys: list[dict[str, Any]] = []

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.cfg.vercel_token}"}
        if self.cfg.vercel_team_id:
            headers["X-Vercel-Team-Id"] = self.cfg.vercel_team_id
        return headers

    def deploy(self, *, lead: Lead, files: dict[str, str]) -> dict[str, str]:
        if not self.cfg.vercel_token:
            raise ExternalSendError("VERCEL_TOKEN is not set")
        slug = "demo-" + "".join(ch.lower() if ch.isalnum() else "-" for ch in lead.name)[:40].strip("-")
        payload = {
            "name": slug or f"demo-{lead.id[:12]}",
            "files": [{"file": name, "data": content} for name, content in files.items()],
            "projectSettings": {"framework": None},
            "target": "preview",
        }
        data = _http_json(VERCEL_DEPLOY_URL, payload, headers=self._headers(), timeout=60)
        url = data.get("url") or data.get("alias") or ""
        if url and not str(url).startswith("http"):
            url = "https://" + str(url)
        dep_id = str(data.get("id") or data.get("deploymentId") or "")
        self.deploys.append({"lead_id": lead.id, "url": url, "deployment_id": dep_id, "dry_run": False})
        return {"url": str(url), "deployment_id": dep_id}

    def delete(self, deployment_id: str) -> dict[str, Any]:
        url = VERCEL_DELETE_URL.format(id=urllib.parse.quote(deployment_id, safe=""))
        if self.cfg.vercel_team_id:
            url += "?" + urllib.parse.urlencode({"teamId": self.cfg.vercel_team_id})
        data = _http_json(url, None, headers=self._headers(), method="DELETE")
        return {"op": "delete", "deployment_id": deployment_id, "response": data}


@dataclass
class RecordingAlerter:
    alerts: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = True

    def send(self, *, title: str, body: str, handoff: dict[str, Any]) -> dict[str, Any]:
        row = {
            "title": title,
            "body": body,
            "handoff": handoff,
            "dry_run": self.dry_run,
            "sent": not self.dry_run,
            "channel": "chairman-alert",
        }
        self.alerts.append(row)
        return row


class LiveAlerter:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.alerts: list[dict[str, Any]] = []

    def send(self, *, title: str, body: str, handoff: dict[str, Any]) -> dict[str, Any]:
        row: dict[str, Any] = {
            "title": title,
            "body": body,
            "handoff": handoff,
            "dry_run": False,
            "sent": False,
            "channel": "chairman-alert",
        }
        if self.cfg.alert_webhook:
            _http_json(
                self.cfg.alert_webhook,
                {"title": title, "body": body, "handoff": handoff, "priority": "max"},
            )
            row["sent"] = True
        self.alerts.append(row)
        return row


@dataclass
class Adapters:
    places: Any
    emailer: Any
    bland: Any
    vercel: Any
    alerter: Any


def build_adapters(cfg: Config, places: Any = None) -> Adapters:
    """Dry-run: recording send adapters. Live: real HTTP/SMTP. Places may be live even in dry-run."""
    if places is None:
        if cfg.fixture_path and cfg.fixture_path.is_file():
            listings = json.loads(cfg.fixture_path.read_text(encoding="utf-8"))
            if isinstance(listings, dict):
                listings = listings.get("listings") or listings.get("places") or []
            places = FakePlaces(listings)
        elif cfg.dry_run or not cfg.places_api_key:
            places = FakePlaces([])
        else:
            places = LivePlaces(cfg.places_api_key)
    if cfg.dry_run:
        return Adapters(
            places=places,
            emailer=RecordingEmailer(dry_run=True),
            bland=RecordingBland(dry_run=True),
            vercel=RecordingVercel(dry_run=True),
            alerter=RecordingAlerter(dry_run=True),
        )
    return Adapters(
        places=places,
        emailer=LiveEmailer(cfg),
        bland=LiveBland(cfg),
        vercel=LiveVercel(cfg),
        alerter=LiveAlerter(cfg),
    )
