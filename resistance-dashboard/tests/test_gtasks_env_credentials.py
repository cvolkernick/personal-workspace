"""Vercel can use existing GT env secrets. No tokens invented or committed."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rt_dashboard.gtasks_bridge import load_google_tasks


class GtasksEnvCredentials(unittest.TestCase):
    def test_empty_dir_without_env_is_not_ok(self):
        gt = load_google_tasks()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ,
                {
                    "GOOGLE_TASKS_CONFIG_DIR": tmp,
                    "GOOGLE_TASKS_TOKEN_JSON": "",
                    "GOOGLE_TASKS_REFRESH_TOKEN": "",
                    "GOOGLE_TASKS_CLIENT_ID": "",
                    "GOOGLE_TASKS_CLIENT_SECRET": "",
                },
                clear=False,
            ):
                status = gt.credentials_status()
        self.assertFalse(status["ok"])
        self.assertFalse(status["refresh_token_present"])
        self.assertIsNone(status.get("source"))

    def test_refresh_token_env_is_ok_without_home_config(self):
        gt = load_google_tasks()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ,
                {
                    "GOOGLE_TASKS_CONFIG_DIR": tmp,
                    "GOOGLE_TASKS_REFRESH_TOKEN": "1//rt-test",
                    "GOOGLE_TASKS_CLIENT_ID": "cid.apps.googleusercontent.com",
                    "GOOGLE_TASKS_CLIENT_SECRET": "cs-test",
                },
                clear=False,
            ):
                os.environ.pop("GOOGLE_TASKS_TOKEN_JSON", None)
                status = gt.credentials_status()
                blob = gt._token_blob_from_env()
        self.assertTrue(status["ok"])
        self.assertEqual(status["source"], "env")
        self.assertTrue(status["refresh_token_present"])
        self.assertEqual(blob["refresh_token"], "1//rt-test")
        self.assertNotIn("1//rt-test", json.dumps(status))

    def test_token_json_env_is_ok(self):
        gt = load_google_tasks()
        raw = json.dumps(
            {
                "refresh_token": "1//from-json",
                "client_id": "cid.apps.googleusercontent.com",
                "client_secret": "cs-test",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ,
                {
                    "GOOGLE_TASKS_CONFIG_DIR": tmp,
                    "GOOGLE_TASKS_TOKEN_JSON": raw,
                },
                clear=False,
            ):
                os.environ.pop("GOOGLE_TASKS_REFRESH_TOKEN", None)
                status = gt.credentials_status()
        self.assertTrue(status["ok"])
        self.assertEqual(status["source"], "env")
        self.assertNotIn("1//from-json", json.dumps(status))

    def test_file_token_wins_when_path_exists(self):
        gt = load_google_tasks()
        with tempfile.TemporaryDirectory() as tmp:
            token = Path(tmp) / "token.json"
            token.write_text(
                json.dumps(
                    {
                        "refresh_token": "1//from-file",
                        "client_id": "file-cid",
                        "client_secret": "file-cs",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {
                    "GOOGLE_TASKS_CONFIG_DIR": tmp,
                    "GOOGLE_TASKS_REFRESH_TOKEN": "1//from-env",
                    "GOOGLE_TASKS_CLIENT_ID": "env-cid",
                    "GOOGLE_TASKS_CLIENT_SECRET": "env-cs",
                },
                clear=False,
            ):
                status = gt.credentials_status()
                blob = gt._load_token_blob()
        self.assertTrue(status["ok"])
        self.assertEqual(status["source"], "file")
        self.assertEqual(blob["refresh_token"], "1//from-file")
        dumped = json.dumps(status)
        self.assertNotIn("1//from-file", dumped)
        self.assertNotIn("1//from-env", dumped)


if __name__ == "__main__":
    unittest.main()
