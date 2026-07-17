"""Sessions module is superseded by workspace.py; keep import smoke if present."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

DASH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASH))


class TestLegacySessionsModule(unittest.TestCase):
    def test_workspace_is_primary(self) -> None:
        import workspace

        self.assertTrue(hasattr(workspace, "collect_workspace_dashboard"))
        self.assertTrue((DASH / "workspace.py").is_file())


if __name__ == "__main__":
    unittest.main()
