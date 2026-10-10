"""FCC bind host: loopback by default, FCC_BIND_HOST override, horizon proxy stays local."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FCC = ROOT / "financial-command"


def _load_fcc_server():
    spec = importlib.util.spec_from_file_location("fcc_server_bind", FCC / "server.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestFccBindHost(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_fcc_server()

    def test_default_bind_is_loopback(self) -> None:
        self.assertEqual(self.mod.resolve_bind_host(None, {}), "127.0.0.1")
        self.assertEqual(self.mod.resolve_bind_host("", {}), "127.0.0.1")
        self.assertEqual(self.mod.resolve_bind_host("   ", {}), "127.0.0.1")

    def test_env_overrides_default(self) -> None:
        self.assertEqual(
            self.mod.resolve_bind_host(None, {"FCC_BIND_HOST": "0.0.0.0"}),
            "0.0.0.0",
        )
        self.assertEqual(
            self.mod.resolve_bind_host(None, {"FCC_BIND_HOST": " 192.0.2.10 "}),
            "192.0.2.10",
        )

    def test_cli_host_wins_over_env(self) -> None:
        self.assertEqual(
            self.mod.resolve_bind_host("192.0.2.20", {"FCC_BIND_HOST": "0.0.0.0"}),
            "192.0.2.20",
        )

    def test_horizon_proxy_stays_on_loopback(self) -> None:
        host, port = self.mod.LENS_UPSTREAMS["/horizon"]
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 8795)
        self.assertEqual(self.mod.LENS_UPSTREAMS["/fleet"][0], "127.0.0.1")

    def test_server_wires_env_name(self) -> None:
        src = (FCC / "server.py").read_text(encoding="utf-8")
        self.assertIn("FCC_BIND_HOST", src)
        self.assertIn("resolve_bind_host(args.host)", src)
        self.assertIn('default=None', src)


if __name__ == "__main__":
    unittest.main()
