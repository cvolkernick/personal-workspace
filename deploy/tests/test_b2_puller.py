#!/usr/bin/env python3
"""Refuse / skip tests for the finley→prism puller (no SSH, no keys in list)."""

from __future__ import annotations

import importlib.util
import json
import shutil
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
        yg = ".local/share/youtube-groom/"
        self.assertTrue(P.classify_source(yg + "state.json").allowed)
        self.assertTrue(P.classify_source(yg + "groom.log").allowed)
        self.assertFalse(P.classify_source(yg + "secrets.json").allowed)
        self.assertFalse(P.classify_source(yg + ".env").allowed)
        # Old app-home locations never existed on prism; no longer pulled.
        self.assertFalse(P.classify_source("youtube-groom/state.json").allowed)
        self.assertFalse(P.classify_source("youtube-groom/never_readd").allowed)

    def test_units_allowed_published_dropped(self):
        self.assertTrue(P.classify_source(".config/systemd/user/b2.service").allowed)
        self.assertFalse(P.classify_source(".buzz/published/note.md").allowed)
        self.assertFalse(P.classify_source("nest-published/guide.md").allowed)


class TestTokenExclude(unittest.TestCase):
    def test_pattern_present(self):
        self.assertIn("*token*.conf*", P.PULL_EXCLUDES)

    def test_token_dropins_excluded(self):
        for name in (
            "github-token.conf",
            "github-token.conf.bak-962",
            ".config/systemd/user/example.service.d/github-token.conf",
            ".config/systemd/user/example.service.d/github-token.conf.bak",
            "api-token.conf.bak.20260101T000000Z",
            "GitHub-Token.CONF",
        ):
            self.assertTrue(P.is_excluded_name(name), msg=name)

    def test_regular_files_not_excluded(self):
        for name in (
            "orchestra-dashboard.service",
            "public-base.conf",
            "treasury_latest.json",
            "token_report.json",
            "state.json",
        ):
            self.assertFalse(P.is_excluded_name(name), msg=name)

    def test_rsync_cmd_carries_exclude(self):
        cmd = Pull.build_rsync_cmd(
            "agent@example-host", "/home/agent/.config/systemd/user/", Path("/tmp/d/u"), False
        )
        self.assertIn("--exclude=*token*.conf*", cmd)
        self.assertLess(cmd.index("--exclude=*token*.conf*"), cmd.index("agent@example-host:/home/agent/.config/systemd/user/"))


class TestPathMapping(unittest.TestCase):
    """Directories keep their trailing slash so rsync copies contents, not the dir."""

    def test_remote_source_keeps_dir_slash(self):
        home = "/home/example-agent"
        self.assertEqual(
            P.remote_source("personal-workspace/treasury/snapshots/", prism_home=home),
            "/home/example-agent/personal-workspace/treasury/snapshots/",
        )
        self.assertEqual(
            P.remote_source(".local/share/youtube-groom/", prism_home=home + "/"),
            "/home/example-agent/.local/share/youtube-groom/",
        )
        self.assertEqual(
            P.remote_source("personal-workspace/financial-command/treasury_latest.json", prism_home=home),
            "/home/example-agent/personal-workspace/financial-command/treasury_latest.json",
        )

    def test_pull_sources_dirs_end_with_slash(self):
        for rel, src in zip(P.PULL_RELATIVE, P.pull_sources(prism_home="/home/example-agent")):
            self.assertEqual(rel.endswith("/"), src.endswith("/"), msg=src)

    def test_dest_mapping(self):
        root = "/srv/example-pull"
        self.assertEqual(
            P.dest_for_source("personal-workspace/treasury/snapshots/", root),
            Path(root) / "treasury" / "snapshots",
        )
        self.assertEqual(
            P.dest_for_source(".local/share/youtube-groom/", root),
            Path(root) / ".local" / "share" / "youtube-groom",
        )
        self.assertEqual(
            P.dest_for_source(".config/systemd/user/", root),
            Path(root) / ".config" / "systemd" / "user",
        )

    def test_rsync_cmd_dir_contents_into_dest(self):
        cmd = Pull.build_rsync_cmd(
            "agent@example-host",
            "/home/agent/personal-workspace/treasury/snapshots/",
            Path("/srv/example-pull/treasury/snapshots"),
            True,
        )
        self.assertEqual(cmd[-2], "agent@example-host:/home/agent/personal-workspace/treasury/snapshots/")
        self.assertEqual(cmd[-1], "/srv/example-pull/treasury/snapshots/")
        self.assertIn("--dry-run", cmd)

    def test_rsync_cmd_file(self):
        cmd = Pull.build_rsync_cmd(
            "agent@example-host", "/home/agent/f.json", Path("/srv/example-pull/f.json"), False
        )
        self.assertEqual(cmd[-2:], ["agent@example-host:/home/agent/f.json", "/srv/example-pull/f.json"])
        self.assertNotIn("--dry-run", cmd)


@unittest.skipIf(shutil.which("rsync") is None, "rsync not installed")
class TestRealRsyncLayout(unittest.TestCase):
    """Run the real rsync argv against a local fixture (empty remote = local src)."""

    def test_no_doubled_dir_and_token_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            units = home / ".config" / "systemd" / "user"
            dropin = units / "example.service.d"
            dropin.mkdir(parents=True)
            (units / "example.service").write_text("[Unit]\n", encoding="utf-8")
            (dropin / "override.conf").write_text("[Service]\n", encoding="utf-8")
            (dropin / "github-token.conf").write_text("fake\n", encoding="utf-8")
            (dropin / "github-token.conf.bak-1").write_text("fake\n", encoding="utf-8")
            dest = Path(tmp) / "pull" / ".config" / "systemd" / "user"
            for _ in range(2):  # idempotent: second run must not nest either
                self.assertTrue(
                    Pull._rsync_remote("", P.remote_source(".config/systemd/user/", prism_home=str(home)), dest, False)
                )
            got = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
            self.assertEqual(got, ["example.service", "example.service.d/override.conf"])
            self.assertFalse((dest / "user").exists())


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
        yg = self.home / ".local" / "share" / "youtube-groom"
        yg.mkdir(parents=True)
        (yg / "state.json").write_text("{}", encoding="utf-8")
        (yg / "groom.log").write_text("ok\n", encoding="utf-8")
        units = self.home / ".config" / "systemd" / "user"
        units.mkdir(parents=True)
        (units / "orchestra-dashboard.service").write_text("[Unit]\n", encoding="utf-8")
        dropin = units / "example.service.d"
        dropin.mkdir()
        (dropin / "github-token.conf").write_text("fake\n", encoding="utf-8")
        (dropin / "github-token.conf.bak-1").write_text("fake\n", encoding="utf-8")

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
        self.assertNotIn("treasury/snapshots/snapshots/treasury_latest.json", dest_rel)
        self.assertIn(".local/share/youtube-groom/state.json", dest_rel)
        self.assertIn(".local/share/youtube-groom/groom.log", dest_rel)
        self.assertIn(".config/systemd/user/orchestra-dashboard.service", dest_rel)
        self.assertFalse(any("token" in r for r in dest_rel), msg=dest_rel)
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


class TestLockedPullCadence(unittest.TestCase):
    """One finley timer: hourly :20 America/New_York. No extra clocks."""

    def test_timer_oncalendar_locked(self):
        timer = (ROOT / "deploy" / "units" / "b2-puller.timer").read_text(
            encoding="utf-8"
        )
        self.assertIn("PULSE LOCK", timer)
        self.assertIn(f"OnCalendar={P.PULL_ONCALENDAR}", timer)
        self.assertIn("America/New_York", timer)
        self.assertIn("*:20:00", timer)
        self.assertNotIn("OnUnitActiveSec", timer)
        self.assertNotIn("OnBootSec", timer)
        self.assertIn("Unit=b2-puller.service", timer)
        self.assertNotIn("vault", timer.lower())

    def test_one_puller_timer_no_sidecar_clocks(self):
        units = ROOT / "deploy" / "units"
        puller_timers = sorted(p.name for p in units.glob("*puller*.timer"))
        self.assertEqual(puller_timers, ["b2-puller.timer"])
        forbidden = (
            "prism-backup.timer",
            "prism-self-backup.timer",
            "b2-units-only.timer",
            "units-only.timer",
            "off-site.timer",
            "offsite-tarball.timer",
            "b2-tarball.timer",
        )
        for name in forbidden:
            self.assertFalse((units / name).exists(), msg=name)

    def test_one_job_covers_books_groom_published_units(self):
        blob = " ".join(P.PULL_RELATIVE)
        self.assertIn("treasury/snapshots", blob)
        self.assertIn(".local/share/youtube-groom", blob)
        self.assertIn(".config/systemd/user", blob)
        # .buzz/published and nest-published never existed on prism; dropped.
        self.assertNotIn(".buzz/published", blob)
        self.assertNotIn("nest-published", blob)
        svc = (ROOT / "deploy" / "units" / "b2-puller.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("PULSE LOCK", svc)
        self.assertIn("pull_from_prism.py", svc)
        self.assertEqual(svc.count("ExecStart="), 1)
        self.assertIn("prism-gateway", svc)
        self.assertNotIn("vault", svc.lower())


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
