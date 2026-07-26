"""Unit tests for Ikigai load/save and payload inclusion (real loaders)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parents[1]
ROOT = ORCH.parent
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ikigai import (  # noqa: E402
    ikigai_for_context,
    load_ikigai,
    normalize_ikigai,
    save_ikigai,
)
from payload import build_orchestra_payload  # noqa: E402
from conductor import build_orchestration_context  # noqa: E402


def _seed_workspace(ws: Path) -> None:
    ik = ws / "strategy" / "ikigai"
    ik.mkdir(parents=True)
    pillars = {
        "version": 1,
        "center": {
            "statement": "Test center: leverage and optionality.",
            "themes": ["AI leverage", "Wealth"],
        },
        "pillars": {
            "love": {"items": ["Building systems"], "notes": ""},
            "good_at": {"items": ["Orchestration"], "notes": ""},
            "world_needs": {"items": ["Autonomy tools"], "notes": ""},
            "paid_for": {"items": ["Thematic bets"], "notes": ""},
        },
        "intersections": {
            "passion": {"summary": "Systems joy", "items": []},
            "mission": {"summary": "Autonomy", "items": []},
            "profession": {"summary": "Craft", "items": []},
            "vocation": {"summary": "Wealth path", "items": []},
        },
        "out_of_bounds": ["Dashboard thrash"],
        "linked_bets": ["AI", "Bitcoin"],
        "linked_life_domains": ["Fitness"],
        "review_cadence": "quarterly",
    }
    (ik / "pillars.json").write_text(json.dumps(pillars, indent=2) + "\n", encoding="utf-8")
    (ik / "ikigai.md").write_text("# Ikigai\n\nTest narrative.\n", encoding="utf-8")
    (ws / "strategy" / "bets.md").write_text("# Bets\n- **AI**\n", encoding="utf-8")
    (ws / "strategy" / "today.md").write_text("# Today\n- [ ] **Do X**\n", encoding="utf-8")
    (ws / "initiatives").mkdir(exist_ok=True)
    (ws / "ops" / "backlog").mkdir(parents=True, exist_ok=True)
    (ws / "ops" / "backlog" / "items.json").write_text(
        json.dumps({"items": []}), encoding="utf-8"
    )


class IkigaiLoaderTests(unittest.TestCase):
    def test_load_multi_pillar_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            _seed_workspace(ws)
            data = load_ikigai(ws)
            self.assertTrue(data["ok"])
            self.assertTrue(data["exists"])
            self.assertIn("leverage", data["center"]["statement"].lower())
            self.assertEqual(data["pillars"]["love"]["items"], ["Building systems"])
            self.assertEqual(data["pillars"]["good_at"]["items"], ["Orchestration"])
            self.assertEqual(data["pillars"]["world_needs"]["items"], ["Autonomy tools"])
            self.assertEqual(data["pillars"]["paid_for"]["items"], ["Thematic bets"])
            self.assertIn("Dashboard thrash", data["out_of_bounds"])
            self.assertIn("AI", data["linked_bets"])

    def test_save_merge_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            _seed_workspace(ws)
            saved = save_ikigai(
                {
                    "center": {"statement": "Updated center statement."},
                    "pillars": {"love": {"items": "A\nB"}},
                    "out_of_bounds": ["No thrash", "No theater"],
                },
                ws,
            )
            self.assertTrue(saved["ok"])
            self.assertEqual(saved["center"]["statement"], "Updated center statement.")
            self.assertEqual(saved["pillars"]["love"]["items"], ["A", "B"])
            # other pillars preserved
            self.assertEqual(saved["pillars"]["good_at"]["items"], ["Orchestration"])
            self.assertEqual(saved["out_of_bounds"], ["No thrash", "No theater"])
            self.assertIsNotNone(saved["updated_at"])
            # disk file matches
            raw = json.loads((ws / "strategy" / "ikigai" / "pillars.json").read_text())
            self.assertEqual(raw["center"]["statement"], "Updated center statement.")

    def test_payload_includes_ikigai_from_real_loader(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            _seed_workspace(ws)
            payload = build_orchestra_payload(ws, probe_ports=False)
            self.assertTrue(payload["ok"])
            ik = payload.get("ikigai") or {}
            self.assertTrue(ik.get("ok") or ik.get("center", {}).get("statement"))
            self.assertIn("leverage", (ik.get("center") or {}).get("statement", "").lower())
            self.assertTrue((ik.get("pillars") or {}).get("love", {}).get("items"))
            self.assertEqual(payload.get("identity"), ik)
            ctx = build_orchestration_context(payload)
            self.assertIn("ikigai", ctx)
            self.assertIn(
                "leverage",
                (ctx["ikigai"].get("center") or {}).get("statement", "").lower(),
            )

    def test_normalize_handles_string_lists(self) -> None:
        n = normalize_ikigai(
            {
                "center": {"statement": "S", "themes": "t1\nt2"},
                "pillars": {"love": {"items": "x\ny"}},
                "out_of_bounds": "a\nb",
            }
        )
        self.assertEqual(n["center"]["themes"], ["t1", "t2"])
        self.assertEqual(n["pillars"]["love"]["items"], ["x", "y"])
        self.assertEqual(n["out_of_bounds"], ["a", "b"])

    def test_real_workspace_pillars_if_present(self) -> None:
        """Drive loader against monorepo strategy/ikigai when checked out."""
        data = load_ikigai(ROOT)
        if not (ROOT / "strategy" / "ikigai" / "pillars.json").is_file():
            self.skipTest("no strategy/ikigai in workspace")
        self.assertTrue(data["exists"])
        self.assertTrue(data["center"]["statement"] or data["pillars"]["love"]["items"])
        slim = ikigai_for_context(data)
        self.assertTrue(slim["center"]["statement"] or slim["pillars"]["love"]["items"])


if __name__ == "__main__":
    unittest.main()
