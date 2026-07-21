"""Canonical allocation block ordering for side-by-side pies."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from holistic.time_allocator.order import sort_allocation_blocks  # noqa: E402


class OrderTests(unittest.TestCase):
    def test_canonical_order(self) -> None:
        blocks = [
            {"id": "lyft", "title": "Lyft", "minutes": 100, "role": "fill"},
            {"id": "_unaccounted", "title": "Free", "minutes": 50, "role": "unaccounted"},
            {"id": "workout", "title": "Workout", "minutes": 60, "role": "session"},
            {"id": "sleep", "title": "Sleep", "minutes": 480, "role": "reserve"},
            {"id": "duchess-walk", "title": "Duchess", "minutes": 45, "role": "fixed"},
            {"id": "errand", "title": "Errand", "minutes": 30, "role": "adhoc"},
        ]
        ordered = [b["id"] for b in sort_allocation_blocks(blocks)]
        self.assertEqual(
            ordered,
            ["sleep", "duchess-walk", "workout", "lyft", "errand", "_unaccounted"],
        )


if __name__ == "__main__":
    unittest.main()
