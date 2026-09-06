"""Quest complete → pantry in_stock for shopping / restock leaves.

Checking off a Restock/Get/Add shopping quest marks the matching
existing ingredient in stock. Uncheck does not mark it out. Unknown
foods are not invented. Non-shopping quests are ignored. Quest complete
stays 200 even if the pantry write fails.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Optional, Tuple

from .inventory_store import INVENTORY_ROW_DEFAULT, load_preview_inventory, save_preview_inventory
from .nutrition_planner import _names_overlap, set_in_stock

SHOPPING_GROUPS = frozenset({"shopping", "shop"})
_SHOP_TITLE = re.compile(
    r"^(?:Restock|Get|Add):?\s+(.+?)(?:\s+[—-]\s+.+)?$",
    re.I,
)


def shopping_name_from_title(title: str) -> str:
    text = (title or "").strip()
    match = _SHOP_TITLE.match(text)
    return (match.group(1) or "").strip() if match else ""


def ingredient_id_from_slug(slug: str) -> str:
    raw = str(slug or "").strip().lower()
    if raw.startswith("buy-"):
        raw = raw[4:]
    raw = re.sub(r"-\d+$", "", raw)
    if raw in ("shop-top", "shopping", "shop"):
        return ""
    return raw


def looks_like_shopping_quest(
    group: str = "",
    title: str = "",
    slug: str = "",
) -> bool:
    g = str(group or "").strip().lower()
    if g in SHOPPING_GROUPS:
        return True
    s = str(slug or "").strip().lower()
    if s.startswith("buy-"):
        return True
    return bool(shopping_name_from_title(title))


def _blocked_uid(user_id: str) -> bool:
    uid = str(user_id or "").strip()
    return (not uid) or uid.lower() == INVENTORY_ROW_DEFAULT


def _words(value: str) -> str:
    return re.sub(r"[-_]+", " ", str(value or "")).strip()


def match_inventory_ingredient(
    inventory: Optional[dict],
    name: str = "",
    iid: str = "",
) -> Optional[dict]:
    """Existing pantry row only. Exact id, then name/id overlap. No create."""
    items = (inventory or {}).get("ingredients") or []
    want_id = str(iid or "").strip().lower()
    want_name = str(name or "").strip()
    if want_id:
        for raw in items:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("id") or "").strip().lower() == want_id:
                return raw
    for raw in items:
        if not isinstance(raw, dict):
            continue
        existing_name = str(raw.get("name") or "")
        existing_id = str(raw.get("id") or "")
        if want_name and _names_overlap(existing_name, want_name):
            return raw
        if want_id and (
            _names_overlap(_words(existing_id), _words(want_id))
            or _names_overlap(existing_name, _words(want_id))
        ):
            return raw
        if want_name and _names_overlap(_words(existing_id), want_name):
            return raw
    return None


def apply_shopping_quest_stock(
    *,
    completed: bool,
    group: str = "",
    title: str = "",
    slug: str = "",
    inventory: Optional[dict] = None,
) -> Tuple[Optional[dict], Dict[str, Any]]:
    """Pure merge. Returns (inventory to persist or None, info)."""
    info: Dict[str, Any] = {
        "ok": True,
        "wrote": False,
        "action": "ignore",
        "reason": "",
    }
    if not completed:
        info["reason"] = "uncheck_noop"
        return None, info
    if not looks_like_shopping_quest(group=group, title=title, slug=slug):
        info["reason"] = "not_shopping"
        return None, info

    name = shopping_name_from_title(title)
    iid = ingredient_id_from_slug(slug)
    if not name and not iid:
        info["reason"] = "no_ingredient"
        return None, info

    current = inventory if isinstance(inventory, dict) else {"ingredients": []}
    match = match_inventory_ingredient(current, name, iid)
    if not match:
        info["ok"] = False
        info["action"] = "skip"
        info["reason"] = "not_found"
        info["name"] = name or iid
        return None, info

    mid = str(match.get("id") or "").strip()
    info["id"] = mid
    info["name"] = str(match.get("name") or name)
    if match.get("in_stock", True):
        info["action"] = "dedupe"
        info["reason"] = "already_in_stock"
        return None, info
    if not mid:
        info["ok"] = False
        info["reason"] = "missing_ingredient_id"
        return None, info
    updated = set_in_stock(current, ingredient_id=mid, in_stock=True)
    info["action"] = "restock"
    info["wrote"] = True
    info["in_stock"] = True
    return updated, info


def attach_shopping_quest_stock(
    result: dict,
    payload: Optional[dict],
    completed: bool,
    *,
    user_id: str,
    load_inventory: Optional[Callable[[str], Tuple[dict, str]]] = None,
    save_inventory: Optional[Callable[[dict, str], dict]] = None,
) -> dict:
    """After a successful GT complete: maybe mark the restocked food in stock.

    Quest complete stays 200 even if the pantry write fails — GT already flipped.
    ``inventory_stock`` on the result is honest about the write.
    """
    payload = payload if isinstance(payload, dict) else {}
    task = result.get("task") if isinstance(result.get("task"), dict) else {}
    group = str(payload.get("group") or "").strip()
    title = str(payload.get("title") or task.get("title") or "").strip()
    slug = str(payload.get("slug") or "").strip()
    if not looks_like_shopping_quest(group=group, title=title, slug=slug):
        result["inventory_stock"] = {
            "ok": True,
            "wrote": False,
            "action": "ignore",
            "reason": "not_shopping",
        }
        return result
    if not completed:
        result["inventory_stock"] = {
            "ok": True,
            "wrote": False,
            "action": "ignore",
            "reason": "uncheck_noop",
        }
        return result
    if _blocked_uid(user_id):
        result["inventory_stock"] = {
            "ok": False,
            "wrote": False,
            "action": "skip",
            "reason": "user_id_required",
        }
        return result

    try:
        loader = load_inventory or load_preview_inventory
        current, _src = loader(user_id)
        updated, info = apply_shopping_quest_stock(
            completed=bool(completed),
            group=group,
            title=title,
            slug=slug,
            inventory=current,
        )
        if updated is not None:
            writer = save_inventory or save_preview_inventory
            saved = writer(updated, user_id)
            info["write"] = {"ok": True}
            info["wrote"] = True
            info["inventory"] = saved if isinstance(saved, dict) else updated
        result["inventory_stock"] = info
    except Exception as exc:  # noqa: BLE001
        result["inventory_stock"] = {
            "ok": False,
            "wrote": False,
            "error": str(exc) or type(exc).__name__,
        }
    return result
