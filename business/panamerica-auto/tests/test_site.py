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
    "Turo rentals",
    "Private rentals",
    "Fleet management",
    "Rental management",
    "Maintenance coordination",
    "Service coordination",
]

# Positioning: rentals/fleet — not dealership sales
FORBIDDEN_PRIMARY_OFFERS = [
    "Vehicle sales",
    "Financing guidance",
    "Parts supply",
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
        self.assertIn('data-service="turo"', self.html)
        self.assertIn('data-service="private"', self.html)
        self.assertIn('data-service="fleet"', self.html)

    def test_rental_positioning_not_dealership_sales(self) -> None:
        """Site must present rentals/fleet ops — not auto sales as a primary offer."""
        self.assertIn("Turo", self.html)
        self.assertIn("private", self.html.lower())
        self.assertIn("fleet management", self.html.lower())
        # Hero/title should not market sales as the brand line
        self.assertNotRegex(
            self.html,
            r"<title>[^<]*Sales",
            "title should not lead with Sales",
        )
        self.assertNotIn("Sales · Service · Parts", self.html)
        for phrase in FORBIDDEN_PRIMARY_OFFERS:
            # Must not appear as a service card heading
            self.assertNotRegex(
                self.html,
                rf"<h3>\s*{re.escape(phrase)}\s*</h3>",
                f"primary service card should not be: {phrase}",
            )

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
        # Port 8795 avoids clash with workflow dashboard (8765)
        self.assertRegex(text, r"DEFAULT_PORT\s*=\s*8795")

    def test_readme_has_verify_steps(self) -> None:
        readme = README.read_text(encoding="utf-8")
        self.assertIn("## Run", readme)
        self.assertIn("## Verify", readme)
        self.assertIn("server.py", readme)
        self.assertIn("Next iteration", readme)
        self.assertIn("8795", readme)
        self.assertIn("Deploy", readme)

    def test_deploy_unit_and_install_exist(self) -> None:
        unit = ROOT / "deploy" / "panamerica-auto.service"
        install = ROOT / "deploy" / "install_remote.sh"
        start = ROOT / "start.command"
        self.assertTrue(unit.is_file(), "missing deploy unit")
        self.assertTrue(install.is_file(), "missing install_remote.sh")
        self.assertTrue(start.is_file(), "missing start.command")
        unit_text = unit.read_text(encoding="utf-8")
        self.assertIn("--port 8795", unit_text)
        self.assertIn("0.0.0.0", unit_text)
        self.assertIn("panamerica-auto/server.py", unit_text)

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
