"""Fund-manager producer topology (#729): Pi systemd, Mac launchd retired."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEPLOY = ROOT / "treasury" / "deploy"
FM_PY = ROOT / "treasury" / "fund_manager.py"
DAILY = ROOT / "treasury" / "fund_manager_daily.sh"
BP_POLL = ROOT / "treasury" / "fund_manager_bp_poll.sh"


class TestFundManagerProducerDeploy(unittest.TestCase):
    def test_mac_plists_are_retired_stubs(self) -> None:
        for name in (
            "com.personalworkspace.fund-manager-daily.plist",
            "com.personalworkspace.fund-manager-bp-poll.plist",
        ):
            text = (DEPLOY / name).read_text(encoding="utf-8")
            self.assertIn("RETIRED (#729)", text)
            self.assertIn("<key>Disabled</key>", text)
            self.assertIn("/usr/bin/true", text)
            self.assertNotIn("fund_manager_daily.sh", text)
            self.assertNotIn("fund_manager_bp_poll.sh", text)
            self.assertNotIn("personal-workspace-worktrees/treasury", text)

    def test_pi_units_use_prism_agent_not_home_pi(self) -> None:
        for name in ("fund-manager.service", "fund-manager-bp-poll.service"):
            text = (DEPLOY / name).read_text(encoding="utf-8")
            self.assertIn("User=prism-agent", text)
            self.assertIn("FCC_HOST_TAG=prism", text)
            self.assertIn("/home/prism-agent/personal-workspace", text)
            self.assertNotIn("/home/pi/", text)
            self.assertIn("TimeoutStartSec=2h", text)

    def test_repo_fund_manager_has_no_ntfy_post(self) -> None:
        text = FM_PY.read_text(encoding="utf-8")
        self.assertNotIn("ntfy.sh", text)
        self.assertNotIn("def push_ntfy", text)
        self.assertIn("warn_retired_ntfy", text)

    def test_wrappers_sync_journal_after_every_run(self) -> None:
        needle = "python3 -m treasury.fund_manager_journal_sync"
        daily = DAILY.read_text(encoding="utf-8")
        poll = BP_POLL.read_text(encoding="utf-8")
        self.assertIn(needle, daily)
        self.assertIn(needle, poll)
        self.assertIn("sync_fm_journal", daily)
        self.assertIn("sync_fm_journal", poll)
        self.assertGreaterEqual(daily.count("sync_fm_journal"), 4)
        self.assertGreaterEqual(poll.count("sync_fm_journal"), 4)

    def test_pi_units_pin_journal_branch(self) -> None:
        for name in ("fund-manager.service", "fund-manager-bp-poll.service"):
            text = (DEPLOY / name).read_text(encoding="utf-8")
            self.assertIn("FM_JOURNAL_BRANCH=work/treasury", text)
            self.assertIn("FCC_HOST_TAG=prism", text)
            # Auth is load_scheduler_env + insteadOf, not a unit EnvironmentFile (#737).
            self.assertNotIn("EnvironmentFile=", text)
            self.assertNotIn("GITHUB_TOKEN=", text)

    def test_journal_sync_uses_workspace_sync_insteadOf(self) -> None:
        text = (ROOT / "treasury" / "fund_manager_journal_sync.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("load_scheduler_env", text)
        self.assertIn("github_token", text)
        self.assertIn(
            "url.https://x-access-token:{token}@github.com/.insteadOf=https://github.com/",
            text,
        )
        self.assertIn('frozenset({"pull", "push", "fetch", "ls-remote"})', text)


if __name__ == "__main__":
    unittest.main()
