"""From-number lock: roadside SMS is 904 via send-904; 906 never goes out.

These tests mock subprocess and urlopen. They do not call the relay,
SMSGate, or Bland, and they do not send a text.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from adapters import (
    BLAND_CALL_URL,
    BLAND_SMS_URL,
    VOICE_FROM_904_UNAVAILABLE,
    ExternalSendError,
    LiveBland,
    RecordingBland,
    deliver_via_send904,
    require_line_904,
)
from config import Config
from phones import LINE_904, LINE_906, is_line_906
from pipeline import shift_clock
from test_pipeline import NOW, _pipe

REPO = Path(__file__).resolve().parents[3]
SEND904 = REPO / "deploy" / "bland-relay" / "send-904"
RELAY_CONFIG = REPO / "deploy" / "bland-relay" / "relay_config.py"
AUTOREPLY = REPO / "deploy" / "bland-relay" / "relay_autoreply.py"


def _ok_proc(extra: str = "") -> mock.Mock:
    body = '{"ok": true, "id": "gw-1", "state": "Pending"}'
    if extra:
        body = extra
    return mock.Mock(returncode=0, stdout=f"200\n{body}\n", stderr="")


class FromNumberLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _pipeline(self):
        return _pipe(self.tmp.name)

    def test_line_904_matches_the_relay_handset(self) -> None:
        text = RELAY_CONFIG.read_text(encoding="utf-8")
        self.assertIn(f'LINE_904 = "{LINE_904}"', text)
        self.assertNotIn(f'LINE_904 = "{LINE_906}"', text)

    def test_auto_reply_906_defaults_off(self) -> None:
        source = AUTOREPLY.read_text(encoding="utf-8")
        self.assertIn('return env("AUTO_REPLY_906_ENABLED") == "1"', source)
        relay_dir = str(AUTOREPLY.parent)
        if relay_dir not in sys.path:
            sys.path.insert(0, relay_dir)
        os.environ.pop("AUTO_REPLY_906_ENABLED", None)
        import bland_relay

        self.assertFalse(bland_relay.auto_reply_906_enabled())
        self.assertEqual(bland_relay.LINE_904, LINE_904)

    def test_require_904_accepts_empty_and_explicit_904(self) -> None:
        self.assertEqual(require_line_904("", channel="sms"), LINE_904)
        self.assertEqual(require_line_904("   ", channel="sms"), LINE_904)
        for raw in (LINE_904, "9043343975", "19043343975", "(904) 334-3975", "+1 904 334 3975"):
            self.assertEqual(require_line_904(raw, channel="sms"), LINE_904, raw)

    def test_require_904_rejects_906_and_other_numbers(self) -> None:
        for raw in (LINE_906, "9062406221", "19062406221", "+1 (906) 240-6221", "906-240-6221"):
            self.assertTrue(is_line_906(raw), raw)
            with self.assertRaises(ExternalSendError) as ctx:
                require_line_904(raw, channel="sms")
            message = str(ctx.exception)
            self.assertIn("906", message)
            self.assertIn(LINE_906, message)
            self.assertIn("not sending", message)
        with self.assertRaises(ExternalSendError) as ctx:
            require_line_904("+15555550100", channel="voice")
        self.assertIn(LINE_904, str(ctx.exception))
        self.assertIn("must be", str(ctx.exception))

    def test_recording_sms_is_labeled_904(self) -> None:
        row = RecordingBland(dry_run=True).send_sms(to="2395550101", body="hi", lead_id="x")
        self.assertEqual(row["from"], LINE_904)
        self.assertNotEqual(row["from"], LINE_906)

    def _live(self, from_number: str = "") -> LiveBland:
        return LiveBland(
            Config(
                dry_run=False,
                copy_approved=True,
                bland_api_key="",
                bland_from_number=from_number,
            )
        )

    def test_live_sms_uses_send904_and_not_bland(self) -> None:
        bland = self._live()
        body = "hello from the roadside desk"
        with mock.patch("adapters.urllib.request.urlopen", side_effect=AssertionError("network")):
            with mock.patch("adapters.subprocess.run", return_value=_ok_proc()) as run:
                with mock.patch.dict(os.environ, {"SEND904_BIN": str(SEND904)}):
                    row = bland.send_sms(to="239-555-0101", body=body, lead_id="lead-1")
        self.assertEqual(run.call_count, 1)
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[1], str(SEND904))
        self.assertEqual(argv[2], "+12395550101")
        self.assertEqual(len(argv), 3)
        self.assertEqual(run.call_args.kwargs["input"], body)
        self.assertNotIn(body, argv)
        blob = str(run.call_args)
        self.assertNotIn("api.bland.ai", blob)
        self.assertNotIn(BLAND_SMS_URL, blob)
        self.assertNotIn(BLAND_CALL_URL, blob)
        self.assertNotIn(LINE_906, blob)
        self.assertNotIn("9062406221", blob)
        self.assertEqual(row["from"], LINE_904)
        self.assertEqual(row["via"], "send-904")
        self.assertEqual(row["to"], "+12395550101")
        self.assertTrue(row["sent"])
        self.assertFalse(row["dry_run"])
        self.assertEqual(row["gateway_id"], "gw-1")

    def test_explicit_904_from_still_uses_send904(self) -> None:
        bland = self._live(LINE_904)
        with mock.patch("adapters.urllib.request.urlopen", side_effect=AssertionError("network")):
            with mock.patch("adapters.subprocess.run", return_value=_ok_proc()) as run:
                with mock.patch.dict(os.environ, {"SEND904_BIN": str(SEND904)}):
                    row = bland.send_sms(to="+12395550101", body="ok", lead_id="lead-1")
        self.assertEqual(row["from"], LINE_904)
        self.assertEqual(run.call_args.args[0][1], str(SEND904))

    def test_906_from_does_not_spawn_send904(self) -> None:
        bland = self._live(LINE_906)
        with mock.patch("adapters.urllib.request.urlopen", side_effect=AssertionError("network")):
            with mock.patch("adapters.subprocess.run", side_effect=AssertionError("spawned")) as run:
                with self.assertRaises(ExternalSendError) as ctx:
                    bland.send_sms(to="+12395550101", body="nope", lead_id="lead-1")
        self.assertIn(LINE_906, str(ctx.exception))
        self.assertFalse(run.called)
        self.assertEqual(bland.sends, [])

    def test_other_from_and_906_destination_do_not_spawn(self) -> None:
        with mock.patch("adapters.subprocess.run", side_effect=AssertionError("spawned")) as run:
            with self.assertRaises(ExternalSendError):
                self._live("+15555550100").send_sms(to="+12395550101", body="nope", lead_id="lead-1")
            with self.assertRaises(ExternalSendError) as ctx:
                self._live("").send_sms(to=LINE_906, body="nope", lead_id="lead-1")
        self.assertIn(LINE_906, str(ctx.exception))
        self.assertFalse(run.called)

    def test_relay_refusal_and_secret_stderr_are_not_a_send(self) -> None:
        refused = mock.Mock(
            returncode=1,
            stdout='403\n{"ok": false, "error": "opt_out"}\n',
            stderr="error: token super-secret-value",
        )
        bland = self._live()
        with mock.patch("adapters.subprocess.run", return_value=refused):
            with mock.patch.dict(os.environ, {"SEND904_BIN": str(SEND904)}):
                with self.assertRaises(ExternalSendError) as ctx:
                    bland.send_sms(to="+12395550101", body="hi", lead_id="lead-1")
        self.assertIn("opt_out", str(ctx.exception))
        self.assertNotIn("super-secret-value", str(ctx.exception))
        self.assertEqual(bland.sends, [])

    def test_echoed_906_is_rejected(self) -> None:
        proc = _ok_proc('{"ok": true, "from": "+19062406221", "id": "gw-9"}')
        with mock.patch("adapters.subprocess.run", return_value=proc) as run:
            with mock.patch.dict(os.environ, {"SEND904_BIN": str(SEND904)}):
                with self.assertRaises(ExternalSendError) as ctx:
                    self._live().send_sms(to="+12395550101", body="hi", lead_id="lead-1")
        self.assertIn(LINE_906, str(ctx.exception))
        self.assertEqual(run.call_count, 1)

    def test_echoed_904_line_is_accepted(self) -> None:
        proc = _ok_proc('{"ok": true, "line": "+19043343975", "id": "gw-2", "state": "Pending"}')
        with mock.patch("adapters.subprocess.run", return_value=proc):
            with mock.patch.dict(os.environ, {"SEND904_BIN": str(SEND904)}):
                row = self._live().send_sms(to="+12395550101", body="hi", lead_id="lead-1")
        self.assertEqual(row["from"], LINE_904)
        self.assertEqual(row["gateway_id"], "gw-2")

    def test_timeout_is_not_retried(self) -> None:
        with mock.patch(
            "adapters.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["send-904"], timeout=25),
        ) as run:
            with mock.patch.dict(os.environ, {"SEND904_BIN": str(SEND904)}):
                with self.assertRaises(ExternalSendError) as ctx:
                    deliver_via_send904("+12395550101", "hi")
        self.assertIn("not retrying", str(ctx.exception))
        self.assertEqual(run.call_count, 1)

    def test_send_path_must_be_named_send904(self) -> None:
        with mock.patch("adapters.subprocess.run", side_effect=AssertionError("spawned")) as run:
            with mock.patch.dict(os.environ, {"SEND904_BIN": "/tmp/send-906"}):
                with self.assertRaises(ExternalSendError) as ctx:
                    self._live().send_sms(to="+12395550101", body="hi", lead_id="lead-1")
        self.assertIn("send-904", str(ctx.exception))
        self.assertFalse(run.called)

    def test_voice_fails_closed_for_unset_904_and_906(self) -> None:
        for from_number in ("", LINE_904, "(904) 334-3975", LINE_906, "+15555550100"):
            bland = self._live(from_number)
            with mock.patch("adapters.urllib.request.urlopen", side_effect=AssertionError("network")):
                with mock.patch("adapters.subprocess.run", side_effect=AssertionError("spawned")) as run:
                    with self.assertRaises(ExternalSendError) as ctx:
                        bland.start_call(to="+12395550101", task="pitch", lead_id="lead-1")
            self.assertFalse(run.called)
            message = str(ctx.exception)
            self.assertNotIn("bland_api_key", message)
            if from_number in ("", LINE_904, "(904) 334-3975"):
                self.assertEqual(message, VOICE_FROM_904_UNAVAILABLE)
                self.assertIn(BLAND_CALL_URL, message)
                self.assertIn(LINE_904, message)
                self.assertIn(LINE_906, message)
            elif is_line_906(from_number):
                self.assertIn("not sending", message)
                self.assertIn(LINE_906, message)
            else:
                self.assertIn("must be", message)
                self.assertIn(LINE_904, message)
            self.assertEqual(bland.calls, [])

    def test_voice_to_906_does_not_dial(self) -> None:
        with mock.patch("adapters.subprocess.run", side_effect=AssertionError("spawned")):
            with self.assertRaises(ExternalSendError) as ctx:
                self._live().start_call(to=LINE_906, task="pitch", lead_id="lead-1")
        self.assertIn(LINE_906, str(ctx.exception))

    def test_pipeline_refuses_906_before_the_adapter(self) -> None:
        pipe = self._pipeline()
        pipe.weekly_pass()
        pipe.cfg.bland_from_number = "+1 (906) 240-6221"
        calls: list[object] = []

        class Spy:
            def send_sms(self, **kwargs: object) -> dict[str, object]:
                calls.append(kwargs)
                raise AssertionError("adapter should not send")

        pipe.adapters.bland = Spy()
        lead = pipe.store.get("lead-set-corolla")
        assert lead is not None
        with self.assertRaises(ExternalSendError) as ctx:
            pipe.send_sms(lead)
        self.assertIn(LINE_906, str(ctx.exception))
        self.assertEqual(calls, [])
        stored = pipe.store.get("lead-set-corolla")
        assert stored is not None
        self.assertEqual(stored.state, "new")

    def test_dry_run_outbox_records_904(self) -> None:
        pipe = self._pipeline()
        pipe.weekly_pass()
        with mock.patch("adapters.subprocess.run", side_effect=AssertionError("subprocess during dry-run")):
            result = pipe.send_sms(pipe.store.get("lead-set-corolla"))  # type: ignore[arg-type]
        self.assertEqual(result["sms"]["from"], LINE_904)
        self.assertTrue(result["sms"]["dry_run"])
        self.assertFalse(result["sms"]["sent"])

    def test_live_voice_does_not_call_the_adapter(self) -> None:
        pipe = self._pipeline()
        pipe.weekly_pass()
        corolla = pipe.store.get("lead-set-corolla")
        assert corolla is not None
        pipe.send_sms(corolla)
        pipe.cfg.dry_run = False
        pipe.cfg.copy_approved = True
        pipe.cfg.now = lambda: shift_clock(NOW, 6)
        calls: list[object] = []

        class Spy:
            def start_call(self, **kwargs: object) -> dict[str, object]:
                calls.append(kwargs)
                raise AssertionError("voice adapter should not be called")

        pipe.adapters.bland = Spy()
        with self.assertRaises(ExternalSendError) as ctx:
            pipe.start_call(pipe.store.get("lead-set-corolla"))  # type: ignore[arg-type]
        self.assertEqual(str(ctx.exception), VOICE_FROM_904_UNAVAILABLE)
        self.assertEqual(calls, [])
        stored = pipe.store.get("lead-set-corolla")
        assert stored is not None
        self.assertEqual(stored.state, "sms_sent")

        pipe.cfg.bland_from_number = LINE_906
        with self.assertRaises(ExternalSendError) as ctx:
            pipe.start_call(stored)
        self.assertIn(LINE_906, str(ctx.exception))
        self.assertEqual(calls, [])
        self.assertEqual(stored.state, "sms_sent")


if __name__ == "__main__":
    unittest.main()
