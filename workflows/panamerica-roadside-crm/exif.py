"""Parse DateTimeOriginal + GPS from JPEG APP1 Exif and HEIC Exif boxes.

Stdlib only. Camera DateTimeOriginal is naive local time — the YYYY-MM-DD
portion is the folder date. GPS is converted to signed decimal degrees.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

EXIF_HEADER = b"Exif\x00\x00"
TIFF_LE = b"II*\x00"
TIFF_BE = b"MM\x00*"

# IFD0 / Exif IFD
TAG_DATETIME = 0x0132
TAG_EXIF_OFFSET = 0x8769
TAG_GPS_OFFSET = 0x8825
TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME_DIGITIZED = 0x9004

# GPS IFD
TAG_GPS_LAT_REF = 1
TAG_GPS_LAT = 2
TAG_GPS_LON_REF = 3
TAG_GPS_LON = 4

TYPE_BYTE = 1
TYPE_ASCII = 2
TYPE_SHORT = 3
TYPE_LONG = 4
TYPE_RATIONAL = 5
TYPE_UNDEFINED = 7
TYPE_SLONG = 9
TYPE_SRATIONAL = 10

_TYPE_SIZE = {
    TYPE_BYTE: 1,
    TYPE_ASCII: 1,
    TYPE_SHORT: 2,
    TYPE_LONG: 4,
    TYPE_RATIONAL: 8,
    TYPE_UNDEFINED: 1,
    TYPE_SLONG: 4,
    TYPE_SRATIONAL: 8,
}


@dataclass
class ExifInfo:
    datetime_original: Optional[datetime] = None
    date_source: str = ""
    gps: Optional[tuple[float, float]] = None

    @property
    def date_iso(self) -> str:
        if self.datetime_original is None:
            return ""
        return self.datetime_original.strftime("%Y-%m-%d")


def parse_exif(data: bytes) -> ExifInfo:
    """Best-effort EXIF from JPEG, HEIC/ISO-BMFF, or a raw TIFF/Exif blob."""
    if not data:
        return ExifInfo()
    tiff = _locate_tiff(data)
    if tiff is None:
        return ExifInfo()
    return _read_tiff(tiff)


def _locate_tiff(data: bytes) -> Optional[bytes]:
    if data[:2] == b"\xff\xd8":
        blob = _jpeg_exif_tiff(data)
        if blob:
            return blob
    if len(data) >= 12 and data[4:8] == b"ftyp":
        blob = _heic_exif_tiff(data)
        if blob:
            return blob
    if data[:4] in (TIFF_LE, TIFF_BE):
        return data
    if EXIF_HEADER in data:
        idx = data.find(EXIF_HEADER)
        return data[idx + len(EXIF_HEADER) :]
    for marker in (TIFF_LE, TIFF_BE):
        idx = data.find(marker)
        if idx >= 0:
            return data[idx:]
    return None


def _jpeg_exif_tiff(data: bytes) -> Optional[bytes]:
    i = 2
    n = len(data)
    while i + 4 <= n and data[i] == 0xFF:
        marker = data[i + 1]
        if marker == 0xDA:  # SOS
            break
        if marker in {0xD8, 0xD9}:
            i += 2
            continue
        if i + 4 > n:
            break
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        start = i + 4
        end = i + 2 + length
        if end > n or length < 2:
            break
        if marker == 0xE1 and data[start : start + 6] == EXIF_HEADER:
            return data[start + 6 : end]
        i = end
    return None


def _heic_exif_tiff(data: bytes) -> Optional[bytes]:
    for payload in _iter_boxes(data, b"Exif") + _iter_boxes(data, b"exif"):
        if payload.startswith(EXIF_HEADER):
            return payload[6:]
        if payload[:4] in (TIFF_LE, TIFF_BE):
            return payload
        # Many iPhone HEICs prefix the TIFF with a 4-byte offset.
        if len(payload) > 8 and payload[4:8] in (TIFF_LE, TIFF_BE):
            return payload[4:]
        if EXIF_HEADER in payload:
            idx = payload.find(EXIF_HEADER)
            return payload[idx + 6 :]
        for marker in (TIFF_LE, TIFF_BE):
            idx = payload.find(marker)
            if idx >= 0:
                return payload[idx:]
    # Nested Exif inside meta / iloc is still often findable as a raw TIFF.
    if EXIF_HEADER in data:
        idx = data.find(EXIF_HEADER)
        return data[idx + 6 :]
    for marker in (TIFF_LE, TIFF_BE):
        idx = data.find(marker)
        if idx >= 0:
            return data[idx:]
    return None


def _iter_boxes(data: bytes, want: bytes) -> list[bytes]:
    found: list[bytes] = []
    i = 0
    n = len(data)
    while i + 8 <= n:
        size = struct.unpack(">I", data[i : i + 4])[0]
        typ = data[i + 4 : i + 8]
        if size == 1 and i + 16 <= n:
            size = struct.unpack(">Q", data[i + 8 : i + 16])[0]
            hdr = 16
        elif size == 0:
            size = n - i
            hdr = 8
        else:
            hdr = 8
        if size < hdr or i + size > n:
            break
        payload = data[i + hdr : i + size]
        if typ == want:
            found.append(payload)
        if typ in {b"moov", b"meta", b"trak", b"mdia", b"minf", b"stbl", b"dinf", b"iprp", b"ipco"}:
            # ISO full boxes (meta/ipco) include a 4-byte version/flags prefix.
            nested = payload[4:] if typ in {b"meta"} and len(payload) > 4 else payload
            found.extend(_iter_boxes(nested, want))
        i += size
        if size == 0:
            break
    return found


def _read_tiff(tiff: bytes) -> ExifInfo:
    if len(tiff) < 8:
        return ExifInfo()
    if tiff[:4] == TIFF_LE:
        endian = "<"
    elif tiff[:4] == TIFF_BE:
        endian = ">"
    else:
        return ExifInfo()
    (ifd0,) = struct.unpack(endian + "I", tiff[4:8])
    ifd = _read_ifd(tiff, ifd0, endian)
    exif_off = _as_int(ifd.get(TAG_EXIF_OFFSET))
    gps_off = _as_int(ifd.get(TAG_GPS_OFFSET))
    exif_ifd = _read_ifd(tiff, exif_off, endian) if exif_off else {}
    gps_ifd = _read_ifd(tiff, gps_off, endian) if gps_off else {}

    raw_dt = (
        _as_ascii(exif_ifd.get(TAG_DATETIME_ORIGINAL))
        or _as_ascii(exif_ifd.get(TAG_DATETIME_DIGITIZED))
        or _as_ascii(ifd.get(TAG_DATETIME))
    )
    taken = _parse_exif_datetime(raw_dt)
    gps = _gps_decimal(gps_ifd)
    return ExifInfo(
        datetime_original=taken,
        date_source="exif" if taken is not None else "",
        gps=gps,
    )


def _read_ifd(tiff: bytes, offset: int, endian: str) -> dict[int, object]:
    out: dict[int, object] = {}
    if offset <= 0 or offset + 2 > len(tiff):
        return out
    (count,) = struct.unpack(endian + "H", tiff[offset : offset + 2])
    pos = offset + 2
    for _ in range(count):
        if pos + 12 > len(tiff):
            break
        tag, typ, cnt = struct.unpack(endian + "HHI", tiff[pos : pos + 8])
        raw = tiff[pos + 8 : pos + 12]
        pos += 12
        size = _TYPE_SIZE.get(typ, 1) * cnt
        if size <= 4:
            data = raw[:size]
        else:
            (off,) = struct.unpack(endian + "I", raw)
            if off < 0 or off + size > len(tiff):
                continue
            data = tiff[off : off + size]
        out[tag] = _decode_value(typ, cnt, data, endian)
    return out


def _decode_value(typ: int, count: int, data: bytes, endian: str) -> object:
    if typ == TYPE_ASCII:
        return data.split(b"\x00", 1)[0].decode("latin-1", errors="replace")
    if typ == TYPE_BYTE or typ == TYPE_UNDEFINED:
        return list(data) if count > 1 else (data[0] if data else 0)
    if typ == TYPE_SHORT:
        vals = struct.unpack(endian + ("H" * count), data[: 2 * count])
        return vals[0] if count == 1 else vals
    if typ == TYPE_LONG:
        vals = struct.unpack(endian + ("I" * count), data[: 4 * count])
        return vals[0] if count == 1 else vals
    if typ == TYPE_SLONG:
        vals = struct.unpack(endian + ("i" * count), data[: 4 * count])
        return vals[0] if count == 1 else vals
    if typ in {TYPE_RATIONAL, TYPE_SRATIONAL}:
        fmt = "I" if typ == TYPE_RATIONAL else "i"
        out = []
        for i in range(count):
            chunk = data[i * 8 : (i + 1) * 8]
            if len(chunk) < 8:
                break
            num, den = struct.unpack(endian + fmt + fmt, chunk)
            out.append((num, den))
        return out[0] if count == 1 else out
    return data


def _as_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, (tuple, list)) and value:
        try:
            return int(value[0])
        except (TypeError, ValueError):
            return 0
    return 0


def _as_ascii(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bytes):
        return value.decode("latin-1", errors="replace").strip()
    return ""


def _parse_exif_datetime(raw: str) -> Optional[datetime]:
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None


def _rational_to_float(value: object) -> Optional[float]:
    if isinstance(value, (tuple, list)) and len(value) == 2 and not isinstance(value[0], (tuple, list)):
        num, den = value
        if den == 0:
            return None
        return float(num) / float(den)
    return None


def _dms_to_decimal(dms: object, ref: str) -> Optional[float]:
    if not isinstance(dms, (tuple, list)) or len(dms) < 3:
        return None
    parts = []
    for item in dms[:3]:
        num = _rational_to_float(item)
        if num is None:
            return None
        parts.append(num)
    deg, minutes, seconds = parts
    decimal = deg + minutes / 60.0 + seconds / 3600.0
    if ref.upper() in {"S", "W"}:
        decimal = -decimal
    return decimal


def _gps_decimal(gps_ifd: dict[int, object]) -> Optional[tuple[float, float]]:
    if not gps_ifd:
        return None
    lat_ref = _as_ascii(gps_ifd.get(TAG_GPS_LAT_REF)) or "N"
    lon_ref = _as_ascii(gps_ifd.get(TAG_GPS_LON_REF)) or "E"
    lat = _dms_to_decimal(gps_ifd.get(TAG_GPS_LAT), lat_ref)
    lon = _dms_to_decimal(gps_ifd.get(TAG_GPS_LON), lon_ref)
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return (lat, lon)


def parse_drive_datetime(raw: str) -> Optional[datetime]:
    """Drive imageMediaMetadata.date is usually 'YYYY:MM:DD HH:MM:SS'."""
    return _parse_exif_datetime(raw)
