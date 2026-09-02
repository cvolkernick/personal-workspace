#!/usr/bin/env python3
"""Sync YNAB accounts into treasury snapshots for FCC.

Accounts:
  - Coinbase One Card (credit) → one_card_latest.json  (actual card spend/liability)
  - RH Checking (checking)     → rh_checking_latest.json (ACH / bank draft float)
  - X Money (checking/cash)    → x_money_latest.json (main last-4 + named spaces)

Auth: ~/.config/ynab/token or env YNAB_TOKEN (never commit tokens).

Usage:
  python3 treasury/ynab_sync.py
  python3 treasury/ynab_sync.py --since 2026-06-01
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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


# Re-fetch YNAB if an on-disk snapshot is older than this (matches FCC stale_after_hours).
# Without this, fetch_rh_checking/fetch_x_money return a "good" cached file forever and
# as_of freezes → ntfy "rh_checking data Nh old" even while launchd is healthy.
DEFAULT_YNAB_MAX_AGE_HOURS = 6.0


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


_LAST4_RE = re.compile(r"(\d{4})\s*$")


def account_last4(name: Optional[str]) -> Optional[str]:
    """Trailing four digits from a YNAB account name (e.g. 'Main – 2201' → '2201')."""
    if not name:
        return None
    m = _LAST4_RE.search(str(name).strip())
    return m.group(1) if m else None


def _norm_last4(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if re.fullmatch(r"\d{4}", s):
        return s
    return account_last4(s)


def _as_last4_set(values: Optional[Iterable[Any]]) -> set:
    out = set()
    for v in values or []:
        n = _norm_last4(str(v) if v is not None else None)
        if n:
            out.add(n)
    return out


def _is_rh_name(name: str) -> bool:
    low = name.lower()
    return "rh" in low or "robinhood" in low


def _x_money_name_hit(name: str) -> bool:
    low = name.lower()
    return "x money" in low or "xmoney" in low.replace(" ", "") or "x-money" in low


def pick_x_money_account(
    accounts: List[Dict[str, Any]],
    prefer_name: Optional[str] = None,
    *,
    exclude_ids: Optional[set] = None,
    prefer_last4: Optional[str] = None,
    exclude_last4: Optional[Iterable[Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Pick X Money main. Last-4 pin beats leftover-checking name heuristics.

    Plaid used to label the sleeve ``Checking – ####``. It is now ``Main – 2201``.
    Scoring leftover checkings by the word "checking" will steal Navy Federal
    (EveryDay Checking – 8680) when several X Money spaces exist — do not guess
    when more than one leftover checking remains.
    """
    open_accts = _open_accounts(accounts)
    exclude_ids = exclude_ids or set()
    excluded_last4 = _as_last4_set(exclude_last4)
    want_last4 = _norm_last4(prefer_last4) or _norm_last4(prefer_name)

    def _eligible(a: Dict[str, Any]) -> bool:
        if a.get("id") in exclude_ids:
            return False
        name = a.get("name") or ""
        if _is_rh_name(name):
            return False
        n4 = account_last4(name)
        if n4 and n4 in excluded_last4:
            return False
        return True

    candidates = [a for a in open_accts if _eligible(a)]

    if prefer_name:
        want = prefer_name.lower()
        for a in candidates:
            if (a.get("name") or "").lower() == want:
                return a

    if want_last4:
        for a in candidates:
            if account_last4(a.get("name")) == want_last4:
                return a
        # Explicit last-4 / renamed-name pin missed — do not guess EveryDay.
        return None

    scored: List[Tuple[int, Dict[str, Any]]] = []
    leftover: List[Dict[str, Any]] = []
    for a in candidates:
        name = a.get("name") or ""
        if a.get("type") not in ("checking", "cash", "savings", None):
            if not _x_money_name_hit(name):
                continue
        if _x_money_name_hit(name):
            scored.append((10, a))
            continue
        if a.get("type") in ("checking", "cash"):
            leftover.append(a)
    if scored:
        scored.sort(key=lambda x: -x[0])
        return scored[0][1]
    if len(leftover) == 1:
        return leftover[0]
    return None


def pick_x_money_spaces(
    accounts: List[Dict[str, Any]],
    space_names: Optional[Iterable[str]] = None,
    *,
    exclude_ids: Optional[set] = None,
    exclude_last4: Optional[Iterable[Any]] = None,
) -> List[Dict[str, Any]]:
    """Match configured X Money space names (prefix or last-4), skipping main/RH/excluded."""
    open_accts = _open_accounts(accounts)
    used = set(exclude_ids or set())
    excluded_last4 = _as_last4_set(exclude_last4)
    picked: List[Dict[str, Any]] = []
    for raw in space_names or []:
        want = str(raw or "").strip()
        if not want:
            continue
        want_low = want.lower()
        want_last4 = _norm_last4(want)
        match: Optional[Dict[str, Any]] = None
        for a in open_accts:
            if a.get("id") in used:
                continue
            name = a.get("name") or ""
            if _is_rh_name(name):
                continue
            n4 = account_last4(name)
            if n4 and n4 in excluded_last4:
                continue
            if a.get("type") not in ("checking", "cash", "savings", None):
                continue
            low = name.lower()
            if low == want_low or low.startswith(want_low) or want_low in low:
                match = a
                break
            if want_last4 and n4 == want_last4:
                match = a
                break
        if match:
            used.add(match.get("id"))
            picked.append(match)
    return picked


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
    """X Money cash/checking via YNAB (Plaid). ``cash`` is signed; ``available`` is clamped."""
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
        "cash": round(raw, 2),
        "available": round(available, 2),
        "cleared_balance": milli_to_units(account.get("cleared_balance")),
        "uncleared_balance": milli_to_units(account.get("uncleared_balance")),
        "spend_30d": spend_30d,
        "inflow_30d": inflow_30d,
        "transaction_count": len(txs_out),
        "transactions": txs_out[:50],
        "notes": (
            "X Money via YNAB/Plaid. Main is last-4 2201 (Main – 2201); named spaces "
            "are separate sleeves. EveryDay Checking – 8680 is Navy Federal, not X Money. "
            "cash is signed (overdraft visible); available is max(0, cash). "
            "Product pays ~6% APY on cash (see config ynab.x_money_apy_est)."
        ),
    }


def _space_brief(snap: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "account_id": snap.get("account_id"),
        "account_name": snap.get("account_name"),
        "last4": account_last4(snap.get("account_name")),
        "cash": snap.get("cash"),
        "available": snap.get("available"),
        "spend_30d": snap.get("spend_30d"),
        "inflow_30d": snap.get("inflow_30d"),
    }


def _roll_up_x_money(
    main: Dict[str, Any], space_snaps: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Keep main identity; cash/spend become main + spaces. Overdraft on main still counts."""
    out = dict(main)
    out["main_cash"] = main.get("cash")
    out["spaces"] = [_space_brief(s) for s in space_snaps]
    if not space_snaps:
        return out
    parts = [main, *space_snaps]
    out["cash"] = round(sum(float(p.get("cash") or 0) for p in parts), 2)
    out["available"] = round(sum(float(p.get("available") or 0) for p in parts), 2)
    out["spend_30d"] = round(sum(float(p.get("spend_30d") or 0) for p in parts), 2)
    out["inflow_30d"] = round(sum(float(p.get("inflow_30d") or 0) for p in parts), 2)
    return out


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
    x_money_account_last4 = ynab_cfg.get("x_money_account_last4")
    x_money_space_names = ynab_cfg.get("x_money_space_names") or []
    x_money_exclude_last4 = ynab_cfg.get("x_money_exclude_last4") or []
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
        accounts,
        prefer_name=x_money_account_name,
        exclude_ids=exclude,
        prefer_last4=x_money_account_last4,
        exclude_last4=x_money_exclude_last4,
    )
    if xm_acct:
        txs = _fetch_account_txs(tok, bid, xm_acct["id"], since)
        x_money = normalize_x_money(
            xm_acct, txs, budget_id=bid, budget_name=bname, source="ynab"
        )
        x_money["token_source"] = tok_src
        x_money["since"] = since
        space_exclude = set(exclude)
        if xm_acct.get("id"):
            space_exclude.add(xm_acct["id"])
        space_accts = pick_x_money_spaces(
            accounts,
            x_money_space_names,
            exclude_ids=space_exclude,
            exclude_last4=x_money_exclude_last4,
        )
        space_snaps: List[Dict[str, Any]] = []
        for sp in space_accts:
            sp_txs = _fetch_account_txs(tok, bid, sp["id"], since)
            space_snaps.append(
                normalize_x_money(
                    sp, sp_txs, budget_id=bid, budget_name=bname, source="ynab"
                )
            )
        x_money = _roll_up_x_money(x_money, space_snaps)
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
    max_age_hours: float = DEFAULT_YNAB_MAX_AGE_HOURS,
) -> Dict[str, Any]:
    """Load RH Checking snapshot; re-sync when missing/error/older than max_age_hours.

    Intent: after one_card live sync writes the YNAB bundle, a fresh file is reused.
    Previously we reused *any* non-error file forever, so as_of froze and FCC/ntfy
    reported multi-day rh_checking staleness while the job still looked healthy.
    """
    snap_path = snapshot_path or (SNAPSHOTS_DIR / "rh_checking_latest.json")
    if prefer_live:
        existing = load_json(snap_path)
        if _snapshot_needs_live_refresh(existing, max_age_hours=max_age_hours):
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
        # Prefer existing file only when still within max_age_hours
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
    """Load X Money snapshot (written by full YNAB sync); re-sync when aged/missing."""
    snap_path = snapshot_path or (SNAPSHOTS_DIR / "x_money_latest.json")
    if prefer_live:
        existing = load_json(snap_path)
        if _snapshot_needs_live_refresh(existing, max_age_hours=max_age_hours):
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
    # Never clobber Mac-pushed (or prior good) snapshots with empty error shells.
    # Pi FCC has no YNAB token; Refresh used to wipe cash feeds to source=empty.
    def _safe_write(writer, data: Dict[str, Any], name: str) -> Path:
        path = SNAPSHOTS_DIR / name
        bad = data.get("source") == "empty" or bool(data.get("live_error"))
        if bad:
            existing = load_json(path)
            if (
                existing
                and existing.get("source") not in (None, "empty")
                and not existing.get("live_error")
            ):
                return path  # preserve good file on disk
        return writer(data)

    p1 = _safe_write(write_one_card_snapshot, one, "one_card_latest.json")
    p2 = _safe_write(write_rh_checking_snapshot, rh, "rh_checking_latest.json")
    p3 = _safe_write(write_x_money_snapshot, xm, "x_money_latest.json")
    one = load_json(p1) or one
    rh = load_json(p2) or rh
    xm = load_json(p3) or xm
    ok = (
        (one.get("source") not in (None, "empty") and not one.get("live_error"))
        or (rh.get("source") not in (None, "empty") and not rh.get("live_error"))
        or (xm.get("source") not in (None, "empty") and not xm.get("live_error"))
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
                    "main_cash": xm.get("main_cash"),
                    "spaces": [
                        {
                            "account": s.get("account_name"),
                            "cash": s.get("cash"),
                        }
                        for s in (xm.get("spaces") or [])
                    ],
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
