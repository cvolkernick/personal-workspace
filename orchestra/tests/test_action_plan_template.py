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
    read_plan_body,
    resolve_domain_plan_path,
    sanitize_domain_id,
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


class SecuritySanitizationTests(unittest.TestCase):
    """Path traversal and unsafe domain ids must be rejected by shipped code."""

    def test_sanitize_rejects_traversal_and_odd_chars(self) -> None:
        self.assertIsNone(sanitize_domain_id("../../../tmp/ap-pwn-test-xyz"))
        self.assertIsNone(sanitize_domain_id("foo/bar"))
        self.assertIsNone(sanitize_domain_id("foo.bar"))
        self.assertIsNone(sanitize_domain_id(""))
        self.assertIsNone(sanitize_domain_id("<script>"))
        self.assertEqual(sanitize_domain_id("finance"), "finance")
        self.assertEqual(sanitize_domain_id("Horizon_Macro"), "horizon_macro")

    def test_ensure_domain_rejects_traversal_no_file_outside_ws(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            outside_probe = Path(td).parent / "ap-pwn-test-xyz.md"
            if outside_probe.is_file():
                outside_probe.unlink()
            evil = "../../../tmp/ap-pwn-test-xyz"
            # also try relative climb within tmp naming
            evil2 = f"../../../{Path(td).name}-escape"
            result = ensure_domain_action_plan(ws, evil)
            self.assertFalse(result.get("ok"))
            self.assertIn("invalid domain_id", result.get("error") or "")
            # No file created under strategy/action-plans with dots
            plans = ws / "strategy" / "action-plans"
            if plans.is_dir():
                for p in plans.rglob("*"):
                    self.assertNotIn("..", str(p))
            # domain_plan_rel must raise on unsafe id
            with self.assertRaises(ValueError):
                domain_plan_rel(evil)
            with self.assertRaises(ValueError):
                resolve_domain_plan_path(ws, evil)
            # ensure second form still fails
            self.assertFalse(ensure_domain_action_plan(ws, evil2).get("ok"))
            # safe ensure still works and stays under action-plans
            ok = ensure_domain_action_plan(ws, "finance")
            self.assertTrue(ok.get("ok"))
            created = Path(ok["path"]).resolve()
            root = (ws / "strategy" / "action-plans").resolve()
            created.relative_to(root)

    def test_read_plan_body_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            r = read_plan_body(ws, layer="domain", domain_id="../etc/passwd")
            self.assertFalse(r.get("ok"))
            self.assertIn("invalid", (r.get("error") or "").lower())


if __name__ == "__main__":
    unittest.main()

