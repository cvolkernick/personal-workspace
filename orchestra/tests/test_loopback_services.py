"""FCC (:8000) and Horizon (:8795) bind 127.0.0.1 on prism.

Probes stay on loopback. Browser links come from PRISM_PUBLIC_BASE, with a
loopback fallback, never a LAN IP or a placeholder origin.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
ORCH = ROOT / "orchestra"
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import collectors  # noqa: E402
import launcher  # noqa: E402
import pulse  # noqa: E402
from collectors import _default_probe_host, collect_finance  # noqa: E402

VALID = "https://fcc.unit.ts.net"
FCC_LOOPBACK = "http://127.0.0.1:8000/financial-command/index.html"
HORIZON_LOOPBACK = "http://127.0.0.1:8795/"
LAN_RE = re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b")


def _env_without_base(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "PRISM_PUBLIC_BASE"}
    env.update(extra)
    return env


class ProbeHostTests(unittest.TestCase):
    def test_probe_host_is_loopback_not_pi_host(self) -> None:
        self.assertEqual(_default_probe_host({}), "127.0.0.1")
        self.assertEqual(_default_probe_host({"PI_HOST": "192.0.2.10"}), "127.0.0.1")
        self.assertEqual(
            _default_probe_host({"PRISM_PUBLIC_BASE": VALID}), "127.0.0.1"
        )
        self.assertEqual(
            _default_probe_host({"ORCHESTRA_PROBE_HOST": "192.0.2.10"}), "192.0.2.10"
        )

    def test_probe_port_defaults_to_loopback(self) -> None:
        seen: list[tuple] = []

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(addr, timeout=None):
            seen.append(addr)
            return _Conn()

        with mock.patch.dict(os.environ, _env_without_base(PI_HOST="192.0.2.10"), clear=True):
            with mock.patch.object(collectors.socket, "create_connection", fake):
                self.assertTrue(collectors.probe_port(8000))
                self.assertTrue(collectors.probe_port(8795))
        self.assertEqual(seen, [("127.0.0.1", 8000), ("127.0.0.1", 8795)])


class FinanceAndHorizonLinkTests(unittest.TestCase):
    def test_finance_link_uses_public_base(self) -> None:
        with mock.patch.dict(os.environ, {"PRISM_PUBLIC_BASE": VALID}):
            with tempfile.TemporaryDirectory() as td:
                snap = collect_finance(Path(td))
        self.assertEqual(snap["url"], VALID + "/financial-command/index.html")
        self.assertEqual(snap["port"], 8000)

    def test_finance_link_falls_back_to_loopback(self) -> None:
        for base in (None, "https://prism.example.ts.net", "http://fcc.unit.ts.net"):
            env = _env_without_base(PI_HOST="192.0.2.10")
            if base is not None:
                env["PRISM_PUBLIC_BASE"] = base
            with mock.patch.dict(os.environ, env, clear=True):
                with tempfile.TemporaryDirectory() as td:
                    snap = collect_finance(Path(td))
            self.assertEqual(snap["url"], FCC_LOOPBACK, base)

    def test_horizon_world_link(self) -> None:
        with mock.patch.dict(os.environ, {"PRISM_PUBLIC_BASE": VALID}):
            self.assertEqual(pulse.horizon_url(), VALID + "/horizon/")
            self.assertEqual(pulse.build_world({})["url"], VALID + "/horizon/")
            self.assertEqual(launcher.domain_spec("horizon")["url"], VALID + "/horizon/")
        with mock.patch.dict(os.environ, _env_without_base(PI_HOST="192.0.2.10"), clear=True):
            self.assertEqual(pulse.horizon_url(), HORIZON_LOOPBACK)
            self.assertEqual(launcher.domain_spec("horizon")["url"], HORIZON_LOOPBACK)


class NoLanIpTests(unittest.TestCase):
    def test_orchestra_runtime_has_no_lan_ip(self) -> None:
        for name in ("collectors.py", "domains.py", "payload.py", "public_base.py", "pulse.py"):
            text = (ORCH / name).read_text(encoding="utf-8")
            self.assertIsNone(LAN_RE.search(text), name)


if __name__ == "__main__":
    unittest.main()
