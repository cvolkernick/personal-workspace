#!/usr/bin/env python3
"""Product → git branch map (issue #560).

Pi FCC live root (prism ``~/personal-workspace``) may only land ``work/treasury``.
FitDash SoT is ``master`` (Vercel). Never pull ``master`` or ``work/holistic``
onto the FCC checkout — that is the 2026-09-09 Jul-index / wrong-tree incident.

Used by ``deploy/workspace_sync.sh`` (refuse before any git mutation) and as
the PR-base lock for FCC-only landings.
"""

from __future__ import annotations

import argparse
import sys
from typing import Iterable, Optional

ALLOWED_FCC_LIVE_SYNC_BRANCHES = frozenset({"work/treasury"})
# Incident cases called out in #560 AC; anything outside the allowlist is refused.
REFUSED_FCC_LIVE_SYNC_BRANCHES = frozenset({"master", "main", "work/holistic"})

PRODUCT_SOT: dict[str, str] = {
    "fcc": "work/treasury",
    "treasury": "work/treasury",
    "fitdash": "master",
}

FCC_PREFIXES = ("treasury/", "financial-command/", "investment/")
FITDASH_PREFIXES = ("resistance-dashboard/", "fitness/")


class SyncBranchRefused(ValueError):
    """SYNC_BRANCH is not on the FCC live-root allowlist."""


class MixedProductError(ValueError):
    """FCC paths mixed with non-FCC paths — split the PR."""


def _norm(path: str) -> str:
    return path.strip().lstrip("./").replace("\\", "/")


def classify_path(path: str) -> str:
    """Return ``fcc``, ``fitdash``, or ``other`` for a repo-relative path."""
    p = _norm(path)
    if not p:
        return "other"
    for prefix in FCC_PREFIXES:
        stem = prefix.rstrip("/")
        if p == stem or p.startswith(prefix):
            return "fcc"
    for prefix in FITDASH_PREFIXES:
        stem = prefix.rstrip("/")
        if p == stem or p.startswith(prefix):
            return "fitdash"
    return "other"


def check_sync_branch(branch: str, *, checkout: str = "fcc-live") -> str:
    """Return *branch* if this checkout may land it; raise otherwise.

    ``checkout='fcc-live'`` is the prism ``~/personal-workspace`` FCC root.
    Other checkout kinds are not locked by this map.
    """
    b = (branch or "").strip()
    if checkout != "fcc-live":
        if not b:
            raise SyncBranchRefused("SYNC_BRANCH is empty")
        return b
    if b not in ALLOWED_FCC_LIVE_SYNC_BRANCHES:
        raise SyncBranchRefused(
            f"FCC live root refuses SYNC_BRANCH={b or 'unset'} "
            f"(allowlist: work/treasury only; never master or work/holistic)"
        )
    return b


def pr_base_for_product(product: str) -> str:
    key = (product or "").strip().lower()
    if key not in PRODUCT_SOT:
        known = ", ".join(sorted(PRODUCT_SOT))
        raise ValueError(f"unknown product {product!r} (want {known})")
    return PRODUCT_SOT[key]


def pr_base_for_paths(paths: Iterable[str]) -> str:
    """PR base for a set of paths.

    FCC-only → ``work/treasury``. FitDash / platform / deploy → ``master``.
    Mixing FCC with anything else is refused so a merge cannot reset the FCC
    live tip onto master.
    """
    kinds = {classify_path(p) for p in paths if (p or "").strip()}
    if not kinds:
        return "master"
    if "fcc" in kinds and kinds - {"fcc"}:
        raise MixedProductError(
            "FCC paths must PR into work/treasury only; "
            "split non-FCC files into a separate PR (do not reset FCC tip to master)"
        )
    if kinds == {"fcc"}:
        return "work/treasury"
    return "master"


def map_rows() -> list[tuple[str, str, str]]:
    return [
        ("FCC + treasury", "work/treasury", "Pi live root; refuse master / work/holistic pulls"),
        ("FitDash", "master", "Vercel production; Mac worktrees OK; not the FCC checkout"),
    ]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd")

    p_check = sub.add_parser("check-sync", help="Exit 0 iff SYNC_BRANCH is allowed")
    p_check.add_argument("--branch", required=True)
    p_check.add_argument("--checkout", default="fcc-live")

    p_prod = sub.add_parser("pr-base", help="Print PR base branch")
    p_prod.add_argument("--product", help="fcc | treasury | fitdash")
    p_prod.add_argument("--path", action="append", dest="paths", help="Repo path (repeatable)")

    sub.add_parser("map", help="Print the product → branch table")

    args = parser.parse_args(argv)
    if args.cmd is None or args.cmd == "map":
        for product, branch, note in map_rows():
            print(f"{product}\t{branch}\t{note}")
        return 0

    try:
        if args.cmd == "check-sync":
            print(check_sync_branch(args.branch, checkout=args.checkout))
            return 0
        if args.cmd == "pr-base":
            if args.product and args.paths:
                parser.error("use --product or --path, not both")
            if args.product:
                print(pr_base_for_product(args.product))
                return 0
            if args.paths:
                print(pr_base_for_paths(args.paths))
                return 0
            parser.error("pr-base requires --product or --path")
    except (SyncBranchRefused, MixedProductError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
