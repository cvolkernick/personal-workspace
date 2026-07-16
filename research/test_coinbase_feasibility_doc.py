#!/usr/bin/env python3
"""Structural verification of the Coinbase automation feasibility research deliverable.

Drives the real artifact on disk (not a reimplementation of findings).
Fails if required matrix sections, product split, or policy classifications are missing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

DOC = Path(__file__).resolve().parent / "coinbase-automation-feasibility.md"

REQUIRED_SUBSTRINGS = [
    # Acceptance: products covered
    "BTC-collateralized",
    "High Yield",
    "Liquid USDC",
    "debit",
    "credit",
    # Product split
    "Exchange Loans Program",
    "retail Morpho",
    "Advanced Trade",
    # Policy classifications
    "Partially",
    "Not",
    "Fully",
    # Surfaces
    "MCP",
    "loan protection",
    "autopay",
    # MCP inventory
    "coinbase",
    "Morpho MCP",
    "CDP CLI MCP",
]

REQUIRED_TABLE_HEADERS = [
    "Read access",
    "Write",
    "Recommended automation surface",
]

POLICY_TARGETS = [
    "LTV",
    "available credit",
    "High Yield",
    "pay credit card",
    "collateral",
]


def main() -> int:
    if not DOC.is_file():
        print(f"FAIL: missing deliverable {DOC}")
        return 1

    text = DOC.read_text(encoding="utf-8")
    if len(text) < 2000:
        print(f"FAIL: deliverable too short ({len(text)} chars)")
        return 1

    missing = [s for s in REQUIRED_SUBSTRINGS if s.lower() not in text.lower()]
    if missing:
        print("FAIL: missing required substrings:")
        for m in missing:
            print(f"  - {m}")
        return 1

    for h in REQUIRED_TABLE_HEADERS:
        if h not in text:
            print(f"FAIL: capability matrix header missing: {h}")
            return 1

    for p in POLICY_TARGETS:
        if p.lower() not in text.lower():
            print(f"FAIL: policy target not discussed: {p}")
            return 1

    # Retail vs institutional must both appear near loan discussion
    if "Exchange" not in text or "Morpho" not in text:
        print("FAIL: must distinguish Exchange loans vs Morpho retail")
        return 1

    # Count markdown tables (capability + summary at minimum)
    table_rows = len(re.findall(r"^\|.+\|$", text, re.M))
    if table_rows < 20:
        print(f"FAIL: expected multi-row matrix tables, found {table_rows} table lines")
        return 1

    print("PASS: coinbase-automation-feasibility.md structural checks OK")
    print(f"  path: {DOC}")
    print(f"  size: {len(text)} chars")
    print(f"  table_lines: {table_rows}")
    print(f"  required_substrings: {len(REQUIRED_SUBSTRINGS)} present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
