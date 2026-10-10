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
        # endpoints.json ships with the tailnet MagicDNS short name, not a LAN IP
        cfg = de.load_endpoints()
        self.assertEqual(host, os.environ.get("PI_HOST") or os.environ.get("DASHBOARD_HOST") or cfg.get("pi_host") or de.FALLBACK_HOST)

    def test_env_pi_host_overrides(self) -> None:
        with mock.patch.dict(os.environ, {"PI_HOST": "100.64.1.2"}, clear=False):
            self.assertEqual(de.pi_host(), "100.64.1.2")

    def test_service_urls_use_pi_not_localhost(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "PRISM_PUBLIC_BASE"}
        env["PI_HOST"] = "192.0.2.10"
        with mock.patch.dict(os.environ, env, clear=True):
            for name in de.services():
                url = de.service_url(name)
                if name in de.LOOPBACK_ONLY_SERVICES:
                    self.assertNotIn("192.0.2.10", url, msg=name)
                    continue
                self.assertIn("192.0.2.10", url, msg=name)
                self.assertNotIn("127.0.0.1", url)
                self.assertNotIn("localhost", url)

    def test_committed_endpoints_have_no_lan_ip(self) -> None:
        text = de.DEFAULT_ENDPOINTS_PATH.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"\b192\.168\.\d{1,3}\.\d{1,3}\b")
        self.assertEqual(de.load_endpoints().get("pi_host"), "prism-gateway")
        self.assertEqual(de.FALLBACK_HOST, "prism-gateway")
        src = (ROOT / "dashboard_endpoints.py").read_text(encoding="utf-8")
        self.assertNotRegex(src, r"\b192\.168\.\d{1,3}\.\d{1,3}\b")

    def test_fcc_and_horizon_links_follow_prism_public_base(self) -> None:
        env = {"PRISM_PUBLIC_BASE": "https://fcc.unit.ts.net", "PI_HOST": "192.0.2.10"}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                de.service_url("financial-command"),
                "https://fcc.unit.ts.net/financial-command/index.html",
            )
            self.assertEqual(de.service_url("horizon"), "https://fcc.unit.ts.net/horizon/")
            m = de.domain_url_map()
            self.assertEqual(
                m["finance"], "https://fcc.unit.ts.net/financial-command/index.html"
            )
            # other services stay on pi_host
            self.assertEqual(de.service_url("orchestra"), "http://192.0.2.10:8790/")

    def test_fcc_and_horizon_links_fall_back_to_loopback(self) -> None:
        for base in (None, "", "https://prism.example.ts.net", "http://fcc.unit.ts.net"):
            env = {k: v for k, v in os.environ.items() if k != "PRISM_PUBLIC_BASE"}
            env["PI_HOST"] = "192.0.2.10"
            if base is not None:
                env["PRISM_PUBLIC_BASE"] = base
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(
                    de.service_url("financial-command"),
                    "http://127.0.0.1:8000/financial-command/index.html",
                    base,
                )
                self.assertEqual(de.service_url("horizon"), "http://127.0.0.1:8795/", base)

    def test_fcc_and_horizon_health_always_loopback(self) -> None:
        env = {"PRISM_PUBLIC_BASE": "https://fcc.unit.ts.net", "PI_HOST": "192.0.2.10"}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                de.health_url("financial-command"), "http://127.0.0.1:8000/api/health"
            )
            self.assertEqual(de.health_url("horizon"), "http://127.0.0.1:8795/api/health")
            self.assertEqual(de.health_url("orchestra"), "http://192.0.2.10:8790/api/health")

    def test_on_merge_health_checks_fcc_and_horizon_on_loopback(self) -> None:
        text = (ROOT / "deploy" / "on_merge.sh").read_text(encoding="utf-8")
        self.assertIn('financial-command|horizon) probe_host="127.0.0.1"', text)
        self.assertNotRegex(text, r"\b192\.168\.\d{1,3}\.\d{1,3}\b")

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

    def test_workspace_sync_script_pins_treasury(self) -> None:
        script = ROOT / "deploy" / "workspace_sync.sh"
        self.assertTrue(script.is_file())
        text = script.read_text(encoding="utf-8")
        self.assertIn('BRANCH="${SYNC_BRANCH:-work/treasury}"', text)
        self.assertIn("allowlist: work/treasury only", text)
        self.assertIn("on_merge.sh", text)
        self.assertNotIn("try-restart", text)
        self.assertIn("GITHUB_TOKEN", text)
        self.assertNotIn('fetch --prune "$REMOTE" master', text)
        # Preserve globs must not match fund_manager_journal_sync.py (#758).
        self.assertIn("-name '*journal.md'", text)
        self.assertIn("-name '*journal.jsonl'", text)
        self.assertNotRegex(text, r"-name '\*journal\*'")
        self.assertIn("! -name '*.py'", text)
        self.assertIn("! -name '*.pyc'", text)

    def test_horizon_is_registered_from_existing_bind(self) -> None:
        cfg = de.load_endpoints()
        services = cfg.get("services") or {}
        self.assertIn("horizon", services)
        self.assertEqual(int(services["horizon"]["port"]), 8795)
        self.assertEqual(services["horizon"].get("path"), "/")
        self.assertIn("horizon", de._DEFAULT_SERVICES)
        self.assertEqual(int(de._DEFAULT_SERVICES["horizon"]["port"]), 8795)
        env = {k: v for k, v in os.environ.items() if k != "PRISM_PUBLIC_BASE"}
        env["PI_HOST"] = "192.0.2.10"
        with mock.patch.dict(os.environ, env, clear=True):
            # Horizon binds 127.0.0.1: never linked on pi_host.
            url = de.service_url("horizon")
            self.assertEqual(url, "http://127.0.0.1:8795/")
            self.assertNotIn("vercel", url.lower())
            self.assertNotIn("192.0.2.10", url)

    def test_life_compass_is_registered_private_port(self) -> None:
        cfg = de.load_endpoints()
        services = cfg.get("services") or {}
        self.assertIn("life-compass", services)
        self.assertEqual(int(services["life-compass"]["port"]), 8793)
        self.assertIn("life-compass", de._DEFAULT_SERVICES)
        self.assertEqual(int(de._DEFAULT_SERVICES["life-compass"]["port"]), 8793)
        with mock.patch.dict(os.environ, {"PI_HOST": "192.0.2.10"}, clear=False):
            url = de.service_url("life-compass")
            self.assertEqual(url, "http://192.0.2.10:8793/")
            self.assertNotIn("127.0.0.1", url)

    def test_cli_prints_orchestra_url(self) -> None:
        env = {**os.environ, "PYTHONPATH": str(ROOT), "PI_HOST": "192.0.2.10"}
        out = subprocess.check_output(
            [sys.executable, str(ROOT / "dashboard_endpoints.py"), "orchestra"],
            env=env,
            text=True,
        ).strip()
        self.assertEqual(out, "http://192.0.2.10:8790/")

    def test_domains_specs_not_localhost(self) -> None:
        env = {**os.environ, "PYTHONPATH": str(ROOT), "PI_HOST": "192.0.2.10"}
        env.pop("PRISM_PUBLIC_BASE", None)
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
            if ":8000/" in line:
                # FCC binds 127.0.0.1: loopback fallback, never the Pi host.
                self.assertEqual(line, "http://127.0.0.1:8000/financial-command/index.html")
                continue
            self.assertNotIn("127.0.0.1", line)
            self.assertNotIn("localhost", line)
            self.assertIn("192.0.2.10", line)


if __name__ == "__main__":
    unittest.main()
