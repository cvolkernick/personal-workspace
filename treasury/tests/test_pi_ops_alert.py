"""GitHub #701 alert sink (#704 / #736)."""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.pi_ops_alert import post_ops_github  # noqa: E402


class TestPostOpsGithub(unittest.TestCase):
    def test_as_markdown_does_not_wrap_body_in_fence(self) -> None:
        captured: dict = {}

        def fake_urlopen(req, timeout=15):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            resp = mock.MagicMock()
            resp.status = 201
            resp.getcode.return_value = 201
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = None
            return resp

        env = {"GITHUB_TOKEN": "ghs_test", "PI_OPS_ALERT_ISSUE": "701"}
        with mock.patch.dict(os.environ, env, clear=False), mock.patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ):
            out = post_ops_github(
                "FCC · fund-manager hold · prism",
                "<!-- fund-manager-decision schema=1 -->\n**kind:** `hold`\n",
                as_markdown=True,
            )
        self.assertTrue(out.get("posted"), out)
        comment = captured["body"]["body"]
        self.assertTrue(comment.startswith("**FCC · fund-manager hold · prism**"))
        self.assertIn("<!-- fund-manager-decision schema=1 -->", comment)
        self.assertNotIn("```\n<!-- fund-manager-decision", comment)

    def test_default_still_fences_plain_alerts(self) -> None:
        captured: dict = {}

        def fake_urlopen(req, timeout=15):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            resp = mock.MagicMock()
            resp.status = 201
            resp.getcode.return_value = 201
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = None
            return resp

        env = {"GITHUB_TOKEN": "ghs_test", "PI_OPS_ALERT_ISSUE": "701"}
        with mock.patch.dict(os.environ, env, clear=False), mock.patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ):
            out = post_ops_github("FCC · stale RH feed", "[prism] robinhood snapshot is old")
        self.assertTrue(out.get("posted"), out)
        comment = captured["body"]["body"]
        self.assertIn("```\n[prism] robinhood snapshot is old\n```", comment)
