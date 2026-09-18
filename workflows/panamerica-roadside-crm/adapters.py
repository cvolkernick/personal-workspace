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
from geocode import FakeGeocoder, NominatimGeocoder
from organize import PhotoCluster, clusters_from_photos, photo_meta_from_row
from phones import e164

BLAND_SMS_URL = "https://api.bland.ai/v1/sms/send"
BLAND_CALL_URL = "https://api.bland.ai/v1/calls"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"


class ExternalSendError(RuntimeError):
    pass


def _http_request(
    url: str,
    payload: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    method: str = "POST",
    timeout: int = 30,
    json_body: bool = True,
) -> bytes:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    if json_body:
        req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read() or b""
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise ExternalSendError(f"HTTP {exc.code} {url.split('?', 1)[0]}: {detail}") from exc


def _http_json(
    url: str,
    payload: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    method: str = "POST",
    timeout: int = 30,
) -> dict[str, Any]:
    raw = _http_request(url, payload=payload, headers=headers, method=method, timeout=timeout)
    if not raw:
        return {}
    data = json.loads(raw.decode("utf-8") or "{}")
    return data if isinstance(data, dict) else {"data": data}


# --- Drive intake -----------------------------------------------------------


@dataclass
class PhotoSet:
    id: str
    name: str
    photos: list[dict[str, str]]
    sidecar_text: str = ""
    ocr: dict[str, str] = field(default_factory=dict)
    spotted_at: str = ""
    location: str = ""
    date_source: str = ""
    location_source: str = ""
    gps: str = ""
    location_flagged: bool = False


def _photoset_from_row(row: dict[str, Any]) -> PhotoSet:
    gps = row.get("gps") or ""
    if not gps and isinstance(row.get("exif_gps"), dict):
        lat = row["exif_gps"].get("lat")
        lon = row["exif_gps"].get("lon")
        if lat is not None and lon is not None:
            gps = f"{lat},{lon}"
    return PhotoSet(
        id=str(row.get("id") or ""),
        name=str(row.get("name") or ""),
        photos=[dict(p) for p in (row.get("photos") or [])],
        sidecar_text=str(row.get("sidecar_text") or ""),
        ocr={str(k): str(v) for k, v in (row.get("ocr") or {}).items()},
        spotted_at=str(row.get("spotted_at") or ""),
        location=str(row.get("location") or ""),
        date_source=str(row.get("date_source") or ""),
        location_source=str(row.get("location_source") or ""),
        gps=str(gps),
        location_flagged=bool(row.get("location_flagged")),
    )


class FakeDrive:
    def __init__(
        self,
        sets: list[dict[str, Any]] | None = None,
        root_photos: list[dict[str, Any]] | None = None,
    ) -> None:
        self.sets = [_photoset_from_row(row) for row in (sets or [])]
        self.root_photos = [dict(p) for p in (root_photos or [])]
        self.calls: list[dict[str, Any]] = []
        self.moves: list[dict[str, Any]] = []
        self.created_folders: list[dict[str, str]] = []
        self._blobs = {
            str(p.get("id") or ""): p.get("jpeg_bytes") or p.get("bytes")
            for p in self.root_photos
            if p.get("jpeg_bytes") or p.get("bytes")
        }

    def list_photo_sets(self) -> list[PhotoSet]:
        self.calls.append({"op": "list_photo_sets"})
        return list(self.sets)

    def download_bytes(self, file_id: str) -> bytes:
        blob = self._blobs.get(file_id)
        return blob if isinstance(blob, (bytes, bytearray)) else b""

    def organize_root(self, geocoder: Any = None) -> list[dict[str, Any]]:
        self.calls.append({"op": "organize_root"})
        if not self.root_photos:
            return []
        metas = []
        for row in self.root_photos:
            blob = row.get("jpeg_bytes") or row.get("bytes")
            blob_b = blob if isinstance(blob, (bytes, bytearray)) else None
            metas.append(photo_meta_from_row(row, blob=blob_b))
        clusters = clusters_from_photos(metas, geocoder=geocoder)
        organized = _apply_clusters(self, clusters)
        self.root_photos = []
        return organized


def _gps_str(gps: tuple[float, float] | None) -> str:
    if not gps:
        return ""
    return f"{gps[0]:.6f},{gps[1]:.6f}"


def _apply_clusters(drive: FakeDrive, clusters: list[PhotoCluster]) -> list[dict[str, Any]]:
    organized: list[dict[str, Any]] = []
    by_id = {str(row.get("id") or ""): row for row in drive.root_photos}
    for cluster in clusters:
        folder_id = f"folder-{cluster.folder_name}"
        drive.created_folders.append({"id": folder_id, "name": cluster.folder_name})
        ocr: dict[str, str] = {}
        photos: list[dict[str, str]] = []
        for photo in cluster.photos:
            src = by_id.get(photo.id) or {}
            photos.append(
                {
                    "id": photo.id,
                    "name": photo.name,
                    "web_view_link": photo.web_view_link or str(src.get("web_view_link") or ""),
                    "mime": photo.mime or str(src.get("mime") or ""),
                }
            )
            if src.get("ocr_text"):
                ocr[photo.id] = str(src["ocr_text"])
            drive.moves.append(
                {
                    "file_id": photo.id,
                    "name": photo.name,
                    "folder": cluster.folder_name,
                    "folder_id": folder_id,
                    "date_source": photo.date_source,
                }
            )
        drive.sets.append(
            PhotoSet(
                id=folder_id,
                name=cluster.folder_name,
                photos=photos,
                ocr=ocr,
                spotted_at=cluster.spotted_at,
                location=cluster.location,
                date_source=cluster.date_source,
                location_source=cluster.location_source,
                gps=_gps_str(cluster.gps),
                location_flagged=cluster.location_flagged,
            )
        )
        organized.append(
            {
                "folder_id": folder_id,
                "folder_name": cluster.folder_name,
                "photo_ids": [p.id for p in cluster.photos],
                "date_source": cluster.date_source,
                "location_source": cluster.location_source,
                "location": cluster.location,
                "spotted_at": cluster.spotted_at,
            }
        )
    return organized


class LiveDrive:
    def __init__(self, token: str, folder_id: str) -> None:
        if not token:
            raise ExternalSendError("GOOGLE_DRIVE_ACCESS_TOKEN is not set")
        if not folder_id:
            raise ExternalSendError("PANAMERICA_ROADSIDE_DRIVE_FOLDER_ID is not set")
        self.token = token
        self.folder_id = folder_id
        self.moves: list[dict[str, Any]] = []
        self.created_folders: list[dict[str, str]] = []
        self._cluster_meta: dict[str, dict[str, Any]] = {}

    def list_photo_sets(self) -> list[PhotoSet]:
        children = self._list_children(self.folder_id)
        sets: list[PhotoSet] = []
        leftover: list[dict[str, str]] = []
        leftover_text: list[str] = []
        for child in children:
            mime = child.get("mimeType") or ""
            name = child.get("name") or ""
            if mime == "application/vnd.google-apps.folder":
                if name.upper().startswith("HOW TO"):
                    continue
                files = self._list_children(child["id"])
                photos, sidecar = _split_files(files)
                if photos or sidecar:
                    meta = self._cluster_meta.get(child["id"]) or {}
                    sets.append(
                        PhotoSet(
                            id=child["id"],
                            name=name,
                            photos=photos,
                            sidecar_text=sidecar,
                            spotted_at=str(meta.get("spotted_at") or ""),
                            location=str(meta.get("location") or ""),
                            date_source=str(meta.get("date_source") or ""),
                            location_source=str(meta.get("location_source") or ""),
                            gps=str(meta.get("gps") or ""),
                            location_flagged=bool(meta.get("location_flagged")),
                        )
                    )
            elif _is_image(mime, name):
                leftover.append(_photo_row(child))
            elif _is_text(mime, name) and not name.upper().startswith("HOW TO"):
                leftover_text.append(name)
        if leftover:
            # organize_root should have filed these; leftover is a visible fallback.
            sets.append(
                PhotoSet(
                    id="root-unsorted",
                    name="_unsorted",
                    photos=leftover,
                    sidecar_text="\n".join(leftover_text),
                    date_source="upload",
                    location_source="missing",
                    location_flagged=True,
                )
            )
        return sets

    def organize_root(self, geocoder: Any = None) -> list[dict[str, Any]]:
        children = self._list_children(self.folder_id)
        loose = [child for child in children if _is_image(child.get("mimeType") or "", child.get("name") or "")]
        if not loose:
            return []
        existing = {
            child.get("name")
            for child in children
            if (child.get("mimeType") or "") == "application/vnd.google-apps.folder"
        }
        metas = []
        for child in loose:
            row = _photo_row(child)
            blob = None
            if not row.get("exif_datetime") or row.get("exif_lat") is None:
                try:
                    blob = self.download_bytes(row["id"])
                except ExternalSendError:
                    blob = None
            metas.append(photo_meta_from_row(row, blob=blob))
        clusters = clusters_from_photos(metas, geocoder=geocoder)
        organized: list[dict[str, Any]] = []
        for cluster in clusters:
            name = cluster.folder_name
            n = 2
            while name in existing:
                name = f"{cluster.folder_name}-{n}"
                n += 1
            cluster.folder_name = name
            folder = self._create_folder(name)
            existing.add(name)
            self._cluster_meta[folder["id"]] = {
                "spotted_at": cluster.spotted_at,
                "location": cluster.location,
                "date_source": cluster.date_source,
                "location_source": cluster.location_source,
                "gps": _gps_str(cluster.gps),
                "location_flagged": cluster.location_flagged,
            }
            for photo in cluster.photos:
                self._move_file(photo.id, folder["id"])
                self.moves.append(
                    {
                        "file_id": photo.id,
                        "name": photo.name,
                        "folder": name,
                        "folder_id": folder["id"],
                        "date_source": photo.date_source,
                    }
                )
            organized.append(
                {
                    "folder_id": folder["id"],
                    "folder_name": name,
                    "photo_ids": [p.id for p in cluster.photos],
                    "date_source": cluster.date_source,
                    "location_source": cluster.location_source,
                    "location": cluster.location,
                    "spotted_at": cluster.spotted_at,
                }
            )
        return organized

    def download_bytes(self, file_id: str) -> bytes:
        params = urllib.parse.urlencode({"alt": "media", "supportsAllDrives": "true"})
        return _http_request(
            f"{DRIVE_FILES_URL}/{urllib.parse.quote(file_id)}?{params}",
            payload=None,
            headers={"authorization": f"Bearer {self.token}"},
            method="GET",
            json_body=False,
        )

    def _create_folder(self, name: str) -> dict[str, str]:
        data = _http_json(
            f"{DRIVE_FILES_URL}?supportsAllDrives=true",
            payload={
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [self.folder_id],
            },
            headers={"authorization": f"Bearer {self.token}"},
            method="POST",
        )
        folder = {"id": str(data.get("id") or ""), "name": name}
        self.created_folders.append(folder)
        return folder

    def _move_file(self, file_id: str, new_parent: str) -> None:
        params = urllib.parse.urlencode(
            {
                "addParents": new_parent,
                "removeParents": self.folder_id,
                "supportsAllDrives": "true",
            }
        )
        _http_json(
            f"{DRIVE_FILES_URL}/{urllib.parse.quote(file_id)}?{params}",
            payload={},
            headers={"authorization": f"Bearer {self.token}"},
            method="PATCH",
        )

    def _list_children(self, folder_id: str) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        page = ""
        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed=false",
                "fields": (
                    "nextPageToken,files(id,name,mimeType,webViewLink,createdTime,modifiedTime,"
                    "imageMediaMetadata(date,location))"
                ),
                "pageSize": "200",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page:
                params["pageToken"] = page
            data = _http_json(
                f"{DRIVE_FILES_URL}?{urllib.parse.urlencode(params)}",
                payload=None,
                headers={"authorization": f"Bearer {self.token}"},
                method="GET",
            )
            chunk = data.get("files") or data.get("data") or []
            files.extend(row for row in chunk if isinstance(row, dict))
            page = str(data.get("nextPageToken") or "")
            if not page:
                break
        return files


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
    meta = file.get("imageMediaMetadata") if isinstance(file.get("imageMediaMetadata"), dict) else {}
    loc = meta.get("location") if isinstance(meta.get("location"), dict) else {}
    row: dict[str, Any] = {
        "id": str(file.get("id") or ""),
        "name": str(file.get("name") or ""),
        "web_view_link": str(file.get("webViewLink") or file.get("web_view_link") or ""),
        "mime": str(file.get("mimeType") or file.get("mime") or ""),
        "created_time": str(file.get("createdTime") or file.get("created_time") or ""),
        "exif_datetime": str(meta.get("date") or file.get("exif_datetime") or ""),
    }
    lat = loc.get("latitude") if loc else file.get("exif_lat")
    lon = loc.get("longitude") if loc else file.get("exif_lon")
    if lat is not None:
        row["exif_lat"] = lat
    if lon is not None:
        row["exif_lon"] = lon
    return row


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
    geocoder: Any


def build_adapters(
    cfg: Config,
    *,
    drive: Any = None,
    ocr: Any = None,
    bland: Any = None,
    geocoder: Any = None,
) -> Adapters:
    if cfg.dry_run:
        fixture_sets, fixture_ocr, fixture_root = _load_fixture(cfg.fixture_path)
        return Adapters(
            drive=drive or FakeDrive(fixture_sets, root_photos=fixture_root),
            ocr=ocr or FakeOcr(fixture_ocr),
            bland=bland or RecordingBland(dry_run=True),
            alerter=RecordingAlerter(dry_run=True),
            geocoder=geocoder or FakeGeocoder(),
        )
    return Adapters(
        drive=drive or LiveDrive(cfg.google_drive_token, cfg.drive_folder_id),
        ocr=ocr or NullOcr(),
        bland=bland or LiveBland(cfg),
        alerter=LiveAlerter(cfg.alert_webhook),
        geocoder=geocoder or NominatimGeocoder(),
    )


def _load_fixture(path: Path | None) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    if path is None or not Path(path).is_file():
        return [], {}, []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    sets = list(data.get("sets") or [])
    root = list(data.get("root_photos") or [])
    ocr: dict[str, str] = {}
    for row in sets:
        for key, value in (row.get("ocr") or {}).items():
            ocr[str(key)] = str(value)
    for row in root:
        if row.get("ocr_text") and row.get("id"):
            ocr[str(row["id"])] = str(row["ocr_text"])
    return sets, ocr, root
