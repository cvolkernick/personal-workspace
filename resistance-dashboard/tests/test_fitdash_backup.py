"""FitDash backup, retention, and scratch restore.

Synthetic rows only. The age round-trip is skipped when the age CLI is absent.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

BACKUP_DIR = Path(__file__).resolve().parents[1] / "ops" / "backup"
sys.path.insert(0, str(BACKUP_DIR))

import backup_common  # noqa: E402
import fitdash_backup  # noqa: E402
import fitdash_prune  # noqa: E402
import fitdash_restore  # noqa: E402
import gh_releases  # noqa: E402

NOW = datetime(2026, 11, 17, 8, 40, tzinfo=timezone.utc)
SECRET_ENV = "denylist-env-value"
SECRET_KEY = "denylist-master-key-value"
SECRET_GROK = "DENY-GROK-SESSION-TOKEN-value"
SECRET_OAUTH = "denylist-oauth-client"
BAROMETER = "synthetic-barometer-marker"
CACHE_MARKER = "cache-must-not-appear"


def _age_ready() -> bool:
    return bool(shutil.which("age") and shutil.which("age-keygen"))


class FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        columns = list(rows[0].keys()) if rows else []
        self.description = [(name, None, None, None, None, None, None) for name in columns]

    def fetchall(self) -> list[dict]:
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeTurso:
    def __init__(self, tables: dict) -> None:
        self.tables = tables
        self.statements: list[str] = []

    def execute(self, sql: str, params=()):
        self.statements.append(sql)
        lowered = " ".join(sql.split()).lower()
        if not lowered.startswith("select"):
            raise AssertionError(f"backup issued a write: {sql}")
        if "from sqlite_master" in lowered:
            rows = [{"name": name, "sql": meta["sql"]} for name, meta in self.tables.items()]
            return FakeCursor(rows)
        match_name = None
        for name in self.tables:
            if f'"{name}"' in sql:
                match_name = name
                break
        if match_name is None:
            raise AssertionError(sql)
        meta = self.tables[match_name]
        if "max(" in lowered:
            updated = [row.get("updated_at") for row in meta["rows"] if row.get("updated_at") is not None]
            created = [row.get("created_at") for row in meta["rows"] if row.get("created_at") is not None]
            return FakeCursor(
                [{
                    "updated_at": max(updated) if updated else None,
                    "created_at": max(created) if created else None,
                }]
            )
        return FakeCursor(list(meta["rows"]))


class FakeGitHub:
    def __init__(self) -> None:
        self.releases: list[dict] = []
        self.assets: list[tuple[str, bytes, str]] = []
        self.deleted: list[str] = []

    def tag_taken(self, tag: str) -> bool:
        return any(item["tag_name"] == tag for item in self.releases)

    def create_release(self, tag: str, name: str, body: str) -> dict:
        release = {
            "tag_name": tag,
            "name": name,
            "body": body,
            "id": len(self.releases) + 1,
            "upload_url": "https://uploads.github.com/repos/cvolkernick/fitdash-backups/releases/1/assets{?name,label}",
            "published_at": "2026-11-17T08:40:00Z",
            "draft": False,
        }
        self.releases.append(release)
        return release

    def upload_asset(self, release: dict, filename: str, data: bytes, content_type: str) -> None:
        self.assets.append((filename, data, content_type))

    def list_releases(self) -> list[dict]:
        return list(self.releases)

    def delete_release(self, release_id: int, tag: str) -> None:
        self.deleted.append(tag)


def _release(tag: str, published: str | None = None, body: str = "") -> fitdash_prune.Release:
    stamp = None
    if published:
        stamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
    item = fitdash_prune.release_from_tag(tag, release_id=1, published_at=stamp, body=body)
    if item is None:
        raise AssertionError(tag)
    return item


def _temp_leftovers(prefix: str) -> set[str]:
    root = Path(tempfile.gettempdir())
    return {str(path) for path in root.glob(prefix + "*")}


class DenylistAndGuards(unittest.TestCase):
    def test_denylist_blocks_secret_filenames(self) -> None:
        denied = [
            "env",
            "env.bak",
            ".env",
            "master.key",
            "master.vercel.key",
            "google-oauth-client.json",
            "My-OAuth-Client.json",
            "session-token.json",
            "grok_sessions",
            "api-secret.txt",
            "credentials.json",
            "id_rsa",
            "notes.pem",
        ]
        for name in denied:
            self.assertTrue(backup_common.is_denied(Path("nested") / name), name)
        self.assertFalse(backup_common.is_denied(Path("phase_barometer.json")))
        self.assertFalse(backup_common.is_denied(Path("hsa") / "default.json"))
        self.assertFalse(backup_common.is_denied(Path("coach_state.json")))
        self.assertTrue(backup_common.path_is_skipped("cache/health.json"))

    def test_read_only_guard(self) -> None:
        backup_common.assert_read_only("SELECT * FROM workout_sessions")
        backup_common.assert_read_only("WITH x AS (SELECT 1) SELECT * FROM x")
        for sql in (
            "DROP TABLE workout_sessions",
            "INSERT INTO workout_sessions(id) VALUES (1)",
            "UPDATE workout_sessions SET notes = 'x'",
            "DELETE FROM workout_sessions",
            "SELECT 1; DROP TABLE t",
            "PRAGMA writable_schema=ON",
        ):
            with self.assertRaises(RuntimeError):
                backup_common.assert_read_only(sql)

    def test_example_files_have_no_secret_material(self) -> None:
        recipients = (BACKUP_DIR / "recipients.txt.example").read_text(encoding="utf-8")
        env = (BACKUP_DIR / "backup.env.example").read_text(encoding="utf-8")
        self.assertNotIn("AGE-SECRET-KEY", recipients)
        self.assertNotIn("ghp_", env)
        self.assertNotIn("github_pat_", env)
        self.assertIn("FITDASH_BACKUP_GH_TOKEN=", env)


class Retention(unittest.TestCase):
    def test_daily_weekly_monthly_yearly_and_newest_floor(self) -> None:
        tags = [
            "bk-20261117",
            "bk-20261117-0100",
            "bk-20261116",
            "bk-20261115",
            "bk-20261110",
            "bk-20261109",
            "bk-20261108",
            "bk-20261101",
            "bk-20261001",
            "bk-20260901",
            "bk-20251101",
            "bk-20240615",
            "bk-20230101",
            "bk-20220303",
        ]
        releases = []
        for tag in tags:
            published = None
            if tag == "bk-20261117":
                published = "2026-11-17T12:00:00Z"
            releases.append(_release(tag, published))
        keep = fitdash_prune.select_keep(releases, daily=3, weekly=2, monthly=2, newest=2)
        self.assertEqual(
            keep,
            {
                "bk-20261117",
                "bk-20261117-0100",
                "bk-20261116",
                "bk-20261115",
                "bk-20261108",
                "bk-20261101",
                "bk-20261001",
                "bk-20260901",
                "bk-20251101",
                "bk-20240615",
                "bk-20230101",
                "bk-20220303",
            },
        )
        deleting = fitdash_prune.releases_to_delete(releases, keep)
        self.assertEqual([item.tag for item in deleting], ["bk-20261109", "bk-20261110"])

    def test_verified_marker_and_same_day_newest_three(self) -> None:
        releases = [
            _release("bk-20261117-0100"),
            _release("bk-20261117-0200"),
            _release("bk-20261117-0300"),
            _release("bk-20261117-0400"),
            _release("bk-20261117-0500", body="checked last-verified-good"),
            _release("bk-20200101"),
        ]
        keep = fitdash_prune.select_keep(releases, daily=14, weekly=8, monthly=12, newest=3)
        # Same-day backups collapse to the newest before the yearly slot is chosen,
        # so the 01:00 and 02:00 copies are not the 2026 snapshot.
        self.assertIn("bk-20261117-0500", keep)
        self.assertIn("bk-20261117-0400", keep)
        self.assertIn("bk-20261117-0300", keep)
        self.assertNotIn("bk-20261117-0100", keep)
        self.assertNotIn("bk-20261117-0200", keep)
        self.assertIn("bk-20200101", keep)

        marked = _release("bk-20261117-0200", body="last-verified-good")
        swapped = [marked if item.tag == "bk-20261117-0200" else item for item in releases]
        keep_marked = fitdash_prune.select_keep(swapped, daily=14, weekly=8, monthly=12, newest=3)
        self.assertIn("bk-20261117-0200", keep_marked)

    def test_state_file_and_dry_run_does_not_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "last-verified-good"
            state.write_text("# note\nbk-20261109\n", encoding="utf-8")
            self.assertEqual(fitdash_prune.read_verified_tags(state), {"bk-20261109"})
            payloads = [
                {
                    "tag_name": "bk-20261117",
                    "id": 1,
                    "published_at": "2026-11-17T12:00:00Z",
                    "name": "bk-20261117",
                    "body": "",
                    "draft": False,
                },
                {
                    "tag_name": "bk-20261109",
                    "id": 2,
                    "published_at": "2026-11-09T08:00:00Z",
                    "name": "old",
                    "body": "",
                    "draft": False,
                },
            ]
            client = FakeGitHub()
            client.releases = payloads
            plan = fitdash_prune.prune(client, apply=False, verified_file=state)
            self.assertEqual(client.deleted, [])
            self.assertIn("bk-20261109", plan.keep)
            self.assertFalse(plan.applied)
            with self.assertRaises(RuntimeError):
                fitdash_prune.releases_to_delete(
                    [_release("bk-20261117"), _release("bk-20261116"), _release("bk-20261115")],
                    set(),
                )

    def test_github_error_redacts_the_token(self) -> None:
        client = gh_releases.GitHubReleases("super-secret-token-value", "cvolkernick/fitdash-backups")
        err = client._fail(403, b"nope super-secret-token-value")
        self.assertNotIn("super-secret-token-value", str(err))
        self.assertIn("[redacted]", str(err))
        self.assertNotIn("super-secret-token-value", repr(client))


class BackupFailures(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.before = _temp_leftovers("fitdash-backup-")

    def tearDown(self) -> None:
        self.assertEqual(_temp_leftovers("fitdash-backup-"), self.before)

    def _opts(self, **kwargs) -> fitdash_backup.BackupOptions:
        config = self.root / "config"
        config.mkdir(exist_ok=True)
        opts = fitdash_backup.BackupOptions(
            dry_run=True,
            no_git=True,
            now=NOW,
            config_dir=config,
            app_dir=self.root / "app",
            state_dir=self.root / "state",
            out_dir=self.root / "out",
            recipients=self.root / "recipients.txt",
            db_path=config / "fitdash.db",
            coaching_db=config / "coaching_targets.sqlite3",
            biomarkers_db=config / "missing-biomarkers.sqlite3",
            coach_state=self.root / "coach" / "coach_state.json",
            systemd_dir=self.root / "systemd",
        )
        for key, value in kwargs.items():
            setattr(opts, key, value)
        return opts

    def _turso(self, updated_at: str) -> FakeTurso:
        return FakeTurso(
            {
                "workout_sessions": {
                    "sql": "CREATE TABLE workout_sessions (id INTEGER PRIMARY KEY, updated_at TEXT, created_at TEXT)",
                    "rows": [{"id": 1, "updated_at": updated_at, "created_at": updated_at}],
                }
            }
        )

    def test_recent_workout_defers_without_writing_an_archive(self) -> None:
        recent = (NOW - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = fitdash_backup.run_backup(self._opts(), turso_conn=self._turso(recent))
        self.assertEqual(result.exit_code, backup_common.EXIT_DEFERRED)
        self.assertEqual(result.status, "deferred")
        self.assertFalse((self.root / "out").exists() and any((self.root / "out").iterdir()))
        last = json.loads((self.root / "state" / "last.json").read_text(encoding="utf-8"))
        self.assertEqual(last["status"], "deferred")
        self.assertNotIn(SECRET_ENV, json.dumps(last))

    def test_old_workout_with_missing_recipients_does_not_defer_or_leak(self) -> None:
        (self.root / "config").mkdir(exist_ok=True)
        (self.root / "config" / "env").write_text(
            f"FITDASH_EXAMPLE_NAME={SECRET_ENV}\n", encoding="utf-8"
        )
        result = fitdash_backup.run_backup(self._opts(), turso_conn=self._turso("2020-01-01T00:00:00Z"))
        self.assertEqual(result.exit_code, 1)
        self.assertNotEqual(result.status, "deferred")
        self.assertIn("recipients file is missing", result.error or "")
        self.assertEqual(list(self.root.rglob("*.tar.gz")), [])
        self.assertEqual(list(self.root.rglob("*.age")), [])
        last = (self.root / "state" / "last.json").read_text(encoding="utf-8")
        self.assertNotIn(SECRET_ENV, last)

    def test_empty_recipients_and_secret_key_and_missing_age_leave_no_plaintext(self) -> None:
        recipients = self.root / "recipients.txt"
        recipients.write_text("# nothing here\n\n", encoding="utf-8")
        result = fitdash_backup.run_backup(self._opts(), turso_conn=self._turso("2020-01-01T00:00:00Z"))
        self.assertIn("empty", result.error or "")
        self.assertEqual(list(self.root.rglob("*.tar.gz")), [])

        recipients.write_text("AGE-SECRET-KEY-1THISISNOTAREALKEY\n", encoding="utf-8")
        result = fitdash_backup.run_backup(self._opts(), turso_conn=self._turso("2020-01-01T00:00:00Z"))
        self.assertIn("secret key", result.error or "")
        self.assertEqual(list(self.root.rglob("*.tar.gz")), [])

        recipients.write_text("age1syntheticpublickeyfortestonly\n", encoding="utf-8")
        result = fitdash_backup.run_backup(
            self._opts(age_bin="/nonexistent/fitdash-age"),
            turso_conn=self._turso("2020-01-01T00:00:00Z"),
        )
        self.assertIn("age is not installed", result.error or "")
        self.assertEqual(list(self.root.rglob("*.tar.gz")), [])
        self.assertEqual(list(self.root.rglob("*.age")), [])

    def test_timestamp_window(self) -> None:
        self.assertTrue(
            backup_common.timestamp_is_recent(NOW - timedelta(minutes=30), NOW)
        )
        self.assertFalse(
            backup_common.timestamp_is_recent(NOW - timedelta(minutes=31), NOW)
        )
        self.assertFalse(backup_common.timestamp_is_recent("not-a-time", NOW))

    def test_schedule_retry_and_record_failure(self) -> None:
        with mock.patch("fitdash_backup.shutil.which", return_value=None):
            with mock.patch.dict(os.environ, {"EXIT_STATUS": "75"}):
                self.assertEqual(fitdash_backup.schedule_retry(), 0)
            with mock.patch.dict(os.environ, {"EXIT_STATUS": "1"}):
                self.assertEqual(fitdash_backup.schedule_retry(), 0)
        state = self.root / "state"
        fitdash_backup.write_last(
            state,
            fitdash_backup.BackupResult(status="ok", exit_code=0, counts={"workout_sessions": 1}),
            NOW,
        )
        self.assertEqual(fitdash_backup.record_failure(state), 0)
        last = json.loads((state / "last.json").read_text(encoding="utf-8"))
        self.assertEqual(last["status"], "error")
        self.assertIn("journal", last["error"])


@unittest.skipUnless(_age_ready(), "age CLI not installed")
class RoundTrip(unittest.TestCase):
    def test_backup_restore_hashes_and_denylist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = _temp_leftovers("fitdash-backup-") | _temp_leftovers("fitdash-restore-")
            config = root / "config"
            config.mkdir()
            (config / "env").write_text(
                f"FITDASH_EXAMPLE_NAME={SECRET_ENV}\nTURSO_DATABASE_URL=libsql://example.invalid/synthetic\n",
                encoding="utf-8",
            )
            (config / "env.bak").write_text("OLD=1\n", encoding="utf-8")
            (config / "master.key").write_text(SECRET_KEY + "\n", encoding="utf-8")
            (config / "master.vercel.key").write_text(SECRET_KEY + "-vercel\n", encoding="utf-8")
            (config / "google-oauth-client.json").write_text(
                json.dumps({"client_secret": SECRET_OAUTH}) + "\n", encoding="utf-8"
            )
            (config / "session-token.json").write_text('{"token":"nope"}\n', encoding="utf-8")
            (config / "cache").mkdir()
            (config / "cache" / "health.json").write_text(
                json.dumps({"marker": CACHE_MARKER}) + "\n", encoding="utf-8"
            )
            (config / "phase_barometer.json").write_text(
                json.dumps({"marker": BAROMETER}) + "\n", encoding="utf-8"
            )
            (config / "hsa").mkdir()
            (config / "hsa" / "default.json").write_text('{"synthetic":true}\n', encoding="utf-8")
            (config / "labs").mkdir()
            (config / "labs" / "index.json").write_text('{"synthetic":true}\n', encoding="utf-8")

            key = os.urandom(32)
            key_path = root / "master.key"
            key_path.write_text(base64.urlsafe_b64encode(key).decode("ascii") + "\n", encoding="utf-8")
            from rt_dashboard.crypto_box import seal

            sealed = seal(
                '{"synthetic":"bench"}',
                aad="user:synthetic:workout",
                key=key,
            )
            db = config / "fitdash.db"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("INSERT INTO users(name) VALUES ('synthetic-user')")
            conn.commit()
            conn.close()
            coaching = config / "coaching_targets.sqlite3"
            conn = sqlite3.connect(coaching)
            conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO settings VALUES ('synthetic', '1')")
            conn.commit()
            conn.close()

            app = root / "app"
            app.mkdir()
            (app / ".served-sha").write_text("deadbeef\n", encoding="utf-8")
            same = app / "same.py"
            same.write_text("print('same')\n", encoding="utf-8")
            (app / "only_on_pi.py").write_text("print('pi-only')\n", encoding="utf-8")
            (app / "env").write_text(f"LEAK={SECRET_ENV}\n", encoding="utf-8")
            digest = backup_common.sha256_file(same)
            hashes = root / "hashes.txt"
            hashes.write_text(f"{digest}  same.py\n", encoding="utf-8")

            coach = root / "coach" / "coach_state.json"
            coach.parent.mkdir()
            coach.write_text('{"synthetic":true}\n', encoding="utf-8")
            systemd = root / "systemd"
            systemd.mkdir()
            (systemd / "resistance-dashboard.service").write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
            (systemd / "other.service").write_text("do-not-pack\n", encoding="utf-8")

            identity = root / "age-identity.txt"
            subprocess.run(["age-keygen", "-o", str(identity)], check=True, capture_output=True)
            public = ""
            for line in identity.read_text(encoding="utf-8").splitlines():
                if "public key:" in line:
                    public = line.split("public key:", 1)[1].strip()
            self.assertTrue(public.startswith("age1"))
            recipients = root / "recipients.txt"
            recipients.write_text(f"# test recipient\n{public}\n", encoding="utf-8")

            turso = FakeTurso(
                {
                    "workout_sessions": {
                        "sql": (
                            "CREATE TABLE workout_sessions ("
                            "id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, "
                            "updated_at TEXT, created_at TEXT, exercises_json TEXT)"
                        ),
                        "rows": [{
                            "id": 1,
                            "user_id": "synthetic",
                            "updated_at": "2020-01-01T00:00:00Z",
                            "created_at": "2020-01-01T00:00:00Z",
                            "exercises_json": sealed,
                        }],
                    },
                    "synthetic_widget": {
                        "sql": (
                            "CREATE TABLE synthetic_widget ("
                            "id INTEGER PRIMARY KEY, note TEXT, score REAL, blob BLOB, extra TEXT)"
                        ),
                        "rows": [{
                            "id": 7,
                            "note": "synthetic-widget",
                            "score": 1.5,
                            "blob": b"\x00\x01",
                            "extra": None,
                        }],
                    },
                    "synthetic_empty": {
                        "sql": "CREATE TABLE synthetic_empty (id INTEGER PRIMARY KEY, name TEXT)",
                        "rows": [],
                    },
                    "grok_sessions": {
                        "sql": "CREATE TABLE grok_sessions (id INTEGER PRIMARY KEY, token TEXT)",
                        "rows": [{"id": 1, "token": SECRET_GROK}],
                    },
                }
            )
            github = FakeGitHub()
            opts = fitdash_backup.BackupOptions(
                dry_run=False,
                no_git=True,
                now=NOW,
                config_dir=config,
                app_dir=app,
                state_dir=root / "state",
                out_dir=root / "out",
                recipients=recipients,
                code_hashes=hashes,
                db_path=db,
                coaching_db=coaching,
                biomarkers_db=config / "biomarkers.sqlite3",
                coach_state=coach,
                systemd_dir=systemd,
            )
            result = fitdash_backup.run_backup(opts, turso_conn=turso, github=github)
            self.assertEqual(result.exit_code, 0, result.error)
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.code_drift, "drift")
            self.assertEqual(result.tag, "bk-20261117")
            self.assertIsNone(result.archive)
            self.assertEqual(github.deleted, [])
            names = [item[0] for item in github.assets]
            self.assertEqual(names, ["fitdash-20261117-0840.tar.gz.age", "manifest.json"])
            manifest_bytes = github.assets[1][1]
            manifest_text = manifest_bytes.decode("utf-8")
            for secret in (SECRET_ENV, SECRET_KEY, SECRET_GROK, SECRET_OAUTH, CACHE_MARKER, sealed):
                self.assertNotIn(secret, manifest_text)
            manifest = json.loads(manifest_text)
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["app_sha"], "deadbeef")
            self.assertEqual(manifest["tables"]["workout_sessions"]["rows"], 1)
            self.assertEqual(manifest["tables"]["synthetic_widget"]["rows"], 1)
            self.assertEqual(manifest["tables"]["synthetic_empty"]["rows"], 0)
            self.assertNotIn("grok_sessions", manifest["tables"])
            self.assertEqual(manifest["code_drift"]["files"], ["only_on_pi.py"])
            self.assertEqual(
                manifest["env_var_names"],
                ["FITDASH_EXAMPLE_NAME", "TURSO_DATABASE_URL"],
            )
            self.assertNotIn("libsql://", manifest_text)
            self.assertTrue(all(sql.lower().startswith("select") for sql in turso.statements))

            age_bytes = github.assets[0][1]
            self.assertTrue(age_bytes.startswith(b"age-encryption.org/"))
            for secret in (SECRET_ENV, SECRET_KEY, SECRET_GROK, SECRET_OAUTH, CACHE_MARKER):
                self.assertNotIn(secret.encode("utf-8"), age_bytes)
            archive = root / "fitdash.tar.gz.age"
            archive.write_bytes(age_bytes)

            target = root / "restore"
            code = fitdash_restore.run_restore(
                fitdash_restore.RestoreOptions(
                    archive=archive,
                    identity=identity,
                    mode="sqlite-only",
                    apply=False,
                    target=target,
                    sample_decrypt=1,
                    master_key=key_path,
                )
            )
            self.assertEqual(code, 0)
            self.assertFalse((target / "turso-restore.sqlite3").exists())

            code = fitdash_restore.run_restore(
                fitdash_restore.RestoreOptions(
                    archive=archive,
                    identity=identity,
                    mode="sqlite-only",
                    apply=True,
                    target=target,
                    sample_decrypt=1,
                    master_key=key_path,
                )
            )
            self.assertEqual(code, 0)
            restored = target / "turso-restore.sqlite3"
            check = sqlite3.connect(restored)
            self.assertEqual(check.execute("SELECT COUNT(*) FROM workout_sessions").fetchone()[0], 1)
            note, score, blob, extra = check.execute(
                "SELECT note, score, blob, extra FROM synthetic_widget"
            ).fetchone()
            self.assertEqual(note, "synthetic-widget")
            self.assertEqual(score, 1.5)
            self.assertEqual(blob, b"\x00\x01")
            self.assertIsNone(extra)
            self.assertEqual(check.execute("SELECT COUNT(*) FROM synthetic_empty").fetchone()[0], 0)
            tables = {
                row[0]
                for row in check.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
            self.assertNotIn("grok_sessions", tables)
            check.execute("CREATE TABLE keep_me (id INTEGER PRIMARY KEY, label TEXT)")
            check.execute("INSERT INTO keep_me VALUES (1, 'leave-this')")
            check.commit()
            check.close()

            code = fitdash_restore.run_restore(
                fitdash_restore.RestoreOptions(
                    archive=archive,
                    identity=identity,
                    mode="sqlite-only",
                    apply=True,
                    target=target,
                    sqlite_db=restored,
                )
            )
            self.assertEqual(code, 0)
            check = sqlite3.connect(restored)
            self.assertEqual(
                check.execute("SELECT label FROM keep_me").fetchone()[0],
                "leave-this",
            )
            self.assertEqual(check.execute("SELECT COUNT(*) FROM workout_sessions").fetchone()[0], 1)
            check.close()

            users = sqlite3.connect(target / "sqlite" / "fitdash.db")
            self.assertEqual(users.execute("SELECT name FROM users").fetchone()[0], "synthetic-user")
            users.close()
            self.assertIn(BAROMETER, (target / "pi-local" / "phase_barometer.json").read_text(encoding="utf-8"))
            self.assertTrue((target / "pi-local" / "hsa" / "default.json").is_file())
            self.assertTrue((target / "pi-local" / "labs" / "index.json").is_file())
            self.assertTrue((target / "pi-local" / "coach_state.json").is_file())
            self.assertTrue((target / "pi-local" / "systemd" / "resistance-dashboard.service").is_file())
            self.assertFalse((target / "pi-local" / "systemd" / "other.service").exists())
            packed = target / "pi-local-code.tgz"
            self.assertTrue(packed.is_file())
            import tarfile

            with tarfile.open(packed, "r:gz") as code_tar:
                members = set(code_tar.getnames())
            self.assertEqual(members, {"only_on_pi.py"})
            tree_text = "\n".join(
                path.read_text(encoding="utf-8", errors="ignore")
                for path in target.rglob("*")
                if path.is_file() and path.suffix not in {".db", ".sqlite3", ".tgz", ".age"}
            )
            for secret in (SECRET_ENV, SECRET_KEY, SECRET_GROK, SECRET_OAUTH, CACHE_MARKER):
                self.assertNotIn(secret, tree_text)
            self.assertEqual(_temp_leftovers("fitdash-backup-") | _temp_leftovers("fitdash-restore-"), before)

            live = root / "live-config"
            live.mkdir()
            code = fitdash_restore.run_restore(
                fitdash_restore.RestoreOptions(
                    archive=archive,
                    identity=identity,
                    mode="sqlite-only",
                    apply=True,
                    target=root / "ignored",
                    target_live=True,
                    yes=False,
                    config_dir=live,
                )
            )
            self.assertEqual(code, 1)
            self.assertEqual(list(live.iterdir()), [])


class Units(unittest.TestCase):
    def test_systemd_units_match_the_schedule(self) -> None:
        unit_dir = BACKUP_DIR / "systemd"
        service = (unit_dir / "fitdash-backup.service").read_text(encoding="utf-8")
        timer = (unit_dir / "fitdash-backup.timer").read_text(encoding="utf-8")
        retry = (unit_dir / "fitdash-backup-retry.timer").read_text(encoding="utf-8")
        failed = (unit_dir / "fitdash-backup-failed.service").read_text(encoding="utf-8")
        self.assertIn("OnFailure=fitdash-backup-failed.service", service)
        self.assertIn("SuccessExitStatus=75", service)
        self.assertIn("--schedule-retry", service)
        self.assertIn("backup.env", service)
        self.assertIn("OnCalendar=*-*-* 03:40:00 America/New_York", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("OnActiveSec=30min", retry)
        self.assertIn("--record-failure", failed)
        self.assertNotIn("buzz messages", failed)
        self.assertNotIn("WantedBy=", failed)
        self.assertNotIn("WantedBy=", retry)


if __name__ == "__main__":
    unittest.main()
