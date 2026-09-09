"""X Money last-4 pin. Navy Federal EveryDay-8680 is out of scope."""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Dict, Iterable, List, Optional, Tuple

# CIC 2026-09-02 / #549: four X Money checkings under one Plaid Item.
SPACE_BY_LAST4 = {
    "2201": "Main",
    "0895": "Auto Fleet",
    "3326": "Collateral",
    "4867": "Utilities",
}
ALLOWED_LAST4 = frozenset(SPACE_BY_LAST4)
EXCLUDE_LAST4 = frozenset({"8680"})  # EveryDay Checking — Navy Federal

_LAST4_RE = re.compile(r"(\d{4})\s*$")
_DIGITS_RE = re.compile(r"\D+")


def dollars_to_cents(value: Any) -> Optional[int]:
    """USD → integer cents. Banker's rounding (ROUND_HALF_EVEN). None stays None."""
    if value is None or value == "":
        return None
    try:
        d = Decimal(str(value))
    except Exception:
        return None
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def cents_to_dollars(cents: Optional[int]) -> Optional[float]:
    if cents is None:
        return None
    return float(Decimal(cents) / Decimal("100"))


def norm_last4(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    digits = _DIGITS_RE.sub("", str(raw).strip())
    if len(digits) >= 4:
        return digits[-4:]
    return None


def last4_of_account(account: Dict[str, Any]) -> Optional[str]:
    """Prefer Plaid `mask`, then trailing 4 digits on name / official_name."""
    n4 = norm_last4(account.get("mask"))
    if n4:
        return n4
    for key in ("name", "official_name"):
        m = _LAST4_RE.search(str(account.get(key) or "").strip())
        if m:
            return m.group(1)
    return None


def space_name_for(last4: Optional[str], account: Optional[Dict[str, Any]] = None) -> Optional[str]:
    if last4 and last4 in SPACE_BY_LAST4:
        return SPACE_BY_LAST4[last4]
    if account:
        name = str(account.get("name") or "").strip()
        if name:
            return name
    return None


def is_in_scope(account: Dict[str, Any]) -> bool:
    n4 = last4_of_account(account)
    if not n4:
        return False
    if n4 in EXCLUDE_LAST4:
        return False
    return n4 in ALLOWED_LAST4


def filter_x_money_accounts(accounts: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Return (in-scope accounts in last4 order, missing last4s)."""
    by_last4: Dict[str, Dict[str, Any]] = {}
    for acct in accounts:
        n4 = last4_of_account(acct)
        if not n4 or n4 in EXCLUDE_LAST4 or n4 not in ALLOWED_LAST4:
            continue
        by_last4[n4] = acct
    ordered = []
    missing = []
    for n4 in ("2201", "0895", "3326", "4867"):
        if n4 in by_last4:
            ordered.append(by_last4[n4])
        else:
            missing.append(n4)
    return ordered, missing


WRITE_TOOL_HINTS = (
    "transfer",
    "payment",
    "ach",
    "wire",
    "send_money",
    "move_money",
    "processor",
    "item_remove",
    "sandbox_fire",
    "create_link",
    "exchange_public",
    "link_token",
)


def is_write_tool(name: str) -> bool:
    low = (name or "").strip().lower().replace("-", "_")
    if low in WRITE_TOOL_HINTS:
        return True
    return any(h in low for h in WRITE_TOOL_HINTS)
