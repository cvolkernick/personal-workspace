"""Tests for public host rewrite (Pi-hosted Orchestrator deep-links)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parents[1]
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))

from public_base import (  # noqa: E402
    public_hostname,
    rewrite_loopback_url,
    rewrite_payload_urls,
)


class PublicBaseTests(unittest.TestCase):
    def test_env_host_wins(self) -> None:
        h = public_hostname(
            request_host_header="ignored:8790",
            env={"ORCHESTRATOR_PUBLIC_HOST": "192.0.2.10"},
        )
        self.assertEqual(h, "192.0.2.10")

    def test_host_header_fallback(self) -> None:
        h = public_hostname(request_host_header="prism-gateway:8790", env={})
        self.assertEqual(h, "prism-gateway")

    def test_rewrite_loopback(self) -> None:
        u = rewrite_loopback_url("http://127.0.0.1:8765/", "192.0.2.10")
        self.assertEqual(u, "http://192.0.2.10:8765/")

    def test_rewrite_payload_domains(self) -> None:
        payload = {
            "ok": True,
            "domains": [
                {"id": "workflow", "url": "http://127.0.0.1:8765/"},
                {"id": "iot", "url": "http://localhost:8780/"},
            ],
            "links": [
                {"id": "finance", "url": "http://127.0.0.1:8000/financial-command/"},
                {"id": "horizon", "url": "http://127.0.0.1:8795/"},
            ],
            "meta": {"url": "http://127.0.0.1:8790/"},
        }
        out = rewrite_payload_urls(payload, "192.0.2.10")
        self.assertEqual(out["domains"][0]["url"], "http://192.0.2.10:8765/")
        self.assertEqual(out["domains"][1]["url"], "http://192.0.2.10:8780/")
        # FCC and Horizon bind 127.0.0.1: their loopback links are not rewritten.
        self.assertEqual(
            out["links"][0]["url"],
            "http://127.0.0.1:8000/financial-command/",
        )
        self.assertEqual(out["links"][1]["url"], "http://127.0.0.1:8795/")
        self.assertEqual(out["meta"]["url"], "http://192.0.2.10:8790/")

    def test_rewrite_deep_link(self) -> None:
        payload = {
            "day_plan": {
                "gates": [
                    {"domain": "finance", "deep_link": "http://127.0.0.1:8000/financial-command/"},
                    {"domain": "holistic", "deep_link": "http://127.0.0.1:8770/"},
                ]
            }
        }
        out = rewrite_payload_urls(payload, "prism-gateway")
        self.assertEqual(
            out["day_plan"]["gates"][0]["deep_link"],
            "http://127.0.0.1:8000/financial-command/",
        )
        self.assertEqual(
            out["day_plan"]["gates"][1]["deep_link"],
            "http://prism-gateway:8770/",
        )

    def test_loopback_only_ports_never_rewritten(self) -> None:
        for url in (
            "http://127.0.0.1:8000/financial-command/index.html",
            "http://localhost:8795/",
            "http://[::1]:8795/api/health",
        ):
            self.assertEqual(rewrite_loopback_url(url, "192.0.2.10"), url)
        self.assertEqual(
            rewrite_loopback_url("http://127.0.0.1:8796/", "192.0.2.10"),
            "http://192.0.2.10:8796/",
        )

    def test_public_base_links_untouched(self) -> None:
        url = "https://fcc.unit.ts.net/financial-command/index.html"
        self.assertEqual(rewrite_loopback_url(url, "192.0.2.10"), url)


if __name__ == "__main__":
    unittest.main()
