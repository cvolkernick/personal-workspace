#!/usr/bin/env python3
"""Horizon daily intelligence pipeline entry point.

Usage:
  python3 research/horizon/run_horizon.py --offline
  python3 research/horizon/run_horizon.py
  python3 research/horizon/run_horizon.py --link-only --offline
  python3 research/horizon/run_horizon.py --publish-offline
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.horizon.pipeline import run_pipeline  # noqa: E402
from research.horizon.publish_offline import publish_offline, result_json  # noqa: E402
from research.horizon.store import DEFAULT_DATA_DIR  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Horizon: global macro / geopolitical world-state + daily synthesis"
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use fixture sources only (no live RSS)",
    )
    parser.add_argument(
        "--link-only",
        action="store_true",
        help="Skip ingestion; recompute strategy linkages against latest world-state",
    )
    parser.add_argument(
        "--publish-offline",
        action="store_true",
        help=(
            "Nest/GH offline publish (#301): stamp version_id on existing latest "
            "SoT (or shipped fixtures with --from-fixtures). Does not invent "
            "facts/rates/regime. Held if no latest and fixtures not requested."
        ),
    )
    parser.add_argument(
        "--from-fixtures",
        action="store_true",
        help="With --publish-offline: build from shipped fixtures (explicit opt-in)",
    )
    parser.add_argument(
        "--version-id",
        default=None,
        help="Optional version_id YYYYMMDDTHHMMSSZ (used with --publish-offline)",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=ROOT,
        help="Workspace root for strategy/investment sources",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory for versioned world-state and briefs",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=None,
        help="Optional path to fixture events JSON",
    )
    args = parser.parse_args(argv)

    if args.from_fixtures and not args.publish_offline:
        parser.error("--from-fixtures requires --publish-offline")
    if args.version_id and not args.publish_offline:
        parser.error("--version-id requires --publish-offline")

    if args.publish_offline:
        result = publish_offline(
            workspace=args.workspace,
            data_dir=args.data_dir,
            version_id=args.version_id,
            from_fixtures=args.from_fixtures,
            fixture_path=args.fixture,
        )
        print(result_json(result), end="")
        return 0 if result.get("ok") else 1

    result = run_pipeline(
        workspace=args.workspace,
        data_dir=args.data_dir,
        offline=args.offline,
        link_only=args.link_only,
        fixture_path=args.fixture,
    )

    summary = {
        "ok": result["ok"],
        "version_id": result["version_id"],
        "offline": result["offline"],
        "link_only": result["link_only"],
        "source_modes": result["source_modes"],
        "node_total": result["node_total"],
        "linkage_count": result["linkage_count"],
        "strategy_paths_exist": result["strategy_paths_exist"],
        "sections": result["sections"],
        "paths": result["paths"],
    }
    print(json.dumps(summary, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
