"""YNAB category map SoT (v0) — load, validate, enabled ids.

See treasury/ynab_category_map.json. Bootstrap (read-only GET) lives in
ynab_category_map_bootstrap.py. Categorize/approve writes live in ynab_write.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

TREASURY_DIR = Path(__file__).resolve().parent
MAP_PATH = TREASURY_DIR / "ynab_category_map.json"
DRAFT_MAP_PATH = TREASURY_DIR / "ynab_category_map.draft.json"

SCHEMA_VERSION = 0
DEFAULT_BUDGET_NAME = "Chris's Plan"
HARD_FORBID = frozenset({"transfer", "payment", "move_money"})
ALLOWED_WRITE_ACTIONS = frozenset({"categorize", "approve", "categorize_approve"})

# YNAB system groups — never treat as enabled write targets.
INTERNAL_GROUP_NAMES = frozenset(
    {
        "internal master category",
        "hidden categories",
    }
)


def empty_map(*, budget_name: str = DEFAULT_BUDGET_NAME) -> Dict[str, Any]:
    """v0 placeholder SoT: empty categories, allow flags on, hard forbid list."""
    return {
        "schema_version": SCHEMA_VERSION,
        "budget_id": "",
        "budget_name": budget_name,
        "allow_approve": True,
        "allow_categorize": True,
        "forbid": sorted(HARD_FORBID),
        "categories": [],
        "payee_rules": [],
        "notes": (
            "budget_id is filled by treasury/ynab_category_map_bootstrap.py "
            "(GET /budgets). Do not invent category ids."
        ),
    }


def load_category_map(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or MAP_PATH
    if not p.is_file():
        raise FileNotFoundError(f"YNAB category map missing: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YNAB category map must be an object: {p}")
    return validate_category_map(data)


def validate_category_map(data: Dict[str, Any]) -> Dict[str, Any]:
    ver = data.get("schema_version")
    if ver not in (SCHEMA_VERSION, None):
        raise ValueError(f"unsupported category map schema_version: {ver}")
    out = dict(data)
    out.setdefault("schema_version", SCHEMA_VERSION)
    out.setdefault("budget_id", "")
    out.setdefault("budget_name", DEFAULT_BUDGET_NAME)
    out.setdefault("allow_approve", True)
    out.setdefault("allow_categorize", True)
    forbid = [str(x) for x in (out.get("forbid") or [])]
    # Floor: hard-forbid cannot be dropped by editing the file.
    out["forbid"] = sorted(set(forbid) | HARD_FORBID)
    cats = out.get("categories")
    if cats is None:
        out["categories"] = []
    elif not isinstance(cats, list):
        raise ValueError("categories must be an array")
    rules = out.get("payee_rules")
    if rules is None:
        out["payee_rules"] = []
    elif not isinstance(rules, list):
        raise ValueError("payee_rules must be an array")
    return out


def effective_forbid(category_map: Dict[str, Any]) -> Set[str]:
    extra = {str(x) for x in (category_map.get("forbid") or [])}
    return set(HARD_FORBID) | extra


def enabled_category_ids(category_map: Dict[str, Any]) -> Set[str]:
    ids: Set[str] = set()
    for row in category_map.get("categories") or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("id") or "").strip()
        if cid and row.get("enabled") is True:
            ids.add(cid)
    return ids


def mapped_category_ids(category_map: Dict[str, Any]) -> Set[str]:
    """All category ids present in the map (enabled or not). Never invents."""
    ids: Set[str] = set()
    for row in category_map.get("categories") or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("id") or "").strip()
        if cid:
            ids.add(cid)
    return ids


def is_category_enabled(category_map: Dict[str, Any], category_id: str) -> bool:
    cid = str(category_id or "").strip()
    return bool(cid) and cid in enabled_category_ids(category_map)


def _looks_like_payment_category(name: str, group_name: str) -> bool:
    n = (name or "").lower()
    g = (group_name or "").lower()
    if "credit card payment" in n or "credit card payments" in g:
        return True
    if n.endswith(" payment") and "card" in n:
        return True
    return False


def _is_internal_group(group_name: str) -> bool:
    return (group_name or "").strip().lower() in INTERNAL_GROUP_NAMES


def categories_from_ynab_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten YNAB GET /categories groups. IDs come from the payload only."""
    data = payload.get("data") if isinstance(payload, dict) else None
    groups: Iterable[Any]
    if isinstance(data, dict) and data.get("category_groups") is not None:
        groups = data.get("category_groups") or []
    else:
        groups = (payload or {}).get("category_groups") or []
    out: List[Dict[str, Any]] = []
    for g in groups:
        if not isinstance(g, dict) or g.get("deleted"):
            continue
        group_id = str(g.get("id") or "").strip()
        if not group_id:
            continue
        group_name = str(g.get("name") or "")
        internal = _is_internal_group(group_name)
        group_hidden = bool(g.get("hidden"))
        for c in g.get("categories") or []:
            if not isinstance(c, dict) or c.get("deleted"):
                continue
            cid = str(c.get("id") or "").strip()
            if not cid:
                continue
            name = str(c.get("name") or "")
            hidden = bool(c.get("hidden")) or group_hidden
            paymentish = _looks_like_payment_category(name, group_name)
            enabled = not hidden and not paymentish and not internal
            out.append(
                {
                    "id": cid,
                    "name": name,
                    "group_id": group_id,
                    "group_name": group_name,
                    "hidden": hidden,
                    "enabled": enabled,
                }
            )
    return out


def build_draft_map(
    *,
    budget_id: str,
    budget_name: str,
    categories: List[Dict[str, Any]],
    token_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble a draft SoT from GET results. Does not invent category ids."""
    seen: Set[str] = set()
    clean: List[Dict[str, Any]] = []
    for row in categories:
        cid = str((row or {}).get("id") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        clean.append(dict(row))
        clean[-1]["id"] = cid
    draft = empty_map(budget_name=budget_name or DEFAULT_BUDGET_NAME)
    draft["budget_id"] = str(budget_id or "")
    draft["categories"] = clean
    draft["notes"] = (
        "DRAFT from GET /budgets + GET /budgets/{id}/categories. "
        "Not SoT until Chris cuts/renames and pins over ynab_category_map.json. "
        "Ids are copied from YNAB only."
    )
    if token_source:
        draft["token_source"] = token_source
    return validate_category_map(draft)
