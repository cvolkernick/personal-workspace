#!/usr/bin/env python3
"""Sync YNAB accounts into treasury snapshots for FCC.

Accounts:
  - Coinbase One Card (credit) → one_card_latest.json  (actual card spend/liability)
  - RH Checking (checking)     → rh_checking_latest.json (ACH / bank draft float)
  - X Money (checking/cash)    → x_money_latest.json (YNAB/Plaid; often labeled Checking – ####)

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

from treasury.adapters import SNAPSHOTS_DIR, load_config, load_json, save_json  # noqa: E402

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


def account_balance_units(account: Dict[str, Any]) -> float:
    if account.get("balance_currency") is not None:
        try:
            return float(account["balance_currency"])
        except (TypeError, ValueError):
            pass
    return milli_to_units(account.get("balance"))


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
            return json.loads(resp.read().decode("utf-8"))
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


def _open_accounts(accounts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [a for a in accounts if not a.get("deleted") and not a.get("closed")]


def pick_one_card_account(
    accounts: List[Dict[str, Any]],
    prefer_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    open_accts = _open_accounts(accounts)
    if prefer_name:
        for a in open_accts:
            if (a.get("name") or "").lower() == prefer_name.lower():
                return a
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
    for a in open_accts:
        if a.get("type") == "creditCard":
            return a
    return None


def pick_rh_checking_account(
    accounts: List[Dict[str, Any]],
    prefer_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    open_accts = _open_accounts(accounts)
    if prefer_name:
        for a in open_accts:
            if (a.get("name") or "").lower() == prefer_name.lower():
                return a
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for a in open_accts:
        name = (a.get("name") or "").lower()
        score = 0
        if a.get("type") in ("checking", "cash"):
            score += 2
        if "rh" in name or "robinhood" in name:
            score += 6
        if "checking" in name:
            score += 3
        if "bank" in name:
            score += 1
        # Don't steal X Money when name is generic "checking"
        if "x money" in name or "xmoney" in name or "x-money" in name:
            score -= 10
        if score:
            scored.append((score, a))
    if scored:
        scored.sort(key=lambda x: -x[0])
        return scored[0][1]
    return None


def pick_x_money_account(
    accounts: List[Dict[str, Any]],
    prefer_name: Optional[str] = None,
    *,
    exclude_ids: Optional[set] = None,
) -> Optional[Dict[str, Any]]:
    """Pick X Money (or leftover non-RH checking). Plaid often names it 'Checking – ####'."""
    open_accts = _open_accounts(accounts)
    exclude_ids = exclude_ids or set()
    if prefer_name:
        for a in open_accts:
            if (a.get("name") or "").lower() == prefer_name.lower():
                return a
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for a in open_accts:
        if a.get("id") in exclude_ids:
            continue
        name = (a.get("name") or "").lower()
        # Never pick RH Checking as X Money
        if "rh" in name or "robinhood" in name:
            continue
        if a.get("type") not in ("checking", "cash", "savings", None):
            # still allow if name screams x money
            if not any(k in name for k in ("x money", "xmoney", "x-money")):
                continue
        score = 0
        if "x money" in name or "xmoney" in name.replace(" ", "") or "x-money" in name:
            score += 10
        if a.get("type") in ("checking", "cash"):
            score += 2
        if "checking" in name:
            score += 2
        if "cash" in name:
            score += 1
        if score:
            scored.append((score, a))
    if scored:
        scored.sort(key=lambda x: -x[0])
        return scored[0][1]
    return None


def _summarize_txs(
    transactions: List[Dict[str, Any]],
    *,
    account_type: str,
) -> Tuple[List[Dict[str, Any]], float, float]:
    """Return (txs_out, spend_30d, inflow_30d)."""
    txs_out: List[Dict[str, Any]] = []
    spend_30d = 0.0
    inflow_30d = 0.0
    cutoff = date.today() - timedelta(days=30)
    for t in transactions:
        if t.get("deleted"):
            continue
        amt = milli_to_units(t.get("amount"))
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
        if account_type == "creditCard":
            if "payment" in pl:
                inflow_30d += abs(amt) if amt > 0 else 0.0
                continue
            if amt < 0:
                spend_30d += abs(amt)
            elif amt > 0:
                inflow_30d += amt
        else:
            # checking: outflows negative in YNAB, inflows positive
            if amt < 0:
                spend_30d += abs(amt)
            elif amt > 0:
                inflow_30d += amt
    return txs_out, round(spend_30d, 2), round(inflow_30d, 2)


def normalize_one_card(
    account: Dict[str, Any],
    transactions: List[Dict[str, Any]],
    *,
    budget_id: str,
    budget_name: str,
    source: str = "ynab",
) -> Dict[str, Any]:
    raw = account_balance_units(account)
    balance_owed = abs(raw) if account.get("type") == "creditCard" else max(0.0, -raw if raw < 0 else raw)
    if account.get("type") == "creditCard":
        balance_owed = abs(raw)
    txs_out, spend_30d, payments_30d = _summarize_txs(transactions, account_type="creditCard")
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
        "card_balance": round(balance_owed, 2),
        "available_credit": None,
        "card_available_credit": None,
        "cleared_balance": milli_to_units(account.get("cleared_balance")),
        "uncleared_balance": milli_to_units(account.get("uncleared_balance")),
        "spend_30d": spend_30d,
        "payments_30d": payments_30d,
        "transaction_count": len(txs_out),
        "transactions": txs_out[:50],
        "notes": (
            "Card data via YNAB (Plaid). Available credit not exposed by YNAB; "
            "set card_available_credit in config if needed."
        ),
    }


def normalize_rh_checking(
    account: Dict[str, Any],
    transactions: List[Dict[str, Any]],
    *,
    budget_id: str,
    budget_name: str,
    source: str = "ynab",
) -> Dict[str, Any]:
    """Checking account: balance is available cash (positive)."""
    raw = account_balance_units(account)
    cash = max(0.0, raw) if account.get("type") in ("checking", "cash", "savings") else raw
    # if overdraft negative, surface available as 0 and note
    available = raw if raw >= 0 else 0.0
    txs_out, spend_30d, inflow_30d = _summarize_txs(
        transactions, account_type=account.get("type") or "checking"
    )
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
        "cash": round(available, 2),
        "available": round(available, 2),
        "cleared_balance": milli_to_units(account.get("cleared_balance")),
        "uncleared_balance": milli_to_units(account.get("uncleared_balance")),
        "spend_30d": spend_30d,
        "inflow_30d": inflow_30d,
        "transaction_count": len(txs_out),
        "transactions": txs_out[:50],
        "notes": (
            "Robinhood Checking via YNAB/Plaid — actual ACH/checking balance and txs. "
            "Prefer this over brokerage MCP cash for bill-pay float."
        ),
    }


def normalize_x_money(
    account: Dict[str, Any],
    transactions: List[Dict[str, Any]],
    *,
    budget_id: str,
    budget_name: str,
    source: str = "ynab",
) -> Dict[str, Any]:
    """X Money cash/checking via YNAB (Plaid). Balance is available cash (positive)."""
    raw = account_balance_units(account)
    available = raw if raw >= 0 else 0.0
    txs_out, spend_30d, inflow_30d = _summarize_txs(
        transactions, account_type=account.get("type") or "checking"
    )
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
        "cash": round(available, 2),
        "available": round(available, 2),
        "cleared_balance": milli_to_units(account.get("cleared_balance")),
        "uncleared_balance": milli_to_units(account.get("uncleared_balance")),
        "spend_30d": spend_30d,
        "inflow_30d": inflow_30d,
        "transaction_count": len(txs_out),
        "transactions": txs_out[:50],
        "notes": (
            "X Money via YNAB/Plaid. Plaid may label the account as 'Checking – ####'. "
            "Cash sleeve separate from RH Checking ACH float. Product pays ~6% APY on cash "
            "(see config ynab.x_money_apy_est)."
        ),
    }


def _load_budget_context(
    token: str,
    *,
    budget_name: Optional[str],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str]:
    budgets = ynab_get("/budgets", token).get("data", {}).get("budgets") or []
    budget = pick_budget(budgets, prefer_name=budget_name)
    bid = budget["id"]
    accounts = ynab_get(f"/budgets/{bid}/accounts", token).get("data", {}).get("accounts") or []
    return budget, accounts, bid


def _fetch_account_txs(
    token: str,
    budget_id: str,
    account_id: str,
    since: str,
) -> List[Dict[str, Any]]:
    return (
        ynab_get(
            f"/budgets/{budget_id}/accounts/{account_id}/transactions",
            token,
            params={"since_date": since},
        )
        .get("data", {})
        .get("transactions")
        or []
    )


def sync_ynab(
    *,
    since: Optional[str] = None,
    budget_name: Optional[str] = None,
    one_card_account_name: Optional[str] = None,
    checking_account_name: Optional[str] = None,
    x_money_account_name: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Sync One Card + RH Checking + X Money; return all payloads."""
    tok, tok_src = (token, "arg") if token else load_ynab_token()
    if not tok:
        err = {
            "source": "empty",
            "as_of": _now(),
            "live_error": "no YNAB token (~/.config/ynab/token or YNAB_TOKEN)",
        }
        return {"one_card": err, "rh_checking": dict(err), "x_money": dict(err)}

    cfg = load_config()
    ynab_cfg = cfg.get("ynab") or {}
    budget_name = budget_name or ynab_cfg.get("budget_name")
    one_card_account_name = one_card_account_name or ynab_cfg.get("account_name")
    checking_account_name = checking_account_name or ynab_cfg.get("checking_account_name")
    x_money_account_name = x_money_account_name or ynab_cfg.get("x_money_account_name")
    since = since or ynab_cfg.get("since") or (date.today() - timedelta(days=90)).isoformat()

    budget, accounts, bid = _load_budget_context(tok, budget_name=budget_name)
    bname = budget.get("name") or ""
    open_names = [a.get("name") for a in accounts if not a.get("deleted")]

    card_acct = pick_one_card_account(accounts, prefer_name=one_card_account_name)
    if card_acct:
        txs = _fetch_account_txs(tok, bid, card_acct["id"], since)
        one_card = normalize_one_card(
            card_acct, txs, budget_id=bid, budget_name=bname, source="ynab"
        )
        one_card["token_source"] = tok_src
        one_card["since"] = since
    else:
        one_card = {
            "source": "ynab",
            "as_of": _now(),
            "live_error": "no Coinbase One Card account found in YNAB",
            "budget_name": bname,
            "accounts": open_names,
            "token_source": tok_src,
        }

    chk_acct = pick_rh_checking_account(accounts, prefer_name=checking_account_name)
    if chk_acct:
        txs = _fetch_account_txs(tok, bid, chk_acct["id"], since)
        rh_checking = normalize_rh_checking(
            chk_acct, txs, budget_id=bid, budget_name=bname, source="ynab"
        )
        rh_checking["token_source"] = tok_src
        rh_checking["since"] = since
    else:
        rh_checking = {
            "source": "ynab",
            "as_of": _now(),
            "live_error": "no RH Checking / Robinhood checking account found in YNAB",
            "budget_name": bname,
            "accounts": open_names,
            "token_source": tok_src,
        }

    exclude = {chk_acct["id"]} if chk_acct and chk_acct.get("id") else set()
    xm_acct = pick_x_money_account(
        accounts, prefer_name=x_money_account_name, exclude_ids=exclude
    )
    if xm_acct:
        txs = _fetch_account_txs(tok, bid, xm_acct["id"], since)
        x_money = normalize_x_money(
            xm_acct, txs, budget_id=bid, budget_name=bname, source="ynab"
        )
        x_money["token_source"] = tok_src
        x_money["since"] = since
    else:
        x_money = {
            "source": "ynab",
            "as_of": _now(),
            "live_error": "no X Money / non-RH checking account found in YNAB",
            "budget_name": bname,
            "accounts": open_names,
            "token_source": tok_src,
        }

    return {
        "one_card": one_card,
        "rh_checking": rh_checking,
        "x_money": x_money,
        "accounts": open_names,
        "budget_name": bname,
    }


def sync_one_card(**kwargs: Any) -> Dict[str, Any]:
    """Backward-compatible: return only the one_card payload."""
    return sync_ynab(**kwargs)["one_card"]


def write_one_card_snapshot(data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    out = path or (SNAPSHOTS_DIR / "one_card_latest.json")
    save_json(out, data)
    return out


def write_rh_checking_snapshot(data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    out = path or (SNAPSHOTS_DIR / "rh_checking_latest.json")
    save_json(out, data)
    return out


def write_x_money_snapshot(data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    out = path or (SNAPSHOTS_DIR / "x_money_latest.json")
    save_json(out, data)
    return out


def _write_ynab_bundle(bundle: Dict[str, Any]) -> None:
    one = bundle.get("one_card") or {}
    rh = bundle.get("rh_checking") or {}
    xm = bundle.get("x_money") or {}
    if one.get("source") != "empty":
        write_one_card_snapshot(one)
    if rh.get("source") != "empty":
        write_rh_checking_snapshot(rh)
    if xm.get("source") != "empty":
        write_x_money_snapshot(xm)


def fetch_one_card(
    *,
    prefer_live: bool = True,
    snapshot_path: Optional[Path] = None,
) -> Dict[str, Any]:
    snap_path = snapshot_path or (SNAPSHOTS_DIR / "one_card_latest.json")
    err = None
    if prefer_live:
        try:
            # Full sync writes all YNAB snapshots
            bundle = sync_ynab()
            one = bundle["one_card"]
            _write_ynab_bundle(bundle)
            if not one.get("live_error"):
                return one
            err = one.get("live_error")
        except Exception as e:
            err = str(e)
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


def fetch_rh_checking(
    *,
    prefer_live: bool = True,
    snapshot_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Load RH Checking snapshot (prefer file if one_card already refreshed live)."""
    snap_path = snapshot_path or (SNAPSHOTS_DIR / "rh_checking_latest.json")
    if prefer_live:
        # If file is missing or empty, run full sync
        existing = load_json(snap_path)
        if not existing or existing.get("source") in (None, "empty") or existing.get("live_error"):
            try:
                bundle = sync_ynab()
                rh = bundle["rh_checking"]
                _write_ynab_bundle(bundle)
                if not rh.get("live_error"):
                    return rh
            except Exception as e:
                err = str(e)
                file_data = load_json(snap_path)
                if file_data:
                    out = dict(file_data)
                    out["live_error"] = err
                    return out
                return {
                    "source": "empty",
                    "as_of": _now(),
                    "cash": None,
                    "live_error": err,
                }
        # Prefer existing fresh file after one_card live sync already wrote it
        if existing and not existing.get("live_error"):
            out = dict(existing)
            out.setdefault("source", out.get("source") or "snapshot")
            return out
    file_data = load_json(snap_path)
    if file_data:
        out = dict(file_data)
        out.setdefault("source", out.get("source") or "snapshot")
        return out
    return {
        "source": "empty",
        "as_of": _now(),
        "cash": None,
        "live_error": "no rh_checking snapshot — run treasury/ynab_sync.py",
    }


def fetch_x_money(
    *,
    prefer_live: bool = True,
    snapshot_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Load X Money snapshot (written by full YNAB sync)."""
    snap_path = snapshot_path or (SNAPSHOTS_DIR / "x_money_latest.json")
    if prefer_live:
        existing = load_json(snap_path)
        if not existing or existing.get("source") in (None, "empty") or existing.get("live_error"):
            try:
                bundle = sync_ynab()
                xm = bundle["x_money"]
                _write_ynab_bundle(bundle)
                if not xm.get("live_error"):
                    return xm
            except Exception as e:
                err = str(e)
                file_data = load_json(snap_path)
                if file_data:
                    out = dict(file_data)
                    out["live_error"] = err
                    return out
                return {
                    "source": "empty",
                    "as_of": _now(),
                    "cash": None,
                    "live_error": err,
                }
        if existing and not existing.get("live_error"):
            out = dict(existing)
            out.setdefault("source", out.get("source") or "snapshot")
            return out
    file_data = load_json(snap_path)
    if file_data:
        out = dict(file_data)
        out.setdefault("source", out.get("source") or "snapshot")
        return out
    return {
        "source": "empty",
        "as_of": _now(),
        "cash": None,
        "live_error": "no x_money snapshot — run treasury/ynab_sync.py",
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync YNAB One Card + RH Checking + X Money into FCC snapshots"
    )
    parser.add_argument("--since", help="YYYY-MM-DD transaction lookback start")
    parser.add_argument("--budget-name", help="Prefer this YNAB budget name")
    parser.add_argument("--account-name", help="Prefer this One Card account name")
    parser.add_argument("--checking-account-name", help="Prefer this RH checking account name")
    parser.add_argument("--x-money-account-name", help="Prefer this X Money account name")
    args = parser.parse_args(argv)
    try:
        bundle = sync_ynab(
            since=args.since,
            budget_name=args.budget_name,
            one_card_account_name=args.account_name,
            checking_account_name=args.checking_account_name,
            x_money_account_name=args.x_money_account_name,
        )
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        return 1

    one = bundle["one_card"]
    rh = bundle["rh_checking"]
    xm = bundle["x_money"]
    p1 = write_one_card_snapshot(one)
    p2 = write_rh_checking_snapshot(rh)
    p3 = write_x_money_snapshot(xm)
    ok = (
        (not one.get("live_error"))
        or (not rh.get("live_error"))
        or (not xm.get("live_error"))
    )
    print(
        json.dumps(
            {
                "ok": bool(ok),
                "one_card": {
                    "path": str(p1),
                    "account": one.get("account_name"),
                    "balance_owed": one.get("balance_owed"),
                    "spend_30d": one.get("spend_30d"),
                    "tx_count": one.get("transaction_count"),
                    "error": one.get("live_error"),
                },
                "rh_checking": {
                    "path": str(p2),
                    "account": rh.get("account_name"),
                    "cash": rh.get("cash"),
                    "spend_30d": rh.get("spend_30d"),
                    "tx_count": rh.get("transaction_count"),
                    "error": rh.get("live_error"),
                },
                "x_money": {
                    "path": str(p3),
                    "account": xm.get("account_name"),
                    "cash": xm.get("cash"),
                    "spend_30d": xm.get("spend_30d"),
                    "tx_count": xm.get("transaction_count"),
                    "error": xm.get("live_error"),
                },
                "accounts_seen": bundle.get("accounts"),
            },
            indent=2,
        )
    )
    clean = (
        not one.get("live_error") and not rh.get("live_error") and not xm.get("live_error")
    )
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
