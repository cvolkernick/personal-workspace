"""Validator for PRISM_PUBLIC_BASE / DAILY_BRIEF_PUBLIC_BASE."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path

import prism_public

ROOT = Path(__file__).resolve().parents[1]
VALID = "https://fcc.unit.ts.net"
PLACEHOLDER = "https://prism.example.ts.net"


class ParseTests(unittest.TestCase):
    def test_https_origin_and_trailing_slash(self) -> None:
        self.assertEqual(prism_public.parse_public_base(VALID), VALID)
        self.assertEqual(prism_public.parse_public_base(VALID + "/"), VALID)
        self.assertEqual(prism_public.parse_public_base("HTTPS://FCC.UNIT.TS.NET"), VALID)

    def test_rejects_scheme_inference_http_ip_and_localhost(self) -> None:
        for raw in (
            "",
            "   ",
            "fcc.unit.ts.net",
            "http://fcc.unit.ts.net",
            "https://192.0.2.1",
            "https://192.0.2.01",
            "https://127.0.0.1",
            "https://localhost",
            "https://fcc.localhost",
            "https://notahost",
            PLACEHOLDER,
            "https://dash.example.ts.net",
            "https://fcc.example",
            "https://fcc.unit.test",
            "https://fcc.unit.invalid",
            "https://user@fcc.unit.ts.net",
            "https://user:pass@fcc.unit.ts.net",
            "https://fcc.unit.ts.net:443",
            "https://fcc.unit.ts.net/horizon",
            "https://fcc.unit.ts.net/other",
            "https://fcc.unit.ts.net?q=1",
            "https://fcc.unit.ts.net#frag",
            "https://fcc..unit.ts.net",
        ):
            self.assertIsNone(prism_public.parse_public_base(raw), raw)

    def test_daily_brief_allows_only_horizon_path(self) -> None:
        self.assertEqual(
            prism_public.parse_public_base(VALID, allow_horizon=True),
            VALID,
        )
        self.assertEqual(
            prism_public.parse_public_base(VALID + "/horizon", allow_horizon=True),
            VALID + "/horizon",
        )
        self.assertEqual(
            prism_public.parse_public_base(VALID + "/horizon/", allow_horizon=True),
            VALID + "/horizon",
        )
        for raw in (
            VALID + "/horizon/extra",
            VALID + "/other",
            PLACEHOLDER + "/horizon",
            "http://fcc.unit.ts.net/horizon",
        ):
            self.assertIsNone(
                prism_public.parse_public_base(raw, allow_horizon=True), raw
            )


class GuardTests(unittest.TestCase):
    def setUp(self) -> None:
        prism_public.reset_warnings()

    def test_unset_warns_and_returns_empty(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(prism_public.guard_prism_public_base({}), "")
            self.assertEqual(prism_public.guard_prism_public_base({}), "")
        text = err.getvalue()
        self.assertEqual(text.count("ERROR:"), 1)
        self.assertIn("unset", text)
        self.assertNotIn("prism.example.ts.net", text)

    def test_invalid_warns_without_using_placeholder(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            origin = prism_public.guard_prism_public_base(
                {"PRISM_PUBLIC_BASE": PLACEHOLDER}
            )
        self.assertEqual(origin, "")
        self.assertIn("invalid", err.getvalue())
        self.assertEqual(prism_public.prism_public_origin({"PRISM_PUBLIC_BASE": PLACEHOLDER}), "")
        self.assertEqual(prism_public.canonical_https_host({}), "")
        self.assertEqual(
            prism_public.canonical_https_host({"PRISM_PUBLIC_BASE": VALID}),
            "fcc.unit.ts.net",
        )

    def test_valid_is_silent_even_under_systemd(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            origin = prism_public.guard_prism_public_base(
                {"PRISM_PUBLIC_BASE": VALID + "/", "INVOCATION_ID": "abc"}
            )
        self.assertEqual(origin, VALID)
        self.assertEqual(err.getvalue(), "")

    def test_systemd_unset_exits_nonzero(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                prism_public.guard_prism_public_base({"INVOCATION_ID": "abc"})
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("ERROR:", err.getvalue())

    def test_systemd_invalid_exits_after_deduped_warning(self) -> None:
        env = {"PRISM_PUBLIC_BASE": "http://fcc.unit.ts.net", "INVOCATION_ID": "abc"}
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                prism_public.guard_prism_public_base(env)
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("ERROR:", err.getvalue())

    def test_reset_warnings_prints_again(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            prism_public.guard_prism_public_base({}, fatal_under_systemd=False)
            prism_public.reset_warnings()
            prism_public.guard_prism_public_base({}, fatal_under_systemd=False)
        self.assertEqual(err.getvalue().count("ERROR:"), 2)


class DailyBriefBaseTests(unittest.TestCase):
    def setUp(self) -> None:
        prism_public.reset_warnings()

    def test_explicit_horizon_and_origin(self) -> None:
        self.assertEqual(
            prism_public.daily_brief_public_base(
                {"DAILY_BRIEF_PUBLIC_BASE": VALID + "/horizon"}
            ),
            VALID + "/horizon",
        )
        self.assertEqual(
            prism_public.daily_brief_public_base({"DAILY_BRIEF_PUBLIC_BASE": VALID}),
            VALID,
        )

    def test_prism_origin_appends_horizon(self) -> None:
        self.assertEqual(
            prism_public.daily_brief_public_base({"PRISM_PUBLIC_BASE": VALID}),
            VALID + "/horizon",
        )

    def test_invalid_explicit_does_not_fall_through(self) -> None:
        err = io.StringIO()
        env = {
            "DAILY_BRIEF_PUBLIC_BASE": PLACEHOLDER + "/horizon",
            "PRISM_PUBLIC_BASE": VALID,
        }
        with redirect_stderr(err):
            self.assertIsNone(prism_public.daily_brief_public_base(env))
        self.assertIn("DAILY_BRIEF_PUBLIC_BASE", err.getvalue())
        self.assertIn("Not falling through", err.getvalue())

    def test_unset_warns_and_cli_does_not_exit_under_systemd(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertIsNone(
                prism_public.daily_brief_public_base({"INVOCATION_ID": "abc"})
            )
        self.assertIn("ERROR:", err.getvalue())
        self.assertIn("relative path", err.getvalue())


class RuntimeSourceTests(unittest.TestCase):
    def test_placeholder_stays_out_of_runtime_modules(self) -> None:
        files = [
            ROOT / "prism_public.py",
            ROOT / "financial-command" / "server.py",
            ROOT / "financial-command" / "pwa.js",
            ROOT / "research" / "daily_brief" / "cli.py",
            ROOT / "orchestra" / "domains.py",
            ROOT / "orchestra" / "collectors.py",
            ROOT / "orchestra" / "server.py",
        ]
        for path in files:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("prism.example.ts.net", text, str(path))


if __name__ == "__main__":
    unittest.main()
