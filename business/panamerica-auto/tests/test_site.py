#!/usr/bin/env python3
"""Structural + live-serve checks for the Panamerica Auto website MVP.

Tests read real shipped files under business/panamerica-auto/ and exercise the
real server entry path (stdlib HTTP) — no mocks of page content.
"""

from __future__ import annotations

import re
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"
CSS = ROOT / "static" / "styles.css"
JS = ROOT / "static" / "app.js"
SERVER = ROOT / "server.py"
README = ROOT / "README.md"

# High-level services expected on the MVP page (HTML may escape &)
REQUIRED_SERVICES = [
    "Vehicle sales",
    "Service &amp; maintenance",
    "Parts supply",
    "Fleet support",
    "Financing guidance",
    "Inspections &amp; prep",
]

REQUIRED_SECTIONS = ["#services", "#why", "#process", "#contact"]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class PanamericaAutoSiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = INDEX.read_text(encoding="utf-8")

    def test_project_files_exist(self) -> None:
        for path in (INDEX, CSS, JS, SERVER, README):
            self.assertTrue(path.is_file(), f"missing {path.relative_to(ROOT)}")

    def test_title_and_brand(self) -> None:
        self.assertIn("Panamerica Auto", self.html)
        self.assertRegex(self.html, r"<title>[^<]*Panamerica Auto")

    def test_service_cards_present(self) -> None:
        for name in REQUIRED_SERVICES:
            self.assertIn(name, self.html, f"service missing: {name}")
        # data-service markers for the six cards
        markers = re.findall(r'data-service="([^"]+)"', self.html)
        self.assertEqual(len(markers), 6, f"expected 6 service cards, got {markers}")

    def test_nav_and_section_anchors(self) -> None:
        for href in REQUIRED_SECTIONS:
            self.assertIn(f'href="{href}"', self.html)
            section_id = href.lstrip("#")
            self.assertIn(f'id="{section_id}"', self.html)

    def test_contact_form_fields(self) -> None:
        for field_id in ("name", "email", "interest", "message"):
            self.assertIn(f'id="{field_id}"', self.html)
        self.assertIn('id="contact-form"', self.html)

    def test_assets_linked(self) -> None:
        self.assertIn('href="static/styles.css"', self.html)
        self.assertIn('src="static/app.js"', self.html)
        self.assertGreater(CSS.stat().st_size, 500)
        self.assertGreater(JS.stat().st_size, 200)

    def test_server_is_runnable_module(self) -> None:
        text = SERVER.read_text(encoding="utf-8")
        self.assertIn("def main", text)
        self.assertIn("SimpleHTTPRequestHandler", text)

    def test_readme_has_verify_steps(self) -> None:
        readme = README.read_text(encoding="utf-8")
        self.assertIn("## Run", readme)
        self.assertIn("## Verify", readme)
        self.assertIn("server.py", readme)
        self.assertIn("Next iteration", readme)

    def test_live_server_serves_home_with_brand_and_services(self) -> None:
        """Launch real server.py twice; GET / must include brand + services."""
        for run in (1, 2):
            port = _free_port()
            proc = subprocess.Popen(
                [sys.executable, str(SERVER), "--port", str(port), "--bind", "127.0.0.1"],
                cwd=str(ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            body = ""
            try:
                deadline = time.time() + 5.0
                last_err: Exception | None = None
                while time.time() < deadline:
                    if proc.poll() is not None:
                        err = ""
                        if proc.stderr is not None:
                            err = proc.stderr.read()
                        self.fail(
                            f"server exited early (run {run}): rc={proc.returncode} "
                            f"stderr={err!r}"
                        )
                    try:
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/", timeout=1.0
                        ) as resp:
                            self.assertEqual(resp.status, 200)
                            body = resp.read().decode("utf-8")
                        break
                    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                        last_err = exc
                        time.sleep(0.05)
                else:
                    self.fail(f"server never responded on run {run}: {last_err}")

                self.assertIn("Panamerica Auto", body, f"brand missing run {run}")
                self.assertIn("High-level services", body, f"services heading missing run {run}")
                for name in REQUIRED_SERVICES:
                    self.assertIn(name, body, f"service missing on run {run}: {name}")
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
                if proc.stderr is not None:
                    proc.stderr.close()



if __name__ == "__main__":
    unittest.main()
