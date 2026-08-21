#!/usr/bin/env python3
"""Local catalog ingest stub for MiKrafts.

Given an image path + title + optional note, write a processed JPEG and append
one row to catalog/items.json. This is not a mail reader. The future Gmail
pipeline should call ``ingest_print`` after applying ``email_contract``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from html import escape
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from PIL import Image, ImageEnhance, ImageOps

SITE_ROOT = Path(__file__).resolve().parent
CATALOG_DIR = SITE_ROOT / "catalog"
ITEMS_PATH = CATALOG_DIR / "items.json"
IMAGES_DIR = CATALOG_DIR / "images"

PLUM = (78, 27, 122)
WHITE = (255, 255, 255)
EMPTY_CATALOG_HTML = '<p class="catalog-empty">No prints in the catalog yet.</p>'


def subject_is_new_print(subject: str) -> bool:
    """True when an inbound subject matches the email contract."""
    return "new print" in (subject or "").lower()


def parse_email_body(body: str) -> tuple[str, str]:
    """Optional body = title on the first line, notes in the remainder."""
    lines = [line.rstrip() for line in (body or "").splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return "", ""
    title = lines[0].strip()
    rest = "\n".join(lines[1:]).strip()
    return title, rest


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return slug or "print"


def load_items(path: Path = ITEMS_PATH) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("catalog/items.json must be a JSON array")
    return [item for item in data if isinstance(item, dict)]


def write_items(items: Sequence[Mapping[str, Any]], path: Path = ITEMS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(items), indent=2) + "\n", encoding="utf-8")


def _corner_average(img: Image.Image, inset: int = 14) -> tuple[int, int, int]:
    w, h = img.size
    box = min(inset, max(1, w // 12), max(1, h // 12))
    samples = [
        img.crop((0, 0, box, box)),
        img.crop((w - box, 0, w, box)),
        img.crop((0, h - box, box, h)),
        img.crop((w - box, h - box, w, h)),
    ]
    pixels: list[tuple[int, int, int]] = []
    for sample in samples:
        sw, sh = sample.size
        pix = sample.load()
        for y in range(sh):
            for x in range(sw):
                pixels.append(pix[x, y])
    n = max(1, len(pixels))
    r = sum(p[0] for p in pixels) // n
    g = sum(p[1] for p in pixels) // n
    b = sum(p[2] for p in pixels) // n
    return r, g, b


def _closer_target(bg: tuple[int, int, int]) -> tuple[int, int, int]:
    r, g, b = bg
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    purple_bias = (r + b) / 2 - g
    if purple_bias > 18 and luminance < 190:
        return PLUM
    if luminance >= 150:
        return WHITE
    dist_white = sum(abs(c - t) for c, t in zip(bg, WHITE))
    dist_plum = sum(abs(c - t) for c, t in zip(bg, PLUM))
    return WHITE if dist_white <= dist_plum else PLUM


def _bias_background(img: Image.Image) -> Image.Image:
    """Push near-background pixels toward white or plum. Does not draw geometry."""
    bg = _corner_average(img)
    target = _closer_target(bg)
    src = img.load()
    out = img.copy()
    dest = out.load()
    w, h = img.size
    threshold = 42
    for y in range(h):
        for x in range(w):
            px = src[x, y]
            dist = abs(px[0] - bg[0]) + abs(px[1] - bg[1]) + abs(px[2] - bg[2])
            if dist > threshold:
                continue
            t = 1.0 - (dist / threshold)
            dest[x, y] = (
                int(px[0] + (target[0] - px[0]) * 0.55 * t),
                int(px[1] + (target[1] - px[1]) * 0.55 * t),
                int(px[2] + (target[2] - px[2]) * 0.55 * t),
            )
    return out


def _center_crop(img: Image.Image, aspect: float = 4 / 3) -> Image.Image:
    w, h = img.size
    current = w / h
    if abs(current - aspect) < 0.04:
        return img
    if current > aspect:
        new_w = int(h * aspect)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    new_h = int(w / aspect)
    top = (h - new_h) // 2
    return img.crop((0, top, w, top + new_h))


def process_image(source: Path, dest: Path) -> Path:
    """Crop, lift contrast, and bias a clean white/plum background.

    Does not invent nozzle, cube, or other geometry on the print.
    """
    img = Image.open(source)
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    longest = max(img.size)
    if longest > 2000:
        scale = 2000 / longest
        img = img.resize(
            (int(img.size[0] * scale), int(img.size[1] * scale)),
            Image.Resampling.LANCZOS,
        )
    img = _center_crop(img)
    img = ImageOps.autocontrast(img, cutoff=1)
    img = ImageEnhance.Contrast(img).enhance(1.08)
    img = _bias_background(img)
    longest = max(img.size)
    if longest > 1200:
        scale = 1200 / longest
        img = img.resize(
            (int(img.size[0] * scale), int(img.size[1] * scale)),
            Image.Resampling.LANCZOS,
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=88, optimize=True)
    return dest


def render_catalog_cards(items: Sequence[Mapping[str, Any]]) -> str:
    """Same card contract as static/catalog.js (tests + docs)."""
    if not items:
        return EMPTY_CATALOG_HTML
    parts: list[str] = []
    for item in items:
        title = escape(str(item.get("title") or ""))
        image = escape(str(item.get("image") or ""))
        added = escape(str(item.get("added") or ""))
        note_raw = str(item.get("note") or "").strip()
        note_html = f'<p class="catalog-note">{escape(note_raw)}</p>' if note_raw else ""
        parts.append(
            '<article class="catalog-card">'
            f'<img src="{image}" alt="{title}">'
            f'<h2 class="catalog-title">{title}</h2>'
            f"{note_html}"
            f'<time class="catalog-added" datetime="{added}">Added {added}</time>'
            "</article>"
        )
    return "".join(parts)


def ingest_print(
    image_path: Path | str,
    title: str,
    note: str = "",
    *,
    site_root: Optional[Path] = None,
    added: Optional[str] = None,
) -> dict[str, Any]:
    """Process ``image_path`` and append one catalog item. Returns the new row."""
    root = Path(site_root) if site_root else SITE_ROOT
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    source = Path(image_path)
    if not source.is_file():
        raise FileNotFoundError(source)

    digest = hashlib.sha256(source.read_bytes() + title.encode("utf-8")).hexdigest()[:8]
    item_id = f"{slugify(title)}-{digest}"
    filename = f"{item_id}.jpg"
    rel_image = f"catalog/images/{filename}"
    dest = root / "catalog" / "images" / filename
    process_image(source, dest)

    items_path = root / "catalog" / "items.json"
    items = load_items(items_path)
    row = {
        "id": item_id,
        "title": title,
        "note": (note or "").strip(),
        "image": rel_image,
        "added": added or date.today().isoformat(),
    }
    items.append(row)
    write_items(items, items_path)
    return row


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path, help="Source photo")
    parser.add_argument("--title", required=True, help="Catalog title")
    parser.add_argument("--note", default="", help="Optional note")
    parser.add_argument(
        "--site-root",
        type=Path,
        default=SITE_ROOT,
        help="MiKrafts package root (default: this directory)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    row = ingest_print(args.image, args.title, args.note, site_root=args.site_root)
    print(json.dumps(row, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
