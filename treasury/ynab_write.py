#!/usr/bin/env python3
"""YNAB categorize + approve write helpers (#340).

Policy (locked):
  - Full categorize + approve within the category map SoT
  - Hard no transfer / payment / move_money without an explicit later order
  - PATCH /budgets/{budget_id}/transactions/{transaction_id} only
  - Payload may contain only category_id + approved
  - Default dry_run=True (first ship)

Live writes stay behind allow_categorize / allow_approve and enabled ids.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.ynab_category_map import (  # noqa: E402
    ALLOWED_WRITE_ACTIONS,
    HARD_FORBID,
    MAP_PATH,
    effective_forbid,
    enabled_category_ids,
    is_category_enabled,
    load_category_map,
)
from treasury.ynab_sync import YNAB_API, load_ynab_token  # noqa: E402

_TX_PATCH_RE = re.compile(r"^/budgets/[^/]+/transactions/[^/]+$")
_FORBIDDEN_PATH_FRAGMENTS = (
    "transfer",
    "payments",
    "move_money",
    "/months/",
)
ALLOWED_TX_FIELDS = frozenset({"category_id", "approved"})


def _refuse(reason: str, **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": False, "refused": True, "reason": reason}
    out.update(extra)
    return out


def assert_action_allowed(action: str, category_map: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    act = (action or "").strip().lower()
    forbid = effective_forbid(category_map)
    if act in forbid or act in HARD_FORBID:
        return _refuse(
            "forbidden_action",
            action=act,
            forbid=sorted(forbid),
        )
    if act not in ALLOWED_WRITE_ACTIONS:
        return _refuse(
            "forbidden_action",
            action=act,
            forbid=sorted(forbid),
            detail="only categorize / approve / categorize_approve are allowed",
        )
    return None


def assert_category_allowed(category_id: str, category_map: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cid = str(category_id or "").strip()
    if not cid:
        return _refuse("out_of_map", category_id=cid, detail="category_id required")
    if not is_category_enabled(category_map, cid):
        return _refuse(
            "out_of_map",
            category_id=cid,
            enabled_ids=sorted(enabled_category_ids(category_map)),
        )
    return None


def _transaction_forbidden_reason(transaction: Optional[Dict[str, Any]]) -> Optional[str]:
    if not transaction:
        return None
    if transaction.get("transfer_account_id") or transaction.get("transfer_transaction_id"):
        return "transfer"
    payee = (transaction.get("payee_name") or "").lower()
    cat = (transaction.get("category_name") or "").lower()
    if transaction.get("transfer_account_id") and "payment" in payee:
        return "payment"
    if "credit card payment" in cat:
        return "payment"
    return None


def build_categorize_approve_payload(category_id: str, *, approve: bool = True) -> Dict[str, Any]:
    return {"transaction": {"category_id": str(category_id), "approved": bool(approve)}}


def assert_patch_allowed(path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Refuse anything that is not a single-transaction category+approve PATCH."""
    if not path.startswith("/"):
        return _refuse("forbidden_path", path=path, detail="path must be YNAB-relative")
    low = path.lower()
    for frag in _FORBIDDEN_PATH_FRAGMENTS:
        if frag in low:
            return _refuse("forbidden_path", path=path, detail=f"path contains {frag}")
    if not _TX_PATCH_RE.match(path):
        return _refuse(
            "forbidden_path",
            path=path,
            detail="only PATCH /budgets/{budget_id}/transactions/{transaction_id}",
        )
    if set(payload.keys()) - {"transaction"}:
        return _refuse("forbidden_payload", detail="payload may only contain transaction")
    tx = payload.get("transaction")
    if not isinstance(tx, dict):
        return _refuse("forbidden_payload", detail="transaction object required")
    extra = set(tx.keys()) - ALLOWED_TX_FIELDS
    if extra:
        return _refuse(
            "forbidden_payload",
            extra=sorted(extra),
            detail="transaction may only contain category_id and approved",
        )
    if "category_id" not in tx:
        return _refuse("forbidden_payload", detail="category_id required")
    return None


def ynab_patch(path: str, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """PATCH helper — gated. Never used for transfer/payment/move_money."""
    blocked = assert_patch_allowed(path, payload)
    if blocked:
        raise RuntimeError(blocked.get("detail") or blocked.get("reason") or "PATCH refused")
    url = YNAB_API + path
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "personal-workspace-fcc/1.0",
        },
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"YNAB HTTP {e.code}: {err_body}") from e


def categorize_and_approve(
    transaction_id: str,
    category_id: str,
    *,
    approve: bool = True,
    dry_run: bool = True,
    action: str = "categorize_approve",
    category_map: Optional[Dict[str, Any]] = None,
    map_path: Optional[Path] = None,
    token: Optional[str] = None,
    transaction: Optional[Dict[str, Any]] = None,
    patch_fn: Any = None,
) -> Dict[str, Any]:
    """Categorize and/or approve one transaction. Default is dry-run (no PATCH)."""
    cmap = category_map if category_map is not None else load_category_map(map_path or MAP_PATH)

    blocked = assert_action_allowed(action, cmap)
    if blocked:
        blocked["dry_run"] = dry_run
        return blocked

    act = (action or "categorize_approve").strip().lower()
    want_approve = bool(approve)

    if not cmap.get("allow_categorize", True):
        return _refuse("policy_flag", flag="allow_categorize", dry_run=dry_run)
    if want_approve and not cmap.get("allow_approve", True):
        return _refuse("policy_flag", flag="allow_approve", dry_run=dry_run)

    blocked = assert_category_allowed(category_id, cmap)
    if blocked:
        blocked["dry_run"] = dry_run
        return blocked

    forbidden = _transaction_forbidden_reason(transaction)
    if forbidden:
        return _refuse("forbidden_action", action=forbidden, dry_run=dry_run)

    budget_id = str(cmap.get("budget_id") or "").strip()
    txid = str(transaction_id or "").strip()
    if not txid:
        return _refuse("missing_transaction_id", dry_run=dry_run)

    payload = build_categorize_approve_payload(category_id, approve=want_approve)
    report = {
        "ok": True,
        "refused": False,
        "dry_run": bool(dry_run),
        "action": act,
        "budget_id": budget_id,
        "budget_name": cmap.get("budget_name"),
        "transaction_id": txid,
        "category_id": category_id,
        "approved": want_approve,
        "method": "PATCH",
        "path": f"/budgets/{budget_id}/transactions/{txid}" if budget_id else None,
        "payload": payload,
    }

    if dry_run:
        report["would_patch"] = {
            "method": "PATCH",
            "path": report["path"] or path,
            "transaction": payload["transaction"],
        }
        if not budget_id:
            report["warning"] = "budget_id blank until bootstrap; live write refused"
        return report

    if not budget_id:
        return _refuse("budget_id_unpinned", dry_run=False, detail="run bootstrap then pin SoT")

    gated = assert_patch_allowed(f"/budgets/{budget_id}/transactions/{txid}", payload)
    if gated:
        gated["dry_run"] = False
        return gated

    tok, tok_src = (token, "arg") if token else load_ynab_token()
    if not tok:
        return _refuse(
            "no_token",
            dry_run=False,
            detail="no YNAB token (~/.config/ynab/token or YNAB_TOKEN)",
        )

    do_patch = patch_fn or ynab_patch
    try:
        patched = do_patch(f"/budgets/{budget_id}/transactions/{txid}", tok, payload)
    except Exception as e:
        return {
            "ok": False,
            "refused": False,
            "reason": "patch_failed",
            "error": str(e),
            "dry_run": False,
            "path": f"/budgets/{budget_id}/transactions/{txid}",
        }
    report["patched"] = patched
    report["token_source"] = tok_src
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="YNAB categorize + approve (default dry-run; no transfers/payments)"
    )
    parser.add_argument("--transaction-id", required=True)
    parser.add_argument("--category-id", required=True)
    parser.add_argument("--map", dest="map_path", help="SoT map path")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Actually PATCH (default is dry-run / report)",
    )
    parser.add_argument(
        "--no-approve",
        action="store_true",
        help="Categorize only (still refused if allow_categorize is false)",
    )
    parser.add_argument(
        "--action",
        default="categorize_approve",
        help="categorize | approve | categorize_approve (transfer/payment/move_money refused)",
    )
    args = parser.parse_args(argv)
    result = categorize_and_approve(
        args.transaction_id,
        args.category_id,
        approve=not args.no_approve,
        dry_run=not args.live,
        action=args.action,
        map_path=Path(args.map_path) if args.map_path else None,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
