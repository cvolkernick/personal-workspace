"""FCC Vercel preview: writes 403, treasury not public, one function, pages not thinned."""

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
        self.assertIn("watchlist", body["features"])
        self.assertIn("capital_flows", body["features"])

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

    def test_agent_brief_rebuilt_only_if_wallet(self):
        from api._lib import dispatch

        env = {
            "FCC_TREASURY_JSON": json.dumps(
                {
                    "evaluation": {
                        "agent_brief": "send to 0x" + ("ab" * 20),
                        "actions": [],
                        "sleeves": {},
                        "stress": {},
                    },
                    "snapshot": {"as_of": "2099-01-01T00:00:00Z"},
                }
            )
        }
        status, body = dispatch("GET", "treasury", env=env)
        self.assertEqual(status, 200)
        self.assertTrue(body.get("agent_brief_rebuilt"))
        self.assertIn("[wallet redacted]", body["evaluation"]["agent_brief"])
        self.assertNotIn("0xabab", body["evaluation"]["agent_brief"])

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
        self.assertEqual(route_from_path("/api/capital-flows"), "capital-flows")
        self.assertEqual(route_from_path("/api/watchlist"), "watchlist")
        self.assertEqual(
            route_from_path("/api/watchlist/deep-dive?symbol=BE"),
            "watchlist-deep-dive",
        )
        self.assertEqual(route_from_path("/"), "page")
        self.assertEqual(route_from_path("/capital-flows.html"), "page")


class FccUiNotGlanceOnly(unittest.TestCase):
    def test_index_keeps_full_panels(self):
        html = (FC / "index.html").read_text(encoding="utf-8")
        for needle in (
            'id="at-a-glance"',
            "Do now",
            'id="stress-grid"',
            'id="buffer-sleeves"',
            'id="nav-capital-flows"',
            'id="nav-watchlist"',
            "positions",
            'id="fcc-vercel-banner"',
            'href="capital-flows.html"',
            'href="watchlist.html"',
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
        self.assertIn("Do not trade or mint", html)


class FccAddendumPages(unittest.TestCase):
    def test_capital_flows_page_not_thinned(self):
        html = (FC / "capital-flows.html").read_text(encoding="utf-8")
        for needle in (
            "Capital Flows",
            "Flow map",
            'id="flow-svg"',
            "Edges (detail)",
            "Open questions",
            'href="index.html"',
            'href="watchlist.html"',
            'id="nav-orchestra"',
            'id="fcc-vercel-banner"',
            "vercel-readonly.js",
            "/api/capital-flows",
        ):
            self.assertIn(needle, html, needle)

    def test_watchlist_page_not_thinned(self):
        html = (FC / "watchlist.html").read_text(encoding="utf-8")
        for needle in (
            "Watchlist",
            "Strategy",
            "Agentic book context",
            "Private companies",
            'id="entries"',
            'id="full-dive-card"',
            'href="index.html"',
            'href="capital-flows.html"',
            'id="nav-orchestra"',
            'id="fcc-vercel-banner"',
            "vercel-readonly.js",
            "/api/watchlist",
        ):
            self.assertIn(needle, html, needle)

    def test_capital_flows_api_is_full_shape(self):
        from api._lib import dispatch

        status, body = dispatch("GET", "capital-flows")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIn("channels", body)
        self.assertIn("income_sources", body)
        self.assertIn("edges", body)
        self.assertIn("layout", body)
        self.assertGreaterEqual(len(body["channels"]), 3)
        self.assertGreaterEqual(len(body["edges"]), 5)
        self.assertNotIn("unavailable", body)

    def test_watchlist_api_is_full_shape(self):
        from api._lib import dispatch

        status, body = dispatch("GET", "watchlist")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIn("entries", body)
        self.assertIn("private_watchlist", body)
        self.assertIn("agentic_held", body)
        self.assertNotIn("unavailable", body)

    def test_watchlist_env_and_deep_dive(self):
        from api._lib import dispatch

        env = {
            "FCC_WATCHLIST_JSON": json.dumps(
                {
                    "ok": True,
                    "entries": [
                        {
                            "symbol": "BE",
                            "name": "fixture",
                            "status": "monitor",
                            "deep_dive": {"markdown": "# fixture dive", "exists": True},
                        }
                    ],
                    "private_watchlist": {"entries": [], "count": 0},
                }
            )
        }
        status, body = dispatch("GET", "watchlist", env=env)
        self.assertEqual(status, 200)
        self.assertEqual(body["entries"][0]["symbol"], "BE")
        status, dive = dispatch(
            "GET",
            "watchlist-deep-dive",
            env=env,
            query={"symbol": ["BE"]},
        )
        self.assertEqual(status, 200)
        self.assertTrue(dive["ok"])
        self.assertEqual(dive["markdown"], "# fixture dive")

    def test_page_rewrites_in_vercel_json(self):
        vercel = json.loads((FC / "vercel.json").read_text(encoding="utf-8"))
        dests = {r["source"]: r["destination"] for r in vercel["rewrites"]}
        self.assertEqual(dests["/"], "/api?_r=page&_p=index")
        self.assertEqual(dests["/capital-flows.html"], "/api?_r=page&_p=capital-flows")
        self.assertEqual(dests["/watchlist.html"], "/api?_r=page&_p=watchlist")
        self.assertEqual(dests["/financial-command/capital-flows.html"], "/api?_r=page&_p=capital-flows")
        self.assertEqual(dests["/financial-command/watchlist.html"], "/api?_r=page&_p=watchlist")
        self.assertEqual(dests["/api/capital-flows"], "/api?_r=capital-flows")
        self.assertEqual(dests["/api/watchlist"], "/api?_r=watchlist")
        self.assertEqual(dests["/api/treasury"], "/api?_r=treasury")

    def test_html_not_static_on_vercel(self):
        vi = (FC / ".vercelignore").read_text(encoding="utf-8")
        for name in ("/index.html", "/capital-flows.html", "/watchlist.html"):
            self.assertIn(name, vi, name)
        for name in ("index.html", "capital-flows.html", "watchlist.html"):
            bundled = FC / "api" / "_pages" / name
            root = FC / name
            self.assertTrue(bundled.is_file(), name)
            self.assertEqual(bundled.read_bytes(), root.read_bytes(), name)


class FccAppLevelAuth(unittest.TestCase):
    """Hobby prod alias skips SSO. Cookie-less must never see FCC pages or treasury JSON."""

    PATHS = ("/", "/api/treasury", "/capital-flows.html", "/watchlist.html")

    def test_cookieless_four_paths_401_json(self):
        from api.index import handle

        for path in self.PATHS:
            status, body = handle("GET", path, headers={})
            self.assertEqual(status, 401, path)
            self.assertEqual(body, {"ok": False, "error": "auth_required"})
            blob = json.dumps(body)
            self.assertNotIn("evaluation", blob)
            self.assertNotIn("snapshot", blob)
            self.assertNotIn("at-a-glance", blob)
            self.assertNotIn("Financial Command", blob)

    def test_cookieless_writes_still_403(self):
        from api.index import handle

        for route in ("/api/config", "/api/refresh", "/api/trade", "/api/mint"):
            status, body = handle("POST", route, headers={})
            self.assertEqual(status, 403, route)
            self.assertEqual(body["error"], "read_only")

    def test_env_oidc_and_oidc_header_do_not_grant_access(self):
        import os
        from api._lib import vercel_auth_present
        from api.index import handle

        os.environ["VERCEL_OIDC_TOKEN"] = "deployment-identity-not-user-auth"
        try:
            self.assertFalse(vercel_auth_present({}))
            self.assertFalse(vercel_auth_present({"cookie": "session=abc"}))
            # Auto-injected Function header is deployment identity, not SSO.
            self.assertFalse(
                vercel_auth_present({"x-vercel-oidc-token": "auto-injected"})
            )
            status, body = handle(
                "GET",
                "/api/treasury",
                headers={"x-vercel-oidc-token": "auto-injected"},
            )
            self.assertEqual(status, 401)
            self.assertEqual(body, {"ok": False, "error": "auth_required"})
        finally:
            os.environ.pop("VERCEL_OIDC_TOKEN", None)

    def test_query_bypass_does_not_grant_access(self):
        from api.index import handle

        status, body = handle(
            "GET",
            "/api/treasury?_vercel_jwt=nope&secret=nope",
            headers={},
        )
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "auth_required")

    def test_jwt_cookie_allows_placeholder_not_live(self):
        from api.index import handle

        headers = {"Cookie": "_vercel_jwt=vercel-sso-proof"}
        status, body = handle("GET", "/api/treasury", headers=headers)
        self.assertEqual(status, 200)
        self.assertIsInstance(body, dict)
        self.assertEqual(body["preview"]["source"], "placeholder")
        self.assertIsNone(body["snapshot"]["as_of"])
        self.assertEqual(body["evaluation"]["actions"], [])
        self.assertNotRegex(json.dumps(body), r"0x[a-fA-F0-9]{20,}")

    def test_jwt_cookie_allows_html_page(self):
        from api.index import handle

        status, body = handle(
            "GET",
            "/",
            headers={"Cookie": "_vercel_jwt=vercel-sso-proof"},
        )
        self.assertEqual(status, 200)
        self.assertIsInstance(body, str)
        self.assertIn('id="at-a-glance"', body)
        self.assertIn('id="fcc-vercel-banner"', body)

    def test_process_env_treasury_not_loaded_this_ship(self):
        import os
        from api.index import handle

        os.environ["FCC_TREASURY_JSON"] = json.dumps(
            {
                "evaluation": {"agent_brief": "must-not-load", "actions": [{"title": "x"}]},
                "snapshot": {"as_of": "2099-01-01T00:00:00Z"},
            }
        )
        try:
            status, body = handle(
                "GET",
                "/api/treasury",
                headers={"Cookie": "_vercel_jwt=vercel-sso-proof"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(body["preview"]["source"], "placeholder")
            self.assertEqual(body["evaluation"]["actions"], [])
            self.assertNotIn("must-not-load", json.dumps(body))
        finally:
            os.environ.pop("FCC_TREASURY_JSON", None)

    def test_route_html_paths(self):
        from api._lib import route_from_path

        self.assertEqual(route_from_path("/"), "page")
        self.assertEqual(route_from_path("/capital-flows.html"), "page")
        self.assertEqual(route_from_path("/watchlist.html"), "page")
        self.assertEqual(route_from_path("/api?_r=page&_p=index"), "page")
        self.assertEqual(route_from_path("/api/treasury"), "treasury")


if __name__ == "__main__":
    unittest.main()
