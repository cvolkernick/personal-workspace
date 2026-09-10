"""Unit tests for the Android remote-loop harness (#617)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import android_loop as al  # noqa: E402


FAILED_CONNECTED = """
> Task :app:connectedDebugAndroidTest

com.cvolkernick.androidloop.fixture.MainActivityTest > displaysLabel[Pixel_3a_API_29(AVD) - 10] FAILED
	java.lang.AssertionError: 'Android Loop Fixture' doesn't match the selected view
	Expected: with text: is "Android Loop Fixture"
	Got: "wrong"
	at androidx.test.espresso.ViewInteraction.check(ViewInteraction.java:158)
	at com.cvolkernick.androidloop.fixture.MainActivityTest.displaysLabel(MainActivityTest.java:21)

com.cvolkernick.androidloop.fixture.MainActivityTest > extraFail[Pixel_3a_API_29(AVD) - 10] FAILED
	java.lang.NullPointerException: boom
	at com.cvolkernick.androidloop.fixture.MainActivityTest.extraFail(MainActivityTest.java:30)

> Task :app:connectedDebugAndroidTest FAILED

FAILURE: Build failed with an exception.

BUILD FAILED in 12s
"""

SUCCESS_CONNECTED = """
> Task :app:connectedDebugAndroidTest

BUILD SUCCESSFUL in 8s
"""


class TestPhraseMap(unittest.TestCase):
    def test_ac_screenshot_phrase(self) -> None:
        self.assertEqual(
            al.map_phrase(
                "Open the Android emulator on the registered machine and take a screenshot."
            ),
            "screenshot",
        )

    def test_ac_assemble_phrase(self) -> None:
        self.assertEqual(
            al.map_phrase("Build the debug APK and report whether it compiled."),
            "assemble",
        )

    def test_ac_run_phrase(self) -> None:
        self.assertEqual(
            al.map_phrase(
                "Install the latest debug build on the emulator, launch the app, and send back a screenshot."
            ),
            "run",
        )

    def test_ac_test_phrase(self) -> None:
        self.assertEqual(
            al.map_phrase("Run instrumented tests and paste the failing stack traces."),
            "test",
        )

    def test_unknown(self) -> None:
        self.assertIsNone(al.map_phrase("deploy the Pi tomorrow"))


class TestFailureParser(unittest.TestCase):
    def test_extracts_stacks(self) -> None:
        failures = al.parse_instrumented_failures(FAILED_CONNECTED)
        self.assertEqual(len(failures), 2)
        self.assertIn("displaysLabel", failures[0]["test"])
        self.assertIn("java.lang.AssertionError", failures[0]["stack"])
        self.assertIn("ViewInteraction.check", failures[0]["stack"])
        self.assertIn("NullPointerException", failures[1]["stack"])

    def test_success_has_no_failures(self) -> None:
        self.assertEqual(al.parse_instrumented_failures(SUCCESS_CONNECTED), [])


class TestJavaMajor(unittest.TestCase):
    def test_modern(self) -> None:
        self.assertEqual(al.java_major('openjdk version "17.0.20" 2025-01-01'), 17)

    def test_legacy_1_8(self) -> None:
        self.assertEqual(al.java_major('java version "1.8.0_292"'), 8)

    def test_eleven(self) -> None:
        self.assertEqual(al.java_major('openjdk version "11.0.11" 2021-04-20'), 11)


class TestFindApkAndAppId(unittest.TestCase):
    def test_application_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            app = root / "app"
            app.mkdir()
            (app / "build.gradle").write_text(
                'android { defaultConfig { applicationId "com.example.foo" } }\n',
                encoding="utf-8",
            )
            self.assertEqual(al.read_application_id(root), "com.example.foo")

    def test_find_debug_apk_newest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            debug = Path(td) / "app" / "build" / "outputs" / "apk" / "debug"
            debug.mkdir(parents=True)
            older = debug / "app-debug.apk"
            newer = debug / "app-debug-2.apk"
            older.write_bytes(b"old")
            newer.write_bytes(b"new")
            found = al.find_debug_apk(Path(td))
            self.assertIsNotNone(found)
            self.assertEqual(found.name, "app-debug-2.apk")


class TestDoctorJson(unittest.TestCase):
    def test_doctor_emits_invoke_block(self) -> None:
        payload = al.cmd_doctor(argparse_ns())
        self.assertEqual(payload["action"], "doctor")
        self.assertIn("checks", payload)
        self.assertEqual(len(payload["invoke"]["phrases"]), 4)
        self.assertTrue(payload["invoke"]["local_only"])
        json.dumps(payload)


def argparse_ns(**kwargs: object) -> object:
    ns = mock.Mock()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


class TestFocusReady(unittest.TestCase):
    def test_launcher(self) -> None:
        self.assertTrue(
            al.is_focus_ready(
                "mCurrentFocus=Window{u0 com.google.android.apps.nexuslauncher/"
                "com.google.android.apps.nexuslauncher.NexusLauncherActivity}"
            )
        )

    def test_setup_wizard_not_ready(self) -> None:
        self.assertFalse(
            al.is_focus_ready("mCurrentFocus=Window{u0 com.google.android.setupwizard/.SetupWizardActivity}")
        )

    def test_fixture_app_ready(self) -> None:
        self.assertTrue(
            al.is_focus_ready(
                "mCurrentFocus=Window{u0 com.cvolkernick.androidloop.fixture/.MainActivity}"
            )
        )


class TestStaleStudioJavaIgnored(unittest.TestCase):
    def test_broken_studio_home_not_selected(self) -> None:
        missing = Path("/Applications/Android Studio.app/Contents/jre/Contents/Home")
        with mock.patch.dict(
            "os.environ",
            {"JAVA_HOME": str(missing), "ANDROID_LOOP_JAVA_HOME": ""},
            clear=False,
        ):
            # Candidate list includes the stale path; usability check must reject it.
            self.assertFalse(al._java_home_usable(missing))


if __name__ == "__main__":
    unittest.main()
