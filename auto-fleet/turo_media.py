"""Persist Turo mail image MIME parts next to the inbox dump.

Email ingest only. Does not invent photos. Chrome (logo / pixel / SVG / GIF)
is dropped. Text-only mail stays text-only.
"""

from __future__ import annotations

import hashlib
import os
import re
from email.message import Message
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

IMAGE_MIMES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/pjpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
    }
)
SKIP_NAME_RE = re.compile(
    r"(logo|icon|spacer|pixel|tracking|banner|favicon|sprite)",
    re.I,
)
CLAIMS_PHOTO_RE = re.compile(r"contains\s+photo", re.I)
MIN_IMAGE_BYTES = 16
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")
_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
}


def media_dir_for(inbox_path: Path | None) -> Optional[Path]:
    if inbox_path is None:
        return None
    p = Path(inbox_path)
    return p.parent / f"{p.stem}_media"


def claims_photos(*parts: str) -> bool:
    return any(CLAIMS_PHOTO_RE.search(p or "") for p in parts)


def normalize_mime(value: str) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def keep_image_part(
    mime: str,
    *,
    filename: str = "",
    size: int = 0,
    content_id: str = "",
) -> bool:
    """True only for real photo MIME parts. Logos / pixels / empty stay out."""
    kind = normalize_mime(mime)
    if kind not in IMAGE_MIMES:
        return False
    if size and size < MIN_IMAGE_BYTES:
        return False
    blob = f"{filename} {content_id}"
    if SKIP_NAME_RE.search(blob):
        return False
    return True


def safe_token(value: str, *, fallback: str = "msg", maxlen: int = 80) -> str:
    cleaned = _SAFE_ID.sub("_", (value or "").strip()).strip("._")
    return (cleaned or fallback)[:maxlen]


def safe_filename(name: str, mime: str, index: int) -> str:
    base = Path(name or "").name
    base = _SAFE_ID.sub("_", base).strip("._")
    if not base or base in {".", ".."}:
        ext = _MIME_EXT.get(normalize_mime(mime), ".bin")
        base = f"photo-{index:02d}{ext}"
    return base[:120]


def write_image_bytes(
    media_dir: Path,
    message_id: str,
    data: bytes,
    *,
    filename: str = "",
    mime: str = "image/jpeg",
    index: int = 1,
    inline: bool = False,
    content_id: str | None = None,
) -> Optional[dict[str, Any]]:
    """Write image bytes to {media_dir}/{message_id}/{filename}. None if skipped."""
    if not data or not keep_image_part(
        mime, filename=filename, size=len(data), content_id=content_id or ""
    ):
        return None
    mid = safe_token(str(message_id or "msg"))
    dest_dir = Path(media_dir) / mid
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_filename(filename, mime, index)
    if dest.exists() and dest.read_bytes() != data:
        stem, suf = dest.stem, dest.suffix
        n = 2
        while dest.exists():
            dest = dest_dir / f"{stem}-{n}{suf}"
            n += 1
    dest.write_bytes(data)
    try:
        os.chmod(dest, 0o600)
        os.chmod(dest_dir, 0o700)
    except OSError:
        pass
    rec: dict[str, Any] = {
        "filename": dest.name,
        "mime": normalize_mime(mime) or "image/jpeg",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "path": str(dest),
        "relpath": f"{mid}/{dest.name}",
    }
    if inline:
        rec["inline"] = True
    if content_id:
        rec["content_id"] = content_id.strip("<>")
    return rec


def attachment_public(rec: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Strip raw bytes from an attachment record. Keep path / sha / mime."""
    if not isinstance(rec, dict):
        return None
    mime = normalize_mime(str(rec.get("mime") or rec.get("mimeType") or ""))
    filename = str(rec.get("filename") or rec.get("name") or "")
    size = rec.get("size")
    try:
        size_i = int(size) if size is not None else 0
    except (TypeError, ValueError):
        size_i = 0
    path = str(rec.get("path") or "")
    relpath = str(rec.get("relpath") or "")
    sha = str(rec.get("sha256") or "")
    if not path and not relpath and not sha:
        return None
    if mime and mime not in IMAGE_MIMES:
        return None
    if not keep_image_part(
        mime or "image/jpeg",
        filename=filename,
        size=size_i,
        content_id=str(rec.get("content_id") or ""),
    ):
        return None
    out: dict[str, Any] = {}
    if filename:
        out["filename"] = Path(filename).name
    if mime:
        out["mime"] = mime
    if size_i:
        out["size"] = size_i
    if sha:
        out["sha256"] = sha
    if path:
        out["path"] = path
    if relpath:
        out["relpath"] = relpath
    if rec.get("inline"):
        out["inline"] = True
    cid = rec.get("content_id")
    if cid:
        out["content_id"] = str(cid).strip("<>")
    return out or None


def normalize_attachments(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        rec = attachment_public(item) if isinstance(item, dict) else None
        if not rec:
            continue
        key = str(rec.get("sha256") or rec.get("path") or rec.get("relpath") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(rec)
    return out


def materialize_attachment_data(
    raw: Mapping[str, Any],
    media_dir: Path,
    message_id: str,
    *,
    index: int = 1,
) -> Optional[dict[str, Any]]:
    """If a record already has path/relpath, keep it. If it has data=, write it."""
    existing = attachment_public(raw)
    if existing and (existing.get("path") or existing.get("relpath")):
        return existing
    data_raw = raw.get("data") or raw.get("bytes")
    blob: bytes = b""
    if isinstance(data_raw, (bytes, bytearray)):
        blob = bytes(data_raw)
    elif isinstance(data_raw, str) and data_raw.strip():
        import base64

        text = data_raw.strip()
        pad = "=" * (-len(text) % 4)
        try:
            blob = base64.urlsafe_b64decode(text + pad)
        except Exception:  # noqa: BLE001
            try:
                blob = base64.b64decode(text + pad)
            except Exception:  # noqa: BLE001
                blob = b""
    if not blob:
        return existing
    return write_image_bytes(
        media_dir,
        message_id,
        blob,
        filename=str(raw.get("filename") or raw.get("name") or ""),
        mime=str(raw.get("mime") or raw.get("mimeType") or "image/jpeg"),
        index=index,
        inline=bool(raw.get("inline")),
        content_id=str(raw.get("content_id") or "") or None,
    )


def attachments_from_email(
    msg: Message,
    media_dir: Path | None,
    message_id: str,
) -> list[dict[str, Any]]:
    """Walk an email.message and persist image parts. Empty if none."""
    if media_dir is None:
        return []
    out: list[dict[str, Any]] = []
    index = 0
    for part in msg.walk():
        ctype = normalize_mime(part.get_content_type() or "")
        filename = part.get_filename() or ""
        cid = str(part.get("Content-ID") or "")
        if not keep_image_part(ctype, filename=filename, content_id=cid):
            continue
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:  # noqa: BLE001
            payload = b""
        if not payload:
            continue
        index += 1
        disp = str(part.get("Content-Disposition") or "").lower()
        rec = write_image_bytes(
            media_dir,
            message_id,
            payload,
            filename=filename,
            mime=ctype,
            index=index,
            inline="inline" in disp or bool(cid),
            content_id=cid or None,
        )
        if rec:
            out.append(rec)
    return out


def merge_attachments(
    *groups: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group or []:
            rec = attachment_public(item) if isinstance(item, dict) else None
            if not rec:
                continue
            key = str(rec.get("sha256") or rec.get("path") or rec.get("relpath") or "")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(rec)
    return merged


def resolve_media_file(media_dir: Path, relpath: str) -> Optional[Path]:
    """Return a file under media_dir, or None if missing / traversal."""
    if not relpath or not media_dir:
        return None
    cleaned = relpath.replace("\\", "/").lstrip("/")
    if not cleaned or ".." in Path(cleaned).parts:
        return None
    root = media_dir.resolve()
    try:
        dest = (root / cleaned).resolve()
    except (OSError, RuntimeError):
        return None
    try:
        dest.relative_to(root)
    except ValueError:
        return None
    if dest.is_file():
        return dest
    return None
