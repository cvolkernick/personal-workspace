"""Glance row for the four X Money Plaid balances (#996).

Plaid ``balances.current`` is the headline. YNAB cash is not read.
Slots match ``account_id`` only, so a rename cannot retarget a chip.
The known extra account named Default is not a chip; it is a follow-up note.
A missing or unexpected account sets ``warning`` instead of rendering a subset.
A failed or item-error read stores no balances, so last-known-good is not live.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Production Item, pinned 2026-10-02. Masks are comments only — never match keys.
# Main 2201, Auto Fleet 0895, Collateral 3326, Utilities 4867.
DEFAULT_ACCOUNTS: tuple[Dict[str, str], ...] = (
    {
        "slot": "main",
        "label": "X Money Main",
        "account_id": "8pe84rZDNpuy6NP9EyMns7OrJ7Ja5gFZA190n",
    },
    {
        "slot": "auto_fleet",
        "label": "X Money Auto Fleet",
        "account_id": "ZzO8KkE17zSRK37EnRaNHNVdnNngLEHvr4xOw",
    },
    {
        "slot": "collateral",
        "label": "X Money Collateral",
        "account_id": "oKN3MEYbvKHjaedZPjwzHEaAkEkgnjsOLJP7E",
    },
    {
        "slot": "utilities",
        "label": "X Money Utilities",
        "account_id": "5pXa0vmw8puL9q3DMLeOUx7e8x8aA1FVQDKox",
    },
)

STALE_AFTER_HOURS = 6.0
CONFIG_DIR = Path.home() / ".config" / "plaid-bank"
PLAID_BALANCE_PATH = "/accounts/balance/get"

FetchFn = Callable[[], Dict[str, Any]]


def _now(now: Optional[datetime] = None) -> datetime:
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return clock.astimezone(timezone.utc)


def _iso(when: datetime) -> str:
    return _now(when).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def stale_after_hours(config: Optional[Dict[str, Any]]) -> float:
    cfg = (config or {}).get("plaid_x_money") or {}
    try:
        hours = float(cfg.get("stale_after_hours") or STALE_AFTER_HOURS)
    except (TypeError, ValueError):
        hours = STALE_AFTER_HOURS
    return hours if hours > 0 else STALE_AFTER_HOURS


def pins_from_config(config: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Config accounts override the module pin when any account_id is set."""
    cfg = (config or {}).get("plaid_x_money") or {}
    raw = cfg.get("accounts")
    if not isinstance(raw, list) or not raw:
        return [dict(row) for row in DEFAULT_ACCOUNTS]
    pins: List[Dict[str, str]] = []
    for index, default in enumerate(DEFAULT_ACCOUNTS):
        item = raw[index] if index < len(raw) and isinstance(raw[index], dict) else {}
        pins.append(
            {
                "slot": str(item.get("slot") or default["slot"]),
                "label": str(item.get("label") or default["label"]),
                "account_id": str(item.get("account_id") or "").strip(),
            }
        )
    if not any(pin["account_id"] for pin in pins):
        return [dict(row) for row in DEFAULT_ACCOUNTS]
    return pins


def _current_dollars(account: Dict[str, Any]) -> Optional[float]:
    balances = account.get("balances")
    if isinstance(balances, dict):
        raw = balances.get("current")
    else:
        raw = account.get("current") if "current" in account else None
    if raw is None or raw == "":
        return None
    try:
        cents = (Decimal(str(raw)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    except Exception:
        return None
    return float(cents / Decimal("100"))


def _is_default_account(account: Dict[str, Any]) -> bool:
    for key in ("name", "official_name"):
        name = str(account.get(key) or "").strip().casefold()
        if name == "default" or name.startswith("default ") or name.startswith("default-"):
            return True
    return False


def _extra_label(account: Dict[str, Any]) -> str:
    name = str(account.get("name") or account.get("official_name") or "").strip()
    return name or "unnamed account"


def _item_error_text(payload: Dict[str, Any]) -> str:
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    err = item.get("error") or payload.get("error")
    if not err:
        return ""
    if isinstance(err, str):
        return err.strip()[:180]
    if not isinstance(err, dict):
        return "Plaid item error"
    code = str(err.get("error_code") or "").strip()
    msg = str(err.get("error_message") or "").strip()
    text = " ".join(part for part in (code, msg) if part)
    return (text or "Plaid item error")[:180]


def _blank_accounts(pins: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    return [
        {
            "slot": pin["slot"],
            "label": pin["label"],
            "account_id": pin["account_id"],
            "current": None,
        }
        for pin in pins
    ]


def _row(
    pins: List[Dict[str, str]],
    *,
    ok: bool,
    stale: bool,
    reason: str,
    now: datetime,
    hours: float,
    accounts: Optional[List[Dict[str, Any]]] = None,
    warning: Optional[str] = None,
    follow_up: Optional[str] = None,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "source": "plaid",
        "ok": ok,
        "stale": stale,
        "stale_reason": reason,
        "as_of": as_of or _iso(now),
        "stale_after_hours": hours,
        "accounts": accounts if accounts is not None else _blank_accounts(pins),
        "warning": warning,
        "follow_up": follow_up,
    }


def failed_row(
    pins: List[Dict[str, str]],
    reason: str,
    *,
    now: Optional[datetime] = None,
    hours: float = STALE_AFTER_HOURS,
) -> Dict[str, Any]:
    return _row(
        pins,
        ok=False,
        stale=True,
        reason=reason[:180],
        now=_now(now),
        hours=hours,
    )


def shape_plaid_x_money(
    payload: Dict[str, Any],
    pins: List[Dict[str, str]],
    *,
    now: Optional[datetime] = None,
    stale_after_hours: float = STALE_AFTER_HOURS,
) -> Dict[str, Any]:
    """Map a Plaid ``/accounts/balance/get`` body onto the four Glance slots."""
    clock = _now(now)
    hours = stale_after_hours if stale_after_hours > 0 else STALE_AFTER_HOURS
    item_error = _item_error_text(payload or {})
    raw_accounts = payload.get("accounts") if isinstance(payload, dict) else None
    accounts = [a for a in raw_accounts or [] if isinstance(a, dict)]
    by_id: Dict[str, Dict[str, Any]] = {}
    extras: List[Dict[str, Any]] = []
    pin_ids = {pin["account_id"] for pin in pins if pin.get("account_id")}
    for account in accounts:
        account_id = str(account.get("account_id") or "").strip()
        if account_id and account_id in pin_ids:
            by_id.setdefault(account_id, account)
        else:
            extras.append(account)

    missing = [pin["label"] for pin in pins if not pin.get("account_id") or pin["account_id"] not in by_id]
    unpinned = [pin["label"] for pin in pins if not pin.get("account_id")]
    defaults = [account for account in extras if _is_default_account(account)]
    unexpected = [account for account in extras if account not in defaults]
    warning_bits: List[str] = []
    if unpinned:
        warning_bits.append("account_ids are not pinned (" + ", ".join(unpinned) + ")")
    elif missing:
        warning_bits.append("missing " + ", ".join(missing))
    if unexpected:
        warning_bits.append(
            "unexpected " + ", ".join(_extra_label(account) for account in unexpected)
        )
    warning = None
    if warning_bits:
        warning = "X Money account set changed: " + "; ".join(warning_bits)
    follow_up = None
    if defaults:
        follow_up = (
            "Follow-up: Plaid account Default is on this Item and is not a Glance chip."
        )

    fetched_at = _parse(payload.get("as_of")) or clock
    aged = clock - fetched_at > timedelta(hours=hours)
    if item_error or aged:
        reason = item_error or f"balance is older than {hours:g}h"
        return _row(
            pins,
            ok=False,
            stale=True,
            reason=reason,
            now=clock,
            hours=hours,
            warning=warning,
            follow_up=follow_up,
            as_of=_iso(fetched_at),
        )

    shaped = []
    for pin in pins:
        account = by_id.get(pin["account_id"]) if pin.get("account_id") else None
        shaped.append(
            {
                "slot": pin["slot"],
                "label": pin["label"],
                "account_id": pin["account_id"],
                "current": _current_dollars(account) if account else None,
            }
        )
    return _row(
        pins,
        ok=True,
        stale=False,
        reason="",
        now=clock,
        hours=hours,
        accounts=shaped,
        warning=warning,
        follow_up=follow_up,
        as_of=_iso(fetched_at),
    )


def is_stale(block: Optional[Dict[str, Any]], *, now: Optional[datetime] = None) -> bool:
    """True when the row must not be shown as a live balance."""
    if not isinstance(block, dict):
        return True
    if block.get("ok") is False or block.get("stale") is True:
        return True
    clock = _now(now)
    as_of = _parse(block.get("as_of"))
    if as_of is None:
        return True
    try:
        hours = float(block.get("stale_after_hours") or STALE_AFTER_HOURS)
    except (TypeError, ValueError):
        hours = STALE_AFTER_HOURS
    if hours <= 0:
        hours = STALE_AFTER_HOURS
    return clock - as_of > timedelta(hours=hours)


def _read_line(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except OSError:
        return ""


def _plaid_host() -> str:
    raw = (os.environ.get("PLAID_ENV") or _read_line(CONFIG_DIR / "env") or "").lower()
    if "sandbox" in raw:
        return "https://sandbox.plaid.com"
    return "https://production.plaid.com"


def _safe_http_error(status: int, body: str) -> str:
    try:
        err = json.loads(body)
    except json.JSONDecodeError:
        return f"Plaid HTTP {status}"
    if not isinstance(err, dict):
        return f"Plaid HTTP {status}"
    code = str(err.get("error_code") or "").strip()
    msg = str(err.get("error_message") or "").strip()
    text = " ".join(part for part in (code, msg) if part)
    if not text:
        return f"Plaid HTTP {status}"
    return f"Plaid HTTP {status}: {text[:160]}"


def fetch_plaid_balances(*, timeout: float = 20.0) -> Dict[str, Any]:
    """Read-only ``/accounts/balance/get``. Raises RuntimeError with no secrets."""
    client_id = (os.environ.get("PLAID_CLIENT_ID") or _read_line(CONFIG_DIR / "client_id")).strip()
    secret = (os.environ.get("PLAID_SECRET") or _read_line(CONFIG_DIR / "secret")).strip()
    access = (os.environ.get("PLAID_ACCESS_TOKEN") or _read_line(CONFIG_DIR / "access_token")).strip()
    missing = [
        name
        for name, value in (
            ("PLAID_CLIENT_ID", client_id),
            ("PLAID_SECRET", secret),
            ("PLAID_ACCESS_TOKEN", access),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("missing Plaid credentials: " + ", ".join(missing))
    payload = json.dumps(
        {"client_id": client_id, "secret": secret, "access_token": access}
    ).encode("utf-8")
    req = urllib.request.Request(
        _plaid_host() + PLAID_BALANCE_PATH,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(_safe_http_error(exc.code, err_body)) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Plaid network error: {exc.reason}") from exc
    try:
        out = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Plaid returned invalid JSON") from exc
    if not isinstance(out, dict):
        raise RuntimeError("Plaid returned no account list")
    return out


def attach_plaid_x_money(
    *,
    config: Optional[Dict[str, Any]],
    prefer_live: bool,
    prior: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    fetch: Optional[FetchFn] = None,
) -> Dict[str, Any]:
    """Live read replaces the block. Failure does not keep prior balances.

    Offline keeps ``prior`` so a config save does not wipe the last producer
    read. The dashboard still blanks the figures once ``as_of`` ages out.
    """
    pins = pins_from_config(config)
    hours = stale_after_hours(config)
    clock = _now(now)
    if not prefer_live:
        if isinstance(prior, dict) and prior.get("source") == "plaid":
            return prior
        return failed_row(
            pins,
            "Plaid X Money was not fetched on this offline run",
            now=clock,
            hours=hours,
        )
    try:
        payload = (fetch or fetch_plaid_balances)()
    except Exception as exc:
        return failed_row(pins, str(exc), now=clock, hours=hours)
    if not isinstance(payload, dict):
        return failed_row(pins, "Plaid returned no account list", now=clock, hours=hours)
    return shape_plaid_x_money(payload, pins, now=clock, stale_after_hours=hours)


def _plaid_block(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pull a Plaid row out of an evaluation doc or a bare block.

    A YNAB ``x_money_latest.json`` has ``source`` other than ``plaid`` and no
    ``plaid_x_money`` key, so it cannot become a Glance row (#996).
    """
    snap = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else None
    host = snap if snap is not None else data
    block = host.get("plaid_x_money") if isinstance(host, dict) else None
    if isinstance(block, dict):
        return block
    if data.get("source") == "plaid" and (
        "accounts" in data or "ok" in data or "stale" in data
    ):
        return data
    return None


def read_prior_plaid_x_money(*paths: Path) -> Optional[Dict[str, Any]]:
    for path in paths:
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        block = _plaid_block(data)
        if isinstance(block, dict):
            return block
    return None
