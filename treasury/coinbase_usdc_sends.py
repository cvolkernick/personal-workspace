"""Coinbase v2 USDC send book — display-only actuals for Thaís, Rent, JR.

Venue is Coinbase v2 USDC `type=send` only. Merge-only send book.
Ignore lend / lock / One Card / X Money. No live-money. No CDP key read.
Rent dest is email only: nvolkern@gmail.com (casefold). status=completed only.
Pending/failed/canceled do not book. No phone. No other dests.

Standing join (Nakatoshi AC #427): first 2026-09-11 1:00 PM America/New_York,
then every 14 days. Weekly $208 / daily $25 after 2026-08-30 / 2026-09-04
never paint. Proof ids baa3976e and 7b8bf83b stay excluded.

Thaís dest is the painted Solana address. Dest $415 is 2026-09-11+ only.
Pre-9/11 dest-only $895 (2026-08-10) and name-on-address still join —
#431 dest rewrite must not blank August. Rent planned stays sheet monthly —
never rewrite to $25 * days or 14×$25. JR self-send is not Rent / Thaís / mint.

No Transfer key. No sender code. Key lives only on prism.
Tests use fixtures only. This module never copies or logs the CDP key.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from treasury.adapters import SNAPSHOTS_DIR

SEND_TYPE = "send"
IGNORE_TYPES = frozenset(
    {
        "retail_defi_lend_withdrawal",
        "lock",
        "credit_card_collateral_lock",
    }
)
USDC = "USDC"
SNAPSHOT_NAME = "coinbase_usdc_sends.json"
# Prism-only. Never commit, never read into git, never copy off prism.
PRISM_KEY_PATH = Path.home() / ".config" / "coinbase" / "cdp-api-key.json"

# Standing dest/cadence painted on #427. Do not invent other dests.
_THAIS_NAME_NEEDLES = frozenset({"thais", "thaís"})
THAIS_DEST = "AwMH3PDTmQBHC7QmMbtw3hmpxvobuXSYTzf25JQpcAVm"
THAIS_DEST_FINGERPRINTS: frozenset[str] = frozenset({THAIS_DEST})
THAIS_STANDING_AMOUNT = 415.0
# 2026-08-10 dest-only monthly. Dest rewrite owns $415 from 2026-09-11 only.
THAIS_PRE_STANDING_AMOUNT = 895.0
THAIS_DEAD_WEEKLY_AMOUNT = 208.0
THAIS_NEVER_PAINT_DATE = date(2026, 9, 4)
THAIS_NETWORK = "solana"

RENT_DEST_EMAIL = "nvolkern@gmail.com"
RENT_DEST_FINGERPRINTS: frozenset[str] = frozenset({RENT_DEST_EMAIL})
RENT_STANDING_AMOUNT = 350.0
RENT_DAILY_AMOUNT = 25.0
RENT_DAILY_LAST_DATE = date(2026, 8, 30)

JR_SELF_SEND_DEST = "CzuRxF4H7qtcCbP37zcLu9DTgcHySmi8NcTsrS6W7bDm"
JR_SELF_SEND_AMOUNT = 70.0
JR_SELF_SEND_LABEL = "self-send"
JR_SELF_SEND_NETWORK = "solana"

PAY_FRIDAY_TZ_NAME = "America/New_York"
PAY_FRIDAY_TZ = ZoneInfo(PAY_FRIDAY_TZ_NAME)
PAY_FRIDAY_FIRST = datetime(2026, 9, 11, 13, 0, 0, tzinfo=PAY_FRIDAY_TZ)
PAY_FRIDAY_CADENCE_DAYS = 14
STANDING_FIRST_DATE = PAY_FRIDAY_FIRST.date()

# Proof / dest-test ids. Prefix match — issue paints the leading hex, not a new UUID.
THAIS_PROOF_ID_PREFIX = "baa3976e"
JR_DEST_TEST_ID_PREFIX = "7b8bf83b"
EXCLUDED_SEND_ID_PREFIXES = frozenset(
    {
        THAIS_PROOF_ID_PREFIX,  # 2026-08-27 $1 Thaís Solana proof
        JR_DEST_TEST_ID_PREFIX,  # 2026-08-30 $5 JR dest-test
    }
)
THAIS_EXCLUDED_SEND_IDS = frozenset(
    {
        "baa3976e-3304-53f7-b168-e35f16325653",  # 2026-08-27 $1 Solana proof
    }
)
JR_EXCLUDED_SEND_IDS = frozenset(
    {
        JR_DEST_TEST_ID_PREFIX,
    }
)


def _fold(val: Any) -> str:
    s = unicodedata.normalize("NFKD", str(val or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.casefold().strip()


def _f(val: Any, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _near_amount(a: float, b: float, *, tol: float = 0.02) -> bool:
    return abs(float(a) - float(b)) <= tol


def tx_id_key(tx: Dict[str, Any]) -> str:
    return str(tx.get("id") or "").strip().casefold()


def is_excluded_send_id(tx: Any) -> bool:
    """Proof / dest-test ids never book as standing Thaís / Rent / JR sends."""
    tid = tx_id_key(tx) if isinstance(tx, dict) else str(tx or "").strip().casefold()
    if not tid:
        return False
    if tid in {_fold(x) for x in THAIS_EXCLUDED_SEND_IDS}:
        return True
    return any(tid == p or tid.startswith(p) for p in EXCLUDED_SEND_ID_PREFIXES)


def tx_type(tx: Dict[str, Any]) -> str:
    return str(tx.get("type") or "").strip().casefold()


def tx_status(tx: Dict[str, Any]) -> str:
    return str(tx.get("status") or "").strip().casefold()


def tx_currency(tx: Dict[str, Any]) -> str:
    amt = tx.get("amount")
    if isinstance(amt, dict):
        return str(amt.get("currency") or tx.get("currency") or "").strip().upper()
    return str(tx.get("currency") or "").strip().upper()


def tx_amount(tx: Dict[str, Any]) -> float:
    amt = tx.get("amount")
    if isinstance(amt, dict):
        return _f(amt.get("amount"), _f(tx.get("native_amount", {}).get("amount") if isinstance(tx.get("native_amount"), dict) else None))
    return _f(amt, _f(tx.get("amount_display")))


def tx_date(tx: Dict[str, Any]) -> Optional[date]:
    raw = str(tx.get("date") or tx.get("created_at") or tx.get("updated_at") or "")
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def tx_in_month(tx: Dict[str, Any], month: date) -> bool:
    d = tx_date(tx)
    if d is None:
        return False
    return d.year == month.year and d.month == month.month


def is_ignored_type(tx: Dict[str, Any]) -> bool:
    return tx_type(tx) in IGNORE_TYPES


def is_usdc_send(tx: Dict[str, Any]) -> bool:
    if not isinstance(tx, dict) or tx.get("deleted"):
        return False
    if is_ignored_type(tx):
        return False
    if tx_type(tx) != SEND_TYPE:
        return False
    if tx_currency(tx) and tx_currency(tx) != USDC:
        return False
    status = tx_status(tx)
    # Book only completed. Pending / failed / canceled / expired do not enter the book.
    if status != "completed":
        return False
    return True


def send_spend_amount(tx: Dict[str, Any]) -> float:
    """Outflow of a USDC send, always positive dollars."""
    amt = tx_amount(tx)
    return round(abs(amt), 2) if amt != 0 else 0.0


def _blob(tx: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in ("description", "details", "to", "from"):
        val = tx.get(key)
        if isinstance(val, dict):
            parts.extend(str(v) for v in val.values() if v)
        elif val:
            parts.append(str(val))
    return _fold(" ".join(parts))


def dest_fingerprint(tx: Dict[str, Any]) -> str:
    dest = tx.get("to") or {}
    if isinstance(dest, dict):
        for key in ("address", "email", "phone", "id"):
            raw = str(dest.get(key) or "").strip()
            if raw:
                return raw
    return str(tx.get("to_address") or tx.get("destination") or "").strip()


def dest_address(tx: Dict[str, Any]) -> str:
    """On-chain dest only. Never email or phone."""
    dest = tx.get("to") or {}
    if isinstance(dest, dict):
        resource = str(dest.get("resource") or "").strip().casefold()
        if resource in {"email", "phone"}:
            return ""
        addr = str(dest.get("address") or "").strip()
        if addr:
            return addr
    return str(tx.get("to_address") or "").strip()


def dest_matches_address(tx: Dict[str, Any], expected: str) -> bool:
    want = str(expected or "").strip()
    if not want:
        return False
    got = dest_address(tx) or dest_fingerprint(tx)
    return bool(got) and got == want


def tx_network(tx: Dict[str, Any]) -> str:
    dest = tx.get("to") or {}
    if isinstance(dest, dict):
        raw = dest.get("network") or dest.get("chain")
        if raw:
            return str(raw).strip().casefold()
    return str(tx.get("network") or tx.get("chain") or "").strip().casefold()


def network_ok(tx: Dict[str, Any], expected: str) -> bool:
    """Missing network is ok (v2 address send). Explicit other chain is not."""
    got = tx_network(tx)
    if not got:
        return True
    return got == str(expected or "").strip().casefold()


def dest_email(tx: Dict[str, Any]) -> str:
    """Email dest only. Never phone or chain address."""
    dest = tx.get("to") or {}
    if isinstance(dest, dict):
        resource = str(dest.get("resource") or "").strip().casefold()
        if resource == "phone":
            return ""
        email = str(dest.get("email") or "").strip()
        if email:
            return _fold(email)
        if resource == "email":
            return _fold(dest.get("address") or dest.get("id") or "")
    return _fold(tx.get("to_email") or "")


def _to_resource(tx: Dict[str, Any]) -> str:
    dest = tx.get("to") or {}
    if isinstance(dest, dict):
        return str(dest.get("resource") or "").strip().casefold()
    return ""


def _thais_name_hit(tx: Dict[str, Any]) -> bool:
    resource = _to_resource(tx)
    if resource and resource != "address":
        return False
    blob = _blob(tx)
    return any(n in blob for n in _THAIS_NAME_NEEDLES)


def _thais_dest_hit(tx: Dict[str, Any]) -> bool:
    return dest_matches_address(tx, THAIS_DEST) or (
        dest_fingerprint(tx) in THAIS_DEST_FINGERPRINTS
        and _to_resource(tx) in {"", "address"}
    )


def matches_thais(tx: Dict[str, Any]) -> bool:
    """Thaís = dest AwMH3… $415 from 2026-09-11, else pre-9/11 dest/named monthly.

    Dest $415 is 2026-09-11+ only. August dest-only $895 still joins (#433).
    Weekly $208 never paints. 2026-09-04 never paints. Proof id excluded.
    """
    if is_excluded_send_id(tx):
        return False
    if dest_matches_address(tx, JR_SELF_SEND_DEST):
        return False
    d = tx_date(tx)
    if d == THAIS_NEVER_PAINT_DATE:
        return False
    amt = send_spend_amount(tx)
    if _near_amount(amt, THAIS_DEAD_WEEKLY_AMOUNT):
        return False
    dest_hit = _thais_dest_hit(tx)
    # Standing $415 dest is 2026-09-11+ only. Do not rewrite August 895.
    if dest_hit and _near_amount(amt, THAIS_STANDING_AMOUNT):
        if not network_ok(tx, THAIS_NETWORK):
            return False
        return d is None or d >= STANDING_FIRST_DATE
    # Dest rewrite owns 9/11+ (415 path above). Pre-9/11 monthly stays.
    if d is not None and d >= STANDING_FIRST_DATE:
        return False
    # Dest-only August 895 after dest rewrite has dest but no name blob.
    # Do not require solana here — dest rewrite owns that gate for $415.
    if dest_hit and _near_amount(amt, THAIS_PRE_STANDING_AMOUNT):
        return True
    return _thais_name_hit(tx)


def matches_rent(tx: Dict[str, Any]) -> bool:
    """Rent = completed v2 USDC type=send to nvolkern@gmail.com only. Email only.

    Daily $25 books through 2026-08-30 only. 8/31–9/10 $25 do not paint.
    Standing $350 is one row from 2026-09-11. JR / Thaís dests never book as Rent.
    """
    if tx_type(tx) != SEND_TYPE:
        return False
    if tx_status(tx) != "completed":
        return False
    if is_excluded_send_id(tx):
        return False
    if dest_matches_address(tx, JR_SELF_SEND_DEST):
        return False
    if dest_matches_address(tx, THAIS_DEST):
        return False
    resource = _to_resource(tx)
    if resource and resource != "email":
        return False
    email = dest_email(tx)
    if not email or email not in {_fold(x) for x in RENT_DEST_FINGERPRINTS}:
        return False
    amt = send_spend_amount(tx)
    d = tx_date(tx)
    if _near_amount(amt, RENT_STANDING_AMOUNT):
        return d is not None and d >= STANDING_FIRST_DATE
    if _near_amount(amt, RENT_DAILY_AMOUNT):
        return d is not None and d <= RENT_DAILY_LAST_DATE
    return False


def matches_jr_self_send(tx: Dict[str, Any]) -> bool:
    """JR self-send = dest CzuRx… standing 70 from 2026-09-11. Not Rent / Thaís / mint."""
    if is_excluded_send_id(tx):
        return False
    if dest_matches_address(tx, THAIS_DEST):
        return False
    if dest_email(tx) and dest_email(tx) in {_fold(x) for x in RENT_DEST_FINGERPRINTS}:
        return False
    if not dest_matches_address(tx, JR_SELF_SEND_DEST):
        return False
    if not network_ok(tx, JR_SELF_SEND_NETWORK):
        return False
    amt = send_spend_amount(tx)
    if not _near_amount(amt, JR_SELF_SEND_AMOUNT):
        return False
    d = tx_date(tx)
    return d is None or d >= STANDING_FIRST_DATE


def extract_raw_txs(payload: Any) -> List[Dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [t for t in payload if isinstance(t, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("transactions", "sends"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [t for t in rows if isinstance(t, dict)]
    data = payload.get("data")
    if isinstance(data, dict):
        rows = data.get("transactions") or data.get("data")
        if isinstance(rows, list):
            return [t for t in rows if isinstance(t, dict)]
    return []


def collect_usdc_sends(payload: Any) -> List[Dict[str, Any]]:
    """Flatten a snapshot / v2 payload to type=send USDC rows. No invent."""
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for tx in extract_raw_txs(payload):
        if not is_usdc_send(tx):
            continue
        tid = str(tx.get("id") or "")
        if tid and tid in seen:
            continue
        if tid:
            seen.add(tid)
        rec = dict(tx)
        rec["amount"] = -abs(send_spend_amount(tx))
        rec["currency"] = USDC
        rec.setdefault("date", (tx.get("created_at") or "")[:10])
        out.append(rec)
    return out


def load_send_book(
    snapshots: Optional[Dict[str, Any]] = None,
    *,
    path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Read the prism snapshot or an in-memory book. Never hits the network."""
    if snapshots:
        for key in ("coinbase_usdc_sends", "usdc_sends"):
            if snapshots.get(key):
                return collect_usdc_sends(snapshots.get(key))
        cb = snapshots.get("coinbase") or {}
        if isinstance(cb, dict) and (cb.get("usdc_transactions") or cb.get("transactions")):
            # coinbase_latest.json is balances-only; ignore unless it carries txs.
            if cb.get("usdc_transactions") or cb.get("account_currency") == USDC:
                return collect_usdc_sends(cb.get("usdc_transactions") or cb)
    p = path or (SNAPSHOTS_DIR / SNAPSHOT_NAME)
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return collect_usdc_sends(data)
    return []


def prism_key_present() -> bool:
    """True when the CDP key file exists on this host. Does not read it."""
    return PRISM_KEY_PATH.is_file()


_MONTH_NUM = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def _item_kind(item_name: str) -> str:
    """First token, after dropping a leading month so 'August Rent' → rent."""
    toks = _fold(item_name).split()
    if not toks:
        return ""
    if toks[0] in _MONTH_NUM and len(toks) > 1:
        return toks[1]
    return toks[0]


def _item_send_month(item_name: str, as_of: date) -> date:
    """Month-prefixed rows use that month. Bare 'Rent' uses as_of month."""
    toks = _fold(item_name).split()
    if toks and toks[0] in _MONTH_NUM:
        return date(as_of.year, _MONTH_NUM[toks[0]], 1)
    return as_of


def _as_et(val: Any) -> datetime:
    if val is None:
        return datetime.now(timezone.utc).astimezone(PAY_FRIDAY_TZ)
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc).astimezone(PAY_FRIDAY_TZ)
        return val.astimezone(PAY_FRIDAY_TZ)
    if isinstance(val, date):
        return datetime.combine(val, time.min, tzinfo=PAY_FRIDAY_TZ)
    raw = str(val).strip()
    try:
        if raw.endswith("Z"):
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            if "T" in raw:
                return dt.replace(tzinfo=timezone.utc).astimezone(PAY_FRIDAY_TZ)
            return dt.replace(tzinfo=PAY_FRIDAY_TZ)
        return dt.astimezone(PAY_FRIDAY_TZ)
    except ValueError:
        try:
            d = date.fromisoformat(raw[:10])
        except ValueError:
            return datetime.now(timezone.utc).astimezone(PAY_FRIDAY_TZ)
        return datetime.combine(d, time.min, tzinfo=PAY_FRIDAY_TZ)


def next_pay_friday(as_of: Any = None) -> datetime:
    """Next 1:00 PM ET pay Friday on the 14-day grid from 2026-09-11."""
    now = _as_et(as_of)
    if now <= PAY_FRIDAY_FIRST:
        return PAY_FRIDAY_FIRST
    step = timedelta(days=PAY_FRIDAY_CADENCE_DAYS)
    periods = (now - PAY_FRIDAY_FIRST) // step
    candidate = PAY_FRIDAY_FIRST + periods * step
    if now <= candidate:
        return candidate
    return candidate + step


def standing_send_defs() -> Tuple[Dict[str, Any], ...]:
    """Merge-only standing join rows. Not completed txs. Not live-money."""
    return (
        {
            "id": "standing-thais-415",
            "kind": "thais",
            "label": "Thaís",
            "type": SEND_TYPE,
            "currency": USDC,
            "amount": THAIS_STANDING_AMOUNT,
            "dest": THAIS_DEST,
            "network": THAIS_NETWORK,
            "cadence_days": PAY_FRIDAY_CADENCE_DAYS,
            "first_at": PAY_FRIDAY_FIRST.isoformat(),
            "tz": PAY_FRIDAY_TZ_NAME,
            "book_as": "thais",
        },
        {
            "id": "standing-nicole-rent-350",
            "kind": "rent",
            "label": "Nicole Rent",
            "type": SEND_TYPE,
            "currency": USDC,
            "amount": RENT_STANDING_AMOUNT,
            "dest": RENT_DEST_EMAIL,
            "dest_kind": "email",
            "cadence_days": PAY_FRIDAY_CADENCE_DAYS,
            "first_at": PAY_FRIDAY_FIRST.isoformat(),
            "tz": PAY_FRIDAY_TZ_NAME,
            "book_as": "rent",
            "book_shape": "one_row",
        },
        {
            "id": "standing-jr-self-send-70",
            "kind": "jr_self_send",
            "label": JR_SELF_SEND_LABEL,
            "type": SEND_TYPE,
            "currency": USDC,
            "amount": JR_SELF_SEND_AMOUNT,
            "dest": JR_SELF_SEND_DEST,
            "network": JR_SELF_SEND_NETWORK,
            "cadence_days": PAY_FRIDAY_CADENCE_DAYS,
            "first_at": PAY_FRIDAY_FIRST.isoformat(),
            "tz": PAY_FRIDAY_TZ_NAME,
            "book_as": "jr_self_send",
            "not": ("rent", "thais", "mint"),
        },
    )


def next_standing_sends(as_of: Any = None) -> List[Dict[str, Any]]:
    nxt = next_pay_friday(as_of)
    out: List[Dict[str, Any]] = []
    for row in standing_send_defs():
        rec = dict(row)
        rec["next_at"] = nxt.isoformat()
        rec["next_date"] = nxt.date().isoformat()
        out.append(rec)
    return out


def merge_standing_into_book(book: Dict[str, Any], *, as_of: Any = None) -> Dict[str, Any]:
    """Merge standing join rows onto an existing type=send book. No live rebuild."""
    out = dict(book) if isinstance(book, dict) else {"transactions": []}
    out["standing"] = list(next_standing_sends(as_of))
    notes = str(out.get("notes") or "")
    merge_note = (
        "merge-only standing join. type=send USDC. "
        "no live rebuild. no live-money."
    )
    if merge_note not in notes:
        out["notes"] = (notes + " " + merge_note).strip()
    return out


def item_sends_for_month(
    sends: Sequence[Dict[str, Any]],
    *,
    item_name: str,
    month: date,
) -> List[Dict[str, Any]]:
    key = _item_kind(item_name)
    send_month = _item_send_month(item_name, month)
    matched: List[Dict[str, Any]] = []
    for tx in sends:
        if not tx_in_month(tx, send_month):
            continue
        if key == "thais" and matches_thais(tx):
            matched.append(tx)
        elif key == "rent" and matches_rent(tx):
            matched.append(tx)
        elif key in {"jr", "self-send", "selfsend"} and matches_jr_self_send(tx):
            matched.append(tx)
    return matched


def actual_for_item(
    sends: Sequence[Dict[str, Any]],
    *,
    item_name: str,
    month: date,
) -> float:
    rows = item_sends_for_month(sends, item_name=item_name, month=month)
    return round(sum(send_spend_amount(t) for t in rows), 2)


def write_send_book(payload: Any, dest: Path) -> Dict[str, Any]:
    """Filter a v2 payload to type=send USDC and write the prism snapshot."""
    sends = collect_usdc_sends(payload)
    book = merge_standing_into_book(
        {
            "source": "coinbase_v2_usdc",
            "account_currency": USDC,
            "transactions": sends,
            "notes": (
                "type=send only. lend/lock ignored. Merge-only standing join. "
                "No live rebuild. No Transfer key. Key stays on prism."
            ),
        }
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(book, indent=2) + "\n", encoding="utf-8")
    return book
