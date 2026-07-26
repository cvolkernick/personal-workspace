"""Unit tests for Ask Grok context builder + offline grounded path (shipped code)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from b2_kb.ask import (  # noqa: E402
    ask_grok,
    build_ask_context,
    offline_grounded_answer,
)
from b2_kb.vault import DEFAULT_VAULT_PATH, index_vault  # noqa: E402

REAL_VAULT = DEFAULT_VAULT_PATH
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mini_vault"


class TestBuildAskContext(unittest.TestCase):
    def test_context_includes_retrieved_note_text(self):
        if not REAL_VAULT.is_dir():
            self.skipTest("no real vault")
        ctx = build_ask_context(
            "What are the high-conviction thematic bets Bitcoin Energy AI?",
            REAL_VAULT,
            top_k=4,
        )
        self.assertGreaterEqual(ctx["hit_count"], 1)
        self.assertTrue(ctx["sources"])
        self.assertTrue(ctx["context_text"])
        # Retrieved body text should mention a known seed concept
        blob = (ctx["context_text"] + " " + " ".join(s["title"] for s in ctx["sources"])).lower()
        self.assertTrue(
            any(k in blob for k in ("bitcoin", "energy", "bet", "ai", "autonomy")),
            f"context missing seed themes: {ctx['sources']}",
        )
        # Paths should be real vault notes
        for s in ctx["sources"]:
            self.assertTrue(s["path"].endswith(".md"))

    def test_context_empty_for_nonsense(self):
        if not REAL_VAULT.is_dir():
            self.skipTest("no real vault")
        ctx = build_ask_context(
            "xyzzy-qwerty-no-vault-match-9f3a2b1c",
            REAL_VAULT,
            top_k=3,
        )
        self.assertEqual(ctx["hit_count"], 0)
        self.assertEqual(ctx["sources"], [])
        self.assertEqual(ctx["context_text"], "")


class TestOfflineGrounded(unittest.TestCase):
    def test_offline_cites_sources_when_hits(self):
        if not REAL_VAULT.is_dir():
            self.skipTest("no real vault")
        result = ask_grok(
            "What are my thematic bets including Bitcoin and Energy?",
            REAL_VAULT,
            force_offline=True,
            top_k=4,
        )
        self.assertEqual(result["mode"], "offline_grounded")
        self.assertTrue(result["answer"])
        self.assertGreater(result["hit_count"], 0)
        self.assertTrue(result["sources"])
        ans = result["answer"]
        # Must reference vault content via title/path
        joined_titles = " ".join(s["title"] for s in result["sources"])
        joined_paths = " ".join(s["path"] for s in result["sources"])
        self.assertTrue(
            any(s["title"] in ans or s["path"] in ans for s in result["sources"]),
            f"answer should cite sources:\n{ans}\n{joined_titles}\n{joined_paths}",
        )

    def test_offline_admits_lack_when_no_hits(self):
        if not REAL_VAULT.is_dir():
            self.skipTest("no real vault")
        result = ask_grok(
            "xyzzy-qwerty-no-vault-match-9f3a2b1c",
            REAL_VAULT,
            force_offline=True,
        )
        self.assertEqual(result["mode"], "offline_grounded")
        self.assertEqual(result["hit_count"], 0)
        self.assertTrue(result["answer"])
        low = result["answer"].lower()
        self.assertTrue(
            "lacks" in low or "does not appear" in low or "no" in low and "relevant" in low,
            result["answer"],
        )

    def test_offline_helper_direct(self):
        ctx = {
            "query": "test",
            "sources": [
                {
                    "path": "domains/X.md",
                    "title": "X",
                    "score": 1,
                    "snippet": "hello world snippet",
                }
            ],
            "context_text": "### X\nhello",
            "context_chars": 10,
            "hit_count": 1,
            "vault_path": "/tmp/v",
        }
        out = offline_grounded_answer(ctx)
        self.assertIn("X", out["answer"])
        self.assertIn("domains/X.md", out["answer"])
        self.assertEqual(out["mode"], "offline_grounded")

    def test_fixture_ask_path(self):
        notes = index_vault(FIXTURE)
        result = ask_grok(
            "alpha-unique-token",
            FIXTURE,
            force_offline=True,
            notes=notes,
        )
        self.assertGreater(result["hit_count"], 0)
        self.assertIn("Alpha", result["answer"] + str(result["sources"]))


if __name__ == "__main__":
    unittest.main()
