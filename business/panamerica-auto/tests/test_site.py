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
CYBERCAB = ROOT / "cybercab-fleet.html"
HERO_IMG = ROOT / "static" / "img" / "tesla-cybercab-hero.jpg"
HERO_CREDITS = ROOT / "static" / "img" / "CREDITS.md"
CSS = ROOT / "static" / "styles.css"
JS = ROOT / "static" / "app.js"
SERVER = ROOT / "server.py"
README = ROOT / "README.md"

FORBIDDEN_OFFERING_CTA = [
    "Invest now",
    "Buy shares",
    "Wire funds",
    "Subscribe now",
    "Purchase shares",
]

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
        for path in (INDEX, CYBERCAB, HERO_IMG, HERO_CREDITS, CSS, JS, SERVER, README):
            self.assertTrue(path.is_file(), f"missing {path.relative_to(ROOT)}")

    def test_homepage_links_cybercab_demo(self) -> None:
        self.assertIn('href="cybercab-fleet.html"', self.html)
        self.assertIn("teaser-strip", self.html)
        self.assertIn('option value="cybercab"', self.html)

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
                self.assertIn("cybercab-fleet.html", body, f"homepage cybercab link missing run {run}")

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/cybercab-fleet.html", timeout=1.0
                ) as resp:
                    self.assertEqual(resp.status, 200)
                    cyber = resp.read().decode("utf-8")
                self.assertIn("DEMO", cyber)
                self.assertIn("not an offer to sell securities", cyber.lower())
                self.assertIn("noindex", cyber)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
                if proc.stderr is not None:
                    proc.stderr.close()



class PanamericaCybercabDemoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = CYBERCAB.read_text(encoding="utf-8")

    def test_demo_banner_and_noindex(self) -> None:
        self.assertIn('name="robots" content="noindex, nofollow"', self.html)
        self.assertIn("demo-banner", self.html)
        self.assertIn("DEMO", self.html)
        self.assertIn("not an offer to sell securities", self.html.lower())
        self.assertRegex(self.html, r"<title>[^<]*Demo")

    def test_not_a_live_offering_cta(self) -> None:
        for phrase in FORBIDDEN_OFFERING_CTA:
            self.assertNotIn(phrase, self.html, f"offering CTA leaked: {phrase}")
        self.assertIn("Register interest", self.html)
        self.assertNotRegex(self.html, r">\s*Invest\s*<")

    def test_tesla_form_is_theirs_not_ours(self) -> None:
        self.assertIn("https://www.tesla.com/robotaxi/interest", self.html)
        self.assertIn("not Tesla", self.html)
        self.assertNotIn("Tesla partner", self.html)
        self.assertIn("not a Tesla dealer", self.html)
        lowered = self.html.lower()
        self.assertIn("not an official tesla asset", lowered)
        self.assertIn("that is not a partnership", lowered)

    def test_swfl_not_claimed_live(self) -> None:
        lowered = self.html.lower()
        self.assertIn("not swfl", lowered)
        self.assertIn("miami", lowered)
        self.assertIn("tampa", lowered)
        self.assertIn("orlando", lowered)

    def test_three_scenarios_and_honest_base_case(self) -> None:
        self.assertIn('data-scenario="conservative"', self.html)
        self.assertIn('data-scenario="base"', self.html)
        self.assertIn('data-scenario="optimistic"', self.html)
        self.assertIn("Does not beat", self.html)
        self.assertIn("Turo", self.html)
        self.assertIn("$1,651", self.html)
        self.assertIn("$3,693", self.html)
        self.assertIn("not the plan", self.html.lower())

    def test_risks_and_interest_form(self) -> None:
        self.assertIn('id="risks"', self.html)
        self.assertIn("Tesla may never sell", self.html)
        self.assertIn('id="interest-form"', self.html)
        for field in ("inv-name", "inv-email", "inv-role", "inv-size", "inv-message", "inv-accredited", "inv-ack"):
            self.assertIn(f'id="{field}"', self.html)
        self.assertIn('src="static/img/tesla-cybercab-hero.jpg"', self.html)
        self.assertGreater(HERO_IMG.stat().st_size, 10_000)
        self.assertNotIn("swfl-cybercab-hero.jpg", self.html)
        self.assertNotIn("illustrative concept", self.html.lower())
        self.assertNotIn("not tesla photography", self.html.lower())

    def test_hero_photo_is_credited_wikimedia(self) -> None:
        lowered = self.html.lower()
        self.assertIn("cc by 4.0", lowered)
        self.assertIn("wikimedia commons", lowered)
        self.assertIn("not affiliated with tesla", lowered)
        credits = HERO_CREDITS.read_text(encoding="utf-8").lower()
        self.assertIn("wikimedia", credits)
        self.assertIn("cc by 4.0", credits)
        self.assertIn("9yz", credits)

    def test_js_binds_interest_form(self) -> None:
        js = JS.read_text(encoding="utf-8")
        self.assertIn("interest-form", js)
        self.assertIn("ack_demo", js)
        self.assertIn("accredited", js)


if __name__ == "__main__":
    unittest.main()
