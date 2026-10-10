"""FCC bind host: loopback by default, FCC_BIND_HOST override, horizon proxy stays local."""

from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock

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
        main = src.split("def main(", 1)[1]
        guard = main.index("prism_public.guard_prism_public_base()")
        refresh = main.index("run_treasury_main(")
        self.assertLess(guard, refresh)

    def test_systemd_missing_base_exits_before_refresh(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"INVOCATION_ID": "unit-test", "PRISM_PUBLIC_BASE": ""},
            clear=False,
        ):
            with mock.patch.object(self.mod, "run_treasury_main") as refresh:
                with self.assertRaises(SystemExit) as ctx:
                    self.mod.main(["--offline", "--no-browser", "--port", "9"])
        self.assertEqual(ctx.exception.code, 1)
        refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
