"""Shared kitchen-open clock for meal-plan tests (#830).

``generate_meal_plan`` / dashboard_body read wall-clock ``local_now``.
Overnight hours 0–3 (viewer TZ) close the kitchen after empty_at (#809),
so quality/diversity tests that omit ``now=`` fail on CI between ~00:00
and 04:00 America/New_York. Pin those tests to a weekday 11:00 ET instant
that is always kitchen-open. Tests that assert overnight behavior pass
an explicit ``now=``.
"""

from __future__ import annotations

from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
KITCHEN_OPEN = datetime(2026, 8, 22, 11, 0, tzinfo=ET)


def frozen_local_now(preferred=None, *, now=None):
    from rt_dashboard.timeutil import local_tz

    clock = now if now is not None else KITCHEN_OPEN
    return clock.astimezone(local_tz(preferred))


def patch_planner_clock():
    return mock.patch("rt_dashboard.timeutil.local_now", side_effect=frozen_local_now)


class FrozenPlannerClockMixin:
    """unittest mixin: freeze ``local_now`` to ``KITCHEN_OPEN`` for the test."""

    def setUp(self):
        super().setUp()
        patcher = patch_planner_clock()
        patcher.start()
        self.addCleanup(patcher.stop)
