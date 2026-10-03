"""#1033 Trends weekly review elevates action bullets above observations."""

from __future__ import annotations

import unittest
from pathlib import Path

from rt_dashboard.coach import build_coach_brief, compute_weekly_review
from rt_dashboard.models import ExerciseEntry, RecoveryStatus, Session, SetEntry, WeightSample

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
AS_OF = "2026-10-03"


def _session(date: str) -> Session:
    return Session(
        date=date,
        session_type="legs",
        exercises=[ExerciseEntry(name="Leg Press", sets=[SetEntry(100, 3, 8)])],
    )


def _rec(score: float = 80, **inputs) -> RecoveryStatus:
    return RecoveryStatus(label="Ready", score=score, reasons=["ok"], inputs=inputs)


def _adh(protein=80, sleep=80, hydration=90):
    out = {}
    if protein is not None:
        out["protein"] = {
            "pct": protein,
            "tolerance": 0.85,
            "hits": 4,
            "days_logged": 5,
        }
    if sleep is not None:
        out["sleep"] = {"pct": sleep}
    if hydration is not None:
        out["hydration"] = {"pct": hydration, "goal_ml": 3000}
    return out


def _review(**kwargs):
    base = dict(
        sessions=[_session(d) for d in ("2026-09-28", "2026-09-30", "2026-10-01", "2026-10-03")],
        recovery=_rec(),
        weight=[
            WeightSample(date="2026-09-28", weight_lbs=180),
            WeightSample(date="2026-10-03", weight_lbs=181),
        ],
        sleep=[],
        nutrition=[],
        targets={},
        adherence=_adh(),
        as_of=AS_OF,
    )
    base.update(kwargs)
    return compute_weekly_review(**base)


def _row(review, prefix: str) -> dict:
    rows = [row for row in review["items"] if row["text"].startswith(prefix)]
    if len(rows) != 1:
        raise AssertionError(f"{prefix!r} matched {len(rows)}: {[r['text'] for r in rows]}")
    return rows[0]


class WeeklyReviewKinds(unittest.TestCase):
    def test_bullets_stay_strings_in_emission_order(self):
        review = _review()
        bullets = review["bullets"]
        self.assertTrue(all(isinstance(b, str) for b in bullets))
        self.assertTrue(bullets[0].startswith("Training:"))
        self.assertTrue(bullets[-1].startswith("Volume model:"))
        self.assertNotIn("Focus:", bullets[-1])
        self.assertEqual(
            [row["text"] for row in sorted(review["items"], key=lambda r: bullets.index(r["text"]))],
            bullets,
        )

    def test_items_put_actions_first_with_priority(self):
        review = _review()
        kinds = [row["kind"] for row in review["items"]]
        self.assertIn("action", kinds)
        self.assertEqual(kinds, sorted(kinds, key=lambda k: 0 if k == "action" else 1))
        for row in review["items"]:
            self.assertEqual(set(row), {"text", "kind", "priority"})
            self.assertEqual(row["priority"], 0 if row["kind"] == "action" else 1)

    def test_steady_week_only_elevates_the_prescription(self):
        review = _review(adherence=_adh(hydration=0))
        actions = [row["text"] for row in review["items"] if row["kind"] == "action"]
        self.assertEqual(actions, [
            "Focus: keep the streak — execute today’s plan and hit protein remaining."
        ])
        self.assertEqual(_row(review, "Training:")["kind"], "observation")
        self.assertEqual(_row(review, "Sleep:")["kind"], "observation")
        self.assertEqual(_row(review, "Protein:")["kind"], "observation")
        self.assertEqual(_row(review, "Hydration:")["kind"], "observation")
        self.assertEqual(_row(review, "Weight:")["kind"], "observation")
        self.assertEqual(_row(review, "Recovery now:")["kind"], "observation")
        self.assertEqual(_row(review, "Volume model:")["kind"], "observation")

    def test_protein_gap_and_low_sessions_are_actions(self):
        review = _review(
            sessions=[_session("2026-10-03")],
            adherence=_adh(protein=40),
        )
        self.assertEqual(_row(review, "Training:")["kind"], "action")
        self.assertEqual(_row(review, "Protein:")["kind"], "action")
        self.assertEqual(_row(review, "Hydration:")["kind"], "observation")
        actions = [row["text"] for row in review["items"] if row["kind"] == "action"]
        self.assertEqual(actions[0][:9], "Training:")
        self.assertEqual(actions[1][:8], "Protein:")
        self.assertIn("protein is the biggest nutrition gap", actions[2])

    def test_missing_protein_logs_are_an_action(self):
        review = _review(adherence=_adh(protein=None))
        row = _row(review, "Protein:")
        self.assertEqual(row["kind"], "action")
        self.assertIn("no food logs", row["text"])
        self.assertIn("keep the streak", _row(review, "Focus:")["text"])

    def test_low_recovery_prescribes_sleep_without_marking_the_status_line(self):
        review = _review(recovery=_rec(30, label="Needs Rest"))
        self.assertEqual(_row(review, "Recovery now:")["kind"], "observation")
        self.assertIn("prioritize sleep", _row(review, "Focus:")["text"])
        self.assertEqual(_row(review, "Focus:")["kind"], "action")

    def test_under_recovered_rhr_is_an_action_and_a_normal_rhr_is_not(self):
        flagged = _review(recovery=_rec(
            rhr_under_recovered=True,
            rhr_today_bpm=68,
            rhr_baseline_bpm=60,
            rhr_baseline_days=14,
            rhr_delta_bpm=8,
        ))
        self.assertEqual(_row(flagged, "RHR:")["kind"], "action")
        self.assertIn("under-recovered", _row(flagged, "RHR:")["text"])
        steady = _review(recovery=_rec(
            rhr_under_recovered=False,
            rhr_today_bpm=60,
            rhr_baseline_bpm=60,
            rhr_baseline_days=14,
            rhr_delta_bpm=0,
        ))
        self.assertEqual(_row(steady, "RHR:")["kind"], "observation")

    def test_invalid_date_has_no_action_header_source(self):
        review = compute_weekly_review(
            sessions=[],
            recovery=_rec(),
            weight=[],
            sleep=[],
            nutrition=[],
            targets={},
            adherence={},
            as_of="not-a-date",
        )
        self.assertEqual(review["bullets"], ["No valid date for review."])
        self.assertEqual(review["items"][0]["kind"], "observation")
        self.assertFalse(any(row["kind"] == "action" for row in review["items"]))

    def test_brief_still_reads_string_bullets(self):
        review = _review()
        rec = _rec()
        brief = build_coach_brief(
            today={"recommendation": "train", "meal": {}, "date": AS_OF},
            weekly=review,
            recovery=rec,
        )
        self.assertIn(review["bullets"][0], brief["markdown"])
        self.assertIn("Week so far:", brief["markdown"])


class WeeklyReviewMarkup(unittest.TestCase):
    def test_action_block_is_accented_and_omitted_when_empty(self):
        fn = JS.split("function renderWeeklyReview", 1)[1].split("async function phaseBaroAct", 1)[0]
        self.assertIn("weekly-review-focus", fn)
        self.assertIn("weekly-review-actions", fn)
        self.assertLess(fn.find("if (actions.length)"), fn.find("Focus this week"))
        self.assertIn("#weekly-review-bullets > .weekly-review-focus", CSS)
        self.assertIn("border-left: 3px solid var(--accent)", CSS)
        self.assertIn("font-weight: 650", CSS)
        self.assertIn("@media (max-width: 560px)", CSS)


if __name__ == "__main__":
    unittest.main()
