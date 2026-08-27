"""FCC Grok login control is secondary; click starts real grok CLI re-auth."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.financial_advisor import reset_grok_login_state  # noqa: E402

FCC = ROOT / "financial-command"
INDEX = FCC / "index.html"

HEADER_IDS = ("nav-horizon", "nav-fleet", "btn-refresh")
SECRET_NEEDLES = (
    "client_secret",
    "access_token",
    "refresh_token",
    "id_token",
    "device_code",
    "XAI_API_KEY=",
    "eyJ",
)
PORT_IN_COPY = re.compile(r":\d{2,5}\b")


class _IdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._stack: list[tuple[str, dict[str, str]]] = []
        self.by_id: dict[str, dict[str, str]] = {}
        self._capture: str | None = None
        self._text: list[str] = []
        self.sticky_ids: set[str] = set()
        self.footer_ids: set[str] = set()
        self._in_sticky = 0
        self._in_footer = 0
        self._in_controls = 0
        self.controls_ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k: (v or "") for k, v in attrs}
        eid = ad.get("id", "")
        classes = ad.get("class", "")
        if tag == "div" and "sticky-top" in classes.split():
            self._in_sticky += 1
        if tag == "footer":
            self._in_footer += 1
        if "controls" in classes.split():
            self._in_controls += 1
        if eid:
            self.by_id[eid] = ad
            if self._in_sticky:
                self.sticky_ids.add(eid)
            if self._in_footer:
                self.footer_ids.add(eid)
            if self._in_controls:
                self.controls_ids.add(eid)
            if tag in ("button", "a"):
                self._capture = eid
                self._text = []
        self._stack.append((tag, ad))

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture is not None and self._stack and self._stack[-1][0] == tag:
            self.by_id[self._capture]["text"] = re.sub(
                r"\s+", " ", "".join(self._text)
            ).strip()
            self._capture = None
        if self._stack:
            _t, ad = self._stack.pop()
            classes = ad.get("class", "")
            if tag == "div" and "sticky-top" in classes.split() and self._in_sticky:
                self._in_sticky -= 1
            if tag == "footer" and self._in_footer:
                self._in_footer -= 1
            if "controls" in classes.split() and self._in_controls:
                self._in_controls -= 1


def _parse(html: str) -> _IdParser:
    p = _IdParser()
    p.feed(html)
    return p


def _load_fcc_server():
    spec = importlib.util.spec_from_file_location("fcc_server_grok_login", FCC / "server.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


_FAKE_GROK = """#!/usr/bin/env python3
import os
import time
print("Please visit https://auth.x.ai/activate", flush=True)
print("And enter code: ABCD-WXYZ", flush=True)
time.sleep(float(os.environ.get("FAKE_GROK_SLEEP", "0.2")))
raise SystemExit(int(os.environ.get("FAKE_GROK_EXIT", "0")))
"""


class TestGrokLoginControl(unittest.TestCase):
    def test_control_present_and_visually_secondary(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        parsed = _parse(html)
        btn = parsed.by_id.get("btn-grok-login")
        self.assertIsNotNone(btn, "missing #btn-grok-login")
        assert btn is not None
        self.assertEqual(btn.get("text"), "Grok login")
        classes = (btn.get("class") or "").split()
        self.assertIn("grok-login", classes)
        self.assertNotIn("primary", classes)
        self.assertNotIn("btn", classes)
        self.assertIn("btn-grok-login", parsed.footer_ids)
        self.assertNotIn("btn-grok-login", parsed.sticky_ids)
        self.assertNotIn("btn-grok-login", parsed.controls_ids)
        self.assertNotRegex(btn.get("title") or "", PORT_IN_COPY)
        self.assertNotRegex(btn.get("text") or "", PORT_IN_COPY)

        settings = parsed.by_id.get("btn-grok-login-settings")
        self.assertIsNotNone(settings)
        assert settings is not None
        self.assertEqual(settings.get("text"), "Grok login")
        self.assertNotIn("primary", (settings.get("class") or "").split())
        self.assertNotIn("btn-grok-login-settings", parsed.sticky_ids)
        self.assertNotIn("btn-grok-login-settings", parsed.controls_ids)

        for hid in HEADER_IDS:
            self.assertIn(hid, parsed.sticky_ids)
        self.assertNotIn("Grok login", parsed.by_id["nav-horizon"].get("text", ""))
        self.assertNotIn("Grok login", parsed.by_id["nav-fleet"].get("text", ""))
        self.assertEqual(parsed.by_id["btn-refresh"].get("text"), "Refresh")

        sticky = html.split('class="sticky-top"', 1)[1].split("</div>", 8)[0]
        self.assertNotIn("Grok login", sticky)
        self.assertIn('id="btn-grok-login"', html.split("<footer>", 1)[1])

    def test_no_secrets_in_html(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        for needle in SECRET_NEEDLES:
            self.assertNotIn(needle, html, f"secret needle {needle!r} in index.html")
        self.assertNotIn("~/.grok/auth.json", html)
        self.assertNotRegex(html, r"xai-[A-Za-z0-9]{8,}")

    def test_other_fcc_surfaces_have_no_header_grok_button(self) -> None:
        for name in ("capital-flows.html", "watchlist.html", "interest-spectrum.html"):
            html = (FCC / name).read_text(encoding="utf-8")
            parsed = _parse(html)
            self.assertNotIn("btn-grok-login", parsed.sticky_ids, name)
            self.assertNotIn("btn-grok-login", parsed.controls_ids, name)
            self.assertNotIn('id="btn-grok-login"', html.split('class="sticky-top"', 1)[1][:2500])


class TestGrokLoginApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_fcc_server()
        cls.port = _free_port()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), cls.mod.FCCHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def tearDown(self) -> None:
        reset_grok_login_state()
        os.environ.pop("FCC_GROK_BIN", None)
        os.environ.pop("FAKE_GROK_EXIT", None)
        os.environ.pop("FAKE_GROK_SLEEP", None)

    def _json(self, method: str, path: str, body: bytes | None = None) -> tuple[int, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, data=body, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw.decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                payload = {"raw": raw.decode("utf-8", errors="replace")[:200]}
            return exc.code, payload

    def test_login_endpoints_exist_and_stay_public(self) -> None:
        get_code, get_body = self._json("GET", "/api/ask/login")
        self.assertEqual(get_code, 200)
        self.assertIn(get_body.get("phase"), ("idle", "starting", "pending", "ok", "fail"))
        self.assertEqual(get_body.get("method"), "grok_cli")
        for banned in SECRET_NEEDLES:
            self.assertNotIn(banned, json.dumps(get_body))

    def test_post_login_starts_real_cli_and_reports_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            grok = Path(td) / "grok"
            grok.write_text(_FAKE_GROK, encoding="utf-8")
            grok.chmod(grok.stat().st_mode | stat.S_IEXEC)
            os.environ["FCC_GROK_BIN"] = str(grok)
            os.environ["FAKE_GROK_SLEEP"] = "0.15"
            os.environ["FAKE_GROK_EXIT"] = "0"
            code, body = self._json("POST", "/api/ask/login", b"{}")
            self.assertIn(code, (200, 503, 500))
            self.assertTrue(body.get("started") or body.get("phase") in ("pending", "starting", "ok"))
            self.assertEqual(body.get("method"), "grok_cli")
            for banned in SECRET_NEEDLES:
                self.assertNotIn(banned, json.dumps(body))
            deadline = time.time() + 5
            last = body
            while time.time() < deadline:
                _, last = self._json("GET", "/api/ask/login")
                if last.get("phase") in ("ok", "fail"):
                    break
                time.sleep(0.05)
            self.assertEqual(last.get("phase"), "ok")
            self.assertTrue(last.get("ok"))
            self.assertEqual(last.get("user_code"), "ABCD-WXYZ")
            self.assertEqual(last.get("verification_uri"), "https://auth.x.ai/activate")
            for banned in SECRET_NEEDLES:
                self.assertNotIn(banned, json.dumps(last))

    def test_post_login_reports_fail_when_cli_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            grok = Path(td) / "grok"
            grok.write_text(_FAKE_GROK, encoding="utf-8")
            grok.chmod(grok.stat().st_mode | stat.S_IEXEC)
            os.environ["FCC_GROK_BIN"] = str(grok)
            os.environ["FAKE_GROK_SLEEP"] = "0.05"
            os.environ["FAKE_GROK_EXIT"] = "3"
            self._json("POST", "/api/ask/login", b"{}")
            deadline = time.time() + 5
            last = {}
            while time.time() < deadline:
                _, last = self._json("GET", "/api/ask/login")
                if last.get("phase") in ("ok", "fail"):
                    break
                time.sleep(0.05)
            self.assertEqual(last.get("phase"), "fail")
            self.assertFalse(last.get("ok"))
            self.assertTrue(last.get("error"))

    def test_post_login_uses_start_grok_login(self) -> None:
        fake = {
            "ok": False,
            "started": True,
            "already": False,
            "phase": "pending",
            "method": "grok_cli",
            "verification_uri": "https://auth.x.ai/activate",
            "user_code": "ZZZZ-YYYY",
            "error": None,
            "auth_ok": False,
            "auth_source": "none",
            "expired": False,
        }
        with mock.patch.object(self.mod, "start_grok_login", return_value=fake) as start:
            code, body = self._json("POST", "/api/ask/login", b"{}")
        start.assert_called_once()
        self.assertEqual(code, 200)
        self.assertEqual(body["user_code"], "ZZZZ-YYYY")
        self.assertEqual(body["method"], "grok_cli")
        self.assertNotIn("access_token", body)

    def test_html_wires_post_ask_login(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn('fetch("/api/ask/login"', html)
        self.assertIn('method: "POST"', html)
        self.assertIn("grokLoginRefresh", html)
        self.assertNotIn("/api/open-orchestra", html)
        self.assertNotIn("vercel", html.lower())


if __name__ == "__main__":
    unittest.main()
