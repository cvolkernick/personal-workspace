"""Daily Brief after the move to the Horizon host (#1091).

FCC keeps two things: the publish CLI shim (financial-command/brief.py) and a
302 from /brief* to BRIEF_BASE_URL. Rendering tests live in
research/daily_brief/tests.
"""

from __future__ import annotations

import http.client
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
FCC = ROOT / "financial-command"
SAMPLES = FCC / "brief-samples"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


brief = _load("fcc_brief_shim_under_test", FCC / "brief.py")
server_mod = _load("fcc_server_brief_redirect_test", FCC / "server.py")


class TestPublishShim(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_shim_publish_writes_edition_to_same_store(self):
        with mock.patch.dict(os.environ, {"FCC_BRIEF_DIR": str(self.root)}):
            os.environ.pop("DAILY_BRIEF_DIR", None)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = brief.main(["publish", str(SAMPLES / "sample-am.json")])
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertTrue(out["ok"])
        self.assertEqual(out["url"], "/daily-brief/2026-10-09/am")
        self.assertTrue((self.root / "2026-10-09-am.json").is_file())

    def test_shim_reexports_store_api(self):
        for name in ("publish", "validate", "normalize", "store_dir", "mac_tasks_from_markdown",
                     "list_editions", "latest_key", "bucket_todos", "BriefError"):
            self.assertTrue(hasattr(brief, name), name)
        for n in ("sample-am", "sample-pm", "sample-empty-todos"):
            ed = json.loads((SAMPLES / f"{n}.json").read_text(encoding="utf-8"))
            self.assertEqual(brief.validate(brief.normalize(ed)), [], n)

    def test_default_store_path_unchanged(self):
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("FCC_BRIEF_DIR", None)
            os.environ.pop("DAILY_BRIEF_DIR", None)
            self.assertTrue(str(brief.store_dir()).endswith(".local/share/fcc/brief-editions"))


class TestRedirectMapping(unittest.TestCase):
    def t(self, path, base=None):
        return server_mod.brief_redirect_target(path, base)

    def test_default_base_is_horizon_lens(self):
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("BRIEF_BASE_URL", None)
            self.assertEqual(server_mod.brief_base_url(), "/horizon/daily-brief")
            self.assertEqual(self.t("/brief"), "/horizon/daily-brief")

    def test_paths_and_query_preserved(self):
        b = "https://host.example:8443/daily-brief"
        cases = {
            "/brief": b,
            "/brief/": b,
            "/brief/archive": b + "/archive",
            "/brief/2026-10-09/am": b + "/2026-10-09/am",
            "/brief/2026-10-09/pm/?ref=x&y=2": b + "/2026-10-09/pm/?ref=x&y=2",
            "/brief?utm=1": b + "?utm=1",
            "/api/brief/latest": b + "/api/latest",
            "/api/brief/editions": b + "/api/editions",
        }
        for src, want in cases.items():
            self.assertEqual(self.t(src, b), want, src)

    def test_env_override_and_trailing_slash(self):
        with mock.patch.dict(os.environ, {"BRIEF_BASE_URL": "/daily-brief/"}):
            self.assertEqual(self.t("/brief/archive"), "/daily-brief/archive")

    def test_non_brief_paths_untouched(self):
        for p in ("/", "/briefing", "/brief.html", "/api/briefs", "/horizon/daily-brief", "/api/treasury"):
            self.assertIsNone(self.t(p), p)


class TestServerRedirects(unittest.TestCase):
    def test_fcc_server_302s_brief_routes(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.FCCHandler)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        port = httpd.server_address[1]
        try:
            with mock.patch.dict(os.environ, {"BRIEF_BASE_URL": "/horizon/daily-brief"}):
                for method in ("GET", "HEAD"):
                    for path, want in (("/brief", "/horizon/daily-brief"),
                                       ("/brief/2026-10-09/am?x=1", "/horizon/daily-brief/2026-10-09/am?x=1"),
                                       ("/brief/archive", "/horizon/daily-brief/archive")):
                        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                        c.request(method, path)
                        r = c.getresponse()
                        r.read()
                        self.assertEqual(r.status, 302, (method, path))
                        self.assertEqual(r.getheader("Location"), want, (method, path))
                        c.close()
        finally:
            httpd.shutdown()
            httpd.server_close()


class TestNavEntry(unittest.TestCase):
    def test_brief_nav_still_points_at_brief(self):
        # The FCC header link stays on /brief, which now redirects.
        js = (FCC / "nav-horizon.js").read_text(encoding="utf-8")
        self.assertIn('"/brief"', js)
        self.assertIn('"nav-brief"', js)
        self.assertNotIn("http://", js)


if __name__ == "__main__":
    unittest.main()
