#!/usr/bin/env python3
"""Regenerate Horizon's home-screen icons (needs Pillow; the server does not).

Draws a sunrise mark: a half sun rising over a horizon line on Horizon's base
background (--bg #060a10, sun --yellow/--orange, line --cyan from index.html).
Writes research/horizon/pwa_icons.json ({name: base64 PNG}); server.py decodes
and serves those as /icon-192.png etc., so the repo carries no binary files.

  python3 research/horizon/make_pwa_icons.py            # rewrite pwa_icons.json
  python3 research/horizon/make_pwa_icons.py --png DIR  # also write PNGs to DIR
"""

from __future__ import annotations

import argparse
import base64
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
OUT = HERE / "pwa_icons.json"

BG_TOP = (15, 22, 32)  # --panel
BG_BOTTOM = (6, 10, 16)  # --bg
SUN_TOP = (245, 197, 66)  # --yellow
SUN_BOTTOM = (240, 160, 96)  # --orange
LINE = (79, 209, 197)  # --cyan

# name -> (size, mark scale). Maskable keeps the mark inside the 80% safe zone.
ICONS = {
    "icon-192.png": (192, 1.0),
    "icon-512.png": (512, 1.0),
    "icon-maskable-192.png": (192, 0.72),
    "icon-maskable-512.png": (512, 0.72),
    "apple-touch-icon.png": (180, 0.9),
    "favicon-32.png": (32, 1.15),
}


def _vgrad(w: int, h: int, top: tuple, bottom: tuple) -> Image.Image:
    col = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        col.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return col.resize((w, h))


def draw(size: int, scale: float) -> Image.Image:
    ss = 4  # supersample for smooth edges
    n = size * ss
    img = _vgrad(n, n, BG_TOP, BG_BOTTOM)
    u = n * scale  # mark unit
    cx = n / 2
    horizon_y = n * 0.5 + u * 0.1
    r = u * 0.27

    # Soft glow behind the sun.
    glow = Image.new("L", (n, n), 0)
    gd = ImageDraw.Draw(glow)
    gr = r * 1.6
    gd.ellipse((cx - gr, horizon_y - gr, cx + gr, horizon_y + gr), fill=38)
    glow = glow.filter(ImageFilter.GaussianBlur(n * 0.08))
    img = Image.composite(Image.new("RGB", (n, n), SUN_BOTTOM), img, glow)

    # Half sun: gradient disc clipped above the horizon.
    sun = _vgrad(n, n, SUN_TOP, SUN_BOTTOM)
    mask = Image.new("L", (n, n), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((cx - r, horizon_y - r, cx + r, horizon_y + r), fill=255)
    md.rectangle((0, horizon_y + u * 0.02, n, n), fill=0)
    img.paste(sun, (0, 0), mask)

    d = ImageDraw.Draw(img)
    # Horizon line.
    lw = max(ss * 2, round(u * 0.035))
    half = u * 0.4
    d.rounded_rectangle(
        (cx - half, horizon_y - lw / 2 + u * 0.02, cx + half, horizon_y + lw / 2 + u * 0.02),
        radius=lw / 2,
        fill=LINE,
    )
    # Two faint reflection dashes below the line (skipped at favicon size).
    if size >= 64:
        for i, frac in enumerate((0.24, 0.13)):
            y = horizon_y + u * (0.11 + 0.08 * i)
            w = u * frac
            h = max(ss, lw * 0.6)
            d.rounded_rectangle((cx - w, y - h / 2, cx + w, y + h / 2), radius=h / 2, fill=(46, 112, 120))
    img = img.resize((size, size), Image.LANCZOS)
    # Palette PNG keeps pwa_icons.json small; 96 colours is plenty for flat art.
    return img.quantize(colors=96, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)


def build() -> dict[str, bytes]:
    out = {}
    for name, (size, scale) in ICONS.items():
        buf = io.BytesIO()
        draw(size, scale).save(buf, format="PNG", optimize=True)
        out[name] = buf.getvalue()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--png", type=Path, help="also write PNG files to this directory")
    args = ap.parse_args(argv)
    icons = build()
    payload = {k: base64.b64encode(v).decode("ascii") for k, v in icons.items()}
    OUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if args.png:
        args.png.mkdir(parents=True, exist_ok=True)
        for k, v in icons.items():
            (args.png / k).write_bytes(v)
    print(f"wrote {OUT.relative_to(HERE.parents[1])} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
