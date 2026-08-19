"""FCC Vercel preview: writes 403, treasury not public, one function, panels intact."""

from __future__ import annotations

import json
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

FC = Path(__file__).resolve().parents[1]
ROOT = FC.parent
if str(FC) not in sys.path:
    sys.path.insert(0, str(FC))


class FccVercelFunctionCount(unittest.TestCase):
    def test_single_dispatcher_under_hobby_cap(self):
        api = FC / "api"
        handlers = [
            p
            for p in api.rglob("*.py")
            if p.name != "__init__.py" and not p.name.startswith("_")
        ]
        self.assertEqual([p.name for p in handlers], ["index.py"])
        self.assertLessEqual(len(handlers), 12)
        vercel = json.loads((FC / "vercel.json").read_text(encoding="utf-8"))
        fns = vercel.get("functions") or {}
        self.assertEqual(list(fns.keys()), ["api/index.py"])
        self.assertLessEqual(len(fns), 12)


class FccTreasuryNotPublic(unittest.TestCase):
    def test_gitignore_and_vercelignore(self):
        gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("financial-command/treasury_latest.json", gi)
        vi = (FC / ".vercelignore").read_text(encoding="utf-8")
        self.assertIn("treasury_latest.json", vi)

    def test_not_tracked_in_git(self):
        out = subprocess.check_output(
            ["git", "-C", str(ROOT), "ls-files", "financial-command/treasury_latest.json"],
            text=True,
        ).strip()
        self.assertEqual(out, "")

    def test_static_path_does_not_leak(self):
        from api._lib import dispatch

        status, body = dispatch("GET", "denied_static")
        self.assertEqual(status, 404)
        blob = json.dumps(body)
        self.assertNotIn("evaluation", blob)
        self.assertNotIn("snapshot", blob)
        self.assertIn("not_public", blob)

    def test_placeholder_has_no_live_numbers(self):
        from api._lib import placeholder_treasury

        data = placeholder_treasury()
        ev = data["evaluation"]
        snap = data["snapshot"]
        self.assertEqual(ev["actions"], [])
        self.assertEqual(ev["sleeves"], {})
        self.assertEqual(ev["stress"], {})
        self.assertIsNone(snap["as_of"])
        blob = json.dumps(data)
        self.assertNotRegex(blob, r"0x[a-fA-F0-9]{20,}")
        self.assertNotRegex(blob, r"bc1[a-z0-9]{20,}")


class FccWritesForbidden(unittest.TestCase):
    def test_post_writes_403_json(self):
        from api._lib import dispatch

        for route in ("config", "refresh", "trade", "mint"):
            status, body = dispatch("POST", route)
            self.assertEqual(status, 403, route)
            self.assertFalse(body["ok"])
            self.assertEqual(body["error"], "read_only")
            self.assertIn("Do not trade or mint", body["message"])

    def test_trade_mint_all_methods_403(self):
        from api._lib import dispatch

        for route in ("trade", "mint"):
            for method in ("GET", "POST", "PUT"):
                status, body = dispatch(method, route)
                self.assertEqual(status, 403, f"{method} {route}")
                self.assertEqual(body["error"], "read_only")


class FccReadPaths(unittest.TestCase):
    def test_health_role(self):
        from api._lib import dispatch

        status, body = dispatch("GET", "health")
        self.assertEqual(status, 200)
        self.assertEqual(body["service"], "financial-command")
        self.assertEqual(body["role"], "vercel-preview")
        self.assertTrue(body["read_only"])

    def test_get_config_has_no_venue_keys(self):
        from api._lib import dispatch

        status, body = dispatch("GET", "config")
        self.assertEqual(status, 200)
        self.assertEqual(body["config"], {})
        self.assertFalse(body["venue_keys"])

    def test_treasury_from_env_not_git_file(self):
        from api._lib import dispatch

        payload = {
            "evaluation": {
                "actions": [{"title": "fixture-only"}],
                "agent_brief": "fixture brief no wallet",
                "sleeves": {"working_usdc": {}},
                "stress": {"overall": "ok"},
            },
            "snapshot": {"as_of": "2099-01-01T00:00:00Z"},
        }
        env = {"FCC_TREASURY_JSON": json.dumps(payload)}
        status, body = dispatch("GET", "treasury", env=env)
        self.assertEqual(status, 200)
        self.assertEqual(body["evaluation"]["agent_brief"], "fixture brief no wallet")
        self.assertTrue(body["preview"]["read_only"])
        self.assertEqual(body["preview"]["source"], "env")
        self.assertFalse(body["stale"])

    def test_stale_when_as_of_older_than_6h(self):
        from api._lib import placeholder_treasury, snapshot_stale

        old = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
        self.assertTrue(snapshot_stale({"snapshot": {"as_of": old}}))
        fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.assertFalse(snapshot_stale({"snapshot": {"as_of": fresh}}))
        self.assertTrue(snapshot_stale(placeholder_treasury()))

    def test_route_aliases(self):
        from api._lib import route_from_path

        self.assertEqual(route_from_path("/api?_r=refresh"), "refresh")
        self.assertEqual(route_from_path("/treasury_latest.json"), "denied_static")
        self.assertEqual(route_from_path("/api/trade/spot"), "trade")
        self.assertEqual(route_from_path("/api/mint"), "mint")


class FccUiNotGlanceOnly(unittest.TestCase):
    def test_index_keeps_full_panels(self):
        html = (FC / "index.html").read_text(encoding="utf-8")
        for needle in (
            'id="at-a-glance"',
            "Do now",
            'id="stress-grid"',
            'id="buffer-sleeves"',
            'id="nav-capital-flows"',
            "positions",
            'id="fcc-vercel-banner"',
        ):
            self.assertIn(needle, html, needle)
        self.assertNotIn('id="at-a-glance" hidden', html)
        self.assertIn('data-m-tab="glance"', html)
        self.assertIn('data-m-tab="cash"', html)

    def test_banner_hidden_unless_vercel_role(self):
        html = (FC / "index.html").read_text(encoding="utf-8")
        marker = 'id="fcc-vercel-banner"'
        self.assertIn(marker, html)
        snippet = html[html.find(marker) : html.find(marker) + 120]
        self.assertIn("hidden", snippet)
        self.assertIn('role === "vercel-preview"', html)
        self.assertIn("Do not trade or mint", html)


if __name__ == "__main__":
    unittest.main()
