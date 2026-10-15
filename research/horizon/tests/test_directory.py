#!/usr/bin/env python3
"""Team & Systems Directory sub-page: /directory HTML (with site nav) and the PDF.

Content is prism-local (public repo); these tests use placeholder fixtures only.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.horizon import server  # noqa: E402


class TestDirectory(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        d = Path(cls._tmp.name)
        (d / "index.html").write_text(
            "<!doctype html><html><head><title>Fixture</title></head><body><h1>Example Team</h1><p>Member A</p></body></html>",
            encoding="utf-8",
        )
        (d / "team-directory.pdf").write_bytes(b"%PDF-1.4\n%fake\n")
        cls._patch = mock.patch.object(server, "DIRECTORY_DIR", d)
        cls._patch.start()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.HorizonHandler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls._patch.stop()
        cls._tmp.cleanup()

    def _get(self, path):
        return urllib.request.urlopen(self.base + path, timeout=5)

    def test_html_has_nav_and_pdf_links(self):
        r = self._get("/directory")
        body = r.read().decode("utf-8")
        self.assertEqual(r.status, 200)
        self.assertIn('id="hz-site-nav"', body)
        self.assertIn('data-nav="directory" aria-current="page"', body)
        self.assertIn('href="directory/team-directory.pdf"', body)
        self.assertIn('href="directory/team-directory.pdf?download=1"', body)

    def test_nav_order_directory_after_model(self):
        keys = [k for k, _l, _r in server.SITE_NAV_LINKS]
        self.assertEqual(keys.index("directory"), keys.index("model") + 1)

    def test_pdf_inline_and_download(self):
        r = self._get("/directory/team-directory.pdf")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.headers["Content-Type"], "application/pdf")
        self.assertTrue(r.headers["Content-Disposition"].startswith("inline"))
        r = self._get("/directory/team-directory.pdf?download=1")
        self.assertTrue(r.headers["Content-Disposition"].startswith("attachment"))

    def test_missing_content_is_404_with_hint(self):
        with tempfile.TemporaryDirectory() as empty, mock.patch.object(server, "DIRECTORY_DIR", Path(empty)):
            for path in ("/directory", "/directory/team-directory.pdf"):
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    self._get(path)
                self.assertEqual(cm.exception.code, 404)
                self.assertIn(b"HORIZON_DIRECTORY_DIR", cm.exception.read())

    def test_not_front_page(self):
        self.assertNotEqual(server.site_nav_active("/"), "directory")
        self.assertEqual(server.landing_route("/directory", ""), None)


if __name__ == "__main__":
    unittest.main()
