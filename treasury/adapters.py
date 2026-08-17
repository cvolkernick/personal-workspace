"""Live + file snapshot adapters for Coinbase liquid balances and Robinhood portfolio/BP."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple  # noqa: F401

TREASURY_DIR = Path(__file__).resolve().parent
SNAPSHOTS_DIR = TREASURY_DIR / "snapshots"
CONFIG_PATH = TREASURY_DIR / "config.json"

def _resolve_coinbase_bin() -> Optional[str]:
    """Locate coinbase CLI even when PATH is stripped (launchd / ensure script)."""
    found = shutil.which("coinbase")
    if found:
        return found
    home = Path.home()
    for cand in (
        home / ".local" / "bin" / "coinbase",
        Path("/opt/homebrew/bin/coinbase"),
        Path("/usr/local/bin/coinbase"),
    ):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def _subprocess_env() -> Dict[str, str]:
    """Env for coinbase CLI under launchd.

    coinbase is `#!/usr/bin/env node`. Launchd PATH often lacks Homebrew, so
    even when we resolve the absolute coinbase path, node is missing → live
    fetch fails and coinbase_latest.json freezes for days.
    """
    env = dict(os.environ)
    # Prefer Homebrew node (coinbase needs modern builtins). Put these first
    # in order so /opt/homebrew/bin wins over /usr/local/bin (older node).
    extras = (
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        str(Path.home() / ".local" / "bin"),
        "/usr/local/bin",
    )
    path_parts = [p for p in (env.get("PATH") or "").split(":") if p]
    # Prepend extras in reverse so first extra ends up leftmost
    for e in reversed(extras):
        if e in path_parts:
            path_parts.remove(e)
        path_parts.insert(0, e)
    env["PATH"] = ":".join(path_parts)
    return env


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
    cb = _resolve_coinbase_bin()
    if not cb:
        return None, "coinbase CLI not found"
    try:
        proc = subprocess.run(
            [cb, "products", "get", "BTC-USD"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_subprocess_env(),
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
    cb = _resolve_coinbase_bin()
    if not cb:
        return None, "coinbase CLI not found (PATH missing homebrew?)"
    try:
        proc = subprocess.run(
            [cb, "balance", "--paginate"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_subprocess_env(),
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
        "coinbase_bin": cb,
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
        # Live failed: demote misleading source="live" so feed ages show honestly.
        if err:
            out["live_error"] = err
            if (out.get("source") or "").lower() in ("live", ""):
                out["source"] = "snapshot"
        else:
            out["source"] = out.get("source") or "snapshot"
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


def _mask_account(acct: Optional[str]) -> Optional[str]:
    if not acct:
        return None
    s = str(acct).strip()
    if len(s) <= 4:
        return s
    return s[-4:]


def _fnum(val: Any, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def normalize_robinhood_payload(data: Dict[str, Any], *, source: str = "snapshot") -> Dict[str, Any]:
    """Normalize MCP/API portfolio payload into treasury fields.

    Accepts flat treasury shape or raw MCP ``{data: {...}}`` envelopes from
    ``get_portfolio``. Extra dual-account fields (``agentic``, ``positions``)
    are preserved when already present on a flat snapshot.
    """
    # Already a flat multi-account snapshot
    if (
        isinstance(data.get("buying_power"), (int, float, str))
        and "total_value" in data
        and data.get("agentic") is not None
        and not isinstance(data.get("buying_power"), dict)
    ):
        out = dict(data)
        out["source"] = source or out.get("source") or "snapshot"
        out.setdefault("as_of", _now())
        out["buying_power"] = _fnum(out.get("buying_power"))
        out["total_value"] = _fnum(out.get("total_value"))
        out["equity_value"] = _fnum(out.get("equity_value"))
        out["cash"] = _fnum(out.get("cash"))
        return out

    body: Dict[str, Any] = data
    inner = data.get("data")
    if isinstance(inner, dict) and (
        "total_value" in inner or "buying_power" in inner or "equity_value" in inner
    ):
        body = inner

    bp_obj = body.get("buying_power")
    if isinstance(bp_obj, dict):
        bp = _fnum(bp_obj.get("buying_power"))
        unlev = bp_obj.get("unleveraged_buying_power")
        unlev = None if unlev is None else _fnum(unlev)
    else:
        bp = _fnum(bp_obj if bp_obj is not None else body.get("buying_power_value"))
        unlev = body.get("unleveraged_buying_power")
        unlev = None if unlev is None else _fnum(unlev)

    acct = data.get("account_number") or body.get("account_number")
    last4 = data.get("account_number_last4") or _mask_account(acct)

    out: Dict[str, Any] = {
        "source": source,
        "as_of": data.get("as_of") or body.get("as_of") or _now(),
        "total_value": _fnum(body.get("total_value")),
        "equity_value": _fnum(body.get("equity_value")),
        "cash": _fnum(body.get("cash")),
        "buying_power": bp,
        "unleveraged_buying_power": unlev,
        "margin_use": body.get("margin_use"),
        "currency": body.get("currency") or "USD",
        "account_number_last4": last4,
    }
    if acct:
        out["account_number"] = str(acct)
    if data.get("agentic_allowed") is not None:
        out["agentic_allowed"] = bool(data.get("agentic_allowed"))
    elif body.get("agentic_allowed") is not None:
        out["agentic_allowed"] = bool(body.get("agentic_allowed"))
    for key in (
        "agentic",
        "positions",
        "accounts",
        "mcp",
        "nickname",
        "brokerage_account_type",
        "account_type",
        "notes",
    ):
        if key in data and data[key] is not None:
            out[key] = data[key]
    return out


def build_robinhood_dual_snapshot(
    *,
    primary_portfolio: Dict[str, Any],
    agentic_portfolio: Optional[Dict[str, Any]] = None,
    primary_account: Optional[str] = None,
    agentic_account: Optional[str] = None,
    primary_positions: Optional[list] = None,
    agentic_positions: Optional[list] = None,
    accounts: Optional[list] = None,
    source: str = "live",
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Merge margin (policy) + agentic (tradable by MCP) into one snapshot.

    Policy DCA / BP floors use the **primary** book. Orders via MCP may only
    place on accounts with ``agentic_allowed=true`` (the agentic book).
    """
    cfg = load_config()
    rh_cfg = cfg.get("robinhood") or {}
    primary_account = primary_account or rh_cfg.get("account_number") or ""
    agentic_account = agentic_account or rh_cfg.get("agentic_account_number") or ""

    primary = normalize_robinhood_payload(
        {
            **(primary_portfolio if isinstance(primary_portfolio, dict) else {}),
            "account_number": primary_account,
            "agentic_allowed": False,
        },
        source=source,
    )
    primary["account_number"] = primary_account
    primary["account_number_last4"] = _mask_account(primary_account)
    primary["agentic_allowed"] = False
    if primary_positions is not None:
        primary["positions"] = primary_positions

    agentic_block: Optional[Dict[str, Any]] = None
    if agentic_portfolio is not None or agentic_account:
        agentic_block = normalize_robinhood_payload(
            {
                **(agentic_portfolio if isinstance(agentic_portfolio, dict) else {}),
                "account_number": agentic_account,
                "agentic_allowed": True,
            },
            source=source,
        )
        agentic_block["account_number"] = agentic_account
        agentic_block["account_number_last4"] = _mask_account(agentic_account)
        agentic_block["agentic_allowed"] = True
        agentic_block["nickname"] = agentic_block.get("nickname") or "Agentic"
        if agentic_positions is not None:
            agentic_block["positions"] = agentic_positions

    snap: Dict[str, Any] = {
        "source": source,
        "as_of": _now(),
        "total_value": primary["total_value"],
        "equity_value": primary["equity_value"],
        "cash": primary["cash"],
        "buying_power": primary["buying_power"],
        "unleveraged_buying_power": primary.get("unleveraged_buying_power"),
        "margin_use": primary.get("margin_use"),
        "currency": primary.get("currency") or "USD",
        "account_number": primary_account,
        "account_number_last4": _mask_account(primary_account),
        "agentic_allowed": False,
        "positions": primary_positions if primary_positions is not None else primary.get("positions"),
        "agentic": agentic_block,
        "accounts": accounts or [],
        "mcp": {
            "url": "https://agent.robinhood.com/mcp/trading",
            "server": "robinhood-trading",
            "connected": True,
            "note": "Trades only on agentic_allowed accounts; primary margin is read/policy only for this agent",
        },
        "notes": notes
        or (
            "Primary margin for DCA/BP policy; Agentic cash account for MCP order placement. "
            "Refresh via agent: get_accounts + get_portfolio ×2 → rh_sync / write snapshot."
        ),
    }
    return snap


def write_robinhood_snapshot(payload: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Persist a Robinhood portfolio payload for dashboard/adapters."""
    snap = path or (SNAPSHOTS_DIR / "robinhood_latest.json")
    if (
        "buying_power" in payload
        and "total_value" in payload
        and "source" in payload
        and not isinstance(payload.get("buying_power"), dict)
    ):
        # Preserve dual-account extras as-is
        out = dict(payload)
        out.setdefault("as_of", _now())
        save_json(snap, out)
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
        "agentic": None,
        "live_error": "no robinhood snapshot — write via agent MCP get_portfolio + treasury/rh_sync.py",
    }


def _merge_manual_with_one_card(
    manual: Dict[str, Any],
    one_card: Dict[str, Any],
) -> Dict[str, Any]:
    """Overlay YNAB One Card fields onto manual.

    Live/snapshot YNAB **wins** over a non-empty manual ``card_balance`` so a
    saved FCC UI override (or stale config) cannot pin a frozen owed amount.
    Available credit still only fills when manual is empty (YNAB rarely has it).
    """
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

    ynab_healthy = (
        one_card.get("source") in ("ynab", "snapshot")
        and not one_card.get("live_error")
        and bal is not None
    )
    # Prefer YNAB when healthy; otherwise only fill empty manual (legacy path)
    if ynab_healthy or (_empty(out.get("card_balance")) and bal is not None):
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
    from treasury.ynab_sync import fetch_one_card, fetch_rh_checking, fetch_x_money

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
    # Live One Card sync also writes RH checking + X Money snapshots
    one_card = fetch_one_card(prefer_live=prefer_live_ynab)
    rh_checking = fetch_rh_checking(prefer_live=prefer_live_ynab)
    x_money = fetch_x_money(prefer_live=prefer_live_ynab)
    from treasury.expenses_sync import fetch_expenses

    expenses = fetch_expenses(prefer_live=prefer_live_expenses)
    from treasury.solana_sync import fetch_solana

    solana = fetch_solana(prefer_live=prefer_live_coinbase, config=cfg)
    manual = _merge_manual_with_one_card(dict(cfg.get("coinbase_manual") or {}), one_card)
    rh_cfg = cfg.get("robinhood") or {}
    # Manual RH yield sleeve (Robinhood Earn / USDG / Morpho) — not a clean MCP field
    rh = dict(rh)
    rh["usdg_earn_usdg"] = rh_cfg.get("usdg_earn_usdg")
    rh["usdg_earn_apy_est"] = rh_cfg.get("usdg_earn_apy_est")
    rh["margin_loan_usd"] = rh_cfg.get("margin_loan_usd")
    rh["equity_collateral_usd"] = rh_cfg.get("equity_collateral_usd")
    ynab_cfg = cfg.get("ynab") or {}
    # X Money cash yield (~6% product APY) — balance from YNAB; rate from config
    x_money = dict(x_money)
    if ynab_cfg.get("x_money_apy_est") is not None:
        x_money["apy_est"] = ynab_cfg.get("x_money_apy_est")
    exp_cfg = cfg.get("expenses_sheet") or {}
    return {
        "as_of": _now(),
        "coinbase": cb,
        "coinbase_manual": manual,
        "one_card": one_card,
        "rh_checking": rh_checking,
        "x_money": x_money,
        "solana": solana,
        "expenses": expenses,
        "robinhood": rh,
        "policy_overrides": cfg.get("policy") or {},
        "meta": {
            "config_path": str(CONFIG_PATH),
            "coinbase_source": cb.get("source"),
            "robinhood_source": rh.get("source"),
            "one_card_source": one_card.get("source"),
            "rh_checking_source": rh_checking.get("source"),
            "x_money_source": x_money.get("source"),
            "solana_source": solana.get("source"),
            "expenses_source": expenses.get("source"),
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
                "x_money_account_name": ynab_cfg.get("x_money_account_name")
                or x_money.get("account_name"),
            },
            "expenses_sheet": {
                "sheet_id": exp_cfg.get("sheet_id") or expenses.get("sheet_id"),
                "sheet_name": expenses.get("sheet_name") or "Personal Expense Sheet",
            },
            "api_limits": {
                "morpho_loan": "app-only",
                "high_yield_vault": "app-only",
                "one_card": "ynab/plaid (balance + txs)",
                "rh_checking": "ynab/plaid (checking balance + ACH-related txs)",
                "x_money": "ynab/plaid (X Money cash ~6% APY; may show as Checking – ####)",
                "solana": "public RPC + Jupiter prices; whitelist SOL/USDC/JR-strcUSX; JR is not HY",
                "expenses": "google sheet: Essential+Fleet=burn; Collateral=investments; Productive Discretionary=capital outlay; Consumer Discretionary=wishlist",
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


def save_config(data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Persist treasury config.json (manual fields + policy).

    Merges known sections into the existing file and **preserves** any other
    top-level keys (e.g. notifications) so a partial UI save cannot drop them.
    Null/empty-string values in a section update do not wipe an existing
    non-empty value unless the client sends a real replacement.
    """
    p = path or CONFIG_PATH
    existing = load_config(p)

    def _merge_section(old: Any, new: Any) -> Dict[str, Any]:
        base = dict(old or {}) if isinstance(old, dict) else {}
        inc = dict(new or {}) if isinstance(new, dict) else {}
        for k, v in inc.items():
            # Keep prior value when UI posts blank for an untouched/empty field
            if v is None or v == "":
                if k in base and base[k] not in (None, ""):
                    continue
            base[k] = v
        return base

    # Start from full existing config so unknown top-level keys survive
    merged: Dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    merged["policy"] = _merge_section(existing.get("policy"), data.get("policy"))
    merged["coinbase_manual"] = _merge_section(
        existing.get("coinbase_manual"), data.get("coinbase_manual")
    )
    merged["robinhood"] = _merge_section(existing.get("robinhood"), data.get("robinhood"))
    merged["ynab"] = _merge_section(existing.get("ynab"), data.get("ynab"))
    merged["expenses_sheet"] = _merge_section(
        existing.get("expenses_sheet"), data.get("expenses_sheet")
    )
    if "solana" in data or existing.get("solana"):
        merged["solana"] = _merge_section(existing.get("solana"), data.get("solana"))
    # Preserve expenses_sheet if empty merge
    if not merged["expenses_sheet"] and existing.get("expenses_sheet"):
        merged["expenses_sheet"] = existing["expenses_sheet"]
    # Preserve notes if not overwritten
    if "notes" not in (data.get("coinbase_manual") or {}) and (existing.get("coinbase_manual") or {}).get(
        "notes"
    ):
        merged["coinbase_manual"]["notes"] = existing["coinbase_manual"]["notes"]
    save_json(p, merged)
    return p
