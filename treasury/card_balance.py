"""One Card balance precedence: explicit manual override vs YNAB.

Unsourced manual numbers lose to a healthy YNAB read (2026-08-05: a saved
$499.23 hid a live $440.18). An explicit ``card_balance_source=manual`` wins
unless that YNAB read is within $1, which means Plaid caught up.

The chosen reading is stale when it must not be shown as current:
YNAB direct-import error, balance evidence older than 7 days, or an explicit
manual entry with no as-of / an as-of older than 7 days. A fresh fetch time
does not make an old balance current.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

CARD_BALANCE_STALE_DAYS = 7
CARD_BALANCE_HEAL_USD = 1.0


def _missing(value: Any) -> bool:
    return value is None or value == ""


def _f(value: Any) -> Optional[float]:
    if _missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        raw = raw + "T00:00:00+00:00"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _age_days(value: Any, now: datetime) -> Optional[float]:
    parsed = _parse(value)
    if parsed is None:
        return None
    return (now - parsed).total_seconds() / 86400.0


def _ynab_raw(one_card: Dict[str, Any]) -> Any:
    raw = one_card.get("card_balance")
    if _missing(raw):
        raw = one_card.get("balance_owed")
    return raw


def _ynab_healthy(one_card: Dict[str, Any], raw: Any) -> bool:
    return (
        one_card.get("source") in ("ynab", "snapshot")
        and not one_card.get("live_error")
        and not _missing(raw)
    )


def _explicit_manual(manual: Dict[str, Any]) -> bool:
    return (
        str(manual.get("card_balance_source") or "").strip().lower() == "manual"
        and not _missing(manual.get("card_balance"))
    )


def _close(left: Any, right: Any) -> bool:
    a = _f(left)
    b = _f(right)
    if a is None or b is None:
        return False
    return abs(a - b) <= CARD_BALANCE_HEAL_USD


def _stale_ynab(one_card: Dict[str, Any], now: datetime) -> Tuple[bool, Optional[str]]:
    if one_card.get("direct_import_in_error") is True:
        return True, "YNAB direct import in error"
    if not _missing(one_card.get("balance_as_of")):
        age = _age_days(one_card.get("balance_as_of"), now)
        if age is None:
            return True, "YNAB card balance as-of is unreadable"
        if age > CARD_BALANCE_STALE_DAYS:
            return (
                True,
                f"YNAB card balance {age:.1f}d old (>{CARD_BALANCE_STALE_DAYS}d)",
            )
        return False, None
    age = _age_days(one_card.get("as_of"), now)
    if age is not None and age > CARD_BALANCE_STALE_DAYS:
        return True, f"YNAB card fetch {age:.1f}d old (>{CARD_BALANCE_STALE_DAYS}d)"
    return False, None


def _stale_manual(manual: Dict[str, Any], now: datetime) -> Tuple[bool, Optional[str]]:
    as_of = manual.get("card_balance_as_of")
    if _missing(as_of):
        return True, "manual card balance has no as-of"
    age = _age_days(as_of, now)
    if age is None:
        return True, "manual card balance as-of is unreadable"
    if age > CARD_BALANCE_STALE_DAYS:
        return True, f"manual card balance {age:.1f}d old (>{CARD_BALANCE_STALE_DAYS}d)"
    return False, None


def resolve_card_balance(
    manual: Optional[Dict[str, Any]],
    one_card: Optional[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Pick the One Card owed amount, source, as-of, and stale flag."""
    man = manual or {}
    oc = one_card or {}
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)

    ynab_raw = _ynab_raw(oc)
    ynab_ok = _ynab_healthy(oc, ynab_raw)
    explicit = _explicit_manual(man)
    healed = explicit and ynab_ok and _close(man.get("card_balance"), ynab_raw)
    ynab_balance = _f(ynab_raw) if not _missing(ynab_raw) else None

    if explicit and not healed:
        chosen = "manual"
    elif ynab_ok:
        chosen = "ynab"
    elif not _missing(man.get("card_balance")):
        chosen = "fallback"
    elif not _missing(ynab_raw):
        chosen = "ynab"
    else:
        chosen = "none"

    if chosen == "manual":
        raw = man.get("card_balance")
        source = "manual"
        as_of = man.get("card_balance_as_of")
        stale, reason = _stale_manual(man, clock)
    elif chosen == "fallback":
        raw = man.get("card_balance")
        source = man.get("card_balance_source") or "manual"
        as_of = man.get("card_balance_as_of")
        stale, reason = False, None
    elif chosen == "ynab":
        raw = ynab_raw
        source = "ynab"
        as_of = oc.get("balance_as_of") or oc.get("as_of")
        stale, reason = _stale_ynab(oc, clock)
    else:
        raw = None
        source = None
        as_of = None
        stale, reason = False, None

    balance = _f(raw)
    disagrees = (
        source == "manual"
        and ynab_balance is not None
        and not _close(balance, ynab_balance)
    )
    return {
        "card_balance_raw": raw,
        "card_balance": balance,
        "card_source": source,
        "card_as_of": as_of,
        "card_stale": stale,
        "card_stale_reason": reason,
        "ynab_card_balance": ynab_balance,
        "ynab_disagrees": disagrees,
    }
