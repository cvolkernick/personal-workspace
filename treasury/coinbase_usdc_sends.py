"""Coinbase v2 USDC send book — display-only actuals for Thaís.

Venue is Coinbase v2 USDC `type=send` only. Ignore lend / lock / One Card / X Money.
Rent dest fingerprint is HOLD (do not guess phone vs address). Recurring send is HOLD.
No Transfer key. No sender code. Key lives only on prism.

Tests use fixtures only. This module never copies or logs the CDP key.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

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

# Thaís attribution is name-on-send only. Do not invent dest fingerprints.
# Rent dest (phone vs address) is HOLD — empty set, never guess.
_THAIS_NAME_NEEDLES = frozenset({"thais", "thaís"})
THAIS_DEST_FINGERPRINTS: frozenset[str] = frozenset()
RENT_DEST_FINGERPRINTS: frozenset[str] = frozenset()


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
    if status in {"failed", "canceled", "cancelled", "expired"}:
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


def _to_resource(tx: Dict[str, Any]) -> str:
    dest = tx.get("to") or {}
    if isinstance(dest, dict):
        return str(dest.get("resource") or "").strip().casefold()
    return ""


def matches_thais(tx: Dict[str, Any]) -> bool:
    """Thaís = named type=send to an address. Never phone / unlabeled / amount-guess."""
    dest = dest_fingerprint(tx)
    if dest and dest in THAIS_DEST_FINGERPRINTS:
        return True
    resource = _to_resource(tx)
    if resource and resource != "address":
        return False
    blob = _blob(tx)
    return any(n in blob for n in _THAIS_NAME_NEEDLES)


def matches_rent(tx: Dict[str, Any]) -> bool:
    """HOLD — dest fingerprint (phone vs address) is still open. Do not guess."""
    dest = dest_fingerprint(tx)
    return bool(dest) and dest in RENT_DEST_FINGERPRINTS


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


def item_sends_for_month(
    sends: Sequence[Dict[str, Any]],
    *,
    item_name: str,
    month: date,
) -> List[Dict[str, Any]]:
    key = _fold(item_name).split()[0] if item_name else ""
    matched: List[Dict[str, Any]] = []
    for tx in sends:
        if not tx_in_month(tx, month):
            continue
        if key == "thais" and matches_thais(tx):
            matched.append(tx)
        elif key == "rent" and matches_rent(tx):
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
    book = {
        "source": "coinbase_v2_usdc",
        "account_currency": USDC,
        "transactions": sends,
        "notes": (
            "type=send only. lend/lock ignored. Recurring send HOLD. "
            "No Transfer key. Key stays on prism."
        ),
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(book, indent=2) + "\n", encoding="utf-8")
    return book
