"""Tests for always-on Pi endpoint resolution and open path."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dashboard_endpoints as de  # noqa: E402


class EndpointResolveTests(unittest.TestCase):
    def test_default_pi_host_from_config_file(self) -> None:
        host = de.pi_host()
        self.assertTrue(host)
        # endpoints.json ships with LAN Pi IP
        cfg = de.load_endpoints()
        self.assertEqual(host, os.environ.get("PI_HOST") or os.environ.get("DASHBOARD_HOST") or cfg.get("pi_host") or de.FALLBACK_HOST)

    def test_env_pi_host_overrides(self) -> None:
        with mock.patch.dict(os.environ, {"PI_HOST": "100.64.1.2"}, clear=False):
            self.assertEqual(de.pi_host(), "100.64.1.2")

    def test_service_urls_use_pi_not_localhost(self) -> None:
        with mock.patch.dict(os.environ, {"PI_HOST": "192.168.100.98"}, clear=False):
            for name in de.services():
                url = de.service_url(name)
                self.assertIn("192.168.100.98", url, msg=name)
                self.assertNotIn("127.0.0.1", url)
                self.assertNotIn("localhost", url)

    def test_domain_url_map_covers_subordinates(self) -> None:
        m = de.domain_url_map()
        for key in ("workflow", "finance", "fitness", "holistic", "iot"):
            self.assertIn(key, m)
            self.assertTrue(m[key].startswith("http"))

    def test_open_dashboard_script_exists_and_names_services(self) -> None:
        script = ROOT / "deploy" / "open_dashboard.sh"
        self.assertTrue(script.is_file())
        text = script.read_text(encoding="utf-8")
        self.assertIn("open_dashboard", text)
        self.assertIn("service_url", text)

    def test_workspace_sync_script_pulls_master(self) -> None:
        script = ROOT / "deploy" / "workspace_sync.sh"
        self.assertTrue(script.is_file())
        text = script.read_text(encoding="utf-8")
        self.assertIn("origin", text)
        self.assertIn("master", text)
        self.assertIn("try-restart", text)
        self.assertIn("GITHUB_TOKEN", text)

    def test_cli_prints_orchestra_url(self) -> None:
        env = {**os.environ, "PYTHONPATH": str(ROOT), "PI_HOST": "192.168.100.98"}
        out = subprocess.check_output(
            [sys.executable, str(ROOT / "dashboard_endpoints.py"), "orchestra"],
            env=env,
            text=True,
        ).strip()
        self.assertEqual(out, "http://192.168.100.98:8790/")

    def test_domains_specs_not_localhost(self) -> None:
        env = {**os.environ, "PYTHONPATH": str(ROOT), "PI_HOST": "192.168.100.98"}
        # Import domains fresh in subprocess so env applies
        code = (
            "import domains; "
            "urls=[d.get('url') for d in domains.DOMAIN_SPECS if d.get('url')]; "
            "print('\\n'.join(urls))"
        )
        out = subprocess.check_output(
            [sys.executable, "-c", code],
            cwd=str(ROOT / "orchestra"),
            env=env,
            text=True,
        )
        for line in out.strip().splitlines():
            self.assertNotIn("127.0.0.1", line)
            self.assertNotIn("localhost", line)
            self.assertIn("192.168.100.98", line)


if __name__ == "__main__":
    unittest.main()
