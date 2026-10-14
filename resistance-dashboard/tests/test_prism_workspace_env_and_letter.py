"""LOCAL_WORKSPACE_DIR with $HOME (systemd EnvironmentFile) and ask/plan letter."""

import os
import unittest
from unittest import mock

import server as fitdash_server


class WorkspaceEnv(unittest.TestCase):
    def test_expands_home_literal(self):
        env = {"LOCAL_WORKSPACE_DIR": "$HOME/personal-workspace", "HOME": "/home/u"}
        with mock.patch.dict(os.environ, {"HOME": "/home/u"}):
            out = fitdash_server.normalize_workspace_env(env)
        self.assertEqual(out, "/home/u/personal-workspace")
        self.assertEqual(env["LOCAL_WORKSPACE_DIR"], "/home/u/personal-workspace")

    def test_expands_tilde_and_leaves_absolute(self):
        with mock.patch.dict(os.environ, {"HOME": "/home/u"}):
            env = {"LOCAL_WORKSPACE_DIR": "~/pw"}
            self.assertEqual(fitdash_server.normalize_workspace_env(env), "/home/u/pw")
            env = {"LOCAL_WORKSPACE_DIR": "/srv/pw"}
            self.assertEqual(fitdash_server.normalize_workspace_env(env), "/srv/pw")
            self.assertEqual(fitdash_server.normalize_workspace_env({}), "")

    def test_catalog_loads_from_expanded_dir(self):
        import tempfile, json
        from pathlib import Path
        from rt_dashboard.github_client import GitHubLiftClient
        from rt_dashboard.workout_store import load_catalog_and_goals

        with tempfile.TemporaryDirectory() as home:
            p = Path(home, "pw", "fitness", "exercises")
            p.mkdir(parents=True)
            (p / "catalog.json").write_text(json.dumps(
                {"exercises": [{"id": "squat", "name": "Back Squat", "available": True}]}))
            with mock.patch.dict(os.environ, {"HOME": home}):
                env = {"LOCAL_WORKSPACE_DIR": "$HOME/pw"}
                ws = fitdash_server.normalize_workspace_env(env)
            client = GitHubLiftClient(prefer_local=True, local_fallback_dir=ws, token="")
            wo = load_catalog_and_goals(client)
            self.assertEqual(wo["sources"]["catalog"], "local")
            self.assertEqual([e["name"] for e in wo["catalog"]["exercises"]], ["Back Squat"])


class StampedLetter(unittest.TestCase):
    def test_uses_plan_session_type(self):
        d = {"workout_store": {"plan": {"session_type": "legs", "is_rest_day": False}}}
        self.assertEqual(fitdash_server.with_stamped_next_session(d)["workout_store"]["next_session_type"], "legs")
        self.assertNotIn("next_session_type", d["workout_store"])  # input not mutated

    def test_keeps_existing_and_rest(self):
        d = {"workout_store": {"next_session_type": "pull", "plan": {"session_type": "legs"}}}
        self.assertEqual(fitdash_server.with_stamped_next_session(d)["workout_store"]["next_session_type"], "pull")
        r = {"workout_store": {"plan": {"session_type": "rest", "is_rest_day": True, "next_session_type": "push"}}}
        self.assertEqual(fitdash_server.with_stamped_next_session(r)["workout_store"]["next_session_type"], "push")
        self.assertIsNone(fitdash_server.with_stamped_next_session(None))

    def test_ask_plan_receives_letter(self):
        h = fitdash_server.DashboardHandler.__new__(fitdash_server.DashboardHandler)
        h.headers = {}
        dash = {"workout_store": {"plan": {"session_type": "legs"}}}
        with mock.patch.object(fitdash_server, "load_dashboard_data", return_value=dash), \
             mock.patch("api.ask.plan.ask_plan_body", return_value=(200, {"ok": True})) as fn:
            h._run_ask_plan({"id": "u1"}, {})
        self.assertEqual(fn.call_args.kwargs["dashboard"]["workout_store"]["next_session_type"], "legs")
