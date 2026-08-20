#!/usr/bin/env python3
"""Refuse / skip tests for the finley→prism puller (no SSH, no keys in list)."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATHS_PY = ROOT / "deploy" / "b2-puller" / "paths.py"
PULL_PY = ROOT / "deploy" / "b2-puller" / "pull_from_prism.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


P = _load("b2_puller_paths", PATHS_PY)
Pull = _load("b2_puller_pull", PULL_PY)


class TestPullListHasNoKeys(unittest.TestCase):
    def test_default_list_clean(self):
        cleaned = P.assert_pull_list_clean()
        self.assertEqual(list(cleaned), list(P.PULL_RELATIVE))
        for rel in P.PULL_RELATIVE:
            self.assertFalse(P.is_refused_name(rel), msg=rel)
            self.assertTrue(P.classify_source(rel).allowed, msg=rel)

    def test_no_secrets_env_or_tokens_in_list(self):
        blob = " ".join(P.PULL_RELATIVE).lower()
        for needle in (
            "secrets.json",
            ".env",
            "workflow-scheduler.env",
            "ynab/token",
            "api_key",
            "apikey",
            "credential",
            "fcc_treasury_json",
            "id_rsa",
            ".pem",
        ):
            self.assertNotIn(needle, blob)

    def test_injected_key_path_fails_clean(self):
        with self.assertRaises(RuntimeError) as ctx:
            P.assert_pull_list_clean(
                list(P.PULL_RELATIVE) + ["iot/secrets.json"]
            )
        self.assertIn("refused", str(ctx.exception).lower())


class TestRefuseSources(unittest.TestCase):
    def test_venue_keys(self):
        keys = [
            "iot/secrets.json",
            ".env",
            "treasury/.env",
            "workflow-scheduler.env",
            ".config/ynab/token",
            "coinbase_api_key.json",
            "robinhood_token",
            "id_rsa",
            "keys/id_ed25519",
            "venue.pem",
            "FCC_TREASURY_JSON",
            "vercel/FCC_TREASURY_JSON",
        ]
        for k in keys:
            c = P.classify_source(k)
            self.assertFalse(c.allowed, msg=k)
            self.assertTrue(c.reason.startswith("refuse"), msg=k)

    def test_snapshots_allowed_not_config(self):
        self.assertTrue(
            P.classify_source("treasury/snapshots/treasury_latest.json").allowed
        )
        self.assertTrue(
            P.classify_source("treasury/snapshots/robinhood_latest.json").allowed
        )
        self.assertFalse(P.classify_source("treasury/config.json").allowed)

    def test_youtube_groom_allowlist_only(self):
        self.assertTrue(P.classify_source("youtube-groom/state.json").allowed)
        self.assertTrue(P.classify_source("youtube-groom/never_readd").allowed)
        self.assertTrue(P.classify_source("youtube-groom/groom.log").allowed)
        self.assertFalse(P.classify_source("youtube-groom/secrets.json").allowed)
        self.assertFalse(P.classify_source("youtube-groom/.env").allowed)

    def test_units_and_nest_published(self):
        self.assertTrue(P.classify_source(".config/systemd/user/b2.service").allowed)
        self.assertTrue(P.classify_source(".buzz/published/note.md").allowed)
        self.assertTrue(P.classify_source("nest-published/guide.md").allowed)


class TestRefuseDest(unittest.TestCase):
    def test_mac_home(self):
        c = P.classify_dest("/Users/cvolkernick/b2-pulls")
        self.assertFalse(c.allowed)
        self.assertIn("Mac", c.reason)

    def test_vercel_and_fcc_env(self):
        self.assertFalse(P.classify_dest("/var/task/FCC_TREASURY_JSON").allowed)
        self.assertFalse(P.classify_dest("/tmp/.vercel/project").allowed)
        self.assertFalse(P.is_refused_dest("/home/finley-agent/b2-pulls/prism"))

    def test_finley_dest_ok(self):
        c = P.classify_dest("/home/finley-agent/b2-pulls/prism")
        self.assertTrue(c.allowed)


class TestPullWritesOnlyAllowlist(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "prism-home"
        self.dest = Path(self.tmp.name) / "finley-pull"
        snap = (
            self.home
            / "personal-workspace"
            / "treasury"
            / "snapshots"
        )
        snap.mkdir(parents=True)
        (snap / "treasury_latest.json").write_text('{"as_of":"test"}', encoding="utf-8")
        (snap / "robinhood_latest.json").write_text('{"ok":true}', encoding="utf-8")
        # Poison: must never be copied even if sitting next to snapshots.
        (self.home / "personal-workspace" / "treasury" / "config.json").write_text(
            '{"coinbase":"no"}', encoding="utf-8"
        )
        (self.home / "iot").mkdir()
        (self.home / "iot" / "secrets.json").write_text('{"k":"v"}', encoding="utf-8")
        yg = self.home / "youtube-groom"
        yg.mkdir()
        (yg / "state.json").write_text("{}", encoding="utf-8")
        (yg / "never_readd").write_text("id1\n", encoding="utf-8")
        (yg / "groom.log").write_text("ok\n", encoding="utf-8")
        units = self.home / ".config" / "systemd" / "user"
        units.mkdir(parents=True)
        (units / "orchestra-dashboard.service").write_text("[Unit]\n", encoding="utf-8")
        pub = self.home / ".buzz" / "published"
        pub.mkdir(parents=True)
        (pub / "note.md").write_text("# nest\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_local_pull_writes_only_pulled(self):
        man = Pull.pull(
            dest_root=self.dest,
            src_root=self.home,
            dry_run=False,
            require_finley=False,
        )
        self.assertTrue(man["ok"])
        self.assertTrue(man["wrote_only_pulled"])
        wrote = []
        for item in man["pulled"]:
            wrote.extend(item.get("wrote") or [])
        dest_files = [p for p in self.dest.rglob("*") if p.is_file()]
        dest_rel = [str(p.relative_to(self.dest)) for p in dest_files]
        self.assertIn("treasury/snapshots/treasury_latest.json", dest_rel)
        self.assertIn("youtube-groom/state.json", dest_rel)
        self.assertIn("youtube-groom/never_readd", dest_rel)
        self.assertIn("youtube-groom/groom.log", dest_rel)
        self.assertIn(".config/systemd/user/orchestra-dashboard.service", dest_rel)
        self.assertIn(".buzz/published/note.md", dest_rel)
        self.assertNotIn("treasury/config.json", dest_rel)
        self.assertNotIn("iot/secrets.json", dest_rel)
        self.assertTrue((self.dest / "MANIFEST.json").is_file())
        # No extra trees invented
        self.assertFalse((self.dest / "iot").exists())

    def test_nested_key_in_allow_dir_aborts(self):
        snap = self.home / "personal-workspace" / "treasury" / "snapshots"
        (snap / "venue.pem").write_text("BEGIN", encoding="utf-8")
        with self.assertRaises(RuntimeError) as ctx:
            Pull.pull(
                dest_root=self.dest,
                src_root=self.home,
                require_finley=False,
            )
        self.assertIn("REFUSE", str(ctx.exception))
        self.assertFalse((self.dest / "treasury" / "snapshots" / "venue.pem").exists())

    def test_mac_dest_refused(self):
        with self.assertRaises(RuntimeError):
            Pull.pull(
                dest_root=Path("/Users/cvolkernick/b2-pulls"),
                src_root=self.home,
                require_finley=False,
            )

    def test_cli_refuse_key_dest(self):
        rc = Pull.main(
            [
                "--src-root",
                str(self.home),
                "--dest",
                "/tmp/.vercel/FCC_TREASURY_JSON",
                "--allow-any-host",
            ]
        )
        self.assertEqual(rc, 2)


class TestB2ServerWording(unittest.TestCase):
    def test_status_says_knowledge_graph_not_vault(self):
        sys.path.insert(0, str(ROOT / "b2-ux"))
        import server as b2server  # noqa: E402

        st = b2server.status_payload()
        blob = json.dumps(st).lower()
        self.assertIn("knowledge graph", st["label"].lower())
        self.assertNotIn("vault", blob)
        self.assertEqual(st["service"], "b2")


if __name__ == "__main__":
    unittest.main()
