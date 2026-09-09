"""FitDash restock → retailer cart (no Google Tasks, no auto-checkout).

Default path: venue-tagged list → Walmart cart / Costco list → done.
Keep is fallback only when a cart write is blocked (auth/session).
Retry re-reads the hold file and tries carts again.

After Chris confirms an order (not this module placing it), call
``confirm_received`` to mark pantry in-stock. No GT complete step.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from .quest_inventory_stock import apply_shopping_quest_stock
from .restock_venues import (
    VENUE_COSTCO,
    VENUE_OTHER,
    VENUE_WALMART,
    group_by_venue,
    restock_line,
    tag_items,
    venue_for_item,
)

WALMART_COOKIE_ENV = "WALMART_SESSION_COOKIE"
COSTCO_COOKIE_ENV = "COSTCO_SESSION_COOKIE"
KEEP_TOKEN_ENV = "GOOGLE_KEEP_MASTER_TOKEN"
KEEP_WEBHOOK_ENV = "GOOGLE_KEEP_HOLD_WEBHOOK"
KEEP_NOTE_TITLE = "FitDash restock hold"
HOLD_FILENAME = "restock_hold.json"

HttpGet = Callable[[str, Dict[str, str]], Dict[str, Any]]
HttpPost = Callable[[str, Dict[str, str], Any], Dict[str, Any]]


def _config_dir() -> Path:
    override = os.environ.get("RESISTANCE_DASHBOARD_CONFIG_DIR")
    base = Path(override).expanduser() if override else Path.home() / ".config" / "resistance-dashboard"
    return base


def hold_path() -> Path:
    return _config_dir() / HOLD_FILENAME


def load_hold() -> dict:
    path = hold_path()
    if not path.is_file():
        return {"items": [], "updated_at": None, "note_title": KEEP_NOTE_TITLE}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"items": [], "updated_at": None, "note_title": KEEP_NOTE_TITLE}
    if not isinstance(data, dict):
        return {"items": [], "updated_at": None, "note_title": KEEP_NOTE_TITLE}
    items = [x for x in (data.get("items") or []) if isinstance(x, dict)]
    return {
        "items": items,
        "updated_at": data.get("updated_at"),
        "note_title": data.get("note_title") or KEEP_NOTE_TITLE,
        "keep": data.get("keep") if isinstance(data.get("keep"), dict) else {},
    }


def save_hold(data: dict) -> dict:
    payload = {
        "items": [x for x in (data.get("items") or []) if isinstance(x, dict)],
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note_title": data.get("note_title") or KEEP_NOTE_TITLE,
        "keep": data.get("keep") if isinstance(data.get("keep"), dict) else {},
    }
    path = hold_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        payload["write_error"] = "hold_file_unwritable"
    return payload


def keep_line(item: dict) -> str:
    line = restock_line(item)
    venue = line.get("venue") or VENUE_OTHER
    action = str(line.get("action") or "Restock").title()
    name = line.get("name") or line.get("id") or "item"
    return f"[{venue}] {action}: {name}"


def session_status(venue: str) -> dict:
    """Honest auth/session probe. Never invents a signed-in cart."""
    if venue == VENUE_WALMART:
        cookie = (os.environ.get(WALMART_COOKIE_ENV) or "").strip()
        return {
            "venue": VENUE_WALMART,
            "ok": bool(cookie),
            "reason": None if cookie else "auth_session",
        }
    if venue == VENUE_COSTCO:
        cookie = (os.environ.get(COSTCO_COOKIE_ENV) or "").strip()
        return {
            "venue": VENUE_COSTCO,
            "ok": bool(cookie),
            "reason": None if cookie else "auth_session",
        }
    return {"venue": venue or VENUE_OTHER, "ok": False, "reason": "unsupported_venue"}


def _http_json(
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Any = None,
    timeout: float = 20,
) -> dict:
    raw = None
    if body is not None:
        raw = json.dumps(body).encode("utf-8")
        headers = dict(headers)
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=raw, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(text) if text.strip() else {}
            except json.JSONDecodeError:
                parsed = {"raw": text[:500]}
            return {"ok": 200 <= getattr(resp, "status", 200) < 300, "status": getattr(resp, "status", 200), "body": parsed}
    except urllib.error.HTTPError as exc:
        err_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return {"ok": False, "status": exc.code, "error": err_text[:400] or str(exc)}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "status": 0, "error": str(exc) or type(exc).__name__}


def _walmart_headers() -> Dict[str, str]:
    cookie = (os.environ.get(WALMART_COOKIE_ENV) or "").strip()
    return {
        "Cookie": cookie,
        "Accept": "application/json",
        "User-Agent": "FitDashRestock/1.0",
    }


def _costco_headers() -> Dict[str, str]:
    cookie = (os.environ.get(COSTCO_COOKIE_ENV) or "").strip()
    return {
        "Cookie": cookie,
        "Accept": "application/json",
        "User-Agent": "FitDashRestock/1.0",
    }


def _pick_cheapest(candidates: List[dict]) -> Optional[dict]:
    """Cheapest unit among search hits. No brand preference."""
    scored: List[tuple] = []
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        offer = str(raw.get("offerId") or raw.get("offer_id") or raw.get("usItemId") or raw.get("id") or "")
        if not offer:
            continue
        price = raw.get("price")
        if price is None:
            price = (raw.get("priceInfo") or {}).get("linePrice") if isinstance(raw.get("priceInfo"), dict) else None
        try:
            amount = float(price)
        except (TypeError, ValueError):
            amount = 10**9
        scored.append((amount, raw, offer))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0])
    raw, offer = scored[0][1], scored[0][2]
    return {
        "offer_id": offer,
        "name": str(raw.get("name") or raw.get("title") or ""),
        "price": scored[0][0] if scored[0][0] < 10**9 else None,
    }


def add_walmart_cart(
    item: dict,
    *,
    http_get: Optional[HttpGet] = None,
    http_post: Optional[HttpPost] = None,
) -> dict:
    """Add one item to the signed-in Walmart cart. Never checkout."""
    line = restock_line(item)
    probe = session_status(VENUE_WALMART)
    if not probe["ok"]:
        return {
            "ok": False,
            "blocked": True,
            "reason": "auth_session",
            "venue": VENUE_WALMART,
            "item": line,
            "checkout": False,
        }
    query = urllib.parse.quote(line.get("query") or line.get("name") or "")
    getter = http_get or (
        lambda url, headers: _http_json("GET", url, headers)
    )
    poster = http_post or (
        lambda url, headers, body: _http_json("POST", url, headers, body)
    )
    search_url = (
        "https://www.walmart.com/orchestra/home/graphql/search"
        f"?query={query}&page=1&prg=desktop"
    )
    found = getter(search_url, _walmart_headers())
    hits: List[dict] = []
    body = found.get("body") if isinstance(found, dict) else {}
    if isinstance(body, dict):
        items = (
            body.get("items")
            or body.get("searchResult")
            or ((body.get("data") or {}).get("search") or {}).get("searchResult")
            or []
        )
        if isinstance(items, dict):
            items = items.get("itemStacks") or items.get("items") or []
        if isinstance(items, list):
            for stack in items:
                if isinstance(stack, dict) and isinstance(stack.get("items"), list):
                    hits.extend(x for x in stack["items"] if isinstance(x, dict))
                elif isinstance(stack, dict):
                    hits.append(stack)
    pick = _pick_cheapest(hits)
    offer = str((item or {}).get("offer_id") or (item or {}).get("us_item_id") or "")
    if pick and not offer:
        offer = pick["offer_id"]
    if not offer:
        return {
            "ok": False,
            "blocked": False,
            "reason": "no_offer",
            "venue": VENUE_WALMART,
            "item": line,
            "checkout": False,
            "search_ok": bool(found.get("ok")),
        }
    add_url = "https://www.walmart.com/api/v3/cart/items"
    added = poster(
        add_url,
        _walmart_headers(),
        {"offerId": offer, "quantity": 1, "location": "CART"},
    )
    ok = bool(isinstance(added, dict) and added.get("ok"))
    return {
        "ok": ok,
        "blocked": (not ok) and int((added or {}).get("status") or 0) in (401, 403),
        "reason": None if ok else (
            "auth_session"
            if int((added or {}).get("status") or 0) in (401, 403)
            else (added or {}).get("error") or "cart_write_failed"
        ),
        "venue": VENUE_WALMART,
        "item": line,
        "offer_id": offer,
        "checkout": False,
        "http_status": (added or {}).get("status"),
    }


def add_costco_list(
    item: dict,
    *,
    http_post: Optional[HttpPost] = None,
) -> dict:
    """Add one item to the signed-in Costco shopping list / cart. Never checkout."""
    line = restock_line(item)
    probe = session_status(VENUE_COSTCO)
    if not probe["ok"]:
        return {
            "ok": False,
            "blocked": True,
            "reason": "auth_session",
            "venue": VENUE_COSTCO,
            "item": line,
            "checkout": False,
        }
    poster = http_post or (
        lambda url, headers, body: _http_json("POST", url, headers, body)
    )
    added = poster(
        "https://www.costco.com/AjaxAddItemToListView",
        _costco_headers(),
        {"keyword": line.get("query") or line.get("name"), "qty": 1},
    )
    ok = bool(isinstance(added, dict) and added.get("ok"))
    return {
        "ok": ok,
        "blocked": (not ok) and int((added or {}).get("status") or 0) in (401, 403),
        "reason": None if ok else (
            "auth_session"
            if int((added or {}).get("status") or 0) in (401, 403)
            else (added or {}).get("error") or "cart_write_failed"
        ),
        "venue": VENUE_COSTCO,
        "item": line,
        "checkout": False,
        "http_status": (added or {}).get("status"),
    }


def park_other(item: dict) -> dict:
    line = restock_line(item)
    return {
        "ok": False,
        "blocked": False,
        "parked": True,
        "reason": "unsupported_venue",
        "venue": VENUE_OTHER,
        "item": line,
        "checkout": False,
    }


def keep_credentials_ok() -> bool:
    return bool(
        (os.environ.get(KEEP_TOKEN_ENV) or "").strip()
        or (os.environ.get(KEEP_WEBHOOK_ENV) or "").strip()
    )


def write_keep_hold(
    items: Iterable[dict],
    *,
    http_post: Optional[HttpPost] = None,
) -> dict:
    """Thin Keep holding checklist with venue tags. Fallback only."""
    lines = [keep_line(x) for x in items if isinstance(x, dict)]
    webhook = (os.environ.get(KEEP_WEBHOOK_ENV) or "").strip()
    token = (os.environ.get(KEEP_TOKEN_ENV) or "").strip()
    if webhook:
        poster = http_post or (
            lambda url, headers, body: _http_json("POST", url, headers, body)
        )
        sent = poster(
            webhook,
            {"Accept": "application/json"},
            {"title": KEEP_NOTE_TITLE, "checklist": lines, "source": "fitdash-restock"},
        )
        ok = bool(isinstance(sent, dict) and sent.get("ok"))
        return {
            "ok": ok,
            "blocked": not ok,
            "reason": None if ok else (sent or {}).get("error") or "keep_write_failed",
            "title": KEEP_NOTE_TITLE,
            "lines": lines,
        }
    if token:
        try:
            import gkeepapi  # type: ignore
        except ImportError:
            return {
                "ok": False,
                "blocked": True,
                "reason": "keep_client_missing",
                "title": KEEP_NOTE_TITLE,
                "lines": lines,
            }
        try:
            keep = gkeepapi.Keep()
            email = (os.environ.get("GOOGLE_KEEP_EMAIL") or "").strip()
            keep.authenticate(email, token)
            note = None
            for candidate in keep.find(query=KEEP_NOTE_TITLE):
                if getattr(candidate, "title", "") == KEEP_NOTE_TITLE:
                    note = candidate
                    break
            if note is None:
                note = keep.createList(KEEP_NOTE_TITLE, [(line, False) for line in lines])
            else:
                existing = {str(getattr(x, "text", "")).strip() for x in (note.items or [])}
                for line in lines:
                    if line not in existing:
                        note.add(line, False)
            keep.sync()
            return {
                "ok": True,
                "blocked": False,
                "reason": None,
                "title": KEEP_NOTE_TITLE,
                "lines": lines,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "blocked": True,
                "reason": str(exc) or type(exc).__name__,
                "title": KEEP_NOTE_TITLE,
                "lines": lines,
            }
    return {
        "ok": False,
        "blocked": True,
        "reason": "auth_session",
        "title": KEEP_NOTE_TITLE,
        "lines": lines,
    }


def _merge_hold_items(existing: List[dict], incoming: List[dict]) -> List[dict]:
    seen = set()
    out: List[dict] = []

    def key(row: dict) -> str:
        line = restock_line(row)
        return f"{line.get('venue')}|{slug_id(line)}"

    def slug_id(line: dict) -> str:
        return str(line.get("id") or line.get("name") or "").strip().lower()

    for row in list(existing) + list(incoming):
        if not isinstance(row, dict):
            continue
        k = key(row)
        if not k or k in seen:
            continue
        seen.add(k)
        line = restock_line(row)
        line["keep_line"] = keep_line(line)
        line["status"] = str(row.get("status") or "held")
        out.append(line)
    return out


def hold_blocked(items: Iterable[dict], *, keep: Optional[dict] = None) -> dict:
    """Park blocked cart items on the Keep-shaped hold + retry queue."""
    incoming = [restock_line(x) for x in items if isinstance(x, dict)]
    current = load_hold()
    merged = _merge_hold_items(list(current.get("items") or []), incoming)
    keep_result = keep if isinstance(keep, dict) else write_keep_hold(merged)
    saved = save_hold(
        {
            "items": merged,
            "note_title": KEEP_NOTE_TITLE,
            "keep": {
                "ok": bool(keep_result.get("ok")),
                "blocked": bool(keep_result.get("blocked")),
                "reason": keep_result.get("reason"),
                "title": KEEP_NOTE_TITLE,
            },
        }
    )
    return {
        "ok": True,
        "held": len(incoming),
        "items": merged,
        "keep": keep_result,
        "hold": saved,
        "retry": True,
    }


def drop_hold_items(items: Iterable[dict]) -> dict:
    current = load_hold()
    drop = set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        line = restock_line(raw)
        drop.add(f"{line.get('venue')}|{str(line.get('id') or line.get('name') or '').strip().lower()}")
    kept = []
    for raw in current.get("items") or []:
        line = restock_line(raw)
        key = f"{line.get('venue')}|{str(line.get('id') or line.get('name') or '').strip().lower()}"
        if key not in drop:
            kept.append(line)
    return save_hold({"items": kept, "note_title": KEEP_NOTE_TITLE, "keep": current.get("keep")})


def restock_list(purchases: Optional[Iterable[dict]]) -> dict:
    """AC1: venue-tagged restock SoT. No Google Tasks."""
    lines = [restock_line(x) for x in (purchases or []) if isinstance(x, dict) and (x.get("name") or x.get("id"))]
    grouped = group_by_venue(lines)
    return {
        "ok": True,
        "source": "fitdash",
        "google_tasks": False,
        "checkout": False,
        "items": lines,
        "by_venue": grouped,
        "counts": {k: len(v) for k, v in grouped.items()},
        "sessions": {
            VENUE_WALMART: session_status(VENUE_WALMART),
            VENUE_COSTCO: session_status(VENUE_COSTCO),
        },
        "hold": load_hold(),
    }


def push_items(
    items: Optional[Iterable[dict]],
    *,
    walmart_add: Optional[Callable[[dict], dict]] = None,
    costco_add: Optional[Callable[[dict], dict]] = None,
    keep_write: Optional[Callable[[Iterable[dict]], dict]] = None,
) -> dict:
    """Route each item to Walmart cart, Costco list, or honest park. No GTs."""
    lines = [restock_line(x) for x in (items or []) if isinstance(x, dict)]
    walmart_fn = walmart_add or add_walmart_cart
    costco_fn = costco_add or add_costco_list
    keep_fn = keep_write or write_keep_hold
    results: List[dict] = []
    blocked: List[dict] = []
    added: List[dict] = []
    parked: List[dict] = []
    for line in lines:
        venue = line.get("venue") or venue_for_item(line)
        if venue == VENUE_WALMART:
            result = walmart_fn(line)
        elif venue == VENUE_COSTCO:
            result = costco_fn(line)
        else:
            result = park_other(line)
        result = result if isinstance(result, dict) else {"ok": False, "reason": "writer_error"}
        result.setdefault("item", line)
        result.setdefault("venue", venue)
        result["checkout"] = False
        results.append(result)
        if result.get("ok"):
            added.append(line)
        elif result.get("parked") or venue == VENUE_OTHER:
            parked.append(line)
        else:
            blocked.append(line)
    keep = None
    hold = None
    if blocked or parked:
        keep = keep_fn(blocked + parked)
        hold = hold_blocked(blocked + parked, keep=keep)
    if added:
        drop_hold_items(added)
    return {
        "ok": True,
        "google_tasks": False,
        "checkout": False,
        "added": added,
        "blocked": blocked,
        "parked": parked,
        "results": results,
        "keep": keep,
        "hold": hold,
        "retry": bool(blocked),
    }


def retry_held(
    *,
    walmart_add: Optional[Callable[[dict], dict]] = None,
    costco_add: Optional[Callable[[dict], dict]] = None,
    keep_write: Optional[Callable[[Iterable[dict]], dict]] = None,
) -> dict:
    """Retry cart writes for items on the Keep/hold checklist."""
    current = load_hold()
    items = [x for x in (current.get("items") or []) if isinstance(x, dict)]
    if not items:
        return {
            "ok": True,
            "google_tasks": False,
            "checkout": False,
            "added": [],
            "blocked": [],
            "parked": [],
            "results": [],
            "keep": current.get("keep"),
            "hold": current,
            "retry": False,
            "reason": "hold_empty",
        }
    return push_items(
        items,
        walmart_add=walmart_add,
        costco_add=costco_add,
        keep_write=keep_write,
    )


def confirm_received(
    items: Iterable[dict],
    *,
    inventory: Optional[dict] = None,
) -> dict:
    """Food-order SOP: mark pantry in-stock/qty. No GT complete. No checkout."""
    current = inventory if isinstance(inventory, dict) else {"ingredients": []}
    writes: List[dict] = []
    updated = current
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        line = restock_line(raw)
        name = line.get("name") or ""
        iid = line.get("id") or ""
        merged, info = apply_shopping_quest_stock(
            completed=True,
            group="shopping",
            title=f"Restock: {name}" if name else "",
            slug=f"buy-{iid}" if iid else "",
            inventory=updated,
        )
        if merged is not None:
            updated = merged
        writes.append(info)
    return {
        "ok": True,
        "google_tasks": False,
        "checkout": False,
        "inventory": updated,
        "writes": writes,
        "wrote": any(w.get("wrote") for w in writes),
    }


def purchases_from_board(today_board: Optional[dict]) -> List[dict]:
    board = today_board if isinstance(today_board, dict) else {}
    raw = board.get("purchases") or board.get("restock") or []
    return tag_items(x for x in raw if isinstance(x, dict))
