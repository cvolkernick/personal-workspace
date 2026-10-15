"""Canonical income matcher, plus Braiins mining income.

The Cash Streams rolling chart classifies external inflows with
``classify_income_source``. The Monday forecast income drift check must
import this function too. A second copy will disagree on what counts.
``bitcoin_mining_income`` is not that matcher. It reads confirmed outputs
to the Braiins payout address (``BRAIINS_PAYOUT_ADDRESS``, else
``treasury/config.json`` ``braiins.payout_address``) and does not look at
YNAB or Plaid.

Match is case-insensitive on payee and category. A needle hits only as a
whole token (bounded by anything that is not a letter or digit), so
"Lyft Inc" and "HW*GrubHub Holdings Inc." count and "Grubby" does not.
Needles are lyft, grubhub or grub, turo, then rewards, interest, refunds,
and cash deposit. Payee is tried first, then category. Within one field,
first source wins, so Lyft still beats a later rewards needle on the same
payee.

This function does not apply transfer, starting-balance, or reconcile
exclusions. Callers that share the cash-streams filter apply those first,
then classify the remaining inflows. Anything that still returns None is
an unidentified credit and must stay under its own payee name.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

# Colors are the chart contract: Lyft pink, Grubhub orange, Turo gray.
# Gray is light enough to read on the Cash Streams dark panel.
# warn_if_zero is only the original three. A quiet Rewards window is normal
# and must not light the renamed-payee warning.
INCOME_SOURCE_LINES: Tuple[Dict[str, object], ...] = (
    {
        "id": "lyft",
        "label": "Lyft",
        "color": "#ff69b4",
        "needles": ("lyft",),
        "warn_if_zero": True,
    },
    {
        "id": "grubhub",
        "label": "Grubhub",
        "color": "#ff8c1a",
        "needles": ("grubhub", "grub"),
        "warn_if_zero": True,
    },
    {
        "id": "turo",
        "label": "Turo",
        "color": "#b7c0c8",
        "needles": ("turo",),
        "warn_if_zero": True,
    },
    {
        "id": "rewards",
        "label": "Rewards",
        "color": "#1abc9c",
        "needles": ("reward", "rewards", "cashback", "cash back"),
    },
    {
        "id": "interest",
        "label": "Interest",
        "color": "#5dade2",
        "needles": ("interest",),
    },
    {
        "id": "refunds",
        "label": "Refunds",
        "color": "#c39bd3",
        "needles": ("refund", "refunds"),
    },
    {
        "id": "cash_deposits",
        "label": "Cash deposits",
        "color": "#d4ac0d",
        "needles": ("cash deposit", "cash deposits"),
    },
)


@lru_cache(maxsize=None)
def _token(needle: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])")


def income_source_ids() -> Tuple[str, ...]:
    return tuple(str(row["id"]) for row in INCOME_SOURCE_LINES)


def income_source_warn_ids() -> Tuple[str, ...]:
    """Sources that should warn when they are $0 and inflow is not."""
    return tuple(
        str(row["id"]) for row in INCOME_SOURCE_LINES if row.get("warn_if_zero")
    )


def income_source_public() -> List[Dict[str, str]]:
    """Legend payload. Needles stay server-side."""
    out: List[Dict[str, str]] = []
    for row in INCOME_SOURCE_LINES:
        out.append(
            {
                "id": str(row["id"]),
                "label": str(row["label"]),
                "color": str(row["color"]),
            }
        )
    return out


def _match_field(blob: str) -> Optional[str]:
    folded = blob.casefold()
    if not folded.strip():
        return None
    for row in INCOME_SOURCE_LINES:
        needles = row["needles"]
        if not isinstance(needles, tuple):
            continue
        if any(_token(str(needle)).search(folded) for needle in needles):
            return str(row["id"])
    return None


def classify_income_source(payee: object = "", category: object = "") -> Optional[str]:
    """Return a named source id when payee or category matches. Else None."""
    for part in (payee, category):
        hit = _match_field(str(part or ""))
        if hit:
            return hit
    return None


# Yellow is the chart's existing --yellow. It stays distinct from Grubhub orange.
BITCOIN_MEAN_DAYS = 90
BITCOIN_COLOR = "#f5c542"
BRAIINS_PAYOUT_ADDRESS_ENV = "BRAIINS_PAYOUT_ADDRESS"
_MEMPOOL_TXS = "https://mempool.space/api/address/{address}/txs"
_MEMPOOL_CHAIN = "https://mempool.space/api/address/{address}/txs/chain/{txid}"
_CANDLE_URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
_CACHE_NAME = "braiins_address_income.json"
_PAGE_CAP = 40
# Coinbase rejects a candle request wider than 300 points. Stay one under.
_CANDLE_CHUNK_DAYS = 299
_UA = "personal-workspace-treasury/bitcoin-mining-income"


def bitcoin_band_public() -> Dict[str, str]:
    """Legend payload for the address-keyed mining band."""
    return {"id": "bitcoin", "label": "Bitcoin", "color": BITCOIN_COLOR}


def bitcoin_history_start(
    today: date,
    *,
    display_days: int = 90,
    mean_days: int = BITCOIN_MEAN_DAYS,
) -> date:
    """First day that can affect the earliest displayed trailing mean."""
    return today - timedelta(days=(display_days - 1) + (mean_days - 1))


def bitcoin_trailing_mean(
    day: date,
    usd_by_day: Mapping[str, float],
    *,
    mean_days: int = BITCOIN_MEAN_DAYS,
) -> float:
    """Trailing daily mean. Missing days count as zero."""
    if mean_days <= 0:
        return 0.0
    total = 0.0
    for offset in range(mean_days):
        key = (day - timedelta(days=offset)).isoformat()
        try:
            total += float(usd_by_day.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
    return round(total / float(mean_days), 2)


def bitcoin_mining_income(
    *,
    today: Optional[date] = None,
    address: Optional[str] = None,
    transactions: Optional[Sequence[Mapping[str, Any]]] = None,
    prices: Optional[Mapping[str, float]] = None,
    fetch_json: Optional[Callable[[str], Any]] = None,
    cache_path: Optional[Path] = None,
    config_path: Optional[Path] = None,
    timeout: float = 20.0,
) -> Dict[str, Any]:
    """Confirmed Braiins payouts in USD, keyed by the UTC day received.

    Counts outputs paid to the payout address. A transaction that spends
    from that address is a Coinbase sweep (change included) and is excluded.
    Unconfirmed transactions are excluded. Plaid's Coinbase feed is not read.

    An unset address (blank ``BRAIINS_PAYOUT_ADDRESS`` and no
    ``braiins.payout_address`` in config) yields an empty series, ``fetched``
    false, and no error. A mempool or price failure falls back to the last
    good cache for this address. The cache stores tx ids, days, and amounts
    — not the address. An empty mempool body is retried once. ``fetched`` is
    false when the address is unset or the read failed and the cache missed.

    Receipts older than ``bitcoin_history_start`` are dropped before pricing.
    One output outside the Coinbase candle window must not fail the receipts
    that the rolling chart can still show. Candle requests are split so a
    wider window stays under the exchange cap.
    """
    end = today or date.today()
    resolved = _resolve_payout_address(address, config_path=config_path)
    if not resolved:
        return _income_result(
            address_set=False, from_cache=False, fetched=False, deposits=[]
        )

    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()
    cache_file = cache_path if cache_path is not None else _default_cache_path()
    getter = fetch_json or (lambda url: _http_json(url, timeout=timeout))

    if transactions is not None:
        raw = mining_deposits_from_txs(resolved, transactions)
        priced = _price_deposits(raw, prices or {})
        return _income_result(
            address_set=True, from_cache=False, fetched=True, deposits=priced
        )

    fetched, failed = _fetch_address_txs(
        resolved,
        getter,
        stop_on=bitcoin_history_start(end),
    )
    if failed or fetched is None:
        return _fallback_or_zero(cache_file, digest)

    raw = mining_deposits_from_txs(resolved, fetched)
    raw = _deposits_in_price_window(raw, bitcoin_history_start(end))
    if prices is not None:
        priced = _price_deposits(raw, prices)
        price_ok = _prices_cover(raw, prices)
    elif not raw:
        priced = []
        price_ok = True
    else:
        closes, price_ok = _fetch_daily_closes(
            getter,
            bitcoin_history_start(end),
            end,
        )
        priced = _price_deposits(raw, closes)
        if price_ok:
            price_ok = _prices_cover(raw, closes)
    if not price_ok:
        return _fallback_or_zero(cache_file, digest)

    _write_cache(cache_file, digest, priced)
    return _income_result(
        address_set=True, from_cache=False, fetched=True, deposits=priced
    )


def mining_deposits_from_txs(
    address: str,
    txs: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Confirmed outputs to ``address``. Sweeps and unconfirmed txs are out."""
    target = address.casefold()
    found: List[Dict[str, Any]] = []
    seen = set()
    for tx in txs:
        if not isinstance(tx, dict):
            continue
        txid = str(tx.get("txid") or "")
        if not txid or txid in seen:
            continue
        status = tx.get("status")
        day = _block_day(status)
        if day is None:
            continue
        if _spends_from(tx, target):
            continue
        sats = _paid_sats(tx, target)
        if sats <= 0:
            continue
        seen.add(txid)
        found.append({"txid": txid, "day": day.isoformat(), "sats": sats})
    return found


def _income_result(
    *,
    address_set: bool,
    from_cache: bool,
    fetched: bool,
    deposits: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    rows = [dict(row) for row in deposits]
    return {
        "ok": True,
        "error": None,
        "address_set": address_set,
        "from_cache": from_cache,
        "fetched": fetched,
        "deposits": rows,
        "usd_by_day": _usd_by_day(rows),
    }


def _resolve_payout_address(
    address: Optional[str] = None,
    *,
    config_path: Optional[Path] = None,
) -> Optional[str]:
    """Env wins. A blank env falls through to ``braiins.payout_address``."""
    if address is not None:
        raw = address
    else:
        raw = os.environ.get(BRAIINS_PAYOUT_ADDRESS_ENV, "")
        if not str(raw or "").strip():
            raw = _payout_address_from_config(config_path)
    text = str(raw or "").strip()
    if not text or re.fullmatch(r"[A-Za-z0-9]+", text) is None:
        return None
    return text


def _payout_address_from_config(config_path: Optional[Path] = None) -> str:
    from treasury.adapters import load_config

    cfg = load_config(config_path) if config_path is not None else load_config()
    brai = cfg.get("braiins") if isinstance(cfg, dict) else None
    if not isinstance(brai, dict):
        return ""
    return str(brai.get("payout_address") or "").strip()


def _default_cache_path() -> Path:
    return Path(__file__).resolve().parent / "snapshots" / _CACHE_NAME


def _block_day(status: object) -> Optional[date]:
    if not isinstance(status, dict) or status.get("confirmed") is not True:
        return None
    try:
        ts = int(status.get("block_time"))
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
    except (OverflowError, OSError, ValueError):
        return None


def _spends_from(tx: Mapping[str, Any], target: str) -> bool:
    for vin in tx.get("vin") or []:
        if not isinstance(vin, dict):
            continue
        prev = vin.get("prevout")
        if not isinstance(prev, dict):
            continue
        addr = str(prev.get("scriptpubkey_address") or "")
        if addr.casefold() == target:
            return True
    return False


def _paid_sats(tx: Mapping[str, Any], target: str) -> int:
    total = 0
    for vout in tx.get("vout") or []:
        if not isinstance(vout, dict):
            continue
        addr = str(vout.get("scriptpubkey_address") or "")
        if addr.casefold() != target:
            continue
        try:
            sats = int(vout.get("value"))
        except (TypeError, ValueError):
            continue
        if sats > 0:
            total += sats
    return total


def _price_deposits(
    deposits: Sequence[Mapping[str, Any]],
    prices: Mapping[str, float],
) -> List[Dict[str, Any]]:
    priced: List[Dict[str, Any]] = []
    for row in deposits:
        day = str(row["day"])
        sats = int(row["sats"])
        close = _close_for_day(day, prices)
        btc = sats / 1e8
        usd = round(btc * close, 2) if close is not None else 0.0
        priced.append(
            {
                "txid": row["txid"],
                "day": day,
                "sats": sats,
                "btc": round(btc, 8),
                "close": close,
                "usd": usd,
            }
        )
    return priced


def _close_for_day(day: str, prices: Mapping[str, float]) -> Optional[float]:
    try:
        close = float(prices[day])
    except (KeyError, TypeError, ValueError):
        return None
    if close <= 0:
        return None
    return close


def _prices_cover(deposits: Sequence[Mapping[str, Any]], prices: Mapping[str, float]) -> bool:
    return all(_close_for_day(str(row["day"]), prices) is not None for row in deposits)


def _deposits_in_price_window(
    deposits: Sequence[Mapping[str, Any]],
    start: date,
) -> List[Dict[str, Any]]:
    """Drop receipts the candle window is not asked to price."""
    kept: List[Dict[str, Any]] = []
    for row in deposits:
        try:
            day = date.fromisoformat(str(row.get("day") or ""))
        except ValueError:
            continue
        if day >= start:
            kept.append(dict(row))
    return kept


def _usd_by_day(deposits: Sequence[Mapping[str, Any]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for row in deposits:
        day = str(row.get("day") or "")
        try:
            usd = float(row.get("usd") or 0.0)
        except (TypeError, ValueError):
            continue
        if not day:
            continue
        out[day] = round(out.get(day, 0.0) + usd, 2)
    return out


def _empty_payload(payload: Any) -> bool:
    if payload is None:
        return True
    if payload == "" or payload == b"":
        return True
    return isinstance(payload, list) and len(payload) == 0


def _fetch_page(
    url: str,
    fetch_json: Callable[[str], Any],
) -> Tuple[Optional[List[Any]], bool]:
    """Return ``(page, failed)``. An empty body is retried once."""
    payload: Any = None
    for attempt in range(2):
        try:
            payload = fetch_json(url)
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError, TypeError):
            payload = None
            if attempt == 0:
                continue
            return None, True
        if _empty_payload(payload):
            if attempt == 0:
                continue
            return [], False
        if not isinstance(payload, list):
            return None, True
        return payload, False
    return None, True


def _fetch_address_txs(
    address: str,
    fetch_json: Callable[[str], Any],
    *,
    stop_on: date,
) -> Tuple[Optional[List[Any]], bool]:
    quoted = urllib.parse.quote(address, safe="")
    first_url = _MEMPOOL_TXS.format(address=quoted)
    page, failed = _fetch_page(first_url, fetch_json)
    if failed or page is None:
        return None, True
    # A still-empty first page is a failed read, not a real empty history.
    if not page:
        return None, True
    txs: List[Any] = list(page)
    cursor = _chain_cursor(page)
    oldest = _oldest_confirmed_day(page)
    pages = 1
    while cursor and pages < _PAGE_CAP:
        if oldest is not None and oldest <= stop_on:
            break
        chain_url = _MEMPOOL_CHAIN.format(
            address=quoted,
            txid=urllib.parse.quote(cursor, safe=""),
        )
        page, failed = _fetch_page(chain_url, fetch_json)
        if failed or page is None:
            return None, True
        if not page:
            break
        nxt = _chain_cursor(page)
        if nxt == cursor:
            break
        txs.extend(page)
        cursor = nxt
        oldest = _oldest_confirmed_day(page)
        pages += 1
    return txs, False


def _chain_cursor(page: Sequence[Any]) -> Optional[str]:
    for tx in reversed(page):
        if not isinstance(tx, dict):
            continue
        if _block_day(tx.get("status")) is None:
            continue
        txid = str(tx.get("txid") or "")
        if txid:
            return txid
    return None


def _oldest_confirmed_day(page: Sequence[Any]) -> Optional[date]:
    days: List[date] = []
    for tx in page:
        if not isinstance(tx, dict):
            continue
        day = _block_day(tx.get("status"))
        if day is not None:
            days.append(day)
    return min(days) if days else None


def _fetch_daily_closes(
    fetch_json: Callable[[str], Any],
    start: date,
    end: date,
) -> Tuple[Dict[str, float], bool]:
    """Daily BTC-USD closes. A range past the candle cap is requested in chunks."""
    if end < start:
        return {}, False
    merged: Dict[str, float] = {}
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=_CANDLE_CHUNK_DAYS - 1))
        part, ok = _fetch_closes_span(fetch_json, cursor, chunk_end)
        if not ok:
            return {}, False
        merged.update(part)
        cursor = chunk_end + timedelta(days=1)
    if not merged:
        return {}, False
    return merged, True


def _fetch_closes_span(
    fetch_json: Callable[[str], Any],
    start: date,
    end: date,
) -> Tuple[Dict[str, float], bool]:
    query = urllib.parse.urlencode(
        {
            "granularity": "86400",
            "start": _iso_z(start),
            "end": _iso_z(end + timedelta(days=1)),
        }
    )
    url = _CANDLE_URL + "?" + query
    payload: Any = None
    for attempt in range(2):
        try:
            payload = fetch_json(url)
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError, TypeError):
            payload = None
            if attempt == 0:
                continue
            return {}, False
        if _empty_payload(payload):
            if attempt == 0:
                continue
            return {}, False
        break
    closes = _closes_from_candles(payload)
    if not closes:
        return {}, False
    return closes, True


def _iso_z(day: date) -> str:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _closes_from_candles(payload: Any) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if not isinstance(payload, list):
        return out
    for row in payload:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            ts = int(row[0])
            close = float(row[4])
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        try:
            day = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            continue
        out[day] = close
    return out


def _read_cache(path: Path, digest: str) -> Optional[List[Dict[str, Any]]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(data, dict) or data.get("address_sha256") != digest:
        return None
    rows = data.get("deposits")
    if not isinstance(rows, list):
        return None
    cleaned: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = str(row.get("day") or "")
        txid = str(row.get("txid") or "")
        try:
            date.fromisoformat(day)
            sats = int(row.get("sats"))
            usd = round(float(row.get("usd")), 2)
        except (TypeError, ValueError):
            continue
        if not txid or sats <= 0:
            continue
        cleaned.append(
            {
                "txid": txid,
                "day": day,
                "sats": sats,
                "btc": round(sats / 1e8, 8),
                "close": row.get("close"),
                "usd": usd,
            }
        )
    return cleaned


def _write_cache(path: Path, digest: str, deposits: Sequence[Mapping[str, Any]]) -> None:
    payload = {
        "address_sha256": digest,
        "deposits": [dict(row) for row in deposits],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _fallback_or_zero(path: Path, digest: str) -> Dict[str, Any]:
    cached = _read_cache(path, digest)
    if cached is None:
        return _income_result(
            address_set=True, from_cache=False, fetched=False, deposits=[]
        )
    return _income_result(
        address_set=True, from_cache=True, fetched=True, deposits=cached
    )


def _http_json(url: str, *, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": _UA},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if not raw or not raw.strip():
        return []
    return json.loads(raw.decode("utf-8"))
