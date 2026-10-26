"""Guard: a changed static file must ship under a new ``?v=`` tag.

server.py serves versioned assets (``/x.js?v=tag``) as
``Cache-Control: public, max-age=31536000, immutable`` and sw.js serves them
stale-while-revalidate. If a file's bytes change but its tag does not, phones
keep the old file for up to a year. #1343 did exactly that: styles.css (new
``[data-m-panel="biomarkers"]`` show rule) and app.js (new
``FitDashBiomarkers.render`` call) changed under their old tags, so on the
phone the Labs tab rendered blank (section hidden by the cached CSS).

``static_asset_pins.json`` records ``{file: {"v": tag, "sha256": hash}}`` for
every versioned asset in index.html. When you edit one of those files:
  1. bump its ``?v=`` tag in static/index.html AND static/sw.js PRECACHE,
  2. bump ``CACHE`` in sw.js,
  3. update the pin (tag + sha256) in tests/static_asset_pins.json.
Updating only the sha256 without a new tag defeats the point of this test.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
PINS = Path(__file__).resolve().parent / "static_asset_pins.json"

_ASSET_RE = re.compile(r'(?:src|href)="/([A-Za-z0-9_.\-]+\.(?:js|css))\?v=([^"]+)"')


def versioned_assets(html: str) -> dict:
    return {name: tag for name, tag in _ASSET_RE.findall(html)}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stale_tag_problems(static_dir: Path, pins: dict) -> list:
    """Files whose content changed while their ?v= tag stayed the same."""
    html = (static_dir / "index.html").read_text()
    out = []
    for name, tag in sorted(versioned_assets(html).items()):
        pin = pins.get(name)
        if pin is None:
            out.append(f"{name}: no pin in static_asset_pins.json")
            continue
        digest = sha256(static_dir / name)
        if pin.get("v") == tag and pin.get("sha256") != digest:
            out.append(
                f"{name}: content changed but ?v={tag} was not bumped (immutable 1y cache: "
                "phones keep the old file). Bump the tag in index.html + sw.js, bump CACHE, "
                "then update tests/static_asset_pins.json."
            )
        elif pin.get("v") != tag:
            out.append(f"{name}: index.html uses ?v={tag} but pin says ?v={pin.get('v')} (update the pin)")
    return out


class StaticAssetPinTests(unittest.TestCase):
    HTML = (STATIC / "index.html").read_text()
    SW = (STATIC / "sw.js").read_text()

    def test_every_changed_asset_has_a_new_version_tag(self):
        pins = json.loads(PINS.read_text())
        self.assertEqual(stale_tag_problems(STATIC, pins), [])

    def test_pins_cover_exactly_the_versioned_assets(self):
        pins = json.loads(PINS.read_text())
        self.assertEqual(sorted(pins), sorted(versioned_assets(self.HTML)))

    def test_sw_precaches_the_same_versioned_urls(self):
        for name, tag in versioned_assets(self.HTML).items():
            self.assertIn(f'"/{name}?v={tag}"', self.SW, name)

    def test_detects_content_change_under_old_tag(self):
        """Replays #1343: styles.css edited, tag kept -> flagged."""
        html = versioned_assets(self.HTML)
        pins = {n: {"v": t, "sha256": "0" * 64} for n, t in html.items()}
        problems = stale_tag_problems(STATIC, pins)
        self.assertTrue(any(p.startswith("styles.css: content changed") for p in problems))
        bumped = dict(pins, **{"styles.css": {"v": "older-tag", "sha256": "0" * 64}})
        problems = stale_tag_problems(STATIC, bumped)
        self.assertTrue(any(p.startswith("styles.css: index.html uses") for p in problems))


if __name__ == "__main__":
    unittest.main()
