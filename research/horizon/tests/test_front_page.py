#!/usr/bin/env python3
"""Daily Brief as Horizon's front page; macro-model dashboard at /model.

Covers Meridian's acceptance notes: only the HTML landing route moves (data
and API paths unchanged), old dashboard deep links still land on /model
(query → server redirect, #anchor → client hop), and the brief keeps its
"not Horizon model output" label. Hostnames here are placeholders.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HORIZON = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "research" / "daily_brief" / "fixtures"
PLACEHOLDER_ORIGIN = "https://horizon-host.example"

from research.daily_brief import store  # noqa: E402
from research.daily_brief.render import NOT_MODEL_LINE  # noqa: E402
from research.daily_brief.render import route as brief_route  # noqa: E402
from research.horizon import server  # noqa: E402

# Data / API / asset paths the dashboard and publish flow read. Must not move.
DATA_PATHS = (
    "/api/health",
    "/api/dashboard",
    "/api/brief",
    "/api/world-state",
    "/api/world_state",
    "/data/briefs/brief_latest.json",
    "/nav-fcc.js",
)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class _LiveServer(unittest.TestCase):
    with_edition = True

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.store_root = Path(cls._tmp.name)
        if cls.with_edition:
            ed = json.loads((FIXTURES / "sample-am.json").read_text(encoding="utf-8"))
            store.publish(copy.deepcopy(ed), root=cls.store_root)
        cls._env = mock.patch.dict(os.environ, {"DAILY_BRIEF_DIR": str(cls.store_root)})
        cls._env.start()
        cls.port = _free_port()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), server.HorizonHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls._env.stop()
        cls._tmp.cleanup()

    def _get(self, path: str) -> tuple[int, dict, bytes]:
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            with _OPENER.open(url, timeout=5) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()


class TestFrontPageWithEdition(_LiveServer):
    def test_root_serves_the_brief(self) -> None:
        code, headers, body = self._get("/")
        self.assertEqual(code, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        html = body.decode("utf-8")
        self.assertIn('data-page="daily-brief"', html)
        self.assertIn("SAMPLE: Daily Brief moves to the Horizon host", html)
        self.assertIn(NOT_MODEL_LINE, html)
        self.assertNotIn('id="domain-heat"', html)
        self.assertNotIn("api/dashboard", html)

    def test_root_brief_links_to_macro_model(self) -> None:
        html = self._get("/")[2].decode("utf-8")
        m = re.search(r'<div class="topbar">(<a [^>]*>[^<]*</a>)', html)
        self.assertIsNotNone(m, "Macro Model link not in the brief header")
        self.assertIn('href="model"', m.group(1))
        self.assertIn(">Macro Model<", m.group(1))
        # Relative link resolves under the /horizon prefix and at the root.
        self.assertEqual(urljoin(PLACEHOLDER_ORIGIN + "/horizon/", "model"), PLACEHOLDER_ORIGIN + "/horizon/model")
        self.assertEqual(urljoin(PLACEHOLDER_ORIGIN + "/", "model"), PLACEHOLDER_ORIGIN + "/model")

    def test_model_serves_dashboard_unchanged(self) -> None:
        code, headers, body = self._get("/model")
        self.assertEqual(code, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertEqual(body, (HORIZON / "index.html").read_bytes())
        self.assertIn(b"domain-heat", body)

    def test_model_links_back_to_daily_brief(self) -> None:
        html = self._get("/model")[2].decode("utf-8")
        m = re.search(r'<a [^>]*id="nav-daily-brief"[^>]*>([^<]*)</a>', html)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "Daily Brief")
        self.assertIn('href="./"', m.group(0))
        self.assertEqual(urljoin(PLACEHOLDER_ORIGIN + "/horizon/model", "./"), PLACEHOLDER_ORIGIN + "/horizon/")
        self.assertEqual(urljoin(PLACEHOLDER_ORIGIN + "/model", "./"), PLACEHOLDER_ORIGIN + "/")

    def test_daily_brief_route_unchanged(self) -> None:
        for path in ("/daily-brief", "/daily-brief/api/latest", "/daily-brief/archive", "/daily-brief/2026-10-09/am"):
            want_code, want_ctype, want_body = brief_route(path)
            code, headers, body = self._get(path)
            self.assertEqual(code, want_code, path)
            self.assertEqual(headers.get("Content-Type"), want_ctype, path)
            self.assertEqual(body.decode("utf-8"), want_body, path)
        latest = json.loads(self._get("/daily-brief/api/latest")[2])
        self.assertTrue(latest["ok"])
        self.assertEqual(latest["url"], "/daily-brief/2026-10-09/am")

    def test_data_paths_unchanged(self) -> None:
        for path in DATA_PATHS:
            code, headers, _ = self._get(path)
            self.assertNotIn(code, (301, 302, 303, 307, 308), path)
            self.assertIsNone(headers.get("Location"), path)
            self.assertIn(code, (200, 404), path)  # 404 only when no brief/world-state is on disk
        self.assertEqual(self._get("/api/health")[0], 200)
        dash = json.loads(self._get("/api/dashboard")[2])
        self.assertEqual(dash.get("service"), "horizon")
        self.assertEqual(self._get("/data/briefs/brief_latest.json")[2], (HORIZON / "data/briefs/brief_latest.json").read_bytes())
        self.assertEqual(self._get("/nav-fcc.js")[2], (HORIZON / "nav-fcc.js").read_bytes())
        # Nothing new under /model/: the dashboard keeps calling the old URLs.
        self.assertEqual(self._get("/model/api/dashboard")[0], 404)

    def test_dashboard_relative_refs_resolve_to_old_paths(self) -> None:
        html = (HORIZON / "index.html").read_text(encoding="utf-8")
        refs = re.findall(r'(?:fetch\(|src=)\s*"([^"]+)"', html)
        self.assertIn("api/dashboard", refs)
        self.assertIn("api/refresh", refs)
        self.assertIn("nav-fcc.js", refs)
        for ref in refs:
            self.assertFalse(ref.startswith("/"), ref)
            for page, base in (("/model", ""), ("/horizon/model", "/horizon")):
                self.assertEqual(urljoin(PLACEHOLDER_ORIGIN + page, ref), f"{PLACEHOLDER_ORIGIN}{base}/{ref}")

    def test_old_root_query_redirects_to_model(self) -> None:
        cases = {
            "/?tab=world": "/model?tab=world",
            "/?tab=strategy&x=1": "/model?tab=strategy&x=1",
            "/index.html": "/model",
            "/index.html?tab=graph": "/model?tab=graph",
            "/model/": "/model",
        }
        for src, dest in cases.items():
            code, headers, _ = self._get(src)
            self.assertEqual(code, 302, src)
            self.assertEqual(headers.get("Location"), dest, src)

    def test_head_matches_get_routing(self) -> None:
        for path, code in (("/", 200), ("/model", 200), ("/?tab=world", 302)):
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method="HEAD")
            try:
                with _OPENER.open(req, timeout=5) as resp:
                    got = resp.status
            except urllib.error.HTTPError as exc:
                got = exc.code
            self.assertEqual(got, code, path)


class TestFrontPageWithoutEdition(_LiveServer):
    with_edition = False

    def test_root_renders_cleanly_without_edition(self) -> None:
        code, _, body = self._get("/")
        self.assertEqual(code, 200)
        html = body.decode("utf-8")
        self.assertIn('data-page="daily-brief"', html)
        self.assertIn("No edition published yet", html)
        self.assertIn(NOT_MODEL_LINE, html)
        self.assertIn(">Macro Model<", html)
        self.assertNotIn("Traceback", html)
        self.assertEqual(self._get("/model")[0], 200)


class TestAnchorHop(unittest.TestCase):
    def test_known_anchors_exist_on_dashboard(self) -> None:
        html = (HORIZON / "index.html").read_text(encoding="utf-8")
        ids = set(re.findall(r'\bid="([^"]+)"', html))
        for a in server.DASHBOARD_ANCHORS:
            self.assertIn(a, ids, a)
        self.assertIn("panel-overview", server.DASHBOARD_ANCHORS)

    def test_hop_script_on_root_brief_only(self) -> None:
        page = server.front_page_html("<html><head></head><body><div class=\"paper\"><div class=\"topbar\"></div></div></body></html>")
        self.assertIn("location.replace(", page)
        self.assertLess(page.index("location.replace("), page.index("</head>"))
        _, _, plain = brief_route("/daily-brief", root=Path(tempfile.gettempdir()) / "no-such-brief-store")
        self.assertNotIn("location.replace(", plain)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_hop_script_behaviour(self) -> None:
        script = re.search(r"<script>(.*?)</script>", server._ANCHOR_HOP, re.S).group(1)
        harness = (
            "const cases = JSON.parse(process.argv[1]); const out = [];"
            "for (const [pathname, search, hash] of cases) {"
            "  let target = null;"
            "  const location = {pathname, search, hash, replace: (u) => { target = u; }};"
            "  (new Function('location', process.argv[2]))(location);"
            "  out.push(target);"
            "}"
            "console.log(JSON.stringify(out));"
        )
        cases = [
            ["/", "", "#panel-world"],
            ["/horizon/", "?tab=x", "#panel-strategy"],
            ["/horizon", "", "#domain-heat"],
            ["/", "", "#sec-front"],
            ["/", "", ""],
        ]
        res = subprocess.run(
            ["node", "-e", harness, json.dumps(cases), script],
            capture_output=True, text=True, timeout=20, check=True,
        )
        self.assertEqual(
            json.loads(res.stdout),
            ["/model#panel-world", "/horizon/model?tab=x#panel-strategy", "/horizon/model#domain-heat", None, None],
        )


class TestFrontPageFlag(unittest.TestCase):
    def test_flag_default_is_daily_brief(self) -> None:
        self.assertEqual(server.FRONT_PAGE, "daily-brief")
        self.assertEqual(server.landing_route("/", ""), ("brief", None))
        self.assertEqual(server.landing_route("/model", ""), ("model", None))
        for p in DATA_PATHS + ("/daily-brief", "/daily-brief/api/latest"):
            self.assertIsNone(server.landing_route(p, ""), p)

    def test_flag_reverts_to_dashboard_root(self) -> None:
        with mock.patch.object(server, "FRONT_PAGE", "model"):
            self.assertEqual(server.landing_route("/", ""), ("model", None))
            self.assertEqual(server.landing_route("/index.html", ""), ("model", None))
            self.assertEqual(server.landing_route("/model", ""), ("model", None))
            self.assertEqual(server.landing_route("/model/", "a=1"), ("redirect", "/model?a=1"))


if __name__ == "__main__":
    unittest.main()
