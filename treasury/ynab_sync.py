#!/usr/bin/env python3
"""Sync Coinbase One Card (and optional YNAB accounts) into treasury snapshots.

Auth: ~/.config/ynab/token or env YNAB_TOKEN (never commit tokens).

Usage:
  python3 treasury/ynab_sync.py
  python3 treasury/ynab_sync.py --since 2026-06-01
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.adapters import SNAPSHOTS_DIR, load_config, save_json  # noqa: E402

YNAB_API = "https://api.ynab.com/v1"
TOKEN_PATHS = (
    Path.home() / ".config" / "ynab" / "token",
    Path.home() / ".config" / "ynab" / "pat",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_ynab_token() -> Tuple[Optional[str], Optional[str]]:
    env = (os.environ.get("YNAB_TOKEN") or os.environ.get("YNAB_PAT") or "").strip()
    if env and env != "COPY_TOKEN_HERE":
        return env, "env"
    for p in TOKEN_PATHS:
        if not p.is_file():
            continue
        tok = p.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        if tok and tok != "COPY_TOKEN_HERE":
            return tok, str(p)
    return None, None


def milli_to_units(milli: Any) -> float:
    try:
        return float(milli) / 1000.0
    except (TypeError, ValueError):
        return 0.0


def ynab_get(path: str, token: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    url = YNAB_API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "personal-workspace-fcc/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"YNAB HTTP {e.code}: {err_body}") from e


def pick_budget(budgets: List[Dict[str, Any]], prefer_name: Optional[str] = None) -> Dict[str, Any]:
    if not budgets:
        raise RuntimeError("No YNAB budgets on this account")
    if prefer_name:
        for b in budgets:
            if (b.get("name") or "").lower() == prefer_name.lower():
                return b
    return budgets[0]


def pick_one_card_account(
    accounts: List[Dict[str, Any]],
    prefer_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    open_accts = [a for a in accounts if not a.get("deleted") and not a.get("closed")]
    if prefer_name:
        for a in open_accts:
            if (a.get("name") or "").lower() == prefer_name.lower():
                return a
    # Prefer credit cards whose name mentions coinbase / one card
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for a in open_accts:
        name = (a.get("name") or "").lower()
        score = 0
        if a.get("type") == "creditCard":
            score += 2
        if "coinbase" in name:
            score += 5
        if "one card" in name or "onecard" in name.replace(" ", ""):
            score += 3
        if "card" in name and "coinbase" in name:
            score += 2
        if score:
            scored.append((score, a))
    if scored:
        scored.sort(key=lambda x: -x[0])
        return scored[0][1]
    # Fallback: first credit card
    for a in open_accts:
        if a.get("type") == "creditCard":
            return a
    return None


def normalize_one_card(
    account: Dict[str, Any],
    transactions: List[Dict[str, Any]],
    *,
    budget_id: str,
    budget_name: str,
    source: str = "ynab",
) -> Dict[str, Any]:
    """Build FCC one_card snapshot from YNAB account + txs.

    YNAB credit cards: balance is typically negative when you owe money.
    We expose balance_owed as a positive liability for FCC policy.
    """
    raw = milli_to_units(account.get("balance"))
    # Prefer balance_currency if present
    if account.get("balance_currency") is not None:
        try:
            raw = float(account["balance_currency"])
        except (TypeError, ValueError):
            pass
    balance_owed = abs(raw) if account.get("type") == "creditCard" else max(0.0, -raw if raw < 0 else raw)
    if account.get("type") == "creditCard":
        balance_owed = abs(raw)

    txs_out = []
    spend_30d = 0.0
    payments_30d = 0.0
    cutoff = date.today() - timedelta(days=30)
    for t in transactions:
        if t.get("deleted"):
            continue
        amt = milli_to_units(t.get("amount"))
        # YNAB: outflow negative for spending on credit? Actually for credit cards
        # purchases are typically negative amounts (increase debt), payments positive.
        payee = t.get("payee_name") or ""
        entry = {
            "id": t.get("id"),
            "date": t.get("date"),
            "payee": payee,
            "amount": amt,
            "amount_display": round(amt, 2),
            "memo": t.get("memo"),
            "cleared": t.get("cleared"),
            "approved": t.get("approved"),
            "category_name": t.get("category_name"),
        }
        txs_out.append(entry)
        try:
            td = date.fromisoformat(t.get("date") or "1970-01-01")
        except ValueError:
            continue
        if td < cutoff:
            continue
        pl = payee.lower()
        if pl in ("starting balance", "starting balances"):
            continue
        if "payment" in pl or amt > 0:
            payments_30d += abs(amt) if amt > 0 else 0.0
            if "payment" in pl:
                continue
        if amt < 0:
            spend_30d += abs(amt)

    # Available credit not provided by YNAB account object by default
    return {
        "source": source,
        "as_of": _now(),
        "provider": "ynab",
        "budget_id": budget_id,
        "budget_name": budget_name,
        "account_id": account.get("id"),
        "account_name": account.get("name"),
        "account_type": account.get("type"),
        "direct_import_linked": account.get("direct_import_linked"),
        "direct_import_in_error": account.get("direct_import_in_error"),
        "balance_raw": raw,
        "balance_owed": round(balance_owed, 2),
        "card_balance": round(balance_owed, 2),  # FCC policy field
        "available_credit": None,
        "card_available_credit": None,
        "cleared_balance": milli_to_units(account.get("cleared_balance")),
        "uncleared_balance": milli_to_units(account.get("uncleared_balance")),
        "spend_30d": round(spend_30d, 2),
        "payments_30d": round(payments_30d, 2),
        "transaction_count": len(txs_out),
        "transactions": txs_out[:50],
        "notes": (
            "Card data via YNAB (Plaid). Available credit not exposed by YNAB; "
            "set card_available_credit in config if needed."
        ),
    }


def sync_one_card(
    *,
    since: Optional[str] = None,
    budget_name: Optional[str] = None,
    account_name: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    tok, tok_src = (token, "arg") if token else load_ynab_token()
    if not tok:
        return {
            "source": "empty",
            "as_of": _now(),
            "live_error": "no YNAB token (~/.config/ynab/token or YNAB_TOKEN)",
        }

    cfg = load_config()
    ynab_cfg = cfg.get("ynab") or {}
    budget_name = budget_name or ynab_cfg.get("budget_name")
    account_name = account_name or ynab_cfg.get("account_name")

    budgets = ynab_get("/budgets", tok).get("data", {}).get("budgets") or []
    budget = pick_budget(budgets, prefer_name=budget_name)
    bid = budget["id"]
    accounts = ynab_get(f"/budgets/{bid}/accounts", tok).get("data", {}).get("accounts") or []
    acct = pick_one_card_account(accounts, prefer_name=account_name)
    if not acct:
        return {
            "source": "ynab",
            "as_of": _now(),
            "live_error": "no credit card / Coinbase One Card account found in YNAB",
            "budget_name": budget.get("name"),
            "accounts": [a.get("name") for a in accounts if not a.get("deleted")],
            "token_source": tok_src,
        }

    since = since or ynab_cfg.get("since") or (date.today() - timedelta(days=90)).isoformat()
    aid = acct["id"]
    txs = (
        ynab_get(
            f"/budgets/{bid}/accounts/{aid}/transactions",
            tok,
            params={"since_date": since},
        )
        .get("data", {})
        .get("transactions")
        or []
    )
    snap = normalize_one_card(
        acct,
        txs,
        budget_id=bid,
        budget_name=budget.get("name") or "",
        source="ynab",
    )
    snap["token_source"] = tok_src
    snap["since"] = since
    return snap


def write_one_card_snapshot(data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    out = path or (SNAPSHOTS_DIR / "one_card_latest.json")
    save_json(out, data)
    return out


def fetch_one_card(
    *,
    prefer_live: bool = True,
    snapshot_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Live YNAB sync with file fallback (same pattern as Robinhood)."""
    snap_path = snapshot_path or (SNAPSHOTS_DIR / "one_card_latest.json")
    err = None
    if prefer_live:
        try:
            live = sync_one_card()
            if live.get("source") != "empty" and not live.get("live_error"):
                write_one_card_snapshot(live, snap_path)
                return live
            err = live.get("live_error")
            if live.get("source") == "ynab" and live.get("live_error"):
                write_one_card_snapshot(live, snap_path)
        except Exception as e:
            err = str(e)
    from treasury.adapters import load_json

    file_data = load_json(snap_path)
    if file_data:
        out = dict(file_data)
        out.setdefault("source", out.get("source") or "snapshot")
        if err:
            out["live_error"] = err
        return out
    return {
        "source": "empty",
        "as_of": _now(),
        "card_balance": None,
        "balance_owed": None,
        "available_credit": None,
        "live_error": err or "no one_card snapshot",
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sync YNAB One Card into treasury snapshot")
    parser.add_argument("--since", help="YYYY-MM-DD transaction lookback start")
    parser.add_argument("--budget-name", help="Prefer this YNAB budget name")
    parser.add_argument("--account-name", help="Prefer this account name")
    args = parser.parse_args(argv)
    try:
        data = sync_one_card(
            since=args.since,
            budget_name=args.budget_name,
            account_name=args.account_name,
        )
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        return 1
    path = write_one_card_snapshot(data)
    print(
        json.dumps(
            {
                "ok": not bool(data.get("live_error")),
                "path": str(path),
                "account": data.get("account_name"),
                "balance_owed": data.get("balance_owed"),
                "spend_30d": data.get("spend_30d"),
                "tx_count": data.get("transaction_count"),
                "error": data.get("live_error"),
            },
            indent=2,
        )
    )
    return 0 if not data.get("live_error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
