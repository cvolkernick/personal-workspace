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
            env={"ORCHESTRATOR_PUBLIC_HOST": "192.168.100.98"},
        )
        self.assertEqual(h, "192.168.100.98")

    def test_host_header_fallback(self) -> None:
        h = public_hostname(request_host_header="prism-gateway:8790", env={})
        self.assertEqual(h, "prism-gateway")

    def test_rewrite_loopback(self) -> None:
        u = rewrite_loopback_url("http://127.0.0.1:8765/", "192.168.100.98")
        self.assertEqual(u, "http://192.168.100.98:8765/")

    def test_rewrite_payload_domains(self) -> None:
        payload = {
            "ok": True,
            "domains": [
                {"id": "workflow", "url": "http://127.0.0.1:8765/"},
                {"id": "iot", "url": "http://localhost:8780/"},
            ],
            "links": [
                {"id": "finance", "url": "http://127.0.0.1:8000/financial-command/"}
            ],
            "meta": {"url": "http://127.0.0.1:8790/"},
        }
        out = rewrite_payload_urls(payload, "192.168.100.98")
        self.assertEqual(out["domains"][0]["url"], "http://192.168.100.98:8765/")
        self.assertEqual(out["domains"][1]["url"], "http://192.168.100.98:8780/")
        self.assertEqual(
            out["links"][0]["url"],
            "http://192.168.100.98:8000/financial-command/",
        )
        self.assertEqual(out["meta"]["url"], "http://192.168.100.98:8790/")


if __name__ == "__main__":
    unittest.main()
