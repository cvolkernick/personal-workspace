"""Weekly lifts-only training-log PDF (#1145).

Previous Monday-Sunday in America/New_York. Real logged sets only.
Quest seeds, cardio, and other session types stay out of the days and
the week totals. Any past week regenerates the same file.
"""

from __future__ import annotations

import io
import os
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from pypdf import PdfReader

from api.auth.session_util import SESSION_COOKIE, make_session
from api.workout._util import dispatch_client_route
from rt_dashboard.models import ExerciseEntry, Session, SetEntry
from rt_dashboard.quest_workout_log import seed_fingerprint
from rt_dashboard.training_log_pdf import (
    collect_week,
    filename_for,
    previous_completed_week,
    resolve_week,
    training_log_from_store,
    training_log_pdf_result,
    write_pdf_response,
)

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")

# Monday 2026-10-19 00:30 ET (EDT, UTC-4).
MONDAY_EARLY_ET = datetime(2026, 10, 19, 4, 30, tzinfo=timezone.utc)
# Friday 2026-10-16 15:00 ET.
FRIDAY_AFTERNOON_ET = datetime(2026, 10, 16, 19, 0, tzinfo=timezone.utc)


def _pdf_text(pdf: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _pages(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


def _seed(name: str, weight: float, sets: int, reps: int) -> ExerciseEntry:
    rows = [SetEntry(weight, sets, reps)]
    return ExerciseEntry(
        name=name,
        sets=rows,
        quest_seeded=True,
        raw=seed_fingerprint(rows),
    )


def _week_sessions() -> list:
    seed_sets = [SetEntry(50, 3, 10)]
    return [
        Session(
            date="2026-10-12",
            session_type="push",
            closed_at="2026-10-12T22:42:00Z",
            notes="easy day",
            exercises=[
                _seed("DB Press", 50, 3, 10),
                ExerciseEntry(
                    name="DB (neutral) Row",
                    sets=[SetEntry(225, 3, 8)],
                    is_pr=True,
                ),
                ExerciseEntry(
                    name="Already trained today",
                    sets=[SetEntry(0, 0, 0)],
                ),
            ],
        ),
        Session(
            date="2026-10-14",
            session_type="pull",
            closed_at="2026-10-14T23:15:00Z",
            exercises=[
                ExerciseEntry(name="Pull-up", sets=[SetEntry(0, 3, 8)]),
                ExerciseEntry(name="Barbell Row", sets=[SetEntry(135, 2, 10)]),
            ],
        ),
        Session(
            date="2026-10-13",
            session_type="cardio",
            notes="zone 2 spin",
            exercises=[
                ExerciseEntry(name="Zone 2 Walk", sets=[SetEntry(0, 1, 30)]),
            ],
        ),
        Session(
            date="2026-10-13",
            session_type="other",
            exercises=[
                ExerciseEntry(name="Hiking", sets=[SetEntry(0, 1, 40)]),
            ],
        ),
        Session(
            date="2026-10-11",
            session_type="legs",
            exercises=[
                ExerciseEntry(name="Squat", sets=[SetEntry(315, 3, 5)]),
            ],
        ),
        Session(
            date="2026-10-18",
            session_type="legs",
            closed_at="2026-10-19T03:30:00Z",
            exercises=[
                ExerciseEntry(name="Romanian Deadlift", sets=[SetEntry(185, 3, 8)]),
            ],
        ),
        {
            "date": "2026-10-12",
            "session_type": "legs",
            "notes": "from a dict row",
            "created_at": "2026-10-12T21:00:00Z",
            "exercises": [
                {
                    "name": "Leg Press",
                    "quest_seeded": False,
                    "sets": [{"weight_lbs": 360, "sets": 2, "reps": 12}],
                }
            ],
        },
    ]


class WeekWindow(unittest.TestCase):
    def test_monday_midnight_et_is_the_week_that_just_ended(self):
        window, err = resolve_week("", now=MONDAY_EARLY_ET)
        self.assertIsNone(err)
        self.assertEqual(window[0].isoformat(), "2026-10-12")
        self.assertEqual(window[1].isoformat(), "2026-10-18")
        self.assertEqual(previous_completed_week(date(2026, 10, 19)), window)

    def test_friday_default_stays_on_the_previous_completed_week(self):
        window, err = resolve_week("", now=FRIDAY_AFTERNOON_ET)
        self.assertIsNone(err)
        self.assertEqual(window[0].isoformat(), "2026-10-05")
        self.assertEqual(window[1].isoformat(), "2026-10-11")

    def test_named_day_snaps_to_monday_and_future_week_is_rejected(self):
        window, err = resolve_week("week=2026-10-14", now=FRIDAY_AFTERNOON_ET)
        self.assertIsNone(err)
        self.assertEqual(window[0].isoformat(), "2026-10-12")
        self.assertEqual(window[1].isoformat(), "2026-10-18")
        _window, bad = resolve_week("week=2026-10-26", now=FRIDAY_AFTERNOON_ET)
        self.assertEqual(bad["error"], "bad_week")
        _window, bad = resolve_week("week=October", now=FRIDAY_AFTERNOON_ET)
        self.assertEqual(bad["error"], "bad_week")


class PdfContents(unittest.TestCase):
    def test_real_lifts_only_with_totals_notes_and_clocks(self):
        status, body = training_log_pdf_result(
            _week_sessions(), "week=2026-10-12", now=MONDAY_EARLY_ET
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["filename"], "fitdash-training-log-2026-10-12.pdf")
        self.assertTrue(body["pdf"].startswith(b"%PDF"))
        text = _pdf_text(body["pdf"])
        self.assertIn("DB (neutral) Row: 3 x 8 @ 225 lb (PR)", text)
        self.assertIn("Barbell Row: 2 x 10 @ 135 lb", text)
        self.assertIn("Pull-up: 3 x 8 @ 0 lb", text)
        self.assertIn("Romanian Deadlift: 3 x 8 @ 185 lb", text)
        self.assertIn("Leg Press: 2 x 12 @ 360 lb", text)
        self.assertIn("Notes: easy day", text)
        self.assertIn("Notes: from a dict row", text)
        self.assertIn("Mon 6:42 PM ET", text)
        self.assertIn("Wed 7:15 PM ET", text)
        self.assertIn("Sun 11:30 PM ET", text)
        self.assertIn("Sessions: 4", text)
        # 225*3*8 + 135*2*10 + 0*3*8 + 185*3*8 + 360*2*12 = 5400+2700+0+4440+8640
        self.assertIn("Volume: 21,180 lb", text)
        self.assertIn("Time is the save clock", text)
        self.assertNotIn("DB Press", text)
        self.assertNotIn("Already trained today", text)
        self.assertNotIn("Zone 2 Walk", text)
        self.assertNotIn("zone 2 spin", text)
        self.assertNotIn("Hiking", text)
        self.assertNotIn("Squat", text)
        self.assertNotIn(seed_fingerprint([SetEntry(50, 3, 10)]), text)

    def test_cardio_only_week_is_an_empty_lift_log(self):
        sessions = [
            Session(
                date="2026-10-13",
                session_type="cardio",
                exercises=[
                    ExerciseEntry(name="Zone 2 Walk", sets=[SetEntry(0, 1, 45)]),
                ],
            )
        ]
        status, body = training_log_pdf_result(
            sessions, "week=2026-10-13", now=MONDAY_EARLY_ET
        )
        self.assertEqual(status, 200)
        text = _pdf_text(body["pdf"])
        self.assertIn("No lifts logged.", text)
        self.assertIn("Sessions: 0", text)
        self.assertIn("Volume: 0 lb", text)
        self.assertIn("Time: -", text)
        self.assertNotIn("Zone 2 Walk", text)

    def test_quest_seed_only_session_does_not_count(self):
        sessions = [
            Session(
                date="2026-10-12",
                session_type="push",
                exercises=[_seed("DB Press", 50, 3, 10)],
            )
        ]
        self.assertEqual(collect_week(sessions, date(2026, 10, 12), date(2026, 10, 18)), [])

    def test_default_monday_file_uses_the_week_that_just_ended(self):
        status, body = training_log_pdf_result(
            _week_sessions(), "", now=MONDAY_EARLY_ET
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["week_start"], "2026-10-12")
        self.assertEqual(body["filename"], filename_for(date(2026, 10, 12)))
        text = _pdf_text(body["pdf"])
        self.assertIn("Romanian Deadlift", text)
        self.assertNotIn("Squat", text)

    def test_friday_default_does_not_include_the_open_week(self):
        status, body = training_log_pdf_result(
            _week_sessions(), "", now=FRIDAY_AFTERNOON_ET
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["week_start"], "2026-10-05")
        text = _pdf_text(body["pdf"])
        self.assertIn("Squat", text)
        self.assertNotIn("Romanian Deadlift", text)
        self.assertNotIn("No lifts logged.", text)

    def test_bad_week_is_not_an_empty_pdf(self):
        status, body = training_log_pdf_result([], "week=nope", now=MONDAY_EARLY_ET)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "bad_week")
        self.assertNotIn("pdf", body)

    def test_store_error_with_no_rows_is_not_an_empty_log(self):
        status, body = training_log_from_store(
            [], "week=2026-10-12", store_error="turso down", now=MONDAY_EARLY_ET
        )
        self.assertEqual(status, 503)
        self.assertEqual(body["error"], "workout_store_unavailable")
        self.assertNotIn("pdf", body)

    def test_long_week_spans_pages(self):
        exercises = [
            ExerciseEntry(name=f"Lift {i}", sets=[SetEntry(100, 3, 8)])
            for i in range(80)
        ]
        sessions = [
            Session(date="2026-10-12", session_type="push", exercises=exercises)
        ]
        status, body = training_log_pdf_result(
            sessions, "week=2026-10-12", now=MONDAY_EARLY_ET
        )
        self.assertEqual(status, 200)
        self.assertGreaterEqual(_pages(body["pdf"]), 2)
        text = _pdf_text(body["pdf"])
        self.assertIn("Lift 0", text)
        self.assertIn("Lift 79", text)
        self.assertIn("continued", text)

    def test_attachment_filename(self):
        class Handler:
            def __init__(self):
                self.status = None
                self.headers = []
                self.wfile = io.BytesIO()

            def send_response(self, code):
                self.status = code

            def send_header(self, key, value):
                self.headers.append((key, value))

            def end_headers(self):
                return None

        handler = Handler()
        write_pdf_response(handler, b"%PDF-1.4\n", 'fitdash-training-log-2026-10-12.pdf')
        self.assertEqual(handler.status, 200)
        header = dict(handler.headers)
        self.assertEqual(header["Content-Type"], "application/pdf")
        self.assertEqual(
            header["Content-Disposition"],
            'attachment; filename="fitdash-training-log-2026-10-12.pdf"',
        )
        self.assertEqual(handler.wfile.getvalue(), b"%PDF-1.4\n")


class RouteAndPhoneDownload(unittest.TestCase):
    def test_history_card_links_the_pdf(self):
        self.assertIn('id="training-log-pdf"', HTML)
        self.assertIn('href="/api/training-log.pdf"', HTML)
        self.assertIn('id="training-log-week"', HTML)
        self.assertIn("/api/training-log.pdf?week=", HTML)
        self.assertIn("fitdash-training-log-YYYY-MM-DD.pdf", HTML)
        self.assertIn('class="btn primary btn-touch"', HTML)
        self.assertIn('if parsed.path == "/api/training-log.pdf":', SERVER)
        self.assertIn("training_log_from_store", SERVER)
        self.assertIn("method_not_allowed", SERVER)

    def test_post_is_rejected(self):
        status, body = dispatch_client_route({}, "_r=training_log_pdf", "POST")
        self.assertEqual(status, 405)
        self.assertEqual(body["error"], "method_not_allowed")

    def test_signed_in_route_reads_the_dashboard_store(self):
        session = Session(
            date="2026-10-12",
            session_type="push",
            notes="route notes",
            exercises=[ExerciseEntry(name="Bench", sets=[SetEntry(225, 3, 5)])],
        )
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            token = make_session(
                {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
            )
            headers = {"Cookie": f"{SESSION_COOKIE}={token}"}
            with mock.patch(
                "api.dashboard._load_sessions",
                return_value=([session], [], "turso"),
            ) as load:
                status, body = dispatch_client_route(
                    headers,
                    "week=2026-10-12",
                    "GET",
                    path="/api/training-log.pdf",
                )
        self.assertEqual(status, 200)
        load.assert_called_once()
        self.assertEqual(body["filename"], "fitdash-training-log-2026-10-12.pdf")
        self.assertIn("Bench: 3 x 5 @ 225 lb", _pdf_text(body["pdf"]))
        self.assertIn("Notes: route notes", _pdf_text(body["pdf"]))

    def test_signed_in_store_error_stays_json(self):
        env = {"GOOGLE_CLIENT_SECRET": "test-secret"}
        with mock.patch.dict(os.environ, env, clear=True):
            token = make_session(
                {"id": "sub-1", "email": "c@example.com", "display_name": "Chris"}
            )
            headers = {"Cookie": f"{SESSION_COOKIE}={token}"}
            with mock.patch(
                "api.dashboard._load_sessions",
                return_value=([], ["turso down"], "turso"),
            ):
                status, body = dispatch_client_route(
                    headers, "_r=training_log_pdf", "GET"
                )
        self.assertEqual(status, 503)
        self.assertEqual(body["error"], "workout_store_unavailable")
        self.assertNotIn("pdf", body)


if __name__ == "__main__":
    unittest.main()
