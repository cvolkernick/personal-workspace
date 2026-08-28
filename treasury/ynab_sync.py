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
YNAB_FEED_NAMES = ("one_card", "rh_checking", "x_money")
# en-dash / em-dash / minus / hyphen variants YNAB/Plaid mix in "Checking – 2201"
_DASH_CHARS = ("\u2013", "\u2014", "\u2212", "\u2010", "\u2011")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Re-fetch YNAB if an on-disk snapshot is older than this (matches FCC stale_after_hours).
DEFAULT_YNAB_MAX_AGE_HOURS = 6.0
# YNAB GET ?since_date returns oldest-first. Snapshots keep the newest N, newest-first.
SNAPSHOT_TX_LIMIT = 50


def fold_dashes(name: Optional[str]) -> str:
    """Lowercase + fold dash glyphs so 'Checking – 2201' matches 'Checking - 2201'."""
    s = name or ""
    for ch in _DASH_CHARS:
        s = s.replace(ch, "-")
    return " ".join(s.lower().split())


def _parse_as_of(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t
    except (TypeError, ValueError):
        return None


def _age_hours(as_of: Optional[datetime]) -> Optional[float]:
    if not as_of:
        return None
    return max(0.0, (datetime.now(timezone.utc) - as_of).total_seconds() / 3600.0)


def _snapshot_needs_live_refresh(
    existing: Optional[Dict[str, Any]],
    *,
    max_age_hours: float = DEFAULT_YNAB_MAX_AGE_HOURS,
) -> bool:
    """True when prefer_live should hit the API (missing, error, or aged past threshold)."""
    if not existing or existing.get("source") in (None, "empty"):
        return True
    if existing.get("live_error"):
        return True
    age = _age_hours(_parse_as_of(existing.get("as_of")))
    if age is None:
        return True
    return age > float(max_age_hours)


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
        want = fold_dashes(prefer_name)
        for b in budgets:
            if fold_dashes(b.get("name")) == want:
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
        want = fold_dashes(prefer_name)
        for a in open_accts:
            if fold_dashes(a.get("name")) == want:
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
        want = fold_dashes(prefer_name)
        for a in open_accts:
            if fold_dashes(a.get("name")) == want:
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
    prefer_id: Optional[str] = None,
    exclude_ids: Optional[set] = None,
) -> Optional[Dict[str, Any]]:
    """Pick X Money. Id pin first, then dash-normalized name, then leftover non-RH checking."""
    open_accts = _open_accounts(accounts)
    exclude_ids = exclude_ids or set()
    pinned = bool(prefer_id or prefer_name)
    if prefer_id:
        for a in open_accts:
            if a.get("id") == prefer_id:
                return a
    if prefer_name:
        want = fold_dashes(prefer_name)
        for a in open_accts:
            if fold_dashes(a.get("name")) == want:
                return a
    if pinned:
        # Closed/renamed pin must miss — do not steal another leftover checking.
        return None
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for a in open_accts:
        if a.get("id") in exclude_ids:
            continue
        name = (a.get("name") or "").lower()
        # Never pick RH Checking as X Money
        if "rh" in name or "robinhood" in name:
            continue
        if a.get("type") not in ("checking", "cash", "savings", None):
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


def closed_pin_reason(
    accounts: List[Dict[str, Any]],
    *,
    prefer_id: Optional[str] = None,
    prefer_name: Optional[str] = None,
) -> Optional[str]:
    """If the pinned id/name exists but is closed/deleted, explain the miss."""
    for a in accounts:
        if prefer_id and a.get("id") == prefer_id:
            if a.get("closed") or a.get("deleted"):
                state = "deleted" if a.get("deleted") else "closed"
                return f"YNAB account id {prefer_id} is {state} ({a.get('name')})"
        if prefer_name and fold_dashes(a.get("name")) == fold_dashes(prefer_name):
            if a.get("closed") or a.get("deleted"):
                state = "deleted" if a.get("deleted") else "closed"
                return f"YNAB account {a.get('name')} is {state}"
    return None


def _summarize_txs(
    transactions: List[Dict[str, Any]],
    *,
    account_type: str,
) -> Tuple[List[Dict[str, Any]], float, float]:
    """Return (txs_out, spend_30d, inflow_30d). txs_out preserves input/API order."""
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
            "category_id": t.get("category_id"),
            "category_name": t.get("category_name"),
            "transfer_account_id": t.get("transfer_account_id"),
            "transfer_transaction_id": t.get("transfer_transaction_id"),
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


def _newest_snapshot_txs(
    txs_out: List[Dict[str, Any]],
    *,
    limit: int = SNAPSHOT_TX_LIMIT,
) -> List[Dict[str, Any]]:
    """Keep the newest N txs, newest-first. YNAB / _summarize_txs preserve oldest-first API order."""
    ordered = sorted(
        txs_out,
        key=lambda t: (str(t.get("date") or ""), str(t.get("id") or "")),
        reverse=True,
    )
    return ordered[:limit]


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
        "transactions": _newest_snapshot_txs(txs_out),
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
        "transactions": _newest_snapshot_txs(txs_out),
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
        "transactions": _newest_snapshot_txs(txs_out),
        "notes": (
            "X Money via YNAB/Plaid. Plaid may label the account as 'Checking – ####'. "
            "Cash sleeve separate from RH Checking ACH float."
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
    # YNAB returns this list oldest-first for since_date; callers must not treat [:N] as newest.
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


def _account_error(
    *,
    live_error: str,
    budget_name: str,
    open_names: List[Any],
    token_source: Optional[str],
) -> Dict[str, Any]:
    return {
        "source": "ynab",
        "as_of": _now(),
        "live_error": live_error,
        "budget_name": budget_name,
        "accounts": open_names,
        "token_source": token_source,
    }


def sync_ynab(
    *,
    since: Optional[str] = None,
    budget_name: Optional[str] = None,
    one_card_account_name: Optional[str] = None,
    checking_account_name: Optional[str] = None,
    x_money_account_name: Optional[str] = None,
    x_money_account_id: Optional[str] = None,
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
    x_money_account_id = x_money_account_id or ynab_cfg.get("x_money_account_id")
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
        one_card = _account_error(
            live_error="no Coinbase One Card account found in YNAB",
            budget_name=bname,
            open_names=open_names,
            token_source=tok_src,
        )

    chk_acct = pick_rh_checking_account(accounts, prefer_name=checking_account_name)
    if chk_acct:
        txs = _fetch_account_txs(tok, bid, chk_acct["id"], since)
        rh_checking = normalize_rh_checking(
            chk_acct, txs, budget_id=bid, budget_name=bname, source="ynab"
        )
        rh_checking["token_source"] = tok_src
        rh_checking["since"] = since
    else:
        rh_checking = _account_error(
            live_error="no RH Checking / Robinhood checking account found in YNAB",
            budget_name=bname,
            open_names=open_names,
            token_source=tok_src,
        )

    exclude = {chk_acct["id"]} if chk_acct and chk_acct.get("id") else set()
    xm_acct = pick_x_money_account(
        accounts,
        prefer_name=x_money_account_name,
        prefer_id=x_money_account_id,
        exclude_ids=exclude,
    )
    if xm_acct:
        txs = _fetch_account_txs(tok, bid, xm_acct["id"], since)
        x_money = normalize_x_money(
            xm_acct, txs, budget_id=bid, budget_name=bname, source="ynab"
        )
        x_money["token_source"] = tok_src
        x_money["since"] = since
    else:
        x_money = _account_error(
            live_error=closed_pin_reason(
                accounts, prefer_id=x_money_account_id, prefer_name=x_money_account_name
            )
            or "no X Money / non-RH checking account found in YNAB",
            budget_name=bname,
            open_names=open_names,
            token_source=tok_src,
        )

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


def _is_bad_payload(data: Dict[str, Any]) -> bool:
    if not data:
        return True
    return data.get("source") == "empty" or bool(data.get("live_error"))


def _prior_is_good(existing: Optional[Dict[str, Any]]) -> bool:
    return bool(
        existing
        and existing.get("source") not in (None, "empty")
        and not existing.get("live_error")
    )


def _safe_write(
    writer: Any,
    data: Dict[str, Any],
    path: Path,
) -> Dict[str, Any]:
    """Write live payload, or preserve a good prior file and surface the skip reason.

    Never clobber Mac-pushed / prior-good snapshots with empty error shells.
    The on-disk file stays intact; the returned dict reports live_error + preserved.
    """
    if _is_bad_payload(data):
        existing = load_json(path)
        if _prior_is_good(existing):
            reason = data.get("live_error") or f"{path.name}: empty payload, preserved prior snapshot"
            return {
                "path": path,
                "preserved": True,
                "skip_reason": reason,
                "payload": data,
            }
    dest = writer(data, path)
    return {
        "path": dest,
        "preserved": False,
        "skip_reason": None,
        "payload": data,
    }


def _write_ynab_bundle(
    bundle: Dict[str, Any],
    *,
    directory: Optional[Path] = None,
) -> Dict[str, Dict[str, Any]]:
    d = directory or SNAPSHOTS_DIR
    writers = {
        "one_card": write_one_card_snapshot,
        "rh_checking": write_rh_checking_snapshot,
        "x_money": write_x_money_snapshot,
    }
    files = {
        "one_card": "one_card_latest.json",
        "rh_checking": "rh_checking_latest.json",
        "x_money": "x_money_latest.json",
    }
    out: Dict[str, Dict[str, Any]] = {}
    for name in YNAB_FEED_NAMES:
        payload = bundle.get(name) or {}
        out[name] = _safe_write(writers[name], payload, d / files[name])
    return out


def feed_status(
    payload: Dict[str, Any],
    *,
    preserved: bool = False,
    skip_reason: Optional[str] = None,
    disk: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Per-feed operator report: {as_of, token_source, live_error|preserved}."""
    src = disk if (preserved and disk) else payload
    out: Dict[str, Any] = {
        "as_of": src.get("as_of"),
        "token_source": src.get("token_source"),
    }
    err = payload.get("live_error")
    if err:
        out["live_error"] = err
    if preserved:
        out["preserved"] = skip_reason or "soft-preserve prior snapshot"
    return out


def ynab_feeds_clean(report: Any) -> bool:
    """True only when every feed is live-clean (no live_error, no preserved)."""
    if report == "ok":
        return True
    if not isinstance(report, dict):
        return False
    for name in YNAB_FEED_NAMES:
        feed = report.get(name)
        if not isinstance(feed, dict):
            return False
        if feed.get("live_error") or feed.get("preserved"):
            return False
    return True


def ynab_feed_soft_preserved(report: Any, feed: str = "x_money") -> bool:
    if not isinstance(report, dict):
        return False
    item = report.get(feed)
    if not isinstance(item, dict):
        return False
    return bool(item.get("preserved"))


def _report_from_writes(
    bundle: Dict[str, Any],
    writes: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    report: Dict[str, Any] = {}
    for name in YNAB_FEED_NAMES:
        wr = writes.get(name) or {}
        payload = bundle.get(name) or wr.get("payload") or {}
        disk = load_json(wr["path"]) if wr.get("path") else None
        report[name] = feed_status(
            payload,
            preserved=bool(wr.get("preserved")),
            skip_reason=wr.get("skip_reason"),
            disk=disk,
        )
    return report


def sync_and_write_report(**kwargs: Any) -> Dict[str, Any]:
    """Live sync + safe writes. Always a per-feed report — never a bare 'ok'."""
    try:
        bundle = sync_ynab(**kwargs)
    except Exception as e:
        err = {
            "as_of": _now(),
            "token_source": None,
            "live_error": str(e),
        }
        return {name: dict(err) for name in YNAB_FEED_NAMES}
    writes = _write_ynab_bundle(bundle)
    return _report_from_writes(bundle, writes)


def _attach_live_error(
    file_data: Optional[Dict[str, Any]],
    err: str,
    *,
    preserved: bool = False,
    skip_reason: Optional[str] = None,
    fallback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return existing (or fallback) with live_error attached — never drop the error."""
    if file_data:
        out = dict(file_data)
        out.setdefault("source", out.get("source") or "snapshot")
        out["live_error"] = err
        if preserved:
            out["preserved"] = skip_reason or err
        return out
    if fallback:
        out = dict(fallback)
        out["live_error"] = err
        return out
    return {
        "source": "empty",
        "as_of": _now(),
        "cash": None,
        "live_error": err,
    }


def fetch_one_card(
    *,
    prefer_live: bool = True,
    snapshot_path: Optional[Path] = None,
) -> Dict[str, Any]:
    snap_path = snapshot_path or (SNAPSHOTS_DIR / "one_card_latest.json")
    err = None
    if prefer_live:
        try:
            bundle = sync_ynab()
            one = bundle["one_card"]
            writes = _write_ynab_bundle(bundle, directory=snap_path.parent)
            if not one.get("live_error"):
                return one
            err = one.get("live_error")
            wr = writes.get("one_card") or {}
            return _attach_live_error(
                load_json(snap_path),
                err,
                preserved=bool(wr.get("preserved")),
                skip_reason=wr.get("skip_reason"),
                fallback=one,
            )
        except Exception as e:
            err = str(e)
    file_data = load_json(snap_path)
    if file_data:
        return _attach_live_error(file_data, err) if err else (
            dict(file_data, **{"source": file_data.get("source") or "snapshot"})
        )
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
    max_age_hours: float = DEFAULT_YNAB_MAX_AGE_HOURS,
) -> Dict[str, Any]:
    """Load RH Checking; re-sync when missing/error/aged. Live errors stay attached."""
    snap_path = snapshot_path or (SNAPSHOTS_DIR / "rh_checking_latest.json")
    if prefer_live:
        existing = load_json(snap_path)
        if _snapshot_needs_live_refresh(existing, max_age_hours=max_age_hours):
            try:
                bundle = sync_ynab()
                rh = bundle["rh_checking"]
                writes = _write_ynab_bundle(bundle, directory=snap_path.parent)
                if not rh.get("live_error"):
                    return rh
                wr = writes.get("rh_checking") or {}
                return _attach_live_error(
                    load_json(snap_path),
                    rh.get("live_error") or "RH Checking live error",
                    preserved=bool(wr.get("preserved")),
                    skip_reason=wr.get("skip_reason"),
                    fallback=rh,
                )
            except Exception as e:
                return _attach_live_error(
                    load_json(snap_path),
                    str(e),
                    fallback={
                        "source": "empty",
                        "as_of": _now(),
                        "cash": None,
                        "live_error": str(e),
                    },
                )
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
    max_age_hours: float = DEFAULT_YNAB_MAX_AGE_HOURS,
) -> Dict[str, Any]:
    """Load X Money; re-sync when aged/missing. Live errors stay attached (never silent stale)."""
    snap_path = snapshot_path or (SNAPSHOTS_DIR / "x_money_latest.json")
    if prefer_live:
        existing = load_json(snap_path)
        if _snapshot_needs_live_refresh(existing, max_age_hours=max_age_hours):
            try:
                bundle = sync_ynab()
                xm = bundle["x_money"]
                writes = _write_ynab_bundle(bundle, directory=snap_path.parent)
                if not xm.get("live_error"):
                    return xm
                wr = writes.get("x_money") or {}
                return _attach_live_error(
                    load_json(snap_path),
                    xm.get("live_error") or "X Money live error",
                    preserved=bool(wr.get("preserved")),
                    skip_reason=wr.get("skip_reason"),
                    fallback=xm,
                )
            except Exception as e:
                return _attach_live_error(
                    load_json(snap_path),
                    str(e),
                    fallback={
                        "source": "empty",
                        "as_of": _now(),
                        "cash": None,
                        "live_error": str(e),
                    },
                )
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
    parser.add_argument("--x-money-account-id", help="Prefer this X Money account id")
    args = parser.parse_args(argv)
    report = sync_and_write_report(
        since=args.since,
        budget_name=args.budget_name,
        one_card_account_name=args.account_name,
        checking_account_name=args.checking_account_name,
        x_money_account_name=args.x_money_account_name,
        x_money_account_id=args.x_money_account_id,
    )
    clean = ynab_feeds_clean(report)
    print(
        json.dumps(
            {
                "ok": clean,
                "one_card": report.get("one_card"),
                "rh_checking": report.get("rh_checking"),
                "x_money": report.get("x_money"),
            },
            indent=2,
        )
    )
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
