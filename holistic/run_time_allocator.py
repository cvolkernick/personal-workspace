#!/usr/bin/env python3
"""Entry point: python3 holistic/run_time_allocator.py <command> …"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from holistic.time_allocator.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
