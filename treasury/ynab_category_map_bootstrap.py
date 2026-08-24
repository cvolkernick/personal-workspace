#!/usr/bin/env python3
"""Read-only YNAB category-map bootstrap (#340).

GET /budgets + GET /budgets/{id}/categories using the sealed PAT
(~/.config/ynab/token). Writes treasury/ynab_category_map.draft.json.

Never PATCH/POST to YNAB. Never overwrites the SoT map unless --overwrite-sot.
Never invents category ids — only copies ids returned by YNAB.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.ynab_category_map import (  # noqa: E402
    DEFAULT_BUDGET_NAME,
    DRAFT_MAP_PATH,
    MAP_PATH,
    build_draft_map,
    categories_from_ynab_payload,
)
from treasury.ynab_sync import fold_dashes, load_ynab_token, ynab_get  # noqa: E402


def pick_budget_strict(budgets: List[Dict[str, Any]], prefer_name: str) -> Dict[str, Any]:
    """Require an exact (dash-folded) name match. Do not invent a budget id."""
    if not budgets:
        raise RuntimeError("No YNAB budgets on this account")
    want = fold_dashes(prefer_name)
    for b in budgets:
        if fold_dashes(b.get("name")) == want:
            if not b.get("id"):
                raise RuntimeError(f"YNAB budget {prefer_name!r} has no id")
            return b
    names = [b.get("name") for b in budgets]
    raise RuntimeError(f"YNAB budget not found: {prefer_name!r} (have {names})")


def fetch_budget_categories(
    token: str,
    *,
    budget_name: str = DEFAULT_BUDGET_NAME,
) -> Dict[str, Any]:
    """GET-only: budgets list + categories for the named budget."""
    budgets_payload = ynab_get("/budgets", token)
    budgets = (budgets_payload.get("data") or {}).get("budgets") or []
    budget = pick_budget_strict(budgets, budget_name)
    bid = str(budget["id"])
    cats_payload = ynab_get(f"/budgets/{bid}/categories", token)
    return {
        "budget": budget,
        "budgets_payload": budgets_payload,
        "categories_payload": cats_payload,
    }


def draft_map_from_fetch(
    fetched: Dict[str, Any],
    *,
    token_source: Optional[str] = None,
) -> Dict[str, Any]:
    budget = fetched.get("budget") or {}
    cats = categories_from_ynab_payload(fetched.get("categories_payload") or {})
    return build_draft_map(
        budget_id=str(budget.get("id") or ""),
        budget_name=str(budget.get("name") or DEFAULT_BUDGET_NAME),
        categories=cats,
        token_source=token_source,
    )


def write_draft_map(
    draft: Dict[str, Any],
    *,
    draft_path: Optional[Path] = None,
    sot_path: Optional[Path] = None,
    overwrite_sot: bool = False,
) -> Dict[str, Any]:
    """Write draft. SoT is untouched unless overwrite_sot is explicitly True."""
    dpath = draft_path or DRAFT_MAP_PATH
    spath = sot_path or MAP_PATH
    dpath.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(draft, indent=2) + "\n"
    dpath.write_text(text, encoding="utf-8")
    sot_written = False
    if overwrite_sot:
        spath.parent.mkdir(parents=True, exist_ok=True)
        spath.write_text(text, encoding="utf-8")
        sot_written = True
    return {
        "ok": True,
        "draft_path": str(dpath),
        "sot_path": str(spath),
        "sot_written": sot_written,
        "budget_id": draft.get("budget_id") or "",
        "budget_name": draft.get("budget_name"),
        "category_count": len(draft.get("categories") or []),
        "category_ids": [c.get("id") for c in (draft.get("categories") or []) if c.get("id")],
    }


def bootstrap_category_map(
    *,
    token: Optional[str] = None,
    budget_name: str = DEFAULT_BUDGET_NAME,
    draft_path: Optional[Path] = None,
    sot_path: Optional[Path] = None,
    overwrite_sot: bool = False,
) -> Dict[str, Any]:
    tok, tok_src = (token, "arg") if token else load_ynab_token()
    if not tok:
        return {
            "ok": False,
            "error": "no YNAB token (~/.config/ynab/token or YNAB_TOKEN)",
            "sot_written": False,
            "category_ids": [],
        }
    fetched = fetch_budget_categories(tok, budget_name=budget_name)
    draft = draft_map_from_fetch(fetched, token_source=tok_src)
    written = write_draft_map(
        draft,
        draft_path=draft_path,
        sot_path=sot_path,
        overwrite_sot=overwrite_sot,
    )
    written["token_source"] = tok_src
    return written


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only bootstrap of YNAB category map draft (no YNAB writes)"
    )
    parser.add_argument(
        "--budget-name",
        default=DEFAULT_BUDGET_NAME,
        help=f"YNAB budget name (default: {DEFAULT_BUDGET_NAME})",
    )
    parser.add_argument("--draft-path", help="Draft JSON path (default: treasury/ynab_category_map.draft.json)")
    parser.add_argument(
        "--overwrite-sot",
        action="store_true",
        help="Also overwrite treasury/ynab_category_map.json (off by default)",
    )
    args = parser.parse_args(argv)
    try:
        result = bootstrap_category_map(
            budget_name=args.budget_name,
            draft_path=Path(args.draft_path) if args.draft_path else None,
            overwrite_sot=args.overwrite_sot,
        )
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e), "sot_written": False}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
