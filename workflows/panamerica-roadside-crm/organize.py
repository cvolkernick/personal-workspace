"""Cluster root-dropped photos into per-car YYYY-MM-DD[-road] folders."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from exif import ExifInfo, parse_drive_datetime, parse_exif

OPS_TZ = ZoneInfo("America/New_York")
CLUSTER_GAP = timedelta(minutes=20)
CLUSTER_METERS = 150.0
EARTH_M = 6371000.0

_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass
class PhotoMeta:
    id: str
    name: str
    taken_at: Optional[datetime] = None
    date_source: str = "upload"
    gps: Optional[tuple[float, float]] = None
    upload_at: Optional[datetime] = None
    mime: str = ""
    web_view_link: str = ""
    ocr_key: str = ""

    @property
    def folder_date(self) -> str:
        if self.taken_at is not None:
            return self.taken_at.strftime("%Y-%m-%d")
        if self.upload_at is not None:
            return self.upload_at.astimezone(OPS_TZ).strftime("%Y-%m-%d")
        return ""


@dataclass
class PhotoCluster:
    photos: list[PhotoMeta]
    folder_name: str = ""
    spotted_at: str = ""
    location: str = ""
    date_source: str = "upload"
    location_source: str = "missing"
    gps: Optional[tuple[float, float]] = None
    location_flagged: bool = False


def slugify(text: str, *, limit: int = 40) -> str:
    slug = _SLUG.sub("-", (text or "").lower()).strip("-")
    return slug[:limit].strip("-")


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_M * math.asin(min(1.0, math.sqrt(h)))


def parse_upload_datetime(raw: str) -> Optional[datetime]:
    text = (raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return parse_drive_datetime(text.replace("T", " ").replace("-", ":", 2))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ZoneInfo("UTC"))
    return stamp


def photo_meta_from_row(
    row: dict[str, Any],
    *,
    blob: Optional[bytes] = None,
) -> PhotoMeta:
    """Build PhotoMeta from a Drive/fake photo row, preferring EXIF bytes then Drive metadata."""
    info = parse_exif(blob or b"") if blob else ExifInfo()
    taken = info.datetime_original
    date_source = info.date_source
    gps = info.gps

    if taken is None:
        drive_dt = parse_drive_datetime(str(row.get("exif_datetime") or ""))
        if drive_dt is not None:
            taken = drive_dt
            date_source = "exif"
    if gps is None:
        lat = row.get("exif_lat")
        lon = row.get("exif_lon")
        if lat is None and isinstance(row.get("exif_gps"), dict):
            lat = row["exif_gps"].get("lat")
            lon = row["exif_gps"].get("lon")
        try:
            if lat is not None and lon is not None:
                gps = (float(lat), float(lon))
        except (TypeError, ValueError):
            gps = None

    upload_at = parse_upload_datetime(str(row.get("created_time") or row.get("createdTime") or ""))
    if taken is None:
        taken = upload_at.replace(tzinfo=None) if upload_at is not None else None
        date_source = "upload"

    return PhotoMeta(
        id=str(row.get("id") or ""),
        name=str(row.get("name") or ""),
        taken_at=taken,
        date_source=date_source if taken is not None else "upload",
        gps=gps,
        upload_at=upload_at,
        mime=str(row.get("mime") or row.get("mimeType") or ""),
        web_view_link=str(row.get("web_view_link") or row.get("webViewLink") or ""),
        ocr_key=str(row.get("id") or row.get("name") or ""),
    )


def _sort_key(photo: PhotoMeta) -> tuple:
    taken = photo.taken_at or datetime.min
    return (photo.folder_date, taken, photo.name, photo.id)


def same_cluster(prev: PhotoMeta, cur: PhotoMeta) -> bool:
    if prev.folder_date and cur.folder_date and prev.folder_date != cur.folder_date:
        return False
    left = prev.taken_at
    right = cur.taken_at
    if left is not None and right is not None:
        delta = right - left if right >= left else left - right
        if delta > CLUSTER_GAP:
            return False
    if prev.gps is not None and cur.gps is not None:
        if haversine_m(prev.gps, cur.gps) > CLUSTER_METERS:
            return False
    return True


def cluster_photos(photos: list[PhotoMeta]) -> list[list[PhotoMeta]]:
    if not photos:
        return []
    ordered = sorted(photos, key=_sort_key)
    groups: list[list[PhotoMeta]] = [[ordered[0]]]
    for photo in ordered[1:]:
        if same_cluster(groups[-1][-1], photo):
            groups[-1].append(photo)
        else:
            groups.append([photo])
    return groups


def _median_gps(photos: list[PhotoMeta]) -> Optional[tuple[float, float]]:
    pts = [p.gps for p in photos if p.gps is not None]
    if not pts:
        return None
    pts = sorted(pts)
    mid = pts[len(pts) // 2]
    return mid


def _cluster_date_source(photos: list[PhotoMeta]) -> str:
    if any(p.date_source == "exif" for p in photos):
        return "exif"
    if any(p.date_source == "folder" for p in photos):
        return "folder"
    return "upload"


def folder_name_for(spotted_at: str, location: str, used: set[str]) -> str:
    base = spotted_at or "unknown-date"
    slug = slugify(location)
    name = f"{base}-{slug}" if slug else base
    if name not in used:
        return name
    n = 2
    while f"{name}-{n}" in used:
        n += 1
    return f"{name}-{n}"


def reverse_label(geocoder: Any, gps: Optional[tuple[float, float]]) -> str:
    if gps is None or geocoder is None:
        return ""
    try:
        label = geocoder.reverse(gps[0], gps[1])
    except Exception:
        return ""
    return str(label or "").strip()


def clusters_from_photos(
    photos: list[PhotoMeta],
    *,
    geocoder: Any = None,
) -> list[PhotoCluster]:
    used: set[str] = set()
    out: list[PhotoCluster] = []
    for group in cluster_photos(photos):
        gps = _median_gps(group)
        location = reverse_label(geocoder, gps)
        if gps is not None:
            location_source = "exif"
            flagged = not bool(location)
            if not location:
                location = f"{gps[0]:.4f}, {gps[1]:.4f}"
        else:
            location_source = "missing"
            flagged = True
        date_source = _cluster_date_source(group)
        spotted = next((p.folder_date for p in group if p.folder_date), "")
        name = folder_name_for(spotted, location, used)
        used.add(name)
        out.append(
            PhotoCluster(
                photos=group,
                folder_name=name,
                spotted_at=spotted,
                location=location if location_source != "missing" else (location or ""),
                date_source=date_source,
                location_source=location_source,
                gps=gps,
                location_flagged=flagged,
            )
        )
        if location_source == "missing":
            out[-1].location = ""
    return out
