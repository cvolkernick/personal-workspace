"""Horizon 'Nodes' card: poll each node's /api/heartbeat and classify it.

Node list: JSON file named by HORIZON_NODES (list, or {"nodes": [...]}) of
{"name", "url", optional "stale_after_s"}; defaults to DEFAULT_NODES.

Status per node:
  stale  unreachable, bad payload, or heartbeat older than stale_after_s
         (an unresponsive node never shows as healthy)
  red    heartbeat status red, or ok=false
  amber  heartbeat status amber, or any degraded service
  green  otherwise
"""

from __future__ import annotations

import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

DEFAULT_NODES: list[dict[str, Any]] = [
    {"name": "prism-gateway", "url": "http://127.0.0.1:8790/api/heartbeat"},
    {"name": "finley-gateway", "url": "http://finley-gateway:8797/api/heartbeat"},
]
DEFAULT_STALE_AFTER_S = 300
FETCH_TIMEOUT_S = 4.0


def load_nodes(env: Optional[dict[str, str]] = None) -> list[dict[str, Any]]:
    env = os.environ if env is None else env
    path = (env.get("HORIZON_NODES") or "").strip()
    if path:
        try:
            data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
            nodes = data.get("nodes") if isinstance(data, dict) else data
            if isinstance(nodes, list):
                return [n for n in nodes if isinstance(n, dict) and n.get("url")]
        except (OSError, json.JSONDecodeError):
            pass
    return [dict(n) for n in DEFAULT_NODES]


def _fetch(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as r:
        return json.loads(r.read().decode("utf-8"))


def _age_s(as_of: Any, now: datetime) -> Optional[float]:
    if not isinstance(as_of, str):
        return None
    try:
        ts = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds())


def _service_down(svc: dict[str, Any]) -> bool:
    if svc.get("health_ok") is not None:
        return not svc.get("health_ok")
    return not svc.get("active")


def summarize_node(
    node: dict[str, Any],
    payload: Optional[dict[str, Any]],
    *,
    error: str = "",
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    stale_after = float(node.get("stale_after_s") or DEFAULT_STALE_AFTER_S)
    out: dict[str, Any] = {
        "name": node.get("name") or node.get("url"),
        "url": node.get("url"),
        "status": "stale",
        "reason": error or "",
        "last_seen": None,
        "age_s": None,
        "vitals": {},
        "services_down": [],
        "alerts": [],
    }
    hb = (payload or {}).get("heartbeat") if isinstance(payload, dict) else None
    if not isinstance(hb, dict):
        out["reason"] = error or "no heartbeat"
        return out
    age = _age_s(hb.get("as_of"), now)
    out["last_seen"] = hb.get("as_of")
    out["age_s"] = None if age is None else round(age)
    out["vitals"] = hb.get("vitals") or {}
    out["services_down"] = [str(s.get("name")) for s in hb.get("services") or [] if _service_down(s)]
    out["alerts"] = [a.get("text") for a in hb.get("alerts") or [] if isinstance(a, dict)]
    if age is None or age > stale_after:
        out["reason"] = f"heartbeat older than {int(stale_after)}s" if age is not None else "no timestamp"
        return out
    status = hb.get("status")
    if status not in ("green", "amber", "red"):
        status = "red" if hb.get("ok") is False else ("amber" if hb.get("degraded") else "green")
    elif hb.get("ok") is False:
        status = "red"
    elif status == "green" and hb.get("degraded"):
        status = "amber"
    out["status"] = status
    return out


def build_nodes_payload(
    nodes: Optional[list[dict[str, Any]]] = None,
    *,
    fetch: Callable[[str], dict[str, Any]] = _fetch,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    nodes = load_nodes() if nodes is None else nodes

    def one(n: dict[str, Any]) -> dict[str, Any]:
        try:
            return summarize_node(n, fetch(str(n["url"])), now=now)
        except Exception as e:  # noqa: BLE001 - any failure means stale
            return summarize_node(n, None, error=f"unreachable: {type(e).__name__}", now=now)

    with ThreadPoolExecutor(max_workers=max(1, min(8, len(nodes)))) as ex:
        rows = list(ex.map(one, nodes))
    return {"ok": True, "nodes": rows}
