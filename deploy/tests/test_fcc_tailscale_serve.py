#!/usr/bin/env python3
"""FCC Tailscale Serve helper stays private HTTPS, never Funnel/Vercel (#623)."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "fcc_tailscale_serve.sh"
UNIT = ROOT / "deploy" / "units" / "fcc-tailscale-serve.service"
SOP = ROOT / "deploy" / "DEPLOY_CONVENTIONS.md"
README = ROOT / "deploy" / "README.md"


class TestFccTailscaleServe(unittest.TestCase):
    def test_script_and_unit_exist_and_are_executable_intent(self) -> None:
        self.assertTrue(SCRIPT.is_file(), SCRIPT)
        self.assertTrue(UNIT.is_file(), UNIT)
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertTrue(src.startswith("#!/usr/bin/env bash"))
        self.assertIn("set -euo pipefail", src)

    def test_serve_loopback_8000_not_funnel(self) -> None:
        src = SCRIPT.read_text(encoding="utf-8")
        unit = UNIT.read_text(encoding="utf-8")
        self.assertIn("serve --bg --yes http://127.0.0.1:8000", src)
        self.assertIn("serve --bg --yes http://127.0.0.1:8000", unit)
        self.assertIn("Never Funnel", src)
        self.assertIn("Never Funnel", unit)
        self.assertIn("tailnet only", src)
        self.assertIn("install_remote.sh", src)
        self.assertIn("work/treasury", src)
        # Status checks are allowed; enabling Funnel is not.
        for raw, label in ((src, "script"), (unit, "unit")):
            for line in raw.splitlines():
                stripped = line.split("#", 1)[0].strip()
                if not stripped:
                    continue
                self.assertNotRegex(
                    stripped,
                    r"tailscale\s+funnel\s+(--bg|on|https)",
                    msg=f"{label} enables funnel: {stripped}",
                )

    def test_does_not_rsync_onto_fcc_live_tree(self) -> None:
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("Does NOT rsync", src)
        for line in src.splitlines():
            stripped = line.split("#", 1)[0].strip()
            if not stripped:
                continue
            self.assertNotRegex(stripped, r"(^|[;&|]\s*)rsync\b", msg=stripped)

    def test_sop_maps_fcc_to_tailscale_not_vercel(self) -> None:
        sop = SOP.read_text(encoding="utf-8")
        self.assertIn("prism-gateway.tailb1085a.ts.net", sop)
        self.assertIn("Tailscale Serve", sop)
        self.assertIn("financial-command", sop)
        self.assertIn("Never Vercel", sop)
        self.assertIn("Never Funnel", sop)
        vercel_section = sop.split("## Private HTTPS", 1)[0]
        self.assertIn("| `fitdash` |", vercel_section)
        self.assertIn("| `mikrafts` |", vercel_section)
        self.assertNotIn("financial-command", vercel_section.split("## Project map", 1)[-1].split("## Rules", 1)[0])

    def test_readme_documents_https_origin(self) -> None:
        readme = README.read_text(encoding="utf-8")
        self.assertIn("https://prism-gateway.tailb1085a.ts.net/", readme)
        self.assertIn("fcc_tailscale_serve.sh", readme)
        self.assertIn("tailnet", readme.lower())


if __name__ == "__main__":
    unittest.main()
