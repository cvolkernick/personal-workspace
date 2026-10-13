"""Boot-time Tailscale Serve re-apply (fcc-tailscale-serve.service): tailnet-only."""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
SCRIPT = DEPLOY / "tailscale-serve-apply.sh"
UNIT = DEPLOY / "fcc-tailscale-serve.service"


class TestTailscaleServeDeploy(unittest.TestCase):
    def test_script_maps_fcc_horizon_and_fitdash_only(self) -> None:
        src = SCRIPT.read_text(encoding="utf-8")
        cmds = [ln.strip() for ln in src.splitlines() if re.match(r"\s*sudo -n", ln)]
        self.assertEqual(
            cmds,
            [
                'sudo -n "$TS" serve --bg --yes --https=443 http://127.0.0.1:8000',
                'sudo -n "$TS" serve --bg --yes --https=10000 http://127.0.0.1:8795',
                'sudo -n "$TS" serve --bg --yes --https=10001 http://127.0.0.1:8787',
            ],
        )
        code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
        self.assertNotIn("funnel", code.lower())
        self.assertNotIn("8443", code)
        self.assertNotIn(" reset", code)

    def test_script_waits_for_running(self) -> None:
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("BackendState", src)
        self.assertIn('[ "$state" = "Running" ]', src)
        self.assertLess(src.index("BackendState"), src.index("sudo -n \"$TS\" serve"))

    @unittest.skipUnless(shutil.which("bash"), "bash missing")
    def test_script_parses(self) -> None:
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_unit_runs_via_bash_and_retries(self) -> None:
        unit = UNIT.read_text(encoding="utf-8")
        self.assertIn(
            "ExecStart=/bin/bash %h/personal-workspace/financial-command/deploy/tailscale-serve-apply.sh",
            unit,
        )
        self.assertIn("Type=oneshot", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("WantedBy=default.target", unit)
        self.assertNotIn("funnel", unit.lower().replace("never funnel", ""))


if __name__ == "__main__":
    unittest.main()
