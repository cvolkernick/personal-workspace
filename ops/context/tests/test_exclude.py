#!/usr/bin/env python3
"""Exclude-list tests for #580 E1 — secrets never enter the backup."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EX_PATH = ROOT / "ops" / "context" / "exclude.py"


def _load():
    spec = importlib.util.spec_from_file_location("ctx_exclude", EX_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ctx_exclude"] = mod
    spec.loader.exec_module(mod)
    return mod


E = _load()


class TestExclude(unittest.TestCase):
    def test_refuses_env_ssh_wallet_vault_credentials(self) -> None:
        bad = [
            ".env",
            "foo.env",
            ".ssh/id_ed25519",
            "id_rsa",
            "id_ed25519",
            "secrets.json",
            "mcp_credentials.json",
            "auth.json",
            "credentials/google.json",
            "wallet.dat",
            "wallets/hot",
            "keystore/utc",
            "secure-vault/item",
            "vault/item",
            "seed.txt",
            "nsec.txt",
            "cron/jobs.json",
            "cron/jobs.json.bak",
            "fitbit_token.json",
            ".x_credentials.json",
            "cdp_api_key.json",
        ]
        for p in bad:
            self.assertTrue(E.is_refused_name(p), msg=p)

    def test_allows_portable_markdown_and_skills(self) -> None:
        good = [
            "MEMORY.md",
            "USER.md",
            "AGENTS.md",
            "TOOLS.md",
            "memory/2026-09-10.md",
            "skills/github/SKILL.md",
            "skills/x-post-reader/SKILL.md",
            "goals/daily.md",
            "CONTEXT.md",
        ]
        for p in good:
            self.assertFalse(E.is_refused_name(p), msg=p)

    def test_content_sniff_private_key_and_nsec(self) -> None:
        self.assertIsNotNone(E.sniff_secret_bytes(b"-----BEGIN OPENSSH PRIVATE KEY-----\nAAA\n"))
        self.assertIsNotNone(E.sniff_secret_bytes(b"note nsec1qqqqqqqqqqqqqqqqqqqqqqqq here"))
        self.assertIsNone(E.sniff_secret_bytes(b"# USER.md\nName: Chris\n"))

    def test_walk_skips_secrets_keeps_memory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ex-") as td:
            root = Path(td)
            (root / "MEMORY.md").write_text("ok\n", encoding="utf-8")
            (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
            ssh = root / ".ssh"
            ssh.mkdir()
            (ssh / "id_ed25519").write_text("BEGIN OPENSSH PRIVATE KEY\n", encoding="utf-8")
            (root / "skills").mkdir()
            (root / "skills" / "SKILL.md").write_text("portable\n", encoding="utf-8")
            allowed = []
            refused = []
            for path, c in E.walk_allowed(root):
                (allowed if c.allowed else refused).append(path.name)
            self.assertIn("MEMORY.md", allowed)
            self.assertIn("SKILL.md", allowed)
            self.assertNotIn("id_ed25519", allowed)
            self.assertNotIn(".env", allowed)


if __name__ == "__main__":
    unittest.main()
