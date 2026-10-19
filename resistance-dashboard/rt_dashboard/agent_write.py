"""Token-locked agent pantry write: ``POST /api/agent/inventory/stock``.

Narrow write companion to the read-only agent API (``agent_read``, #1281).

Auth reuses the house pantry-write model (``service_auth``): a Chris-bound
``FITDASH_INVENTORY_AGENT_TOKEN`` whose tenant is
``FITDASH_INVENTORY_AGENT_USER_ID`` (``inventory_agent_principal``). The
read token (``FITDASH_AGENT_TOKEN``), the house ``FITDASH_SERVICE_TOKEN``,
loopback and browser sessions do not authorize it. Writes land on that
user's own pantry row (``inventory_principal_uid``, never ``default``).

Only ``in_stock`` and ``qty`` on existing items may change. No create, no
delete, no other field. ``stock`` (in|low|out) is kept consistent with the
requested ``in_stock``: ``false`` -> out; ``true`` keeps an existing in/low
and only lifts ``out`` to ``in``.

Audit first: when Turso is live, the audit rows and the pantry upsert run in
one Turso batch transaction (BEGIN .. COMMIT, ROLLBACK on any failure), so a
save never lands without its audit rows. When Turso is dark (file SoT) the
audit lines are appended to JSONL (``FITDASH_INVENTORY_AUDIT_PATH`` or
``<DASHBOARD_CACHE_DIR>/nutrition_inventory_audit.jsonl``) *before* the
save; if the save then fails a ``status: aborted`` line with the same
``txn`` id is appended, so the log never misses a landed change.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .service_auth import (
    INVENTORY_AGENT_TOKEN_ENV,
    INVENTORY_AGENT_USER_ID_ENV,
    _token_match,
    inventory_agent_principal,
    service_token_from_headers,
)

AGENT_WRITE_ALERT = "fitdash_agent_write_auth"
WRITE_PATH = "/api/agent/inventory/stock"
MAX_ITEMS = 100
ITEM_KEYS = frozenset({"id", "in_stock", "qty"})
BODY_KEYS = frozenset({"items", "updated_by"})
MAX_UPDATED_BY = 64
AUDIT_FILE = "nutrition_inventory_audit.jsonl"
AUDIT_FIELDS = ("in_stock", "stock", "qty")
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
# Read / house tokens must never double as the pantry-write token.
_OTHER_TOKEN_ENVS = ("FITDASH_AGENT_TOKEN", "FITDASH_SERVICE_TOKEN")


def utc_now_iso(now: Optional[datetime] = None) -> str:
    n = now or datetime.now(timezone.utc)
    return n.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- auth
def _denied(reason: str, message: str) -> Dict[str, Any]:
    return {"ok": False, "error": "auth_required", "reason": reason, "message": message, "alert": AGENT_WRITE_ALERT}


def agent_write_principal(headers) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """(principal, None) when allowed, else (None, 401 body). Never echoes a token."""
    expected = (os.environ.get(INVENTORY_AGENT_TOKEN_ENV) or "").strip()
    bound = (os.environ.get(INVENTORY_AGENT_USER_ID_ENV) or "").strip()
    shared = any(_token_match(expected, (os.environ.get(e) or "").strip()) for e in _OTHER_TOKEN_ENVS)
    if not expected or not bound or bound == "default" or shared:
        return None, _denied(
            "unconfigured",
            f"{INVENTORY_AGENT_TOKEN_ENV} / {INVENTORY_AGENT_USER_ID_ENV} not set (or token reused); agent writes are locked.",
        )
    if not service_token_from_headers(headers):
        return None, _denied("missing", f"Send Authorization: Bearer <{INVENTORY_AGENT_TOKEN_ENV}>.")
    principal = inventory_agent_principal(headers)
    if not principal:
        return None, _denied("invalid", "Inventory agent token rejected.")
    return principal, None


def agent_write_gate(headers) -> Optional[Dict[str, Any]]:
    """None when allowed, else a 401 body."""
    return agent_write_principal(headers)[1]


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
def _stock_of(ing: Dict[str, Any]) -> str:
    from .nutrition_planner import normalize_stock

    return normalize_stock(ing)


def stock_for_request(current: str, in_stock: bool) -> str:
    """Requested in_stock -> stock, keeping in/low when already usable."""
    if not in_stock:
        return "out"
    return current if current in ("in", "low") else "in"


def apply_stock_updates(
    inventory: Dict[str, Any], items: List[Dict[str, Any]], updated_by: str, ts: str
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Pure. Returns (new_inventory, touched_items, audit_lines)."""
    from copy import deepcopy

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
            old_stock = _stock_of(ing)
            old = ing.get("in_stock") if isinstance(ing.get("in_stock"), bool) else None
            new_stock = stock_for_request(old_stock, it["in_stock"])
            ing["stock"] = new_stock
            ing["in_stock"] = new_stock != "out"
            line["in_stock"] = {"old": old, "new": ing["in_stock"]}
            if new_stock != old_stock:
                line["stock"] = {"old": old_stock, "new": new_stock}
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
                "stock": ing.get("stock"),
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


def audit_sql_rows(lines: List[Dict[str, Any]]) -> List[Tuple[str, Tuple[Any, ...]]]:
    out = []
    for ln in lines:
        for field in AUDIT_FIELDS:
            if field in ln:
                out.append((
                    "INSERT INTO nutrition_inventory_audit(ts, updated_by, item_id, field, old_value, new_value)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (ln["ts"], ln["updated_by"], str(ln["id"]), field,
                     json.dumps(ln[field]["old"]), json.dumps(ln[field]["new"])),
                ))
    return out


def commit_turso(user_id: str, inv: Dict[str, Any], lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Audit rows first, then the pantry upsert, in one transaction."""
    from .inventory_store import ENSURE_INVENTORY_SQL, _as_inventory, _turso_get_inventory, inventory_principal_uid
    from .turso_http import connect

    uid = inventory_principal_uid(user_id)
    blob = json.dumps(_as_inventory(inv), separators=(",", ":"))
    now = utc_now_iso()
    stmts = audit_sql_rows(lines) + [(
        "INSERT INTO nutrition_inventory(user_id, payload, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(user_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at",
        (uid, blob, now),
    )]
    with connect() as conn:
        conn.execute(ENSURE_INVENTORY_SQL)
        conn.execute(ENSURE_AUDIT_SQL)
        conn.execute_transaction(stmts)
    saved = _turso_get_inventory(uid)
    if not isinstance(saved, dict) or not isinstance(saved.get("ingredients"), list):
        raise RuntimeError("turso write not visible on readback")
    return saved


def commit_file(
    user_id: str, inv: Dict[str, Any], lines: List[Dict[str, Any]],
    file_client_factory: Optional[Callable[[], Any]] = None,
    audit_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Turso dark: JSONL audit first, then the file save; abort marker on failure."""
    from .inventory_store import persist_inventory

    txn = uuid.uuid4().hex
    append_audit_jsonl([dict(ln, txn=txn, status="pending") for ln in lines], audit_path)
    try:
        client = file_client_factory() if file_client_factory else None
        write = persist_inventory(inv, user_id, file_client=client, message="nutrition: agent stock write")
        if not write.get("ok"):
            raise RuntimeError("inventory write failed")
    except Exception:
        append_audit_jsonl([{"ts": utc_now_iso(), "txn": txn, "status": "aborted"}], audit_path)
        raise
    return write.get("inventory") or inv


class Store:
    """Injectable pantry access so contract tests run without Turso / GitHub.

    ``load(user_id)`` reads that user's row. ``commit(user_id, inv, audit)``
    must record the audit lines before (or atomically with) the save.
    """

    def __init__(
        self,
        load: Optional[Callable[[str], Dict[str, Any]]] = None,
        commit: Optional[Callable[[str, Dict[str, Any], List[Dict[str, Any]]], Dict[str, Any]]] = None,
        file_client_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.load = load or _default_load
        self.commit = commit or (lambda uid, inv, lines: _default_commit(uid, inv, lines, file_client_factory))


def _default_load(user_id: str) -> Dict[str, Any]:
    from .inventory_store import inventory_principal_uid, load_preview_inventory

    inv, _src = load_preview_inventory(inventory_principal_uid(user_id))
    return inv


def _default_commit(user_id, inv, lines, file_client_factory=None) -> Dict[str, Any]:
    from .turso_http import turso_enabled

    if turso_enabled():
        return commit_turso(user_id, inv, lines)
    return commit_file(user_id, inv, lines, file_client_factory)


# ---------------------------------------------------------------- handler
def respond_agent_stock_write(
    headers, body: Any, store: Store, *, now: Optional[datetime] = None
) -> Tuple[int, Dict[str, Any]]:
    principal, denied = agent_write_principal(headers)
    if denied:
        return 401, denied
    uid = str(principal.get("user_id") or "").strip()
    current = store.load(uid) or {"ingredients": []}
    try:
        items, who = validate_stock_body(body, current)
    except ValueError as e:
        return 400, {"ok": False, "error": "bad_request", "message": str(e)}
    ts = utc_now_iso(now)
    updated, touched, audit = apply_stock_updates(current, items, who, ts)
    saved = store.commit(uid, updated, audit)
    saved_by = {
        str(i.get("id")).strip().lower(): i for i in (saved or {}).get("ingredients") or [] if isinstance(i, dict)
    }
    for t in touched:  # echo what actually landed
        s = saved_by.get(str(t["id"]).strip().lower())
        if s:
            t["in_stock"] = s.get("in_stock") if isinstance(s.get("in_stock"), bool) else None
            t["stock"] = s.get("stock")
            t["qty"] = s.get("qty")
    return 200, {"ok": True, "generated_at": utc_now_iso(now), "as_of": ts, "updated_by": who, "items": touched}
