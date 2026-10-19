"""Token-locked agent pantry write: ``POST /api/agent/inventory/stock``.

Narrow write companion to the read-only agent API (``agent_read``, #1281).
Gated by its own env token, ``FITDASH_AGENT_WRITE_TOKEN``. The read token
(``FITDASH_AGENT_TOKEN``), the house ``FITDASH_SERVICE_TOKEN``, the
inventory-agent token, loopback and browser sessions do not authorize it.
The write token is not accepted anywhere else (each other route compares
against its own env var). Header parsing + constant-time compare are reused
from ``service_auth`` (#1028 / #1029).

Only ``in_stock`` and ``qty`` on existing items may change. No create, no
delete. Writes go through the same storage path as ``POST
/api/inventory/stock`` (``load_preview_inventory`` -> ``set_in_stock`` ->
``persist_inventory``) on the same pantry row ``GET /api/agent/inventory``
reads. Every field change appends one line to an append-only audit log
beside the pantry store: a Turso table ``nutrition_inventory_audit`` when
Turso is live, else JSONL (``FITDASH_INVENTORY_AUDIT_PATH`` or
``<DASHBOARD_CACHE_DIR>/nutrition_inventory_audit.jsonl``).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .service_auth import INVENTORY_AGENT_TOKEN_ENV, _token_match, service_token_from_headers

AGENT_WRITE_TOKEN_ENV = "FITDASH_AGENT_WRITE_TOKEN"
AGENT_WRITE_ALERT = "fitdash_agent_write_auth"
WRITE_PATH = "/api/agent/inventory/stock"
MAX_ITEMS = 100
ITEM_KEYS = frozenset({"id", "in_stock", "qty"})
BODY_KEYS = frozenset({"items", "updated_by"})
MAX_UPDATED_BY = 64
AUDIT_FILE = "nutrition_inventory_audit.jsonl"
ENSURE_AUDIT_SQL = """
CREATE TABLE IF NOT EXISTS nutrition_inventory_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  updated_by TEXT NOT NULL,
  item_id TEXT NOT NULL,
  field TEXT NOT NULL,
  old_value TEXT,
  new_value TEXT
)
"""
_OTHER_TOKEN_ENVS = ("FITDASH_AGENT_TOKEN", "FITDASH_SERVICE_TOKEN", INVENTORY_AGENT_TOKEN_ENV)


def utc_now_iso(now: Optional[datetime] = None) -> str:
    n = now or datetime.now(timezone.utc)
    return n.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- auth
def agent_write_gate(headers) -> Optional[Dict[str, Any]]:
    """None when allowed, else a 401 body. Never echoes the token."""
    expected = (os.environ.get(AGENT_WRITE_TOKEN_ENV) or "").strip()
    provided = service_token_from_headers(headers)
    shared = any(
        _token_match(expected, (os.environ.get(e) or "").strip()) for e in _OTHER_TOKEN_ENVS
    )
    if expected and not shared and provided and _token_match(provided, expected):
        return None
    if not expected or shared:
        reason = "unconfigured"
        message = f"{AGENT_WRITE_TOKEN_ENV} is not set (or reuses another token); agent writes are locked."
    elif not provided:
        reason = "missing"
        message = "Send Authorization: Bearer <agent write token>."
    else:
        reason = "invalid"
        message = "Agent write token rejected."
    return {"ok": False, "error": "auth_required", "reason": reason, "message": message, "alert": AGENT_WRITE_ALERT}


# ---------------------------------------------------------------- validation
def _bad(msg: str) -> ValueError:
    return ValueError(msg)


def validate_stock_body(body: Any, inventory: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str]:
    """Return (items, updated_by) or raise ValueError with a safe message."""
    if not isinstance(body, dict):
        raise _bad("body must be a JSON object")
    extra = sorted(set(body) - BODY_KEYS)
    if extra:
        raise _bad(f"unknown fields: {', '.join(map(str, extra))}")
    who = body.get("updated_by")
    if not isinstance(who, str) or not who.strip():
        raise _bad("updated_by (non-empty string) is required")
    who = who.strip()
    if len(who) > MAX_UPDATED_BY:
        raise _bad(f"updated_by longer than {MAX_UPDATED_BY} chars")
    items = body.get("items")
    if not isinstance(items, list):
        raise _bad("items must be a list")
    if not items:
        raise _bad("items must not be empty")
    if len(items) > MAX_ITEMS:
        raise _bad(f"at most {MAX_ITEMS} items per request")
    known = {
        str(i.get("id")).strip().lower()
        for i in (inventory.get("ingredients") or [])
        if isinstance(i, dict) and i.get("id") is not None
    }
    seen = set()
    out: List[Dict[str, Any]] = []
    for n, it in enumerate(items):
        if not isinstance(it, dict):
            raise _bad(f"items[{n}] must be an object")
        extra = sorted(set(it) - ITEM_KEYS)
        if extra:
            raise _bad(f"items[{n}] unknown fields: {', '.join(map(str, extra))}")
        iid = it.get("id")
        if not isinstance(iid, str) or not iid.strip():
            raise _bad(f"items[{n}].id must be a non-empty string")
        key = iid.strip().lower()
        if key not in known:
            raise _bad(f"items[{n}].id is not a known item")
        if key in seen:
            raise _bad(f"items[{n}].id is duplicated")
        seen.add(key)
        if "in_stock" not in it and "qty" not in it:
            raise _bad(f"items[{n}] needs in_stock or qty")
        if "in_stock" in it and not isinstance(it["in_stock"], bool):
            raise _bad(f"items[{n}].in_stock must be a boolean")
        if "qty" in it:
            q = it["qty"]
            if q is not None and (isinstance(q, bool) or not isinstance(q, (int, float))):
                raise _bad(f"items[{n}].qty must be a number or null")
            if isinstance(q, float) and q != q:  # NaN
                raise _bad(f"items[{n}].qty must be a number or null")
            if q is not None and q < 0:
                raise _bad(f"items[{n}].qty must be >= 0")
        out.append({k: it[k] for k in ("in_stock", "qty") if k in it} | {"key": key})
    return out, who


# ---------------------------------------------------------------- apply
def apply_stock_updates(
    inventory: Dict[str, Any], items: List[Dict[str, Any]], updated_by: str, ts: str
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Pure. Returns (new_inventory, touched_items, audit_lines)."""
    from copy import deepcopy

    from .nutrition_planner import set_in_stock

    inv = deepcopy(inventory)
    by_key = {
        str(i.get("id")).strip().lower(): i
        for i in inv.get("ingredients") or []
        if isinstance(i, dict) and i.get("id") is not None
    }
    touched: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []
    for it in items:
        ing = by_key[it["key"]]
        line: Dict[str, Any] = {"ts": ts, "updated_by": updated_by, "id": ing.get("id")}
        if "in_stock" in it:
            old = ing.get("in_stock") if isinstance(ing.get("in_stock"), bool) else None
            if old is not it["in_stock"]:
                inv = set_in_stock(inv, str(ing.get("id")), it["in_stock"])
                by_key = {
                    str(i.get("id")).strip().lower(): i
                    for i in inv.get("ingredients") or []
                    if isinstance(i, dict) and i.get("id") is not None
                }
                ing = by_key[it["key"]]
            line["in_stock"] = {"old": old, "new": it["in_stock"]}
        if "qty" in it:
            line["qty"] = {"old": ing.get("qty"), "new": it["qty"]}
            ing["qty"] = it["qty"]
        ing["updated_at"] = ts
        ing["updated_by"] = updated_by
        touched.append(
            {
                "id": ing.get("id"),
                "name": ing.get("name"),
                "in_stock": ing.get("in_stock") if isinstance(ing.get("in_stock"), bool) else None,
                "qty": ing.get("qty"),
                "updated_at": ts,
                "updated_by": updated_by,
            }
        )
        audit.append(line)
    inv["updated_at"] = ts
    return inv, touched, audit


# ---------------------------------------------------------------- storage
def audit_jsonl_path() -> Path:
    env = (os.environ.get("FITDASH_INVENTORY_AUDIT_PATH") or "").strip()
    if env:
        return Path(env)
    from .dashboard_cache import CACHE_DIR

    return Path(CACHE_DIR) / AUDIT_FILE


def append_audit_jsonl(lines: List[Dict[str, Any]], path: Optional[Path] = None) -> None:
    p = path or audit_jsonl_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:  # append-only
        for ln in lines:
            fh.write(json.dumps(ln, separators=(",", ":"), sort_keys=True) + "\n")


def append_audit_turso(lines: List[Dict[str, Any]]) -> None:
    from .turso_http import connect

    with connect() as conn:
        conn.execute(ENSURE_AUDIT_SQL)
        for ln in lines:
            for field in ("in_stock", "qty"):
                if field in ln:
                    conn.execute(
                        "INSERT INTO nutrition_inventory_audit(ts, updated_by, item_id, field, old_value, new_value)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (ln["ts"], ln["updated_by"], str(ln["id"]), field,
                         json.dumps(ln[field]["old"]), json.dumps(ln[field]["new"])),
                    )


class Store:
    """Injectable pantry access so contract tests run without Turso / GitHub."""

    def __init__(
        self,
        load: Optional[Callable[[], Dict[str, Any]]] = None,
        persist: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        append_audit: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
        file_client_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.load = load or _default_load
        self.persist = persist or (lambda inv: _default_persist(inv, file_client_factory))
        self.append_audit = append_audit or _default_audit


def _default_load() -> Dict[str, Any]:
    from .inventory_store import load_preview_inventory

    inv, _src = load_preview_inventory("")  # same row GET /api/agent/inventory reads
    return inv


def _default_persist(inv: Dict[str, Any], file_client_factory: Optional[Callable[[], Any]] = None) -> Dict[str, Any]:
    from .inventory_store import persist_inventory
    from .turso_http import turso_enabled

    client = None
    if not turso_enabled() and file_client_factory is not None:
        client = file_client_factory()
    write = persist_inventory(inv, "", file_client=client, message="nutrition: agent stock write")
    if not write.get("ok"):
        raise RuntimeError("inventory write failed")
    return write.get("inventory") or inv


def _default_audit(lines: List[Dict[str, Any]]) -> None:
    from .turso_http import turso_enabled

    if turso_enabled():
        append_audit_turso(lines)
    else:
        append_audit_jsonl(lines)


# ---------------------------------------------------------------- handler
def respond_agent_stock_write(
    headers, body: Any, store: Store, *, now: Optional[datetime] = None
) -> Tuple[int, Dict[str, Any]]:
    denied = agent_write_gate(headers)
    if denied:
        return 401, denied
    current = store.load() or {"ingredients": []}
    try:
        items, who = validate_stock_body(body, current)
    except ValueError as e:
        return 400, {"ok": False, "error": "bad_request", "message": str(e)}
    ts = utc_now_iso(now)
    updated, touched, audit = apply_stock_updates(current, items, who, ts)
    saved = store.persist(updated)
    store.append_audit(audit)
    saved_by = {
        str(i.get("id")).strip().lower(): i for i in (saved or {}).get("ingredients") or [] if isinstance(i, dict)
    }
    for t in touched:  # echo what actually landed
        s = saved_by.get(str(t["id"]).strip().lower())
        if s:
            t["in_stock"] = s.get("in_stock") if isinstance(s.get("in_stock"), bool) else None
            t["qty"] = s.get("qty")
    return 200, {"ok": True, "generated_at": utc_now_iso(now), "as_of": ts, "updated_by": who, "items": touched}
