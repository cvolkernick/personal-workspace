"""Publish-time image URL rule for every image field (front page, section, item)."""

from __future__ import annotations

import copy
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

from research.daily_brief import cli
from research.daily_brief.tests._common import TmpStore, sample, store

GOOD = (
    "https://example.com/front.jpg",
    "https://example.com/a/b/pic.JPEG?w=800&h=600",
    "https://cdn.example.org/x.png",
    "https://example.com/x.webp",
    "https://example.com/x.gif",
    "https://example.com/x.avif",
    "https://example.com/caf%C3%A9.jpg#frag",
    "https://images.unsplash.com/photo-123?auto=format",
    "https://upload.wikimedia.org/wikipedia/commons/a/ab/Thing",
    "https://assets.imgix.net/some/key",
    "https://example.com:443/x.jpg",
)

BAD = {
    "http://example.com/x.jpg": "https",
    "ftp://example.com/x.jpg": "https",
    "data:image/png;base64,AAAA": "https",
    "javascript:alert(1)//.png": "https",
    "/tmp/local-frame.jpg": "https",
    "not-a-url": "https",
    "https://127.0.0.1/x.jpg": "private",
    "https://10.0.0.5/x.jpg": "private",
    "https://192.168.1.10/x.jpg": "private",
    "https://172.16.4.4/x.jpg": "private",
    "https://169.254.10.20/x.jpg": "private",
    "https://100.64.0.1/x.jpg": "private",
    "https://100.88.12.34/x.jpg": "private",
    "https://[::1]/x.jpg": "private",
    "https://[fd00::1234]/x.jpg": "private",
    "https://[fe80::1]/x.jpg": "private",
    "https://0.0.0.0/x.jpg": "private",
    "https://203.0.113.7/x.jpg": "address",
    "https://localhost/x.jpg": "private",
    "https://localhost./x.jpg": "private",
    "https://LOCALHOST/x.jpg": "private",
    "https://gateway/x.jpg": "private",
    "https://host.example.ts.net/x.jpg": "private",
    "https://HOST.EXAMPLE.TS.NET/x.jpg": "private",
    "https://printer.local/x.jpg": "private",
    "https://box.internal/x.jpg": "private",
    "https://nas.home.arpa/x.jpg": "private",
    "https://user:pw@example.com/x.jpg": "credentials",
    "https://example.com/page.html": "image",
    "https://example.com/x.jpg.exe": "image",
    "https://example.com/": "image",
    "https://example.com/x.svg": "image",
    "https://example.com/x .jpg": "whitespace",
    "https://example.com:99999/x.jpg": "valid URL",
    "https:///x.jpg": "host",
    "": "empty",
    "   ": "empty",
}


class TestImageRule(unittest.TestCase):
    def test_good_urls_pass(self):
        for url in GOOD:
            self.assertIsNone(store.image_url_problem(url), url)

    def test_bad_urls_refused_with_reason(self):
        for url, why in BAD.items():
            got = store.image_url_problem(url)
            self.assertIsNotNone(got, url)
            self.assertIn(why, got, url)

    def test_non_string_refused(self):
        for v in (1, ["https://example.com/x.jpg"], {"src": "https://example.com/x.jpg"}, True):
            self.assertIsNotNone(store.image_url_problem(v), v)


def _with_images(front=None, section=None, items=()):
    ed = sample("sample-am")
    if front is not None:
        ed["front_page"]["image"] = front
    sec = ed["sections"][0]
    if section is not None:
        sec["image"] = section
    sec["items"] = list(sec["items"]) + [
        {"text": f"SAMPLE: picture {k}", "image": u} for k, u in enumerate(items)
    ]
    return ed


class TestDropBadImages(unittest.TestCase):
    def test_every_image_field_is_checked(self):
        ed = _with_images(
            front="https://127.0.0.1/front.jpg",
            section="https://host.example.ts.net/day.jpg",
            items=("https://example.com/ok.jpg", "http://example.com/plain.jpg", "https://10.1.2.3/x.png"),
        )
        n0 = len(sample("sample-am")["sections"][0]["items"])
        out, warns = store.drop_bad_images(ed)
        self.assertNotIn("image", out["front_page"])
        self.assertNotIn("image", out["sections"][0])
        items = out["sections"][0]["items"]
        self.assertEqual(items[n0]["image"], "https://example.com/ok.jpg")
        self.assertNotIn("image", items[n0 + 1])
        self.assertNotIn("image", items[n0 + 2])
        self.assertEqual(items[n0 + 1]["text"], "SAMPLE: picture 1")
        self.assertEqual(len(warns), 4, warns)
        joined = "\n".join(warns)
        for where in ("front_page.image", "sections[0].image",
                      f"sections[0].items[{n0 + 1}].image", f"sections[0].items[{n0 + 2}].image"):
            self.assertIn(where, joined)
        self.assertTrue(all("dropped" in w for w in warns))

    def test_input_not_mutated(self):
        ed = _with_images(front="http://example.com/x.jpg", section="https://10.0.0.1/x.jpg",
                          items=("https://localhost/x.jpg",))
        before = copy.deepcopy(ed)
        store.drop_bad_images(ed)
        self.assertEqual(ed, before)

    def test_good_and_absent_images_untouched(self):
        ed = _with_images(front="https://example.com/f.jpg", section="https://example.com/s.png")
        out, warns = store.drop_bad_images(ed)
        self.assertEqual(warns, [])
        self.assertEqual(out, ed)
        plain = sample("sample-pm")
        self.assertEqual(store.drop_bad_images(plain), (plain, []))

    def test_null_and_empty_image_are_not_warnings(self):
        ed = _with_images(front=None, section="", items=())
        ed["front_page"]["image"] = None
        self.assertEqual(store.drop_bad_images(ed)[1], [])


class TestPublishWithBadImages(TmpStore):
    def test_publish_drops_bad_images_and_succeeds(self):
        ed = _with_images(
            front="https://192.168.0.2/front.jpg",
            section="https://example.com/day.jpg",
            items=("https://box.internal/x.jpg", "https://example.com/pier.webp"),
        )
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            dest = store.publish(ed, root=self.root)
        written = json.loads(dest.read_text(encoding="utf-8"))
        self.assertNotIn("image", written["front_page"])
        self.assertEqual(written["sections"][0]["image"], "https://example.com/day.jpg")
        imgs = [it.get("image") for it in written["sections"][0]["items"] if isinstance(it, dict)]
        self.assertIn("https://example.com/pier.webp", imgs)
        self.assertNotIn("https://box.internal/x.jpg", imgs)
        self.assertEqual(written["front_page"]["headline"], ed["front_page"]["headline"])
        self.assertIn("warning: front_page.image: dropped", err.getvalue())
        self.assertIn("box.internal", json.dumps(ed))  # caller's dict is untouched

    def test_non_string_front_image_no_longer_fails_publish(self):
        ed = sample("sample-am")
        ed["front_page"]["image"] = 42
        path, warns = store.publish_report(ed, root=self.root)
        self.assertTrue(path.is_file())
        self.assertEqual(warns, ["front_page.image: dropped (not a string)"])

    def test_publish_report_clean_edition_has_no_warnings(self):
        path, warns = store.publish_report(sample("sample-am"), root=self.root)
        self.assertTrue(path.is_file())
        self.assertEqual(warns, [])

    def test_schema_errors_still_fail(self):
        ed = _with_images(front="http://example.com/x.jpg")
        del ed["front_page"]["headline"]
        with mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(store.BriefError):
                store.publish(ed, root=self.root)

    def test_cli_reports_warnings(self):
        src = self.root / "in.json"
        src.write_text(json.dumps(_with_images(items=("https://127.0.0.1/x.jpg",))), encoding="utf-8")
        env = mock.patch.dict(os.environ, {"DAILY_BRIEF_DIR": str(self.root / "store")})
        for cmd in ("validate", "publish"):
            out, err = io.StringIO(), io.StringIO()
            with env, redirect_stdout(out), mock.patch("sys.stderr", err):
                rc = cli.main([cmd, str(src)])
            self.assertEqual(rc, 0, cmd)
            payload = json.loads(out.getvalue())
            self.assertTrue(payload["ok"], cmd)
            self.assertEqual(len(payload["warnings"]), 1, cmd)
            self.assertIn(".image: dropped (private", payload["warnings"][0])
        self.assertIn("daily-brief: warning:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
