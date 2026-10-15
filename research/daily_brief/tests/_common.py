"""Shared fixtures for Daily Brief tests (stdlib unittest)."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

from research.daily_brief import store  # noqa: E402


def sample(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class TmpStore(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def pub(self, ed: dict) -> Path:
        return store.publish(copy.deepcopy(ed), root=self.root)

    def pub_all(self) -> None:
        for n in ("sample-empty-todos", "sample-am", "sample-pm"):
            self.pub(sample(n))
