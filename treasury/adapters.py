"""Live + file snapshot adapters for Coinbase liquid balances and Robinhood portfolio/BP."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple  # noqa: F401

TREASURY_DIR = Path(__file__).resolve().parent
SNAPSHOTS_DIR = TREASURY_DIR / "snapshots"
CONFIG_PATH = TREASURY_DIR / "config.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or CONFIG_PATH
    data = load_json(p)
    if not data:
        return {
            "policy": {},
            "coinbase_manual": {},
            "robinhood": {"account_number": ""},
        }
    return data


def _parse_coinbase_balance_payload(payload: Dict[str, Any]) -> Dict[str, float]:
    """Extract liquid USDC/USD/BTC available from coinbase balance JSON."""
    totals = {"USDC": 0.0, "USD": 0.0, "BTC": 0.0}
    accounts = payload.get("accounts") or []
    for a in accounts:
        cur = (a.get("currency") or "").upper()
        if cur not in totals:
            continue
        try:
            totals[cur] += float((a.get("available_balance") or {}).get("value") or 0)
        except (TypeError, ValueError):
            continue
    return totals


def fetch_btc_usd_price(*, timeout: float = 20.0) -> Tuple[Optional[float], Optional[str]]:
    """Fetch mid/last BTC-USD via coinbase products get."""
    try:
        proc = subprocess.run(
            ["coinbase", "products", "get", "BTC-USD"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return None, "coinbase CLI not found"
    except subprocess.TimeoutExpired:
        return None, "coinbase products get timed out"
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout or "products get failed").strip()[:300]
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return None, f"invalid products JSON: {e}"
    price = data.get("price")
    try:
        return float(price), None
    except (TypeError, ValueError):
        return None, "no price in products get response"


def fetch_coinbase_liquid_live(
    *,
    timeout: float = 30.0,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Run `coinbase balance --paginate` and extract liquid USDC/BTC.

    Returns (result, error). result has liquid_usdc, liquid_btc, raw_currencies, source.
    """
    try:
        proc = subprocess.run(
            ["coinbase", "balance", "--paginate"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return None, "coinbase CLI not found"
    except subprocess.TimeoutExpired:
        return None, "coinbase balance timed out"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "coinbase balance failed").strip()
        return None, err[:500]

    text = proc.stdout.strip()
    if not text:
        return None, "empty coinbase balance output"

    accounts = []
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError as e:
            return None, f"invalid JSON from coinbase: {e}"
        if isinstance(obj, dict) and "accounts" in obj:
            accounts.extend(obj.get("accounts") or [])
        idx = end

    merged = {"accounts": accounts}
    totals = _parse_coinbase_balance_payload(merged)
    btc_price, price_err = fetch_btc_usd_price(timeout=min(20.0, timeout))
    liquid_btc = totals["BTC"]
    result = {
        "source": "live",
        "as_of": _now(),
        "liquid_usdc": totals["USDC"] + totals["USD"],
        "liquid_btc": liquid_btc,
        "btc_usd_price": btc_price,
        "liquid_btc_usd": (liquid_btc * btc_price) if btc_price is not None else None,
        "by_currency": totals,
        "account_count": len(accounts),
    }
    if price_err:
        result["btc_price_error"] = price_err
    return result, None


def fetch_coinbase_liquid(
    *,
    prefer_live: bool = True,
    snapshot_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Live Coinbase liquid read with file fallback."""
    snap = snapshot_path or (SNAPSHOTS_DIR / "coinbase_latest.json")
    err = None
    if prefer_live:
        live, err = fetch_coinbase_liquid_live()
        if live is not None:
            save_json(snap, live)
            return live
    file_data = load_json(snap)
    if file_data:
        out = dict(file_data)
        out["source"] = out.get("source") or "snapshot"
        if err:
            out["live_error"] = err
        return out
    return {
        "source": "empty",
        "as_of": _now(),
        "liquid_usdc": 0.0,
        "liquid_btc": 0.0,
        "live_error": err or "no snapshot",
    }


def fetch_robinhood_from_file(snapshot_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    snap = snapshot_path or (SNAPSHOTS_DIR / "robinhood_latest.json")
    return load_json(snap)


def normalize_robinhood_payload(data: Dict[str, Any], *, source: str = "snapshot") -> Dict[str, Any]:
    """Normalize MCP/API portfolio payload into treasury fields."""
    # Accept either flat treasury shape or raw MCP data envelope
    body: Dict[str, Any] = data
    inner = data.get("data")
    if isinstance(inner, dict) and (
        "total_value" in inner or "buying_power" in inner or "equity_value" in inner
    ):
        body = inner

    bp_obj = body.get("buying_power")
    if isinstance(bp_obj, dict):
        bp = float(bp_obj.get("buying_power") or 0)
        unlev = bp_obj.get("unleveraged_buying_power")
        unlev = None if unlev is None else float(unlev)
    else:
        bp = float(bp_obj or body.get("buying_power_value") or 0)
        unlev = body.get("unleveraged_buying_power")
        unlev = None if unlev is None else float(unlev)

    return {
        "source": source,
        "as_of": data.get("as_of") or body.get("as_of") or _now(),
        "total_value": float(body.get("total_value") or 0),
        "equity_value": float(body.get("equity_value") or 0),
        "cash": float(body.get("cash") or 0),
        "buying_power": bp,
        "unleveraged_buying_power": unlev,
        "margin_use": body.get("margin_use"),
        "currency": body.get("currency") or "USD",
        "account_number_last4": data.get("account_number_last4"),
    }


def write_robinhood_snapshot(payload: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Persist a Robinhood portfolio payload for dashboard/adapters."""
    snap = path or (SNAPSHOTS_DIR / "robinhood_latest.json")
    if "buying_power" in payload and "total_value" in payload and "source" in payload:
        save_json(snap, payload)
        return snap
    norm = normalize_robinhood_payload(payload, source=payload.get("source") or "live")
    save_json(snap, norm)
    return snap


def fetch_robinhood(
    *,
    prefer_live_file: bool = True,
    snapshot_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Read Robinhood portfolio/BP from snapshot (written by agent/MCP or tests).

    Live MCP is session-bound; CLI automation uses the last written snapshot.
    """
    snap = snapshot_path or (SNAPSHOTS_DIR / "robinhood_latest.json")
    file_data = load_json(snap) if prefer_live_file or True else None
    if file_data:
        if "buying_power" in file_data and not isinstance(file_data.get("buying_power"), dict):
            out = dict(file_data)
            out.setdefault("source", "snapshot")
            return out
        return normalize_robinhood_payload(file_data, source=file_data.get("source") or "snapshot")
    return {
        "source": "empty",
        "as_of": _now(),
        "total_value": 0.0,
        "equity_value": 0.0,
        "cash": 0.0,
        "buying_power": 0.0,
        "unleveraged_buying_power": None,
        "margin_use": None,
        "live_error": "no robinhood snapshot — write via agent MCP get_portfolio",
    }


def _merge_manual_with_one_card(
    manual: Dict[str, Any],
    one_card: Dict[str, Any],
) -> Dict[str, Any]:
    """Overlay YNAB One Card fields onto manual when manual card fields are empty."""
    out = dict(manual)
    if one_card.get("source") in (None, "empty") or one_card.get("live_error"):
        # Still allow partial overlay if balance present
        if one_card.get("card_balance") is None and one_card.get("balance_owed") is None:
            return out
    bal = one_card.get("card_balance")
    if bal is None:
        bal = one_card.get("balance_owed")
    avail = one_card.get("card_available_credit")
    if avail is None:
        avail = one_card.get("available_credit")

    def _empty(v: Any) -> bool:
        return v is None or v == ""

    if _empty(out.get("card_balance")) and bal is not None:
        out["card_balance"] = bal
        out["card_balance_source"] = "ynab"
    if _empty(out.get("card_available_credit")) and avail is not None:
        out["card_available_credit"] = avail
        out["card_available_credit_source"] = "ynab"
    return out


def build_snapshot(
    *,
    config: Optional[Dict[str, Any]] = None,
    prefer_live_coinbase: bool = True,
    prefer_live_ynab: bool = True,
    prefer_live_expenses: Optional[bool] = None,
) -> Dict[str, Any]:
    """Merge live/file venue reads with manual fields, YNAB, expense sheet."""
    from treasury.ynab_sync import fetch_one_card, fetch_rh_checking

    if prefer_live_expenses is None:
        prefer_live_expenses = prefer_live_ynab

    cfg = config if config is not None else load_config()
    cb = fetch_coinbase_liquid(prefer_live=prefer_live_coinbase)
    # Attach BTC price to snapshot file path results if missing
    if cb.get("btc_usd_price") is None and prefer_live_coinbase:
        price, _err = fetch_btc_usd_price()
        if price is not None:
            cb["btc_usd_price"] = price
            cb["liquid_btc_usd"] = _f_safe(cb.get("liquid_btc")) * price
    rh = fetch_robinhood()
    # Live One Card sync also writes RH checking snapshot
    one_card = fetch_one_card(prefer_live=prefer_live_ynab)
    rh_checking = fetch_rh_checking(prefer_live=prefer_live_ynab)
    from treasury.expenses_sync import fetch_expenses

    expenses = fetch_expenses(prefer_live=prefer_live_expenses)
    manual = _merge_manual_with_one_card(dict(cfg.get("coinbase_manual") or {}), one_card)
    from treasury.morpho_borrow_sync import fetch_morpho_borrow
    from treasury.morpho_hy_sync import fetch_morpho_hy
    from treasury.solstice_jr_sync import fetch_solstice_jr
    from treasury.usdg_hy_sync import fetch_usdg_hy

    from treasury.morpho_position_sync import (
        fetch_morpho_position,
        overlay_manual_with_position,
    )

    morpho_hy = fetch_morpho_hy(prefer_live=prefer_live_coinbase)
    usdg_hy = fetch_usdg_hy(prefer_live=prefer_live_coinbase)
    morpho_borrow = fetch_morpho_borrow(prefer_live=prefer_live_coinbase)
    morpho_position = fetch_morpho_position(
        prefer_live=prefer_live_coinbase, config=cfg
    )
    manual = overlay_manual_with_position(manual, morpho_position)
    solstice_jr = fetch_solstice_jr(prefer_live=prefer_live_coinbase)
    rh_cfg = cfg.get("robinhood") or {}
    # Overlay FCC settings yield/principal so the Settings form can re-show
    # a human override after Refresh. Live APY stays on snapshot.usdg_hy.
    rh = dict(rh)
    for key in (
        "usdg_earn_usdg",
        "usdg_earn_apy_est",
        "usdg_hy_apy_est",
        "rh_margin_apr",
        "margin_apr",
        "margin_loan_usd",
        "equity_collateral_usd",
    ):
        val = rh_cfg.get(key)
        if val is None or val == "":
            continue
        # 0 APR overlay is an empty settings override — do not paint as books.
        if key in ("rh_margin_apr", "margin_apr"):
            try:
                if float(val) == 0.0:
                    continue
            except (TypeError, ValueError):
                continue
        rh[key] = val
    ynab_cfg = cfg.get("ynab") or {}
    exp_cfg = cfg.get("expenses_sheet") or {}
    return {
        "as_of": _now(),
        "coinbase": cb,
        "coinbase_manual": manual,
        "morpho_hy": morpho_hy,
        "usdg_hy": usdg_hy,
        "morpho_borrow": morpho_borrow,
        "morpho_position": morpho_position,
        "solstice_jr": solstice_jr,
        "one_card": one_card,
        "rh_checking": rh_checking,
        "expenses": expenses,
        "robinhood": rh,
        "policy_overrides": cfg.get("policy") or {},
        "meta": {
            "config_path": str(CONFIG_PATH),
            "coinbase_source": cb.get("source"),
            "robinhood_source": rh.get("source"),
            "one_card_source": one_card.get("source"),
            "rh_checking_source": rh_checking.get("source"),
            "expenses_source": expenses.get("source"),
            "morpho_hy_source": morpho_hy.get("source"),
            "usdg_hy_source": usdg_hy.get("source"),
            "morpho_borrow_source": morpho_borrow.get("source"),
            "morpho_position_source": morpho_position.get("source"),
            "solstice_jr_source": solstice_jr.get("source"),
            "rh_accounts": {
                "primary": rh_cfg.get("account_number"),
                "agentic": rh_cfg.get("agentic_account_number"),
                "notes": rh_cfg.get("notes"),
            },
            "ynab": {
                "budget_name": ynab_cfg.get("budget_name") or one_card.get("budget_name"),
                "account_name": ynab_cfg.get("account_name") or one_card.get("account_name"),
                "checking_account_name": ynab_cfg.get("checking_account_name")
                or rh_checking.get("account_name"),
            },
            "expenses_sheet": {
                "sheet_id": exp_cfg.get("sheet_id") or expenses.get("sheet_id"),
                "sheet_name": expenses.get("sheet_name") or "Personal Expense Sheet",
            },
            "api_limits": {
                "morpho_loan": "app-only",
                "high_yield_vault": (
                    "balance app-only; vault_apy Morpho GraphQL vaultV2 "
                    "avgNetApy (Steakhouse HY USDC / Base; vault reference "
                    "only; ≠ Coinbase One product rate; soft-fail; no scrape; "
                    "do not invent product_apy)"
                ),
                "usdg_hy": (
                    "APY Morpho GraphQL vaultV2 avgNetApy "
                    "(Steakhouse USDG / Robinhood Chain 4663; soft-fail; no scrape). "
                    "Balance app-only. Do not invent a post-Gold rate."
                ),
                "morpho_borrow": (
                    "APR Morpho GraphQL marketById avgBorrowApy "
                    "(cbBTC/USDC / Base "
                    "0x9103c3b4e834476c9a62ea009ba2c884ee42e94e6e314a26f04d312434191836; "
                    "soft-fail; no scrape). Principal/collateral/LTV/liq price "
                    "from userByAddress on the Coinbase Borrow SCW (snapshot."
                    "morpho_position). Writes stay app-only. Do not invent rates."
                ),
                "morpho_position": (
                    "Loan books Morpho GraphQL userByAddress (Coinbase Borrow "
                    "SCW on Base). Public, no Coinbase auth, no backup-link sig. "
                    "Soft-fail keeps prior sidecar; Settings is not a source. "
                    "HY vault is a different wallet. Writes app-only."
                ),
                "solstice_jr": (
                    "JR-strcUSX live epoch APY from STRC-USX AccountingState "
                    "juniorApy (same formula as app.solstice.finance/strcusx). "
                    "Public getAccountInfo; HTML scrape rejected; partner REST "
                    "is instruction-only. Soft-fail keeps prior; spectrum stays "
                    "~20% docs_target if no live quote. Do not invent a print."
                ),
                "one_card": "ynab/plaid (balance + txs)",
                "rh_checking": "ynab/plaid (checking balance + ACH-related txs)",
                "expenses": "google sheet: Personal=upcoming estimates; Discretionary=capital targets",
                "rh_brokerage": "MCP portfolio cash/BP (trading), distinct from RH Checking",
                "external_usdc_send": "not via Advanced Trade transfer",
            },
        },
    }


def _f_safe(x: Any) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


# Loan books come from Morpho GraphQL (SCW). Settings must not store or
# re-introduce them — git-zero / form POST used to wipe the live feed.
LOAN_MANUAL_KEYS = (
    "loan_principal_usdc",
    "collateral_btc_usd",
    "collateral_btc",
    "ltv",
    "variable_apr",
    "morpho_borrow_apr",
    "liquidation_price_btc_usd",
    "health_factor",
    "morpho_wallet",
    "morpho_position_source",
)


def save_config(data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Persist treasury config.json (manual fields + policy)."""
    p = path or CONFIG_PATH
    existing = load_config(p)
    merged = {
        k: v
        for k, v in existing.items()
        if k not in {"policy", "coinbase_manual", "robinhood", "ynab", "expenses_sheet"}
    }
    merged.update(
        {
            "policy": {**(existing.get("policy") or {}), **(data.get("policy") or {})},
            "coinbase_manual": {
                **(existing.get("coinbase_manual") or {}),
                **(data.get("coinbase_manual") or {}),
            },
            "robinhood": {**(existing.get("robinhood") or {}), **(data.get("robinhood") or {})},
            "ynab": {**(existing.get("ynab") or {}), **(data.get("ynab") or {})},
            "expenses_sheet": {
                **(existing.get("expenses_sheet") or {}),
                **(data.get("expenses_sheet") or {}),
            },
        }
    )
    if data.get("morpho"):
        merged["morpho"] = {**(existing.get("morpho") or {}), **(data.get("morpho") or {})}
    # Preserve expenses_sheet if empty merge
    if not merged["expenses_sheet"] and existing.get("expenses_sheet"):
        merged["expenses_sheet"] = existing["expenses_sheet"]
    # Preserve notes if not overwritten
    if "notes" not in (data.get("coinbase_manual") or {}) and (existing.get("coinbase_manual") or {}).get(
        "notes"
    ):
        merged["coinbase_manual"]["notes"] = existing["coinbase_manual"]["notes"]
    man = dict(merged.get("coinbase_manual") or {})
    for k in LOAN_MANUAL_KEYS:
        man.pop(k, None)
    merged["coinbase_manual"] = man
    save_json(p, merged)
    return p
