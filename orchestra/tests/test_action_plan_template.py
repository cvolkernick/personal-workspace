"""Unit tests for nested Action Plan template (pure ensure + skeleton).

Drives shipped functions in action_plan_template — no re-implementation.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parents[1]
ROOT = ORCH.parent
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))

from action_plan_template import (  # noqa: E402
    REQUIRED_MARKERS,
    TEMPLATE_REL,
    MACRO_PLAN_REL,
    collect_action_plans,
    domain_plan_rel,
    ensure_domain_action_plan,
    ensure_macro_action_plan,
    ensure_template_file,
    skeleton_has_required_markers,
    skeleton_text,
)
from payload import build_orchestra_payload  # noqa: E402


class SkeletonTests(unittest.TestCase):
    def test_builtin_skeleton_has_required_markers(self) -> None:
        text = skeleton_text(None, title="Test Domain", generated="2026-01-01T00:00:00+00:00")
        missing = skeleton_has_required_markers(text)
        self.assertEqual(missing, [], f"missing markers: {missing}")
        for m in REQUIRED_MARKERS:
            self.assertIn(m, text)

    def test_on_disk_template_has_required_markers(self) -> None:
        path = ROOT / TEMPLATE_REL
        self.assertTrue(path.is_file(), f"committed template missing: {path}")
        text = path.read_text(encoding="utf-8")
        # Placeholders before substitution still contain structure markers
        missing = skeleton_has_required_markers(
            text.replace("{{TITLE}}", "X").replace("{{GENERATED}}", "Y")
        )
        self.assertEqual(missing, [])


class EnsureTests(unittest.TestCase):
    def test_ensure_macro_creates_then_leaves_intact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            first = ensure_macro_action_plan(ws)
            self.assertTrue(first["ok"])
            self.assertTrue(first["created"])
            self.assertTrue(first["exists"])
            path = Path(first["path"])
            self.assertTrue(path.is_file())
            self.assertEqual(first["rel_path"], MACRO_PLAN_REL)
            body1 = path.read_text(encoding="utf-8")
            self.assertEqual(skeleton_has_required_markers(body1), [])
            self.assertIn("Orchestrator", body1)

            # Mutate file; second ensure must not overwrite
            path.write_text(body1 + "\n\n<!-- sentinel unique -->\n", encoding="utf-8")
            second = ensure_macro_action_plan(ws)
            self.assertTrue(second["ok"])
            self.assertFalse(second["created"])
            body2 = path.read_text(encoding="utf-8")
            self.assertIn("sentinel unique", body2)

    def test_ensure_domain_finance_creates_from_skeleton(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            # seed template from builtin
            ensure_template_file(ws)
            first = ensure_domain_action_plan(ws, "finance")
            self.assertTrue(first["ok"])
            self.assertTrue(first["created"])
            self.assertEqual(first["rel_path"], domain_plan_rel("finance"))
            path = Path(first["path"])
            body = path.read_text(encoding="utf-8")
            self.assertEqual(skeleton_has_required_markers(body), [])
            self.assertIn("Finance", body)

            path.write_text(body + "\nKEEP\n", encoding="utf-8")
            second = ensure_domain_action_plan(ws, "finance")
            self.assertFalse(second["created"])
            self.assertIn("KEEP", path.read_text(encoding="utf-8"))

    def test_collect_action_plans_status_without_creating_domains(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            ensure_macro_action_plan(ws)
            snap = collect_action_plans(ws)
            self.assertTrue(snap["macro"]["exists"])
            self.assertEqual(snap["section_title"], "Today's Focus → Domain Action Plan")
            self.assertEqual(snap["run_control_label"], "Run Domain Template")
            # domains listed but not auto-created
            self.assertTrue(len(snap["domains"]) >= 1)
            fin = next(d for d in snap["domains"] if d["id"] == "finance")
            self.assertFalse(fin["exists"])
            self.assertEqual(fin["rel_path"], "strategy/action-plans/finance.md")


class PayloadIntegrationTests(unittest.TestCase):
    def test_payload_exposes_action_plans_and_today_focus(self) -> None:
        # Real workspace payload path — drives shipped build_orchestra_payload
        payload = build_orchestra_payload(ROOT, probe_ports=False)
        self.assertTrue(payload.get("ok"))
        tf = payload.get("today_focus") or {}
        self.assertIn("items", tf)
        self.assertTrue(
            (tf.get("today_path") or "").endswith("today.md")
            or tf.get("today_path") == "strategy/today.md"
        )
        plans = payload.get("action_plans")
        self.assertIsInstance(plans, dict)
        self.assertIn("macro", plans)
        self.assertIn("domains", plans)
        self.assertEqual(plans.get("section_title"), "Today's Focus → Domain Action Plan")
        self.assertTrue((plans.get("macro") or {}).get("rel_path") == MACRO_PLAN_REL)


if __name__ == "__main__":
    unittest.main()
