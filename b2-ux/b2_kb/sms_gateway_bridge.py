"""Poll SMS Gateway for Android (Local Server) → allowlisted B2 ingest.

Credentials (not in git):
  ~/B2/inbox/meta/sms_gateway_auth.json
  {
    "base_url": "http://100.121.147.109:8080",
    "username": "...",
    "password": "..."
  }

CLI:
  python3 -m b2_kb.sms_gateway_bridge probe
  python3 -m b2_kb.sms_gateway_bridge backfill
  python3 -m b2_kb.sms_gateway_bridge poll
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .sms_contact import (
    find_contact,
    ingest_messages,
    load_contacts,
    meta_dir,
    normalize_address,
)
from .vault import resolve_vault_path

AUTH_FILE = "sms_gateway_auth.json"
BRIDGE_STATE = "sms_gateway_bridge_state.json"
DEFAULT_BASE = "http://100.121.147.109:8080"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_auth(vault: Path) -> Dict[str, str]:
    env_user = (os.environ.get("SMS_GATEWAY_USER") or "").strip()
    env_pass = (os.environ.get("SMS_GATEWAY_PASSWORD") or "").strip()
    env_base = (os.environ.get("SMS_GATEWAY_URL") or "").strip()
    path = meta_dir(vault) / AUTH_FILE
    data: Dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"invalid {path}: {e}") from e
    user = env_user or str(data.get("username") or data.get("user") or "").strip()
    password = env_pass or str(data.get("password") or data.get("pass") or "").strip()
    base = (
        env_base
        or str(data.get("base_url") or data.get("url") or DEFAULT_BASE).strip().rstrip("/")
    )
    if not user or not password:
        raise SystemExit(
            f"missing credentials: set {path} with username/password "
            f"or SMS_GATEWAY_USER / SMS_GATEWAY_PASSWORD"
        )
    return {"base_url": base, "username": user, "password": password}


def _request(
    auth: Dict[str, str],
    method: str,
    path: str,
    *,
    query: Optional[Dict[str, Any]] = None,
    body: Optional[dict] = None,
    timeout: float = 30.0,
) -> Tuple[int, Any, Dict[str, str]]:
    q = ""
    if query:
        q = "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
    url = auth["base_url"] + path + q
    raw = None
    headers = {
        "Authorization": "Basic "
        + base64.b64encode(f"{auth['username']}:{auth['password']}".encode()).decode(),
        "Accept": "application/json",
    }
    if body is not None:
        raw = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=raw, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            code = resp.getcode() or 200
    except urllib.error.HTTPError as e:
        resp_body = e.read().decode("utf-8", errors="replace")
        hdrs = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
        code = e.code
        if code >= 400:
            raise SystemExit(f"HTTP {code} {method} {path}: {resp_body[:500]}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"connection failed {auth['base_url']}: {e}") from e

    if not resp_body.strip():
        return code, None, hdrs
    try:
        return code, json.loads(resp_body), hdrs
    except json.JSONDecodeError:
        return code, resp_body, hdrs


def probe(vault_path: Optional[str] = None) -> Dict[str, Any]:
    vault = resolve_vault_path(vault_path)
    # health is unauthenticated on device
    base = DEFAULT_BASE
    auth_path = meta_dir(vault) / AUTH_FILE
    if auth_path.is_file():
        try:
            base = str(json.loads(auth_path.read_text()).get("base_url") or base)
        except json.JSONDecodeError:
            pass
    base = (os.environ.get("SMS_GATEWAY_URL") or base).rstrip("/")
    try:
        with urllib.request.urlopen(base + "/health", timeout=5) as resp:
            health = json.loads(resp.read().decode())
    except Exception as e:
        return {"ok": False, "base_url": base, "error": str(e)}

    out: Dict[str, Any] = {
        "ok": True,
        "base_url": base,
        "health": health,
        "auth_file": str(auth_path),
        "auth_configured": auth_path.is_file()
        or bool(os.environ.get("SMS_GATEWAY_USER")),
    }
    if out["auth_configured"]:
        try:
            auth = load_auth(vault)
            code, data, hdrs = _request(
                auth, "GET", "/inbox", query={"limit": 1, "offset": 0}
            )
            total = hdrs.get("x-total-count")
            out["inbox_sample_ok"] = code == 200
            out["inbox_total_count"] = total
            out["inbox_sample_type"] = type(data).__name__
            if isinstance(data, list) and data:
                # keys only — no message bodies in status output
                out["inbox_item_keys"] = sorted(data[0].keys())
        except SystemExit as e:
            out["inbox_error"] = str(e)
    return out


def gateway_item_to_message(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map Local Server inbox item → sms_contact ingest message."""
    if not isinstance(item, dict):
        return None
    address = str(
        item.get("sender")
        or item.get("phoneNumber")
        or item.get("address")
        or item.get("from")
        or ""
    ).strip()
    body = str(
        item.get("content")
        or item.get("message")
        or item.get("text")
        or item.get("body")
        or item.get("contentPreview")
        or ""
    )
    if not address:
        return None
    ts = str(
        item.get("createdAt")
        or item.get("receivedAt")
        or item.get("date")
        or item.get("timestamp")
        or utc_now_iso()
    )
    mid = str(item.get("id") or item.get("messageId") or "")
    msg: Dict[str, Any] = {
        "address": address,
        "body": body,
        "direction": "in",
        "ts": ts,
        "type": str(item.get("type") or "SMS"),
    }
    if mid:
        msg["id"] = f"smsgw:{mid}"
    return msg


def fetch_inbox_page(
    auth: Dict[str, str],
    *,
    limit: int = 100,
    offset: int = 0,
    msg_type: Optional[str] = "SMS",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    query: Dict[str, Any] = {"limit": limit, "offset": offset}
    if msg_type:
        query["type"] = msg_type
    if date_from:
        query["from"] = date_from
    if date_to:
        query["to"] = date_to
    code, data, hdrs = _request(auth, "GET", "/inbox", query=query)
    total = hdrs.get("x-total-count")
    total_i = int(total) if total and str(total).isdigit() else None
    if not isinstance(data, list):
        raise SystemExit(f"unexpected /inbox payload: {type(data)}")
    return [x for x in data if isinstance(x, dict)], total_i


def backfill(
    vault_path: Optional[str] = None,
    *,
    limit: int = 200,
    max_pages: int = 200,
    msg_types: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Page Local Server GET /inbox → allowlist → B2.

    Default: **no type filter** (device returns full inbox; type=SMS can under-count).
    Pass msg_types=["SMS", ...] only if you need a subset.
    """
    vault = resolve_vault_path(vault_path)
    auth = load_auth(vault)
    contacts = load_contacts(vault)
    if not contacts:
        raise SystemExit("no allowlisted contacts in sms_contacts.json")

    # None type = unfiltered inbox (preferred). Explicit list = filter each type.
    type_passes: List[Optional[str]] = list(msg_types) if msg_types else [None]
    all_raw: List[Dict[str, Any]] = []
    pages = 0
    for t in type_passes:
        offset = 0
        while pages < max_pages:
            page, total = fetch_inbox_page(
                auth, limit=limit, offset=offset, msg_type=t
            )
            pages += 1
            if not page:
                break
            all_raw.extend(page)
            offset += len(page)
            if total is not None and offset >= total:
                break
            if len(page) < limit:
                break

    mapped: List[Dict[str, Any]] = []
    skipped_no_match = 0
    for item in all_raw:
        msg = gateway_item_to_message(item)
        if not msg:
            continue
        if not find_contact(contacts, msg["address"]):
            skipped_no_match += 1
            continue
        mapped.append(msg)

    # oldest first so capture reads chronologically on first write
    mapped.sort(key=lambda m: m.get("ts") or "")

    result = ingest_messages({"messages": mapped}, vault, notify_summary=True)
    state_path = meta_dir(vault) / BRIDGE_STATE
    state = {
        "last_backfill_at": utc_now_iso(),
        "last_raw_count": len(all_raw),
        "last_matched": len(mapped),
        "last_ingest": {
            k: result.get(k)
            for k in ("accepted", "rejected", "duplicates", "by_contact", "ok")
        },
        "base_url": auth["base_url"],
    }
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "pages": pages,
        "raw_fetched": len(all_raw),
        "matched_allowlist": len(mapped),
        "skipped_other_senders": skipped_no_match,
        "ingest": result,
        "state_path": str(state_path),
    }


def poll_once(vault_path: Optional[str] = None, *, limit: int = 50) -> Dict[str, Any]:
    """Fetch newest page(s) and ingest allowlisted (dedupe handles repeats)."""
    vault = resolve_vault_path(vault_path)
    auth = load_auth(vault)
    contacts = load_contacts(vault)
    page, _total = fetch_inbox_page(auth, limit=limit, offset=0, msg_type=None)
    mapped = []
    for item in page:
        msg = gateway_item_to_message(item)
        if msg and find_contact(contacts, msg["address"]):
            mapped.append(msg)
    result = ingest_messages({"messages": mapped}, vault, notify_summary=True)
    return {
        "ok": True,
        "fetched": len(page),
        "matched": len(mapped),
        "ingest": result,
        "as_of": utc_now_iso(),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SMS Gateway Local Server → B2 bridge")
    parser.add_argument("--vault", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe", help="Health + optional authenticated inbox sample")
    p_bf = sub.add_parser("backfill", help="Page full inbox → allowlist → B2")
    p_bf.add_argument("--limit", type=int, default=100)
    p_bf.add_argument("--max-pages", type=int, default=200)
    p_poll = sub.add_parser("poll", help="One poll of recent inbox")
    p_poll.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)

    if args.cmd == "probe":
        print(json.dumps(probe(args.vault), indent=2))
        return 0
    if args.cmd == "backfill":
        print(
            json.dumps(
                backfill(args.vault, limit=args.limit, max_pages=args.max_pages),
                indent=2,
            )
        )
        return 0
    if args.cmd == "poll":
        print(json.dumps(poll_once(args.vault, limit=args.limit), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
