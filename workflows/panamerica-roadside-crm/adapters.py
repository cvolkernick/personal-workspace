"""External I/O adapters. Dry-run uses recording fakes — no SMS, voice, or alert HTTP."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from config import Config
from phones import e164

BLAND_SMS_URL = "https://api.bland.ai/v1/sms/send"
BLAND_CALL_URL = "https://api.bland.ai/v1/calls"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"


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


# --- Drive intake -----------------------------------------------------------


@dataclass
class PhotoSet:
    id: str
    name: str
    photos: list[dict[str, str]]
    sidecar_text: str = ""
    ocr: dict[str, str] = field(default_factory=dict)


class FakeDrive:
    def __init__(self, sets: list[dict[str, Any]] | None = None) -> None:
        self.sets = [PhotoSet(
            id=str(row.get("id") or ""),
            name=str(row.get("name") or ""),
            photos=[dict(p) for p in (row.get("photos") or [])],
            sidecar_text=str(row.get("sidecar_text") or ""),
            ocr={str(k): str(v) for k, v in (row.get("ocr") or {}).items()},
        ) for row in (sets or [])]
        self.calls: list[dict[str, Any]] = []

    def list_photo_sets(self) -> list[PhotoSet]:
        self.calls.append({"op": "list_photo_sets"})
        return list(self.sets)


class LiveDrive:
    def __init__(self, token: str, folder_id: str) -> None:
        if not token:
            raise ExternalSendError("GOOGLE_DRIVE_ACCESS_TOKEN is not set")
        if not folder_id:
            raise ExternalSendError("PANAMERICA_ROADSIDE_DRIVE_FOLDER_ID is not set")
        self.token = token
        self.folder_id = folder_id

    def list_photo_sets(self) -> list[PhotoSet]:
        children = self._list_children(self.folder_id)
        sets: list[PhotoSet] = []
        loose_photos: list[dict[str, str]] = []
        loose_text: list[str] = []
        for child in children:
            mime = child.get("mimeType") or ""
            name = child.get("name") or ""
            if mime == "application/vnd.google-apps.folder":
                files = self._list_children(child["id"])
                photos, sidecar = _split_files(files)
                if photos or sidecar:
                    sets.append(PhotoSet(id=child["id"], name=name, photos=photos, sidecar_text=sidecar))
            elif _is_image(mime, name):
                loose_photos.append(_photo_row(child))
            elif _is_text(mime, name) and not name.upper().startswith("HOW TO"):
                loose_text.append(name)
        if loose_photos:
            sets.append(
                PhotoSet(
                    id="root-unsorted",
                    name="_unsorted",
                    photos=loose_photos,
                    sidecar_text="\n".join(loose_text),
                )
            )
        return sets

    def _list_children(self, folder_id: str) -> list[dict[str, Any]]:
        q = f"'{folder_id}' in parents and trashed=false"
        params = urllib.parse.urlencode(
            {
                "q": q,
                "fields": "files(id,name,mimeType,webViewLink,createdTime,modifiedTime)",
                "pageSize": "200",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
        )
        data = _http_json(
            f"{DRIVE_FILES_URL}?{params}",
            payload=None,
            headers={"authorization": f"Bearer {self.token}"},
            method="GET",
        )
        files = data.get("files") or data.get("data") or []
        return [row for row in files if isinstance(row, dict)]


def _is_image(mime: str, name: str) -> bool:
    if mime.startswith("image/"):
        return True
    lower = name.lower()
    return lower.endswith((".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif"))


def _is_text(mime: str, name: str) -> bool:
    if mime.startswith("text/") or mime in {
        "application/vnd.google-apps.document",
        "application/json",
    }:
        return True
    lower = name.lower()
    return lower.endswith((".txt", ".md", ".json"))


def _photo_row(file: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(file.get("id") or ""),
        "name": str(file.get("name") or ""),
        "web_view_link": str(file.get("webViewLink") or ""),
        "mime": str(file.get("mimeType") or ""),
    }


def _split_files(files: list[dict[str, Any]]) -> tuple[list[dict[str, str]], str]:
    photos: list[dict[str, str]] = []
    sidecar_bits: list[str] = []
    for row in files:
        mime = str(row.get("mimeType") or "")
        name = str(row.get("name") or "")
        if _is_image(mime, name):
            photos.append(_photo_row(row))
        elif _is_text(mime, name):
            sidecar_bits.append(name)
    return photos, "\n".join(sidecar_bits)


class FakeOcr:
    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self.mapping = dict(mapping or {})
        self.calls: list[str] = []

    def read_text(self, photo: dict[str, str]) -> str:
        key = photo.get("id") or photo.get("name") or ""
        self.calls.append(key)
        return self.mapping.get(key) or self.mapping.get(photo.get("name") or "") or ""


class NullOcr:
    def read_text(self, photo: dict[str, str]) -> str:
        return ""


# --- Bland ------------------------------------------------------------------


@dataclass
class RecordingBland:
    sends: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = True

    def send_sms(self, *, to: str, body: str, lead_id: str) -> dict[str, Any]:
        row = {
            "channel": "sms",
            "to": e164(to) or to,
            "body": body,
            "lead_id": lead_id,
            "dry_run": self.dry_run,
            "sent": not self.dry_run,
        }
        self.sends.append(row)
        return row

    def start_call(self, *, to: str, task: str, lead_id: str) -> dict[str, Any]:
        row = {
            "channel": "voice",
            "to": e164(to) or to,
            "task": task,
            "lead_id": lead_id,
            "dry_run": self.dry_run,
            "sent": not self.dry_run,
        }
        self.calls.append(row)
        return row


class LiveBland:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.sends: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []

    def send_sms(self, *, to: str, body: str, lead_id: str) -> dict[str, Any]:
        if not self.cfg.bland_api_key:
            raise ExternalSendError("BLAND_API_KEY is not set")
        dest = e164(to) or to
        payload: dict[str, Any] = {
            "user_number": dest,
            "agent_message": body,
            "channel": "sms",
            "metadata": {"lead_id": lead_id, "workflow": "panamerica-roadside-crm"},
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
            "to": dest,
            "body": body,
            "lead_id": lead_id,
            "dry_run": False,
            "sent": True,
            "conversation_id": ((data.get("data") or {}).get("conversation_id")),
        }
        self.sends.append(row)
        return row

    def start_call(self, *, to: str, task: str, lead_id: str) -> dict[str, Any]:
        if not self.cfg.bland_api_key:
            raise ExternalSendError("BLAND_API_KEY is not set")
        dest = e164(to) or to
        payload: dict[str, Any] = {
            "phone_number": dest,
            "task": task,
            "metadata": {"lead_id": lead_id, "workflow": "panamerica-roadside-crm"},
        }
        if self.cfg.bland_from_number:
            payload["from"] = self.cfg.bland_from_number
        if self.cfg.bland_agent_id:
            payload["pathway_id"] = self.cfg.bland_agent_id
        if self.cfg.webhook_public_url:
            payload["webhook"] = self.cfg.webhook_public_url
        data = _http_json(
            BLAND_CALL_URL,
            payload,
            headers={"authorization": self.cfg.bland_api_key},
        )
        row = {
            "channel": "voice",
            "to": dest,
            "task": task,
            "lead_id": lead_id,
            "dry_run": False,
            "sent": True,
            "call_id": data.get("call_id") or (data.get("data") or {}).get("call_id"),
        }
        self.calls.append(row)
        return row


@dataclass
class RecordingAlerter:
    alerts: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = True

    def send(self, *, title: str, body: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "title": title,
            "body": body,
            "payload": payload,
            "dry_run": self.dry_run,
            "sent": not self.dry_run,
        }
        self.alerts.append(row)
        return row


class LiveAlerter:
    def __init__(self, webhook: str) -> None:
        self.webhook = webhook
        self.alerts: list[dict[str, Any]] = []

    def send(self, *, title: str, body: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.webhook:
            row = {
                "title": title,
                "body": body,
                "payload": payload,
                "dry_run": False,
                "sent": False,
                "reason": "no alert webhook",
            }
            self.alerts.append(row)
            return row
        _http_json(self.webhook, {"title": title, "body": body, "payload": payload})
        row = {"title": title, "body": body, "payload": payload, "dry_run": False, "sent": True}
        self.alerts.append(row)
        return row


@dataclass
class Adapters:
    drive: Any
    ocr: Any
    bland: Any
    alerter: Any


def build_adapters(
    cfg: Config,
    *,
    drive: Any = None,
    ocr: Any = None,
    bland: Any = None,
) -> Adapters:
    if cfg.dry_run:
        fixture_sets, fixture_ocr = _load_fixture(cfg.fixture_path)
        return Adapters(
            drive=drive or FakeDrive(fixture_sets),
            ocr=ocr or FakeOcr(fixture_ocr),
            bland=bland or RecordingBland(dry_run=True),
            alerter=RecordingAlerter(dry_run=True),
        )
    return Adapters(
        drive=drive or LiveDrive(cfg.google_drive_token, cfg.drive_folder_id),
        ocr=ocr or NullOcr(),
        bland=bland or LiveBland(cfg),
        alerter=LiveAlerter(cfg.alert_webhook),
    )


def _load_fixture(path: Path | None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if path is None or not Path(path).is_file():
        return [], {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    sets = list(data.get("sets") or [])
    ocr: dict[str, str] = {}
    for row in sets:
        for key, value in (row.get("ocr") or {}).items():
            ocr[str(key)] = str(value)
    return sets, ocr
