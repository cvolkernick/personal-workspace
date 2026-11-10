"""FitDash MCP server: stdio wrapper over the FitDash agent API.

Slice 2 of #1281. The section tools issue a single GET against
/api/agent/<section> and return the JSON body unchanged (including
generated_at / as_of).

Coaching targets (#1370) add one read and one narrow write, both with the
same agent token:

  get_coaching_targets  GET  /api/agent/coaching_targets
  set_coaching_target   POST /api/agent/coaching_targets

A settings override wins. Otherwise the phase calendar (coach rules when
the calendar does not set that target). steps must be 1000–30000.

Env:
  FITDASH_AGENT_TOKEN       bearer token (else read from FITDASH_AGENT_TOKEN_FILE)
  FITDASH_AGENT_TOKEN_FILE  default /workspace/.fitdash_agent_token
  FITDASH_API_BASE          default https://prism-gateway.tailb1085a.ts.net:10001
  FITDASH_PROXY             e.g. socks5h://127.0.0.1:1055 (remote DNS); empty = direct
  FITDASH_TIMEOUT           seconds, default 20
  FITDASH_INVENTORY_AGENT_TOKEN       pantry-write bearer (else ..._FILE); only
  FITDASH_INVENTORY_AGENT_TOKEN_FILE  update_pantry uses it. Default
                                      /workspace/.fitdash_inventory_agent_token
"""
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE = "https://prism-gateway.tailb1085a.ts.net:10001"
DEFAULT_TOKEN_FILE = "/workspace/.fitdash_agent_token"
DEFAULT_WRITE_TOKEN_FILE = "/workspace/.fitdash_inventory_agent_token"
STOCK_FILTERS = ("all", "in", "low", "out")
PANTRY_WRITE_PATHS = ("inventory/items", "inventory/stock")
MAX_PANTRY_OPS = 100
SECTIONS = ("targets", "inventory", "logs", "plan", "training", "recovery")
MAX_LOG_DAYS = 92


class FitDashError(RuntimeError):
    """Error safe to surface to the MCP client (never contains the token)."""


def _base_url() -> str:
    return os.environ.get("FITDASH_API_BASE", DEFAULT_BASE).rstrip("/")


def _proxy() -> str | None:
    p = os.environ.get("FITDASH_PROXY", "").strip()
    if not p:
        return None
    # httpx's SOCKS transport already resolves hostnames on the proxy side,
    # so socks5h:// (curl --socks5-hostname) maps to socks5://.
    if p.startswith("socks5h://"):
        p = "socks5://" + p[len("socks5h://"):]
    return p


def _token() -> str:
    tok = os.environ.get("FITDASH_AGENT_TOKEN", "").strip()
    if tok:
        return tok
    path = Path(os.environ.get("FITDASH_AGENT_TOKEN_FILE", DEFAULT_TOKEN_FILE))
    try:
        tok = path.read_text().strip()
    except OSError:
        tok = ""
    if not tok:
        raise FitDashError(
            "No FitDash agent token: set FITDASH_AGENT_TOKEN or FITDASH_AGENT_TOKEN_FILE"
        )
    return tok


def _write_token() -> str:
    tok = os.environ.get("FITDASH_INVENTORY_AGENT_TOKEN", "").strip()
    if tok:
        return tok
    path = Path(os.environ.get("FITDASH_INVENTORY_AGENT_TOKEN_FILE", DEFAULT_WRITE_TOKEN_FILE))
    try:
        tok = path.read_text().strip()
    except OSError:
        tok = ""
    if not tok:
        raise FitDashError(
            "No FitDash inventory agent token: set FITDASH_INVENTORY_AGENT_TOKEN or "
            "FITDASH_INVENTORY_AGENT_TOKEN_FILE"
        )
    return tok


def _check_date(name: str, value: str | None) -> _dt.date | None:
    if value in (None, ""):
        return None
    try:
        return _dt.date.fromisoformat(value)
    except ValueError:
        raise FitDashError(f"{name} must be YYYY-MM-DD, got {value!r}") from None


def fetch(section: str, params: dict[str, str] | None = None,
          transport: httpx.BaseTransport | None = None, *,
          path: str | None = None) -> Any:
    """GET /api/agent/<section> and return the decoded JSON as-is."""
    if path is None:
        if section not in SECTIONS:
            raise FitDashError(f"unknown section {section!r}")
        path = f"/api/agent/{section}"
        label = section
    else:
        label = path
    url = f"{_base_url()}{path}"
    headers = {"Authorization": f"Bearer {_token()}", "Accept": "application/json"}
    kwargs: dict[str, Any] = {"timeout": float(os.environ.get("FITDASH_TIMEOUT", "20"))}
    if transport is not None:
        kwargs["transport"] = transport
    elif _proxy():
        kwargs["proxy"] = _proxy()
    try:
        with httpx.Client(**kwargs) as client:
            resp = client.get(url, params=params or None, headers=headers)
    except httpx.TimeoutException:
        raise FitDashError(f"FitDash API timed out: {url}") from None
    except (httpx.TransportError, OSError) as exc:
        raise FitDashError(
            f"FitDash API unreachable at {url} ({type(exc).__name__}); "
            "check FITDASH_API_BASE / FITDASH_PROXY and that the tailnet proxy is up"
        ) from None
    if resp.status_code == 401:
        raise FitDashError(
            "FitDash API returned 401 Unauthorized: agent token missing, wrong or rotated"
        )
    if resp.status_code == 403:
        raise FitDashError("FitDash API returned 403 Forbidden")
    if resp.status_code >= 400:
        detail = resp.text[:300].replace(headers["Authorization"], "Bearer ***")
        raise FitDashError(f"FitDash API {label} returned HTTP {resp.status_code}: {detail}")
    try:
        return resp.json()
    except ValueError:
        raise FitDashError(f"FitDash API {label} returned non-JSON body") from None


def post(path: str, body: dict, transport: httpx.BaseTransport | None = None) -> Any:
    """POST /api/agent/<path> with the pantry-write token. Only pantry paths."""
    if path not in PANTRY_WRITE_PATHS:
        raise FitDashError(f"unknown write path {path!r}")
    url = f"{_base_url()}/api/agent/{path}"
    auth = f"Bearer {_write_token()}"
    headers = {"Authorization": auth, "Accept": "application/json"}
    kwargs: dict[str, Any] = {"timeout": float(os.environ.get("FITDASH_TIMEOUT", "20"))}
    if transport is not None:
        kwargs["transport"] = transport
    elif _proxy():
        kwargs["proxy"] = _proxy()
    try:
        with httpx.Client(**kwargs) as client:
            resp = client.post(url, json=body, headers=headers)
    except httpx.TimeoutException:
        raise FitDashError(f"FitDash API timed out: {url} (write may or may not have landed; re-read the pantry)") from None
    except (httpx.TransportError, OSError) as exc:
        raise FitDashError(
            f"FitDash API unreachable at {url} ({type(exc).__name__}); "
            "check FITDASH_API_BASE / FITDASH_PROXY and that the tailnet proxy is up"
        ) from None
    if resp.status_code == 401:
        raise FitDashError(
            "FitDash API returned 401 Unauthorized: inventory agent token missing, wrong or rotated"
        )
    if resp.status_code >= 400:
        detail = resp.text[:300].replace(auth, "Bearer ***")
        raise FitDashError(f"FitDash API {path} returned HTTP {resp.status_code}: {detail}")
    try:
        return resp.json()
    except ValueError:
        raise FitDashError(f"FitDash API {path} returned non-JSON body") from None


def get_pantry(stock: str = "all", query: str | None = None,
               transport: httpx.BaseTransport | None = None) -> dict:
    """Full ingredient library from GET /api/agent/inventory, optionally filtered."""
    s = (stock or "all").strip().lower()
    if s not in STOCK_FILTERS:
        raise FitDashError(f"stock must be one of {', '.join(STOCK_FILTERS)}")
    body = fetch("inventory", transport=transport)
    data = (body or {}).get("data") or {}
    items = list(data.get("items") or [])
    if s != "all":
        items = [i for i in items if (i.get("stock") or ("in" if i.get("in_stock") else "out")) == s]
    q = (query or "").strip().lower()
    if q:
        items = [i for i in items if q in str(i.get("name") or "").lower() or q in str(i.get("id") or "").lower()]
    return {
        "generated_at": body.get("generated_at"), "as_of": body.get("as_of"),
        "count": data.get("count"), "in_stock_count": data.get("in_stock_count"),
        "out_of_stock_count": data.get("out_of_stock_count"),
        "filter": {"stock": s, "query": q or None}, "returned": len(items), "items": items,
        "note": data.get("note"),
    }


def update_pantry(add: list[dict] | None = None, restock: list[dict] | None = None,
                  out_of_stock: list[str] | None = None, updated_by: str = "fitdash-mcp",
                  transport: httpx.BaseTransport | None = None) -> dict:
    """Pantry-only write. add -> POST inventory/items; restock / out_of_stock ->
    POST inventory/stock. Validates shape locally; the server re-validates."""
    add, restock, out_of_stock = list(add or []), list(restock or []), list(out_of_stock or [])
    if not (add or restock or out_of_stock):
        raise FitDashError("nothing to do: pass add, restock and/or out_of_stock")
    if len(add) + len(restock) + len(out_of_stock) > MAX_PANTRY_OPS:
        raise FitDashError(f"at most {MAX_PANTRY_OPS} pantry changes per call")
    stock_items: list[dict] = []
    seen: set[str] = set()
    for r in restock:
        if isinstance(r, str):
            r = {"id": r}
        if not isinstance(r, dict) or not str(r.get("id") or "").strip():
            raise FitDashError("restock entries need an id")
        extra = set(r) - {"id", "qty"}
        if extra:
            raise FitDashError(f"restock entries only take id and qty (got {', '.join(sorted(extra))})")
        item = {"id": str(r["id"]).strip(), "in_stock": True}
        if "qty" in r:
            item["qty"] = r["qty"]
        stock_items.append(item)
    for iid in out_of_stock:
        if not isinstance(iid, str) or not iid.strip():
            raise FitDashError("out_of_stock entries must be item ids")
        stock_items.append({"id": iid.strip(), "in_stock": False, "qty": 0})
    for it in stock_items:
        k = it["id"].lower()
        if k in seen:
            raise FitDashError(f"item {it['id']!r} appears more than once")
        seen.add(k)
    result: dict[str, Any] = {"added": [], "stock_updated": []}
    if add:
        r = post("inventory/items", {"items": add, "updated_by": updated_by}, transport=transport)
        result["added"] = r.get("items") or []
        result["as_of"] = r.get("as_of")
    if stock_items:
        r = post("inventory/stock", {"items": stock_items, "updated_by": updated_by}, transport=transport)
        result["stock_updated"] = r.get("items") or []
        result["as_of"] = r.get("as_of")
    return result


def fetch_logs(from_: str | None = None, to: str | None = None,
               transport: httpx.BaseTransport | None = None) -> Any:
    f, t = _check_date("from", from_), _check_date("to", to)
    if f and t:
        if f > t:
            raise FitDashError("from must be on or before to")
        if (t - f).days + 1 > MAX_LOG_DAYS:
            raise FitDashError(f"logs range is at most {MAX_LOG_DAYS} days")
    params = {k: v.isoformat() for k, v in (("from", f), ("to", t)) if v}
    return fetch("logs", params, transport=transport)


# Keep these aligned with rt_dashboard.coaching_targets.BOUNDS.
COACHING_BOUNDS = {
    "steps": (1000, 30000),
    "kcal": (1200, 6000),
    "hydration_ml": (500, 8000),
}


def validate_coaching_target(target: str, value: int | None) -> int | None:
    """Reject a target outside the server bounds before the request is sent."""
    if target not in COACHING_BOUNDS:
        raise FitDashError("target must be steps, kcal, or hydration_ml")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FitDashError(f"{target} must be an integer or null")
    if isinstance(value, float) and not float(value).is_integer():
        raise FitDashError(f"{target} must be an integer or null")
    number = int(value)
    lo, hi = COACHING_BOUNDS[target]
    if number < lo or number > hi:
        raise FitDashError(f"{target} must be between {lo} and {hi}")
    return number


def post_json(path: str, body: dict, transport: httpx.BaseTransport | None = None) -> Any:
    """POST JSON with the same agent token and error wording as GET."""
    url = f"{_base_url()}{path}"
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    kwargs: dict[str, Any] = {"timeout": float(os.environ.get("FITDASH_TIMEOUT", "20"))}
    if transport is not None:
        kwargs["transport"] = transport
    elif _proxy():
        kwargs["proxy"] = _proxy()
    try:
        with httpx.Client(**kwargs) as client:
            resp = client.post(url, json=body, headers=headers)
    except httpx.TimeoutException:
        raise FitDashError(f"FitDash API timed out: {url}") from None
    except (httpx.TransportError, OSError) as exc:
        raise FitDashError(
            f"FitDash API unreachable at {url} ({type(exc).__name__}); "
            "check FITDASH_API_BASE / FITDASH_PROXY and that the tailnet proxy is up"
        ) from None
    if resp.status_code == 401:
        raise FitDashError(
            "FitDash API returned 401 Unauthorized: agent token missing, wrong or rotated"
        )
    if resp.status_code == 403:
        raise FitDashError("FitDash API returned 403 Forbidden")
    if resp.status_code >= 400:
        detail = resp.text[:300].replace(headers["Authorization"], "Bearer ***")
        raise FitDashError(f"FitDash API {path} returned HTTP {resp.status_code}: {detail}")
    try:
        return resp.json()
    except ValueError:
        raise FitDashError(f"FitDash API {path} returned non-JSON body") from None


def fetch_coaching_targets(as_of: str | None = None,
                           transport: httpx.BaseTransport | None = None) -> Any:
    _check_date("as_of", as_of)
    params = {"as_of": as_of} if as_of else None
    return fetch("", params, transport=transport, path="/api/agent/coaching_targets")


def post_coaching_target(target: str, value: int | None,
                         transport: httpx.BaseTransport | None = None) -> Any:
    number = validate_coaching_target(target, value)
    return post_json(
        "/api/agent/coaching_targets",
        {"target": target, "value": number},
        transport=transport,
    )


def build_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("fitdash")

    @mcp.tool()
    def fitdash_targets() -> dict:
        """FitDash nutrition/training targets (read-only)."""
        return fetch("targets")

    @mcp.tool()
    def fitdash_inventory() -> dict:
        """FitDash food/supplement inventory (read-only)."""
        return fetch("inventory")

    @mcp.tool(name="get_pantry")
    def get_pantry_tool(stock: str = "all", query: str | None = None) -> dict:
        """Full FitDash ingredient library (pantry): id, name, category, stock (in|low|out),
        in_stock, qty, serving_label / serving_g and per_serving macros (calories, protein_g,
        carbs_g, fat_g, fiber_g, sugar_g, sodium_mg; null when none recorded).
        stock: all|in|low|out (default all). query: optional name/id substring."""
        return get_pantry(stock, query)

    @mcp.tool(name="update_pantry")
    def update_pantry_tool(add: list[dict] | None = None, restock: list[dict] | None = None,
                           out_of_stock: list[str] | None = None,
                           updated_by: str = "fitdash-mcp") -> dict:
        """Pantry-only write (FitDash inventory agent token). No deletes, no edits to existing items.
        add: new items [{name, serving_g?, serving_label?, category?, calories?, protein_g?,
        carbs_g?, fat_g?, fiber_g?, sugar_g?, sodium_mg?, notes?, qty?, in_stock? (default true),
        id? (default slug of name)}]; an existing id/name is rejected (restock it instead).
        restock: [{id, qty?}] marks items in stock. out_of_stock: [id] marks items out (qty 0).
        updated_by: short label recorded in the audit log."""
        return update_pantry(add, restock, out_of_stock, updated_by)

    @mcp.tool()
    def fitdash_logs(from_date: str | None = None, to_date: str | None = None) -> dict:
        """FitDash daily logs. Dates YYYY-MM-DD; default last 7 days, max 92 days."""
        return fetch_logs(from_date, to_date)

    @mcp.tool()
    def fitdash_plan() -> dict:
        """FitDash current plan (read-only)."""
        return fetch("plan")

    @mcp.tool()
    def fitdash_training() -> dict:
        """FitDash training state (read-only)."""
        return fetch("training")

    @mcp.tool()
    def fitdash_recovery() -> dict:
        """FitDash recovery state (read-only)."""
        return fetch("recovery")

    @mcp.tool()
    def get_coaching_targets(as_of: str | None = None) -> dict:
        """Effective coaching targets (steps, kcal, hydration_ml).

        A settings override wins when one is set. Otherwise the phase
        calendar, or the coach rules when the calendar does not set that
        target. as_of is YYYY-MM-DD; omit it for the server's local today.
        """
        return fetch_coaching_targets(as_of)

    @mcp.tool()
    def set_coaching_target(target: str, value: int | None) -> dict:
        """Set one coaching-target override. Pass null to clear it.

        target is steps, kcal, or hydration_ml.
        steps is an integer from 1000 to 30000.
        kcal is an integer from 1200 to 6000.
        hydration_ml is an integer from 500 to 8000.
        """
        return post_coaching_target(target, value)

    return mcp


def main() -> None:
    build_server().run()  # stdio


if __name__ == "__main__":
    main()
