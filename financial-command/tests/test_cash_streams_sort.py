"""Cash Streams Period averages: rows run largest $/day first, ties stable."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "cash-streams.html"
SIM = Path(__file__).with_name("cash_streams_sort_sim.js")


def _points(days: int, **bands: float) -> list[dict]:
    start = date(2026, 7, 27)
    return [
        {"date": (start + timedelta(days=i)).isoformat(), "inflow": 0, "outflow": 0, **bands}
        for i in range(days)
    ]


def _render(rolling_bands: dict, groups: list[tuple[str, float]], days: int = 90) -> dict:
    fixture = {
        "days": days,
        "rolling": {"points": _points(days, **rolling_bands)},
        "streams": {
            "window": {"days": days},
            "nodes": [{"layer": "group", "name": n, "amount": a} for n, a in groups],
            "totals": {"outflow": sum(a for _, a in groups)},
        },
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(fixture, fh)
        path = fh.name
    try:
        proc = subprocess.run(
            ["node", str(SIM), str(PAGE), path],
            capture_output=True,
            text=True,
            timeout=15,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr + proc.stdout)
    return json.loads(proc.stdout)


# Live-shaped 90d values from the ask, plus a Refunds/Cash deposits tie at 0.
LIVE = {
    "lyft": 52.49,
    "grubhub": 4.12,
    "turo": 47.94,
    "rewards": 1.74,
    "interest": 0.07,
    "refunds": 0,
    "cash_deposits": 0,
    "bitcoin": 14.65,
}


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestPeriodAveragesSort(unittest.TestCase):
    def test_income_sorted_desc_other_floored_and_last_on_tie(self) -> None:
        # Server sent a negative remainder: Other floors to 0 and ties with
        # Refunds and Cash deposits, so it stays after them (input order).
        out = _render({**LIVE, "other": -3.5}, [("Housing", 900)])
        self.assertEqual(
            [label for label, _ in out["income"]],
            ["Lyft", "Turo", "Bitcoin", "Grubhub", "Rewards", "Interest",
             "Refunds", "Cash deposits", "Other"],
        )
        self.assertEqual(dict(out["income"])["Other"], 0.0)
        values = [v for _, v in out["income"]]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_other_takes_its_sorted_spot(self) -> None:
        out = _render({**LIVE, "other": 9.3}, [("Housing", 900)])
        labels = [label for label, _ in out["income"]]
        self.assertEqual(labels[:5], ["Lyft", "Turo", "Bitcoin", "Other", "Grubhub"])
        self.assertEqual(dict(out["income"])["Other"], 9.3)

    def test_other_floor_from_inflow_remainder(self) -> None:
        # No server Other: remainder = inflow - named YNAB bands, floored at 0.
        bands = {**LIVE, "inflow": 52.49 + 47.94 + 4.12 + 1.74 + 0.07 + 2.0}
        out = _render(bands, [("Housing", 900)])
        labels = [label for label, _ in out["income"]]
        self.assertEqual(labels.index("Other"), labels.index("Grubhub") + 1)
        self.assertAlmostEqual(dict(out["income"])["Other"], 2.0, places=2)

    def test_expense_groups_sorted_desc_ties_stable_outflow_last(self) -> None:
        groups = [("Subscriptions", 90), ("Food", 900), ("Housing", 1800), ("Auto", 900)]
        out = _render(LIVE, groups)
        self.assertEqual(
            [label for label, _ in out["expenses"]],
            ["Housing", "Food", "Auto", "Subscriptions", "Outflow"],
        )
        self.assertEqual(dict(out["expenses"])["Outflow"], 41.0)

    def test_chart_band_stack_order_unchanged(self) -> None:
        html = PAGE.read_text(encoding="utf-8")
        self.assertIn("for (const band of ROLLING_BANDS)", html)
        self.assertIn("sortByPerDayDesc(bandAverages(points))", html)
        self.assertIn("sortByPerDayDesc(exp.rows)", html)


if __name__ == "__main__":
    unittest.main()
