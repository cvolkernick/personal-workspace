"""Quest check-off and manual Log share one training date (2026-11-25).

Quest posted civil ``local_today`` as ``date``, and the server kept it as a
#771 picker choice. At 03:30 ET that wrote 11/25 while the Log form wrote
11/24 (4 AM cutoff, #1304), so one Legs night split into two rows. Quest now
uses ``default_log_date`` (client ``training_day`` or the 4 AM rule). An
explicit ``#log-date`` / history Save date stays authoritative (#771).
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from rt_dashboard.models import Session
from rt_dashboard.quest_workout_log import (
    attach_lift_quest_log,
    quest_log_context,
    quest_log_day,
)
from rt_dashboard.training_day import default_log_date, resolve_log_date
from rt_dashboard.workout_log import (
    finalize_logged_session,
    find_same_day_session,
    merge_log_with_history,
    parse_log_body,
    plan_session_date_move,
)

ET = ZoneInfo("America/New_York")
TZ = "America/New_York"
ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def _et(h, m=0, day=25):
    return datetime(2026, 11, day, h, m, tzinfo=ET)


class Store:
    """In-memory sessions table keyed by (date, session_type)."""

    def __init__(self, sessions=()):
        self.rows = list(sessions)

    def load(self, _uid=""):
        return list(self.rows)

    def persist(self, _uid, session):
        self.rows = [
            s
            for s in self.rows
            if not (
                str(s.date)[:10] == str(session.date)[:10]
                and s.session_type == session.session_type
            )
        ]
        self.rows.append(session)
        return {"ok": True, "path": "memory"}


def _quest_payload(**extra):
    body = {
        "group": "training",
        "title": "Back Extension Machine (160 lb 2×14)",
        "slug": "ex-back-extension-machine",
        "session_type": "legs",
        "next_session_type": "legs",
        "tz": TZ,
    }
    body.update(extra)
    return body


def quest_check_off(store, payload, *, now):
    day, sessions, tw = quest_log_context(
        "u1", payload, load_sessions=store.load, now=now
    )
    tw = dict(tw, session_type="legs")
    return attach_lift_quest_log(
        {"ok": True},
        payload,
        True,
        user_id="u1",
        sessions=sessions,
        today=day,
        today_workout=tw,
        persist=store.persist,
    )["workout_log"]


def manual_log(store, *, date, now, name="Hack Squat", weight=105, sets=1, reps=6):
    session = parse_log_body(
        {
            "session_type": "legs",
            "date": date,
            "tz": TZ,
            "exercises": [
                {"name": name, "weight_lbs": weight, "sets": sets, "reps": reps}
            ],
        },
        now=now,
    )
    history = store.load()
    prior = find_same_day_session(history, session.date, session.session_type)
    session = merge_log_with_history(session, history)
    session = finalize_logged_session(session, existing=prior, week_sessions=history)
    store.persist("u1", session)
    return session


def _names(session):
    return [e.name for e in session.exercises]


class DefaultLogDate(unittest.TestCase):
    def test_pre_cutoff_is_prior_training_day(self):
        self.assertEqual(default_log_date(None, now=_et(3, 30), tz_name=TZ), "2026-11-24")
        self.assertEqual(default_log_date("", now=_et(0, 1), tz_name=TZ), "2026-11-24")
        self.assertEqual(default_log_date(None, now=_et(3, 59), tz_name=TZ), "2026-11-24")

    def test_at_and_after_cutoff_is_that_day(self):
        self.assertEqual(default_log_date(None, now=_et(4, 0), tz_name=TZ), "2026-11-25")
        self.assertEqual(default_log_date(None, now=_et(4, 30), tz_name=TZ), "2026-11-25")

    def test_utc_stamp_does_not_slice_civil_utc_date(self):
        now = datetime(2026, 11, 25, 8, 30, tzinfo=timezone.utc)  # 03:30 ET
        self.assertEqual(default_log_date(None, now=now, tz_name=TZ), "2026-11-24")

    def test_client_training_day_wins_when_valid(self):
        self.assertEqual(
            default_log_date("2026-11-24", now=_et(3, 30), tz_name=TZ), "2026-11-24"
        )
        self.assertEqual(
            default_log_date("not-a-date", now=_et(3, 30), tz_name=TZ), "2026-11-24"
        )
        self.assertEqual(
            default_log_date("2026-13-40", now=_et(4, 30), tz_name=TZ), "2026-11-25"
        )


class QuestLogContext(unittest.TestCase):
    def test_auto_civil_today_is_not_honored_before_cutoff(self):
        payload = _quest_payload(date="2026-11-25", training_day="2026-11-24")
        day, _s, _tw = quest_log_context(
            "u1", payload, load_sessions=lambda _u: [], now=_et(3, 30)
        )
        self.assertEqual(day, "2026-11-24")

    def test_civil_date_alone_is_ignored_before_cutoff(self):
        # Old cached client: date only, no training_day.
        payload = _quest_payload(date="2026-11-25")
        day, _s, _tw = quest_log_context(
            "u1", payload, load_sessions=lambda _u: [], now=_et(3, 30)
        )
        self.assertEqual(day, "2026-11-24")
        self.assertEqual(quest_log_day(payload, now=_et(4, 0)), "2026-11-25")


class QuestCheckOffDates(unittest.TestCase):
    def test_check_off_0000_to_0359_lands_on_prior_training_day(self):
        for h, m in ((0, 0), (1, 15), (3, 30), (3, 59)):
            store = Store()
            log = quest_check_off(
                store, _quest_payload(quest_day="2026-11-25"), now=_et(h, m)
            )
            self.assertTrue(log["wrote"], log)
            self.assertEqual(log["date"], "2026-11-24", (h, m))
            self.assertEqual([s.date for s in store.rows], ["2026-11-24"])

    def test_check_off_at_or_after_0400_lands_on_that_day(self):
        for h, m in ((4, 0), (4, 30), (13, 0)):
            store = Store()
            log = quest_check_off(store, _quest_payload(), now=_et(h, m))
            self.assertEqual(log["date"], "2026-11-25", (h, m))
            self.assertEqual([s.date for s in store.rows], ["2026-11-25"])


class SameSession(unittest.TestCase):
    def test_quest_then_manual_log_one_session_0330(self):
        store = Store()
        quest_check_off(
            store,
            _quest_payload(date="2026-11-25", training_day="2026-11-24"),
            now=_et(3, 30),
        )
        # Log form prefills #log-date with the same training day.
        manual_log(store, date="2026-11-24", now=_et(3, 31))
        self.assertEqual(len(store.rows), 1)
        self.assertEqual(store.rows[0].date, "2026-11-24")
        self.assertEqual(
            sorted(_names(store.rows[0])), ["Back Extension Machine", "Hack Squat"]
        )

    def test_manual_log_then_quest_one_session_0330(self):
        store = Store()
        manual_log(store, date="", now=_et(3, 30), name="Leg Press", weight=208, reps=10)
        self.assertEqual(store.rows[0].date, "2026-11-24")
        quest_check_off(
            store,
            _quest_payload(date="2026-11-25", training_day="2026-11-24"),
            now=_et(3, 31),
        )
        self.assertEqual(len(store.rows), 1)
        self.assertEqual(
            sorted(_names(store.rows[0])), ["Back Extension Machine", "Leg Press"]
        )

    def test_after_cutoff_both_land_on_civil_today(self):
        store = Store()
        quest_check_off(store, _quest_payload(training_day="2026-11-25"), now=_et(4, 0))
        manual_log(store, date="2026-11-25", now=_et(4, 30))
        self.assertEqual(len(store.rows), 1)
        self.assertEqual(store.rows[0].date, "2026-11-25")


class ExplicitPick771(unittest.TestCase):
    def test_explicit_log_date_is_honored(self):
        self.assertEqual(
            resolve_log_date("2026-11-25", now=_et(3, 30), tz_name=TZ), "2026-11-25"
        )
        store = Store()
        quest_check_off(
            store,
            _quest_payload(date="2026-11-25", training_day="2026-11-24"),
            now=_et(3, 30),
        )
        # User changed #log-date to 11/25: stays there, does not join 11/24.
        manual_log(store, date="2026-11-25", now=_et(3, 35))
        self.assertEqual(sorted(s.date for s in store.rows), ["2026-11-24", "2026-11-25"])
        picked = find_same_day_session(store.rows, "2026-11-25", "legs")
        self.assertEqual(_names(picked), ["Hack Squat"])

    def test_explicit_past_pick_after_cutoff_is_honored(self):
        session = parse_log_body(
            {
                "session_type": "legs",
                "date": "2026-11-20",
                "tz": TZ,
                "exercises": [{"name": "Leg Press", "weight_lbs": 208, "sets": 1, "reps": 10}],
            },
            now=_et(13, 0),
        )
        self.assertEqual(session.date, "2026-11-20")

    def test_history_save_date_still_moves_by_picked_civil_day(self):
        store = Store()
        quest_check_off(store, _quest_payload(training_day="2026-11-24"), now=_et(3, 30))
        moved = plan_session_date_move(
            store.rows,
            session_type="legs",
            from_date="2026-11-24",
            to_date="2026-11-25",
            tz_name=TZ,
            now=_et(3, 40),
        )
        self.assertEqual(moved.date, "2026-11-25")
        self.assertEqual(_names(moved), ["Back Extension Machine"])


class ClientWiring(unittest.TestCase):
    def _handler(self):
        return JS.split("async function onDailyQuestClick", 1)[1].split(
            "function bindDailyQuestClicks", 1
        )[0]

    def test_quest_does_not_send_civil_today_as_log_date(self):
        handler = self._handler()
        self.assertNotIn("date: localToday", handler)
        self.assertIn("quest_day: localToday", handler)
        self.assertIn("training_day: questLogDayISO(state)", handler)

    def test_quest_log_day_helper_uses_training_day_then_log_day(self):
        fn = JS.split("function questLogDayISO", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("meta.training_day", fn)
        self.assertIn("logDayISO()", fn)
        self.assertNotIn("local_today", fn)

    def test_optimistic_paint_keys_off_training_day(self):
        apply = JS.split("function applyWorkoutLogToLocalState", 1)[1].split(
            "\n  }\n", 1
        )[0]
        self.assertIn("log.date", apply)
        self.assertIn("questLogDayISO(state)", apply)
        self.assertNotIn("local_today", apply)
        render = JS.split("function renderTodayLoggedLifts", 1)[1].split(
            "function applyWorkoutLogToLocalState", 1
        )[0]
        self.assertIn("questLogDayISO(src)", render)
        self.assertNotIn("local_today", render)

    def test_server_reads_quest_day_for_board_leaf(self):
        util = (ROOT / "api" / "workout" / "_util.py").read_text(encoding="utf-8")
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn('payload.get("quest_day") or payload.get("date")', util)
        self.assertIn('body.get("quest_day") or body.get("date")', server)


if __name__ == "__main__":
    unittest.main()
