"""DoorDash meal restock: inventory gaps → shopping list → dd-cli order flow.

Coach / dashboard path:
  1. Inspect in-stock inventory vs meal plan + staple suggestions
  2. Build a grocery shopping list of missing items
  3. Drive ``dd-cli`` (DoorDash CLI) to search a store, fill a cart, and
     either return a checkout URL or (only with explicit confirm) submit.

Safety: live cart mutations require ``execute=True``. Actual payment submit
requires ``confirm=True``. Default is preview / dry-run only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Sequence


# Preferred store search when the user has not pinned a store_id.
DEFAULT_STORE_QUERY = "grocery"
DEFAULT_MAX_ITEMS = 12


def _slug(name: str) -> str:
    s = "".join(ch.lower() if ch.isalnum() else "-" for ch in (name or "").strip())
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-") or "item"


def _norm_name(name: str) -> str:
    return " ".join(str(name or "").lower().split())


def _ingredient_rows(inventory: Optional[dict]) -> List[dict]:
    inv = inventory or {}
    out: List[dict] = []
    for raw in inv.get("ingredients") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        in_stock = raw.get("in_stock", True)
        if isinstance(in_stock, str):
            in_stock = in_stock.strip().lower() not in ("0", "false", "no", "out")
        out.append(
            {
                "id": str(raw.get("id") or _slug(name)),
                "name": name,
                "category": str(raw.get("category") or "other"),
                "serving_label": str(raw.get("serving_label") or "1 serving"),
                "in_stock": bool(in_stock),
                "calories": raw.get("calories"),
                "protein_g": raw.get("protein_g"),
                "carbs_g": raw.get("carbs_g"),
                "fat_g": raw.get("fat_g"),
            }
        )
    return out


def build_meal_restock_list(
    inventory: Optional[dict],
    meal_plan: Optional[dict] = None,
    inventory_suggestions: Optional[dict] = None,
    *,
    max_items: int = DEFAULT_MAX_ITEMS,
) -> Dict[str, Any]:
    """Identify grocery items missing for planned meals / macro coverage.

    Priority:
      1. Inventory marked out of stock (restock — already known pantry items)
      2. Inventory suggestions (restock|add) from the staple recommender
      3. When meal plan is empty / thin, emphasize protein staples still missing
    """
    max_items = max(1, int(max_items or DEFAULT_MAX_ITEMS))
    rows = _ingredient_rows(inventory)
    stocked = [r for r in rows if r["in_stock"]]
    out_of_stock = [r for r in rows if not r["in_stock"]]
    mp = meal_plan or {}
    sug = inventory_suggestions or {}

    stocked_names = {_norm_name(r["name"]) for r in stocked}
    plan_items = list(mp.get("items") or [])
    plan_empty = not plan_items and not (mp.get("meals") or [])
    stocked_count = int(mp.get("stocked_count") if mp.get("stocked_count") is not None else len(stocked))

    shopping: List[dict] = []
    seen: set = set()

    def _push(item: dict) -> None:
        name = str(item.get("name") or "").strip()
        if not name:
            return
        key = _norm_name(name)
        if key in seen:
            return
        if key in stocked_names and item.get("action") != "restock":
            # Already in stock — skip net-new adds
            return
        seen.add(key)
        shopping.append(
            {
                "name": name,
                "id": item.get("id") or _slug(name),
                "action": item.get("action") or "add",
                "reason": item.get("reason") or "",
                "category": item.get("category") or "other",
                "qty": int(item.get("qty") or 1),
                "query": item.get("query") or name,
                "calories": item.get("calories"),
                "protein_g": item.get("protein_g"),
                "source": item.get("source") or "inventory",
            }
        )

    # 1) Explicit out-of-stock pantry
    for r in out_of_stock:
        reason = "Marked out of stock — needed back in pantry for meal plans."
        if plan_empty:
            reason = "Out of stock and meal plan has no stocked items — restock to unlock meals."
        _push(
            {
                **r,
                "action": "restock",
                "reason": reason,
                "source": "inventory_out",
                "qty": 1,
            }
        )

    # 2) Coach / staple suggestions
    for s in (sug.get("suggestions") if isinstance(sug, dict) else None) or []:
        if not isinstance(s, dict):
            continue
        action = str(s.get("action") or "add")
        _push(
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "action": action,
                "reason": s.get("reason") or "Suggested staple for remaining macros.",
                "category": s.get("category") or "other",
                "calories": s.get("calories"),
                "protein_g": s.get("protein_g"),
                "source": "inventory_suggestions",
                "qty": 1,
            }
        )

    # 3) If still empty but plan blocked, seed generic high-value staples
    if not shopping and (plan_empty or stocked_count == 0):
        for name, cat, reason in (
            ("Chicken breast", "protein", "High-protein staple to unlock meal plans."),
            ("Plain nonfat Greek yogurt", "protein", "Fast protein staple for remaining macros."),
            ("Eggs", "protein", "Flexible protein staple."),
            ("Rice", "carb", "Carb staple for training days."),
            ("Broccoli", "veg", "Low-cal volume vegetable."),
        ):
            if _norm_name(name) in stocked_names:
                continue
            _push(
                {
                    "name": name,
                    "category": cat,
                    "action": "add",
                    "reason": reason,
                    "source": "default_staples",
                    "qty": 1,
                }
            )

    shopping = shopping[:max_items]

    summary_bits = []
    if out_of_stock:
        summary_bits.append(f"{len(out_of_stock)} out of stock")
    if plan_empty:
        summary_bits.append("meal plan empty (needs stock)")
    elif plan_items:
        summary_bits.append(f"meal plan has {len(plan_items)} stocked items")
    if shopping:
        summary_bits.append(f"{len(shopping)} to order")
    else:
        summary_bits.append("pantry covers planned meals")

    return {
        "items": shopping,
        "count": len(shopping),
        "stocked_count": stocked_count,
        "out_of_stock_count": len(out_of_stock),
        "meal_plan_empty": plan_empty,
        "meal_plan_message": str(mp.get("message") or ""),
        "suggestions_summary": (sug.get("summary") if isinstance(sug, dict) else None),
        "summary": " · ".join(summary_bits),
        "needs_order": len(shopping) > 0,
        "store_query_default": DEFAULT_STORE_QUERY,
    }


def dd_cli_binary() -> str:
    return os.environ.get("FITDASH_DD_CLI") or shutil.which("dd-cli") or "dd-cli"


def dd_cli_available() -> bool:
    path = dd_cli_binary()
    if os.path.isabs(path) or os.sep in path:
        return os.path.isfile(path) and os.access(path, os.X_OK)
    return shutil.which(path) is not None


def run_dd_cli(
    args: Sequence[str],
    *,
    json_output: bool = True,
    timeout_sec: float = 90.0,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Run ``dd-cli`` with optional ``--json-output``. Never raises."""
    binary = dd_cli_binary()
    cmd: List[str] = [binary]
    if json_output:
        cmd.append("--json-output")
    cmd.extend(str(a) for a in args)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "cmd": cmd,
            "stdout": "",
            "stderr": "",
            "data": {"planned_cmd": cmd},
            "returncode": 0,
        }
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env={**os.environ},
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "dry_run": False,
            "cmd": cmd,
            "error": f"dd-cli not found ({binary}). Install DoorDash CLI and ensure it is on PATH.",
            "stdout": "",
            "stderr": "",
            "data": None,
            "returncode": 127,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "dry_run": False,
            "cmd": cmd,
            "error": f"dd-cli timed out after {timeout_sec}s",
            "stdout": "",
            "stderr": "",
            "data": None,
            "returncode": 124,
        }

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    data: Any = None
    if stdout:
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            data = {"raw_text": stdout}

    ok = proc.returncode == 0
    err = None
    if not ok:
        err = stderr or stdout or f"dd-cli exit {proc.returncode}"
        # Common credential gate
        low = (err or "").lower()
        if "credential" in low or "sign in" in low or "login" in low:
            err = (
                f"{err} — run `dd-cli login` in a terminal (waitlist-approved DoorDash account)."
            )

    return {
        "ok": ok,
        "dry_run": False,
        "cmd": cmd,
        "stdout": stdout,
        "stderr": stderr,
        "data": data,
        "returncode": proc.returncode,
        "error": err,
    }


def _item_queries(items: Sequence[dict]) -> List[str]:
    out: List[str] = []
    for it in items:
        q = str(it.get("query") or it.get("name") or "").strip()
        if q:
            out.append(q)
    return out


def plan_dd_cli_commands(
    items: Sequence[dict],
    *,
    store_query: str = DEFAULT_STORE_QUERY,
    store_id: Optional[str] = None,
    tip_cents: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Human-readable command plan for logging / dry-run.

    Exact flags can vary by dd-cli version; live execution probes help text and
    falls back to documented command names from the DoorDash CLI release notes.
    """
    queries = _item_queries(items)
    list_text = ", ".join(queries)
    steps: List[Dict[str, Any]] = [
        {
            "step": "auth_check",
            "args": ["--version"],
            "purpose": "Verify dd-cli is installed",
        },
        {
            "step": "find_store",
            "args": (
                ["find-nearby-stores", "--query", store_query]
                if not store_id
                else ["store-details", "--store-id", store_id]
            ),
            "purpose": "Locate a nearby grocery/retail store",
        },
        {
            "step": "build_list",
            "args": ["build-grocery-list", "--query", list_text],
            "purpose": "Build DoorDash grocery list from missing ingredients",
        },
    ]
    for q in queries:
        steps.append(
            {
                "step": "find_item",
                "args": ["find-items", "--query", q]
                + (["--store-id", store_id] if store_id else []),
                "purpose": f"Search catalog for {q}",
            }
        )
    steps.append(
        {
            "step": "cart_show",
            "args": ["cart", "show"],
            "purpose": "Inspect active cart after adds",
        }
    )
    steps.append(
        {
            "step": "preview",
            "args": ["order", "preview"],
            "purpose": "Preview pricing / fees before pay",
        }
    )
    checkout_args = ["order", "checkout-url"]
    if tip_cents is not None:
        checkout_args.extend(["--tip-cents", str(int(tip_cents))])
    steps.append(
        {
            "step": "checkout_url",
            "args": checkout_args,
            "purpose": "Browser checkout fallback URL",
        }
    )
    steps.append(
        {
            "step": "submit",
            "args": ["order", "submit"],
            "purpose": "Place order (only when confirm=True)",
            "requires_confirm": True,
        }
    )
    return steps


def execute_restock_order(
    restock_list: dict,
    *,
    execute: bool = False,
    confirm: bool = False,
    store_query: Optional[str] = None,
    store_id: Optional[str] = None,
    tip_cents: Optional[int] = None,
    max_find_items: int = 8,
) -> Dict[str, Any]:
    """Run or dry-run the DoorDash restock flow for a shopping list.

    Parameters
    ----------
    execute:
        When False (default), only return the shopping list + planned commands.
        When True, invoke dd-cli for store/list/cart/preview/checkout-url.
    confirm:
        When True with execute, attempt ``order submit`` after preview.
        Payment is never attempted without this flag.
    """
    items = list((restock_list or {}).get("items") or [])
    store_q = (store_query or restock_list.get("store_query_default") or DEFAULT_STORE_QUERY).strip()
    plan = plan_dd_cli_commands(
        items, store_query=store_q, store_id=store_id, tip_cents=tip_cents
    )

    result: Dict[str, Any] = {
        "ok": True,
        "execute": bool(execute),
        "confirm": bool(confirm),
        "dd_cli_available": dd_cli_available(),
        "dd_cli_binary": dd_cli_binary(),
        "restock": restock_list,
        "planned_steps": plan,
        "steps_run": [],
        "cart_uuid": None,
        "checkout_url": None,
        "store_id": store_id,
        "message": "",
    }

    if not items:
        result["message"] = "Nothing to order — inventory covers planned meals."
        result["ok"] = True
        return result

    if not execute:
        result["message"] = (
            f"Preview only: {len(items)} item(s) for DoorDash restock. "
            "POST with execute=true to drive dd-cli (confirm=true to submit payment)."
        )
        result["dry_run_commands"] = [
            " ".join([dd_cli_binary(), "--json-output", *s["args"]]) for s in plan
        ]
        return result

    if not dd_cli_available():
        result["ok"] = False
        result["message"] = "dd-cli not installed or not on PATH."
        result["error"] = result["message"]
        return result

    # --- live execution (best-effort against dd-cli surface) ---------------
    steps_run: List[dict] = []

    def _run(step_name: str, args: Sequence[str]) -> dict:
        r = run_dd_cli(args, json_output=True, dry_run=False)
        entry = {"step": step_name, **r}
        steps_run.append(entry)
        return entry

    ver = subprocess_version()
    steps_run.append({"step": "version", **ver})

    if store_id:
        det = _run("store_details", ["store-details", "--store-id", store_id])
    else:
        det = _run("find_nearby_stores", ["find-nearby-stores", "--query", store_q])
        sid = _extract_store_id(det.get("data"))
        if sid:
            store_id = sid
            result["store_id"] = sid

    queries = _item_queries(items)[: max(1, int(max_find_items))]
    list_arg = "; ".join(queries)
    built = _run("build_grocery_list", ["build-grocery-list", "--query", list_arg])
    if not built.get("ok"):
        for q in queries:
            args = ["find-items", "--query", q]
            if store_id:
                args.extend(["--store-id", store_id])
            _run(f"find_items:{q}", args)

    cart = _run("cart_show", ["cart", "show"])
    cart_uuid = _extract_cart_uuid(cart.get("data"))
    if cart_uuid:
        result["cart_uuid"] = cart_uuid

    _run("order_preview", ["order", "preview"])
    checkout_args = ["order", "checkout-url"]
    if cart_uuid:
        checkout_args.extend(["--cart-uuid", cart_uuid])
    if tip_cents is not None:
        checkout_args.extend(["--tip-cents", str(int(tip_cents))])
    checkout = _run("checkout_url", checkout_args)
    result["checkout_url"] = _extract_checkout_url(
        checkout.get("data"), checkout.get("stdout") or ""
    )

    if confirm:
        submit_args = ["order", "submit"]
        if cart_uuid:
            submit_args.extend(["--cart-uuid", cart_uuid])
        submitted = _run("order_submit", submit_args)
        result["submitted"] = bool(submitted.get("ok"))
        if not submitted.get("ok"):
            result["ok"] = False
            result["error"] = submitted.get("error") or "order submit failed"
            result["message"] = (
                "Cart/checkout prepared but submit failed. "
                "Use checkout_url if present, or re-run after `dd-cli login`."
            )
        else:
            result["message"] = "Order submit reported success via dd-cli."
    else:
        auth_errors = [
            s
            for s in steps_run
            if not s.get("ok")
            and (
                "login" in str(s.get("error") or "").lower()
                or "credential" in str(s.get("error") or "").lower()
            )
        ]
        if auth_errors and not result.get("checkout_url"):
            result["ok"] = False
            result["error"] = auth_errors[0].get("error")
            result["message"] = (
                "dd-cli needs sign-in. Run `dd-cli login`, then retry execute=true."
            )
        else:
            result["message"] = (
                f"Executed DoorDash restock steps for {len(items)} item(s) "
                "(preview/checkout only; pass confirm=true to submit payment)."
            )

    result["steps_run"] = steps_run
    if result.get("ok") is not False:
        result["ok"] = True
    return result


def subprocess_version() -> Dict[str, Any]:
    binary = dd_cli_binary()
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        out = (proc.stdout or proc.stderr or "").strip()
        return {
            "ok": proc.returncode == 0,
            "cmd": [binary, "--version"],
            "stdout": out,
            "stderr": (proc.stderr or "").strip(),
            "returncode": proc.returncode,
            "data": {"version": out},
            "error": None if proc.returncode == 0 else out,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "cmd": [binary, "--version"],
            "error": str(e),
            "stdout": "",
            "stderr": "",
            "returncode": 1,
            "data": None,
        }


def _walk_dicts(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_dicts(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from _walk_dicts(x)


def _extract_store_id(data: Any) -> Optional[str]:
    if data is None:
        return None
    for d in _walk_dicts(data):
        for key in ("store_id", "storeId", "id"):
            if key in d and d[key] and ("store" in key.lower() or key == "id"):
                # Prefer explicit store keys
                if key in ("store_id", "storeId"):
                    return str(d[key])
        if "store_id" in d and d["store_id"]:
            return str(d["store_id"])
        if "storeId" in d and d["storeId"]:
            return str(d["storeId"])
    # looser: first dict with both name and id under stores list
    if isinstance(data, dict):
        for key in ("stores", "results", "data", "items"):
            arr = data.get(key)
            if isinstance(arr, list) and arr and isinstance(arr[0], dict):
                cand = arr[0].get("store_id") or arr[0].get("storeId") or arr[0].get("id")
                if cand:
                    return str(cand)
    return None


def _extract_cart_uuid(data: Any) -> Optional[str]:
    if data is None:
        return None
    for d in _walk_dicts(data):
        for key in ("cart_uuid", "cartUuid", "cart_id", "cartId"):
            if d.get(key):
                return str(d[key])
    return None


def _extract_checkout_url(data: Any, stdout: str = "") -> Optional[str]:
    if isinstance(data, dict):
        for d in _walk_dicts(data):
            for key in ("checkout_url", "checkoutUrl", "url", "browser_url"):
                val = d.get(key)
                if isinstance(val, str) and val.startswith("http"):
                    return val
    if stdout:
        for token in stdout.split():
            if token.startswith("http://") or token.startswith("https://"):
                return token.strip().rstrip(").,]")
    return None


def coach_doordash_block(
    inventory: Optional[dict],
    meal_plan: Optional[dict],
    inventory_suggestions: Optional[dict],
) -> Dict[str, Any]:
    """Compact coach payload for Today / Nutrition coach."""
    restock = build_meal_restock_list(
        inventory, meal_plan, inventory_suggestions
    )
    return {
        **restock,
        "dd_cli_available": dd_cli_available(),
        "how_to": (
            "Ask coach: “order missing groceries” or use Restock via DoorDash. "
            "Requires `dd-cli login` once. Preview first; confirm to place the order."
        ),
    }
