"""Minimal JPEG / HEIC bytes with DateTimeOriginal + GPS for parser tests."""

from __future__ import annotations

import struct
from datetime import datetime
from typing import Optional


def _entry(tag: int, typ: int, count: int, value: bytes) -> bytes:
    raw = value + b"\x00" * 4
    return struct.pack("<HHI", tag, typ, count) + raw[:4]


def _rational_triple(deg: float) -> bytes:
    sign = 1 if deg >= 0 else -1
    val = abs(deg)
    d = int(val)
    m_full = (val - d) * 60
    m = int(m_full)
    s = (m_full - m) * 60
    s_num = int(round(s * 10000))
    return struct.pack("<IIIIII", d, 1, m, 1, s_num, 10000) if sign else b""


def build_tiff(*, taken: Optional[datetime], lat: Optional[float], lon: Optional[float]) -> bytes:
    extra = bytearray()
    extra_base_placeholder = 0  # filled after IFD sizes are known

    def add(blob: bytes) -> int:
        off = extra_base_placeholder + len(extra)
        extra.extend(blob)
        return off

    exif_entries = []
    if taken is not None:
        ascii_dt = taken.strftime("%Y:%m:%d %H:%M:%S") + "\x00"
        off = add(ascii_dt.encode("ascii"))
        exif_entries.append(_entry(0x9003, 2, len(ascii_dt), struct.pack("<I", off)))

    gps_entries = []
    if lat is not None and lon is not None:
        lat_ref = b"N\x00" if lat >= 0 else b"S\x00"
        lon_ref = b"E\x00" if lon >= 0 else b"W\x00"
        gps_entries.append(_entry(1, 2, 2, lat_ref))
        lat_off = add(_rational_triple(lat))
        gps_entries.append(_entry(2, 5, 3, struct.pack("<I", lat_off)))
        gps_entries.append(_entry(3, 2, 2, lon_ref))
        lon_off = add(_rational_triple(lon))
        gps_entries.append(_entry(4, 5, 3, struct.pack("<I", lon_off)))

    # Header (8) + IFD0 (2 + 12*n + 4) + Exif IFD + GPS IFD + extra
    ifd0_entries = []
    header = 8
    ifd0_size = 2 + 12 * (int(bool(exif_entries)) + int(bool(gps_entries))) + 4
    cursor = header + ifd0_size
    exif_ifd_off = 0
    gps_ifd_off = 0
    if exif_entries:
        exif_ifd_off = cursor
        cursor += 2 + 12 * len(exif_entries) + 4
        ifd0_entries.append(_entry(0x8769, 4, 1, struct.pack("<I", exif_ifd_off)))
    if gps_entries:
        gps_ifd_off = cursor
        cursor += 2 + 12 * len(gps_entries) + 4
        ifd0_entries.append(_entry(0x8825, 4, 1, struct.pack("<I", gps_ifd_off)))

    extra_base = cursor
    shift = extra_base - extra_base_placeholder
    if shift:
        # Rebuild entries whose 4-byte payload is an offset into extra.
        def shift_ifd(entries: list[bytes]) -> list[bytes]:
            out = []
            for ent in entries:
                tag, typ, count = struct.unpack("<HHI", ent[:8])
                (val,) = struct.unpack("<I", ent[8:12])
                size = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8}.get(typ, 1) * count
                if size > 4:
                    val += shift
                out.append(_entry(tag, typ, count, struct.pack("<I", val) if size > 4 else ent[8:12]))
            return out

        exif_entries = shift_ifd(exif_entries)
        gps_entries = shift_ifd(gps_entries)

    def pack_ifd(entries: list[bytes]) -> bytes:
        return struct.pack("<H", len(entries)) + b"".join(entries) + struct.pack("<I", 0)

    tiff = bytearray()
    tiff.extend(b"II*\x00")
    tiff.extend(struct.pack("<I", 8))
    tiff.extend(pack_ifd(ifd0_entries))
    if exif_entries:
        tiff.extend(pack_ifd(exif_entries))
    if gps_entries:
        tiff.extend(pack_ifd(gps_entries))
    tiff.extend(extra)
    return bytes(tiff)


def build_jpeg_with_exif(
    *,
    taken: Optional[datetime] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
) -> bytes:
    tiff = build_tiff(taken=taken, lat=lat, lon=lon)
    app1 = b"Exif\x00\x00" + tiff
    return b"\xff\xd8\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + b"\xff\xd9"


def build_heic_with_exif(
    *,
    taken: Optional[datetime] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
) -> bytes:
    tiff = build_tiff(taken=taken, lat=lat, lon=lon)
    payload = b"Exif\x00\x00" + tiff
    exif_box = struct.pack(">I", 8 + len(payload)) + b"Exif" + payload
    ftyp = struct.pack(">I", 20) + b"ftypheic" + struct.pack(">I", 0) + b"mif1"
    return ftyp + exif_box
