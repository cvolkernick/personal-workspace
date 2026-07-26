"""Unit tests for shipped vault index/search/retrieve against real B2 vault + fixture."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from b2_kb.vault import (  # noqa: E402
    DEFAULT_VAULT_PATH,
    extract_wikilinks,
    index_vault,
    list_notes,
    read_note,
    retrieve,
    search_notes,
)

REAL_VAULT = DEFAULT_VAULT_PATH
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mini_vault"


class TestWikilinks(unittest.TestCase):
    def test_extract_wikilinks_unique_order(self):
        text = "See [[Alpha]] and [[Beta|label]] and [[Alpha]] again [[Gamma#head]]."
        links = extract_wikilinks(text)
        self.assertEqual(links, ["Alpha", "Beta", "Gamma"])


class TestRealVault(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not REAL_VAULT.is_dir():
            raise unittest.SkipTest(f"real vault missing: {REAL_VAULT}")
        cls.notes = index_vault(REAL_VAULT)

    def test_vault_has_seed_notes(self):
        self.assertGreaterEqual(len(self.notes), 5)
        paths = {n.path for n in self.notes}
        self.assertTrue(any("Hub" in p or "00 Home" in p for p in paths))
        self.assertTrue(any("HOWTO" in p for p in paths))

    def test_obsidian_config_exists(self):
        self.assertTrue((REAL_VAULT / ".obsidian").is_dir())
        self.assertTrue((REAL_VAULT / "README.md").is_file())

    def test_wikilink_graph_among_seeds(self):
        # At least two notes mutually linked via [[wikilinks]]
        title_to_paths = {}
        for n in self.notes:
            title_to_paths.setdefault(n.title, []).append(n.path)
            title_to_paths.setdefault(Path(n.path).stem, []).append(n.path)
        linked = 0
        for n in self.notes:
            for target in n.wikilinks:
                if target in title_to_paths or any(
                    Path(p).stem == target for p in title_to_paths.get(target, [])
                ):
                    # resolve: target matches some note title or stem
                    if any(
                        t == target or Path(m.path).stem == target or m.title == target
                        for m in self.notes
                        for t in [m.title]
                    ):
                        linked += 1
                        break
        self.assertGreaterEqual(
            linked,
            2,
            "expected ≥2 notes with resolvable [[wikilinks]] to other seed notes",
        )

    def test_search_known_seed_term(self):
        hits = search_notes("Bitcoin", REAL_VAULT)
        self.assertTrue(hits, "search for Bitcoin should hit seed notes")
        titles_paths = " ".join(h["title"] + " " + h["path"] for h in hits)
        self.assertTrue(
            any(
                k in titles_paths.lower()
                for k in ("strategy", "bet", "finance", "investment", "hub")
            ),
            f"unexpected hits: {hits[:3]}",
        )
        # Expected note among strategy/bets seeds
        self.assertTrue(
            any("Strategy" in h["title"] or "Bets" in h["title"] or "Finance" in h["title"] for h in hits)
            or any("Strategy" in h["path"] or "Finance" in h["path"] for h in hits)
        )

    def test_search_returns_path_and_snippet(self):
        hits = search_notes("Obsidian", REAL_VAULT, limit=5)
        self.assertTrue(hits)
        h = hits[0]
        self.assertIn("path", h)
        self.assertIn("title", h)
        self.assertIn("snippet", h)
        self.assertTrue(h["path"].endswith(".md"))

    def test_read_note_matches_file(self):
        hub = next(n for n in self.notes if "Hub" in n.path or "Hub" in n.title)
        note = read_note(hub.path, REAL_VAULT)
        self.assertIsNotNone(note)
        disk = (REAL_VAULT / hub.path).read_text(encoding="utf-8")
        self.assertEqual(note.body, disk)
        self.assertGreater(len(note.body), 50)
        # Fixture vault must not pollute the real global vault
        paths = {n.path for n in self.notes}
        self.assertFalse(any(p.startswith("tests/") for p in paths))
        self.assertNotIn("server.py", paths)

    def test_retrieve_packs_body_excludes_unrelated(self):
        hits = retrieve("thematic bets Energy Bitcoin AI Autonomy Robotics", REAL_VAULT, top_k=3)
        self.assertTrue(hits)
        paths = [h["path"] for h in hits]
        # Should prefer strategy bets over unrelated pure IoT-only if scores work
        joined = " ".join(paths).lower()
        self.assertTrue(
            "strategy" in joined or "bet" in joined or "hub" in joined or "howto" in joined
        )
        for h in hits:
            self.assertIn("body", h)
            self.assertTrue(h["body"])

    def test_list_notes_api_shape(self):
        items = list_notes(REAL_VAULT)
        self.assertGreaterEqual(len(items), 5)
        self.assertIn("path", items[0])
        self.assertIn("title", items[0])


class TestFixtureVault(unittest.TestCase):
    """Same code path against a tiny fixture vault."""

    def test_fixture_search_and_retrieve(self):
        self.assertTrue(FIXTURE.is_dir())
        notes = index_vault(FIXTURE)
        self.assertGreaterEqual(len(notes), 2)
        hits = search_notes("alpha-unique-token", FIXTURE, notes=notes)
        self.assertEqual(len(hits), 1)
        self.assertIn("Alpha", hits[0]["title"])
        packed = retrieve("alpha-unique-token", FIXTURE, top_k=2, notes=notes)
        self.assertEqual(packed[0]["path"], hits[0]["path"])
        self.assertIn("alpha-unique-token", packed[0]["body"])
        # Unrelated query should not invent alpha
        emptyish = search_notes("zzzz-no-such-term-999", FIXTURE, notes=notes)
        self.assertEqual(emptyish, [])

    def test_index_skips_obsidian_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".obsidian").mkdir()
            (root / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
            (root / "Note.md").write_text("# Note\n\nHello [[Other]]\n", encoding="utf-8")
            (root / "Other.md").write_text("# Other\n\nBack to [[Note]]\n", encoding="utf-8")
            notes = index_vault(root)
            paths = {n.path for n in notes}
            self.assertEqual(paths, {"Note.md", "Other.md"})
            links = extract_wikilinks(notes[0].body if notes[0].path == "Note.md" else notes[1].body)
            self.assertTrue(links)


if __name__ == "__main__":
    unittest.main()


class TestDocumentedLaunchPaths(unittest.TestCase):
    """Seed/HOWTO must document the real on-disk package path (regression: b2-ux-ux)."""

    def test_howto_and_readmes_point_at_b2_ux(self):
        workspace = Path(__file__).resolve().parents[2]  # personal-workspace
        pkg = workspace / "b2-ux"
        self.assertTrue((pkg / "start.sh").is_file(), f"missing entry {pkg}/start.sh")
        docs = [
            workspace / "brain2" / "HOWTO - Using B2.md",
            workspace / "brain2" / "00 Home - B2 Hub.md",
            workspace / "brain2" / "README.md",
            workspace / "brain2" / "map" / "Personal Workspace Map.md",
            pkg / "README.md",
        ]
        for doc in docs:
            text = doc.read_text(encoding="utf-8")
            self.assertNotIn("b2-ux-ux", text, f"bad path in {doc}")
            # At least one launch-related doc mentions the real package
        howto = docs[0].read_text(encoding="utf-8")
        self.assertIn("personal-workspace/b2-ux", howto)
        pkg_readme = (pkg / "README.md").read_text(encoding="utf-8")
        self.assertIn("b2-ux/", pkg_readme)
        self.assertNotRegex(pkg_readme, r"(?m)^b2/$")
