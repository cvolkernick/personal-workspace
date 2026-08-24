"""Read-only Auto Fleet / turo_inbox brief (#295). Token/snapshot allow vs deny."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional
from unittest import mock

PKG = Path(__file__).resolve().parents[1]
ROOT = PKG.parent
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import agent_fleet  # noqa: E402
import turo_gmail  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MIKES = FIXTURES / "turo_mikes_vehicle.json"
EMPTY = PKG / "data" / "turo_inbox.json"
SHIPPED_SNAPSHOT = PKG / "data" / "agent_fleet_latest.json"


class _Headers:
    def __init__(self, data: Optional[Dict[str, str]] = None) -> None:
        self._data = {str(k): str(v) for k, v in (data or {}).items()}

    def get(self, key: str, default: Any = None) -> Any:
        lower = {k.lower(): v for k, v in self._data.items()}
        return lower.get(str(key).lower(), default)


def _fixture_jpeg(tag: bytes = b"blocked-in") -> bytes:
    comment = tag[:60]
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        + b"\xff\xfe"
        + bytes([(len(comment) + 2) >> 8, (len(comment) + 2) & 0xFF])
        + comment
        + b"\xff\xd9"
    )


class AgentFleetExportTests(unittest.TestCase):
    def test_empty_shipped_inbox_is_honest_stale(self) -> None:
        packet = agent_fleet.export_agent_fleet(inbox_path=EMPTY)
        self.assertTrue(packet["ok"])
        self.assertTrue(packet["read_only"])
        self.assertTrue(packet["stale"])
        self.assertIn("dark", str(packet.get("stale_reason") or "").lower())
        self.assertEqual(packet["unit_count"], 5)
        by_id = {u["id"]: u for u in packet["units"]}
        for uid in (
            "m3-2020",
            "m3-2022",
            "corolla-2022",
            "corolla-2024",
            "r1s-2023",
        ):
            self.assertEqual(by_id[uid]["bookings"], [])
            self.assertEqual(by_id[uid]["schedule"], [])
        self.assertEqual(packet["unmatched"], [])
        self.assertNotIn("99112233", json.dumps(packet))
        self.assertNotIn("Spark", json.dumps(packet))
        self.assertNotIn("Kia", json.dumps(packet))
        self.assertEqual(agent_fleet.secret_leaks(packet), [])

    def test_dump_derived_bookings_no_invented_trips(self) -> None:
        packet = agent_fleet.export_agent_fleet(inbox_path=MIKES)
        self.assertTrue(packet["ok"])
        by_id = {u["id"]: u for u in packet["units"]}
        m3 = by_id["m3-2022"]
        self.assertEqual(len(m3["bookings"]), 1)
        rec = m3["bookings"][0]
        self.assertEqual(rec["trip_id"], "99112233")
        self.assertEqual(rec["guest"], "Alex Rivera")
        self.assertEqual(rec["unit_id"], "m3-2022")
        self.assertEqual(rec["status"], "booked")
        self.assertTrue(m3["schedule"])
        self.assertEqual(m3["schedule"][0]["trip_id"], "99112233")
        self.assertEqual(by_id["corolla-2024"]["bookings"], [])
        self.assertEqual(by_id["m3-2020"]["bookings"], [])
        blob = json.dumps(packet)
        self.assertNotIn("Jessica", blob)
        self.assertNotIn("Kia", blob)
        self.assertNotIn("32786339", blob)
        self.assertNotIn("5YJ3E1EA6NF289917", blob)
        self.assertNotIn("111088614673", blob)
        self.assertNotIn("finance", blob)
        self.assertNotIn("dimo", blob.lower())
        self.assertNotIn("google_tasks", blob.lower())
        self.assertEqual(agent_fleet.secret_leaks(packet), [])

    def test_identity_omits_vin_account_lender(self) -> None:
        packet = agent_fleet.export_agent_fleet(inbox_path=EMPTY)
        for unit in packet["units"]:
            ident = unit["identity"]
            self.assertNotIn("vin", ident)
            self.assertNotIn("account", ident)
            self.assertNotIn("lender", ident)
            self.assertNotIn("plate", ident)
            self.assertIn("role", ident)

    def test_attachments_metadata_without_absolute_path(self) -> None:
        jpeg = _fixture_jpeg(b"blocked-in")
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "turo_inbox.json"
            photo = Path(td) / "turo_inbox_media" / "msg-photo-1" / "blocked-in.jpg"
            photo.parent.mkdir(parents=True)
            photo.write_bytes(jpeg)
            dest.write_text(
                json.dumps(
                    {
                        "as_of": "2026-08-19T15:10:00+00:00",
                        "source": "test_fixture",
                        "messages": [
                            {
                                "id": "msg-photo-1",
                                "from": "Turo <noreply@mail.turo.com>",
                                "subject": (
                                    "(Mike's vehicle) - Pat's trip with your "
                                    "Toyota Corolla is booked!"
                                ),
                                "date": "Tue, 19 Aug 2026 15:10:00 +0000",
                                "body": (
                                    "Toyota Corolla 2024\nbooked by Pat Kim\n"
                                    "Reservation ID #60619999\n"
                                ),
                                "attachments": [
                                    {
                                        "filename": "blocked-in.jpg",
                                        "mime": "image/jpeg",
                                        "size": len(jpeg),
                                        "sha256": hashlib.sha256(jpeg).hexdigest(),
                                        "path": str(photo),
                                        "relpath": "msg-photo-1/blocked-in.jpg",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            packet = agent_fleet.export_agent_fleet(inbox_path=dest)
            by_id = {u["id"]: u for u in packet["units"]}
            rec = by_id["corolla-2024"]["bookings"][0]
            self.assertEqual(len(rec["attachments"]), 1)
            att = rec["attachments"][0]
            self.assertEqual(att["filename"], "blocked-in.jpg")
            self.assertEqual(att["relpath"], "msg-photo-1/blocked-in.jpg")
            self.assertEqual(att["sha256"], hashlib.sha256(jpeg).hexdigest())
            self.assertNotIn("path", att)
            self.assertNotIn("data", att)
            self.assertNotIn(str(photo), json.dumps(packet))
            self.assertTrue(by_id["corolla-2024"]["photos"])
            self.assertEqual(agent_fleet.secret_leaks(packet), [])

    def test_missing_inbox_is_honest_empty(self) -> None:
        missing = Path("/tmp/auto-fleet-missing-inbox-does-not-exist.json")
        packet = agent_fleet.export_agent_fleet(inbox_path=missing)
        self.assertTrue(packet["stale"])
        self.assertEqual(packet["inbox"]["state"], "unconfigured")
        for unit in packet["units"]:
            self.assertEqual(unit["bookings"], [])

    def test_secret_leaks_refuse_publish(self) -> None:
        dirty = {
            "ok": True,
            "read_only": True,
            "DIMO_API_KEY": "secret-value",
            "units": [],
        }
        self.assertIn("secret_marker", agent_fleet.secret_leaks(dirty))
        with self.assertRaises(RuntimeError):
            agent_fleet.assert_no_secrets(dirty)
        cookie = {
            "ok": True,
            "read_only": True,
            "cookie": "SID=abc",
            "units": [],
        }
        leaks = agent_fleet.secret_leaks(cookie)
        self.assertTrue(leaks)
        with self.assertRaises(RuntimeError):
            agent_fleet.assert_no_secrets(cookie)

    def test_shipped_snapshot_is_stale_empty(self) -> None:
        data = json.loads(SHIPPED_SNAPSHOT.read_text(encoding="utf-8"))
        self.assertTrue(data["ok"])
        self.assertTrue(data["stale"])
        self.assertEqual(data["unit_count"], 5)
        for unit in data["units"]:
            self.assertEqual(unit["bookings"], [])
        self.assertEqual(agent_fleet.secret_leaks(data), [])


class SnapshotAllowDenyTests(unittest.TestCase):
    def test_snapshot_missing_is_deny(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "nope.json"
            self.assertFalse(agent_fleet.snapshot_present(missing, env={}))
            self.assertIsNone(agent_fleet.load_snapshot(missing, env={}))
            with mock.patch("sys.stdout"):
                rc = agent_fleet.main(["--read", "--out", str(missing)])
            self.assertEqual(rc, 2)

    def test_snapshot_present_is_allow(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "agent_fleet.json"
            published = agent_fleet.publish_from_inbox(inbox_path=MIKES, dest=dest)
            self.assertEqual(published, dest)
            self.assertTrue(agent_fleet.snapshot_present(dest, env={}))
            loaded = agent_fleet.load_snapshot(dest, env={})
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertTrue(loaded["ok"])
            self.assertTrue(loaded["read_only"])
            self.assertEqual(loaded["source"], "snapshot")
            self.assertTrue(loaded["stale"])
            by_id = {u["id"]: u for u in loaded["units"]}
            self.assertEqual(by_id["m3-2022"]["bookings"][0]["trip_id"], "99112233")
            self.assertEqual(agent_fleet.secret_leaks(loaded), [])
            with mock.patch("sys.stdout"):
                rc = agent_fleet.main(["--read", "--out", str(dest)])
            self.assertEqual(rc, 0)

    def test_snapshot_read_does_not_fake_freshness(self) -> None:
        """Cadence AC: snapshot consume is stale — never look live when writer is dark."""
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "agent_fleet.json"
            fake_fresh = {
                "ok": True,
                "read_only": True,
                "as_of": "2026-08-24T02:00:00+00:00",
                "stale": False,
                "stale_reason": None,
                "source": "inbox",
                "unit_count": 0,
                "units": [],
                "unmatched": [],
                "inbox": {
                    "state": "parsed",
                    "status": "fixture",
                    "poll_interval_s": 900,
                    "payout_destination": "X Money",
                },
            }
            dest.write_text(json.dumps(fake_fresh), encoding="utf-8")
            loaded = agent_fleet.load_snapshot(dest, env={})
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertTrue(loaded["stale"])
            self.assertEqual(loaded["source"], "snapshot")
            self.assertIn("not a live writer", str(loaded.get("stale_reason") or ""))
            self.assertEqual(loaded["units"], [])

    def test_inline_snapshot_json_env(self) -> None:
        packet = agent_fleet.export_agent_fleet(inbox_path=MIKES)
        env = {"AUTO_FLEET_AGENT_SNAPSHOT_JSON": json.dumps(packet)}
        self.assertTrue(agent_fleet.snapshot_present(env=env))
        loaded = agent_fleet.load_snapshot(env=env)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(
            loaded["units"][1]["bookings"][0]["trip_id"]
            if loaded["units"][1]["id"] == "m3-2022"
            else next(
                u["bookings"][0]["trip_id"]
                for u in loaded["units"]
                if u["id"] == "m3-2022"
            ),
            "99112233",
        )

    def test_dirty_snapshot_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "agent_fleet.json"
            dest.write_text(
                json.dumps({"ok": True, "read_only": True, "DIMO_API_KEY": "x"}),
                encoding="utf-8",
            )
            self.assertIsNone(agent_fleet.load_snapshot(dest, env={}))

    def test_serve_prefers_snapshot_when_writer_dark(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "agent_fleet.json"
            agent_fleet.publish_from_inbox(inbox_path=MIKES, dest=dest)
            missing = Path(td) / "missing-inbox.json"
            packet = agent_fleet.serve_agent_fleet(
                inbox_path=missing,
                env={"AUTO_FLEET_AGENT_SNAPSHOT": str(dest)},
            )
            self.assertEqual(packet["source"], "snapshot")
            self.assertTrue(packet["stale"])
            by_id = {u["id"]: u for u in packet["units"]}
            self.assertEqual(by_id["m3-2022"]["bookings"][0]["trip_id"], "99112233")


class AgentFleetHttpAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.get(k)
            for k in (
                "AUTO_FLEET_SERVICE_TOKEN",
                "AUTO_FLEET_SERVICE_LOOPBACK",
                "VERCEL",
            )
        }
        os.environ.pop("VERCEL", None)

    def tearDown(self) -> None:
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def test_loopback_allow_without_token(self) -> None:
        os.environ.pop("AUTO_FLEET_SERVICE_TOKEN", None)
        os.environ["AUTO_FLEET_SERVICE_LOOPBACK"] = "1"
        code, body = agent_fleet.handle_agent_fleet_http(
            _Headers(),
            "127.0.0.1",
            inbox_path=EMPTY,
        )
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["stale"])
        self.assertEqual(body["unit_count"], 5)

    def test_token_allow_vs_deny(self) -> None:
        os.environ["AUTO_FLEET_SERVICE_TOKEN"] = "house-secret"
        os.environ["AUTO_FLEET_SERVICE_LOOPBACK"] = "0"
        denied_code, denied = agent_fleet.handle_agent_fleet_http(
            _Headers(),
            "192.168.100.5",
            inbox_path=MIKES,
        )
        self.assertEqual(denied_code, 401)
        self.assertEqual(denied["error"], "auth_required")
        self.assertNotIn("units", denied)
        self.assertNotIn("99112233", json.dumps(denied))
        self.assertNotIn("house-secret", json.dumps(denied))

        code, body = agent_fleet.handle_agent_fleet_http(
            _Headers({"Authorization": "Bearer house-secret"}),
            "192.168.100.5",
            inbox_path=MIKES,
        )
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        by_id = {u["id"]: u for u in body["units"]}
        self.assertEqual(by_id["m3-2022"]["bookings"][0]["trip_id"], "99112233")
        self.assertNotIn("house-secret", json.dumps(body))

        code2, body2 = agent_fleet.handle_agent_fleet_http(
            _Headers({"X-Auto-Fleet-Service-Token": "house-secret"}),
            "10.0.0.8",
            inbox_path=MIKES,
        )
        self.assertEqual(code2, 200)
        self.assertTrue(body2["ok"])

        wrong, wrong_body = agent_fleet.handle_agent_fleet_http(
            _Headers({"Authorization": "Bearer nope"}),
            "192.168.100.5",
            inbox_path=MIKES,
        )
        self.assertEqual(wrong, 401)
        self.assertEqual(wrong_body["error"], "auth_required")


class WriterPublishTests(unittest.TestCase):
    def test_gmail_main_fetch_publishes_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "dump.json"
            missing = Path(td) / "no-token.json"
            env_file = Path(td) / "empty.env"
            env_file.write_text("# no gmail keys\n", encoding="utf-8")
            rc = turo_gmail.main(
                [
                    "--fetch",
                    "--out",
                    str(dest),
                    "--token",
                    str(missing),
                    "--env-file",
                    str(env_file),
                ]
            )
            self.assertEqual(rc, 0)
            snap = Path(td) / "agent_fleet.json"
            self.assertTrue(snap.is_file(), snap)
            packet = json.loads(snap.read_text(encoding="utf-8"))
            self.assertTrue(packet["ok"])
            self.assertTrue(packet["read_only"])
            self.assertEqual(packet["source"], "snapshot")
            for unit in packet["units"]:
                self.assertEqual(unit["bookings"], [])
            self.assertEqual(agent_fleet.secret_leaks(packet), [])


class VercelStyleExportTests(unittest.TestCase):
    def test_vercel_handler_deny_without_token(self) -> None:
        api = PKG / "api" / "agent" / "fleet.py"
        ns: dict[str, Any] = {"__file__": str(api)}
        exec(api.read_text(encoding="utf-8"), ns)
        with mock.patch.dict(
            os.environ,
            {"AUTO_FLEET_SERVICE_TOKEN": "house-secret", "VERCEL": "1"},
            clear=False,
        ):
            code, body = ns["agent_fleet_body"](_Headers(), "")
        self.assertEqual(code, 401)
        self.assertEqual(body["error"], "auth_required")

    def test_vercel_handler_allow_with_token_and_snapshot(self) -> None:
        api = PKG / "api" / "agent" / "fleet.py"
        ns: dict[str, Any] = {"__file__": str(api)}
        exec(api.read_text(encoding="utf-8"), ns)
        packet = agent_fleet.export_agent_fleet(inbox_path=MIKES)
        with mock.patch.dict(
            os.environ,
            {
                "AUTO_FLEET_SERVICE_TOKEN": "house-secret",
                "VERCEL": "1",
                "AUTO_FLEET_AGENT_SNAPSHOT_JSON": json.dumps(packet),
            },
            clear=False,
        ):
            code, body = ns["agent_fleet_body"](
                _Headers({"Authorization": "Bearer house-secret"}),
                "",
            )
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        by_id = {u["id"]: u for u in body["units"]}
        self.assertEqual(by_id["m3-2022"]["bookings"][0]["trip_id"], "99112233")


if __name__ == "__main__":
    unittest.main()
