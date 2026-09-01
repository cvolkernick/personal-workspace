#!/usr/bin/env python3
"""Ask Grok — financial advisor grounded in FCC / treasury state.

Same auth pattern as Orchestra Conductor:
  XAI_API_KEY or ~/.grok/auth.json SuperGrok session.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

XAI_API_BASE = os.environ.get("XAI_API_BASE", "https://api.x.ai/v1").rstrip("/")
DEFAULT_MODEL = os.environ.get("XAI_MODEL", "grok-4-1-fast-non-reasoning")
AUTH_PATH = Path.home() / ".grok" / "auth.json"
MAX_CONTEXT_CHARS = int(os.environ.get("FCC_ASK_MAX_CONTEXT_CHARS", "32000"))
REQUEST_TIMEOUT = int(os.environ.get("FCC_ASK_TIMEOUT_SEC", "90"))
GROK_LOGIN_TIMEOUT_SEC = int(os.environ.get("FCC_GROK_LOGIN_TIMEOUT_SEC", "480"))
_GROK_LOGIN_PUBLIC_KEYS = (
    "ok",
    "started",
    "already",
    "phase",
    "method",
    "verification_uri",
    "user_code",
    "error",
    "auth_ok",
    "auth_source",
    "expired",
)
_URI_RE = re.compile(r"https://[^\s>'\"\\]+")
_CODE_LABEL_RE = re.compile(
    r"(?:user[_\s-]?code|enter(?:\s+the)?\s+code|code)\s*[:=]\s*([A-Z0-9][A-Z0-9-]{3,24})",
    re.I,
)
_CODE_BARE_RE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")
_SECRET_LINE_RE = re.compile(
    r"(?i)(access_token|refresh_token|id_token|client_secret|client_id|"
    r"authorization:\s*bearer|device_code)\s*[:=]\s*\S+"
)
_login_lock = threading.Lock()
_login_proc: subprocess.Popen[str] | None = None
_login_output: list[str] = []
_login_started_at: float | None = None
_login_phase: str = "idle"
_login_error: str | None = None
_login_uri: str | None = None
_login_user_code: str | None = None

SYSTEM_PROMPT = """You are the Financial Advisor for the user's Financial Command Center (FCC).

You help them understand liquidity, bills/cash coach, Morpho/One Card, mining income
(Braiins), agentic fund manager (Robinhood agentic only), and capital-flow priorities.

You answer using TREASURY DATA JSON plus sound personal-finance judgment.
Do **not** invent balances, dates, or holdings that are not in the data.

Rules:
- Prefer **actionable** advice: what to pay, what to fund, what to defer, what to check in-app.
- Respect venue constraints: Coinbase (spot/vault/Morpho/One Card), X Money, RH Checking,
  agentic RH book is separate capital (do not treat agentic NAV as bill-pay cash).
- Flag stale or missing feeds when relevant (as_of ages, unknown LTV, empty X Money, etc.).
- Coach allocations are **advisory only** — do not claim money was moved.
- Fund manager is agentic-only; no advice to trade the primary brokerage margin book.
- Format replies in **GitHub-flavored Markdown** (## headings, bullets, **bold**, `code`).
- When comparing options or numbers, use a **GFM pipe table** with a header row and
  a separator row (`| --- | --- |`). Keep one row per line; never put whole tables
  on a single line or use spaces-only alignment without pipes.
- Be concise. Lead with the answer, then brief rationale.
- Do not discuss API keys, tokens, or how to bypass auth.
"""

ADVISOR_SUGGESTIONS = [
    "What should I pay first with the cash I actually have?",
    "How bad is my liquidity stress right now — and what would un-red it?",
    "Given Morpho LTV and One Card, what is the safest next $100 use?",
    "Is the agentic fund book healthy, or should I wait for pending capital?",
    "Summarize ASIC/Braiins income timing vs my bill calendar.",
]


class AdvisorError(Exception):
    def __init__(self, message: str, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def _parse_expires_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_grok_auth_entry() -> dict | None:
    if not AUTH_PATH.is_file():
        return None
    try:
        data = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data:
        return None
    best = None
    best_exp = None
    now = datetime.now(timezone.utc)
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        token = entry.get("key") or entry.get("access_token")
        if not token:
            continue
        exp = _parse_expires_at(entry.get("expires_at"))
        if exp and exp <= now:
            if best is None:
                best = entry
            continue
        if best is None or (exp and (best_exp is None or exp > best_exp)):
            best = entry
            best_exp = exp
    return best


def resolve_xai_credentials() -> dict[str, Any]:
    api_key = (os.environ.get("XAI_API_KEY") or "").strip()
    if api_key:
        return {
            "token": api_key,
            "source": "xai_api_key",
            "email": None,
            "expires_at": None,
            "expired": False,
        }
    entry = _read_grok_auth_entry()
    if not entry:
        return {
            "token": None,
            "source": "none",
            "email": None,
            "expires_at": None,
            "expired": False,
            "error": (
                "No SuperGrok session found. Sign in with `grok login`, "
                "or set XAI_API_KEY from console.x.ai."
            ),
        }
    token = (entry.get("key") or entry.get("access_token") or "").strip()
    exp = _parse_expires_at(entry.get("expires_at"))
    now = datetime.now(timezone.utc)
    expired = bool(exp and exp <= now)
    return {
        "token": token if token else None,
        "source": "supergrok_session",
        "email": entry.get("email"),
        "expires_at": entry.get("expires_at"),
        "expired": expired,
        "error": (
            "SuperGrok session expired. Run `grok login`, then try again."
            if expired
            else (None if token else "Empty session token in ~/.grok/auth.json")
        ),
    }


def auth_status() -> dict[str, Any]:
    creds = resolve_xai_credentials()
    return {
        "ok": bool(creds.get("token")) and not creds.get("expired"),
        "source": creds.get("source"),
        "email": creds.get("email"),
        "expires_at": creds.get("expires_at"),
        "expired": bool(creds.get("expired")),
        "model": DEFAULT_MODEL,
        "error": creds.get("error"),
        "suggestions": list(ADVISOR_SUGGESTIONS),
    }


def _which_grok() -> str | None:
    override = (os.environ.get("FCC_GROK_BIN") or "").strip()
    if override:
        p = Path(override)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    found = shutil.which("grok")
    if found:
        return found
    for p in (
        Path.home() / ".grok" / "bin" / "grok",
        Path.home() / ".local" / "bin" / "grok",
        Path("/opt/homebrew/bin/grok"),
        Path("/usr/local/bin/grok"),
    ):
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def _redact_secrets(line: str) -> str:
    text = (line or "").replace("\x00", "")
    return _SECRET_LINE_RE.sub(r"\1=<redacted>", text)[:400]


def _https_uri(raw: str | None) -> str | None:
    uri = (raw or "").strip().rstrip(").,]>\"'")
    if not uri.startswith("https://"):
        return None
    if any(tok in uri.lower() for tok in ("access_token", "refresh_token", "id_token")):
        return None
    return uri[:300]


def parse_grok_login_output(blob: str) -> dict[str, str | None]:
    """Pull public device-auth fields from grok CLI text. Never tokens."""
    uri = None
    for match in _URI_RE.finditer(blob or ""):
        candidate = _https_uri(match.group(0))
        if candidate:
            uri = candidate
            if any(n in candidate for n in ("auth.x.ai", "accounts.x.ai", "activate", "device")):
                break
    code = None
    labeled = _CODE_LABEL_RE.search(blob or "")
    if labeled:
        code = labeled.group(1).strip().rstrip(").,")
    if not code:
        bare = _CODE_BARE_RE.search(blob or "")
        if bare:
            code = bare.group(1)
    if code and len(code) > 32:
        code = None
    return {"verification_uri": uri, "user_code": code}


def _public_login_view(*, started: bool = False, already: bool = False) -> dict[str, Any]:
    auth = auth_status()
    phase = _login_phase
    out = {
        "ok": phase == "ok",
        "started": started or phase in ("starting", "pending"),
        "already": already,
        "phase": phase,
        "method": "grok_cli",
        "verification_uri": _https_uri(_login_uri),
        "user_code": _login_user_code,
        "error": _login_error,
        "auth_ok": bool(auth.get("ok")),
        "auth_source": auth.get("source"),
        "expired": bool(auth.get("expired")),
    }
    return {k: out.get(k) for k in _GROK_LOGIN_PUBLIC_KEYS}


def reset_grok_login_state(*, kill: bool = True) -> None:
    """Test/helper: drop in-flight grok login state."""
    global _login_proc, _login_started_at, _login_phase, _login_error
    global _login_uri, _login_user_code
    with _login_lock:
        proc = _login_proc
        if kill and proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        _login_proc = None
        _login_output.clear()
        _login_started_at = None
        _login_phase = "idle"
        _login_error = None
        _login_uri = None
        _login_user_code = None


def _refresh_login_from_output_locked() -> None:
    global _login_uri, _login_user_code
    parsed = parse_grok_login_output("\n".join(_login_output))
    if parsed.get("verification_uri"):
        _login_uri = parsed["verification_uri"]
    if parsed.get("user_code"):
        _login_user_code = parsed["user_code"]
    if _login_phase == "starting" and (_login_uri or _login_user_code):
        _login_phase_set_pending()


def _login_phase_set_pending() -> None:
    global _login_phase
    if _login_phase in ("starting", "idle"):
        _login_phase = "pending"


def _pump_grok_login(proc: subprocess.Popen[str]) -> None:
    global _login_phase, _login_error, _login_proc
    try:
        stdout = proc.stdout
        if stdout is not None:
            try:
                for raw in stdout:
                    line = _redact_secrets(raw.rstrip("\n"))
                    with _login_lock:
                        _login_output.append(line)
                        if len(_login_output) > 80:
                            del _login_output[:20]
                        _refresh_login_from_output_locked()
            finally:
                try:
                    stdout.close()
                except Exception:
                    pass
        code = proc.wait(timeout=GROK_LOGIN_TIMEOUT_SEC)
        with _login_lock:
            if code == 0:
                _login_phase = "ok"
                _login_error = None
            else:
                _login_phase = "fail"
                tail = " ".join(_login_output[-4:]).strip()
                _login_error = (
                    _redact_secrets(tail)[:220]
                    if tail
                    else f"grok login exited {code}"
                )
    except subprocess.TimeoutExpired:
        with _login_lock:
            _login_phase = "fail"
            _login_error = "Grok login timed out"
        try:
            proc.kill()
        except Exception:
            pass
    except Exception as exc:
        with _login_lock:
            _login_phase = "fail"
            _login_error = _redact_secrets(str(exc))[:220]
    finally:
        with _login_lock:
            if _login_proc is proc:
                _login_proc = None


def start_grok_login() -> dict[str, Any]:
    """Start the existing `grok login --device-auth` CLI. Public fields only."""
    global _login_proc, _login_started_at, _login_phase, _login_error
    global _login_uri, _login_user_code
    with _login_lock:
        proc = _login_proc
        if proc is not None and proc.poll() is None:
            _refresh_login_from_output_locked()
            return _public_login_view(started=True, already=True)
        grok = _which_grok()
        if not grok:
            _login_phase = "fail"
            _login_error = "grok CLI not found"
            _login_uri = None
            _login_user_code = None
            return _public_login_view(started=False)
        _login_output.clear()
        _login_uri = None
        _login_user_code = None
        _login_error = None
        _login_phase = "starting"
        _login_started_at = time.time()
        try:
            spawned = subprocess.Popen(
                [grok, "login", "--device-auth"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            _login_phase = "fail"
            _login_error = _redact_secrets(str(exc))[:220]
            return _public_login_view(started=False)
        _login_proc = spawned
        threading.Thread(
            target=_pump_grok_login,
            args=(spawned,),
            daemon=True,
            name="fcc-grok-login",
        ).start()
    time.sleep(0.25)
    with _login_lock:
        _refresh_login_from_output_locked()
        return _public_login_view(started=_login_phase in ("starting", "pending", "ok"))


def grok_login_status() -> dict[str, Any]:
    """Public snapshot of in-flight or last grok CLI login."""
    global _login_phase, _login_error
    with _login_lock:
        proc = _login_proc
        if proc is not None and proc.poll() is None:
            if (
                _login_started_at
                and time.time() - _login_started_at > GROK_LOGIN_TIMEOUT_SEC
            ):
                try:
                    proc.kill()
                except Exception:
                    pass
                _login_phase = "fail"
                _login_error = "Grok login timed out"
            else:
                _refresh_login_from_output_locked()
        return _public_login_view(
            started=_login_phase in ("starting", "pending"),
            already=_login_phase in ("starting", "pending"),
        )


def _slim_actions(actions: list, limit: int = 8) -> list[dict[str, Any]]:
    out = []
    for a in actions[:limit]:
        if not isinstance(a, dict):
            continue
        out.append(
            {
                "priority": a.get("priority"),
                "kind": a.get("kind"),
                "title": a.get("title"),
                "actor": a.get("actor"),
                "detail": (a.get("detail") or "")[:180],
                "api_reachable": a.get("api_reachable"),
            }
        )
    return out


def _slim_tx(txs: list, limit: int = 5) -> list[dict[str, Any]]:
    out = []
    for t in (txs or [])[:limit]:
        if not isinstance(t, dict):
            continue
        out.append(
            {
                "date": t.get("date"),
                "payee": t.get("payee"),
                "amount": t.get("amount_display", t.get("amount")),
            }
        )
    return out


def build_treasury_context(
    treasury: dict[str, Any],
    *,
    coach: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compact FCC snapshot for the model (balances, stress, coach, fund, mining)."""
    snap = treasury.get("snapshot") or {}
    ev = treasury.get("evaluation") or {}
    inp = ev.get("inputs") or {}
    stress = ev.get("stress") or {}
    actions = ev.get("actions") or []
    sleeves = ev.get("sleeves") or {}
    dq = ev.get("data_quality") or {}
    fm = treasury.get("fund_manager") or ev.get("fund_manager") or {}
    fma = fm.get("analysis") or {}
    br = treasury.get("braiins") or {}
    oc = snap.get("one_card") or {}
    xm = snap.get("x_money") or {}
    rhc = snap.get("rh_checking") or {}
    ex = snap.get("expenses") or {}
    es = ex.get("summary") or {}
    man = snap.get("coinbase_manual") or {}

    coach_slim: dict[str, Any] | None = None
    if isinstance(coach, dict) and coach.get("ok"):
        rows = coach.get("obligations") or coach.get("ranked") or []
        top = []
        for o in rows[:10]:
            if not isinstance(o, dict):
                continue
            top.append(
                {
                    "item": o.get("item"),
                    "venue": o.get("venue") or o.get("from_label"),
                    "due_date": o.get("due_date") or o.get("due_date_raw"),
                    "days_until_due": o.get("days_until_due"),
                    "amount_due": o.get("amount_due"),
                    "allocated": o.get("allocated"),
                    "gap": o.get("gap"),
                    "status": o.get("status"),
                    "overdue": o.get("overdue"),
                }
            )
        coach_slim = {
            "advice": (coach.get("advice") or [])[:6],
            "residuals": coach.get("residuals"),
            "habits": {
                k: (coach.get("habits") or {}).get(k)
                for k in (
                    "total_liquid_available",
                    "runway_days_at_sheet_burn",
                    "personal_daily_burn_est",
                    "obligation_pressure",
                    "card_balance_owed",
                )
            },
            "top_obligations": top,
            "unfunded_count": len(coach.get("unfunded") or []),
        }

    holdings = []
    for h in fma.get("holdings") or fma.get("positions") or []:
        if not isinstance(h, dict):
            continue
        holdings.append(
            {
                "symbol": h.get("symbol") or h.get("ticker"),
                "market_value": h.get("market_value") or h.get("value_usd"),
                "qty": h.get("quantity") or h.get("qty"),
            }
        )
        if len(holdings) >= 12:
            break

    return {
        "as_of": snap.get("as_of") or treasury.get("as_of"),
        "stress": stress,
        "inputs": {
            "working_usdc": inp.get("working_usdc", inp.get("liquid_usdc")),
            "liquid_usdc": inp.get("liquid_usdc"),
            "vault_usdc": inp.get("vault_usdc") or man.get("vault_usdc"),
            "ltv": inp.get("ltv") if inp.get("ltv") is not None else man.get("ltv"),
            "loan_principal_usdc": inp.get("loan_principal_usdc")
            if inp.get("loan_principal_usdc") is not None
            else man.get("loan_principal_usdc"),
            "collateral_btc_usd": inp.get("collateral_btc_usd")
            if inp.get("collateral_btc_usd") is not None
            else man.get("collateral_btc_usd"),
            "liquidation_price_btc_usd": inp.get("liquidation_price_btc_usd"),
            "health_factor": inp.get("health_factor"),
            "card_balance": inp.get("card_balance") or oc.get("balance_owed") or man.get("card_balance"),
            "card_security_deposit_usdc": inp.get("card_security_deposit_usdc")
            or man.get("one_card_security_deposit_usdc"),
            "rh_checking_cash": inp.get("rh_checking_cash", rhc.get("cash")),
            "x_money_cash": inp.get("x_money_cash", xm.get("cash")),
            "x_money_apy_est": inp.get("x_money_apy_est", xm.get("apy_est")),
            "solana_book_usd": inp.get("solana_book_usd"),
            "solana_jr_strcusx": inp.get("solana_jr_strcusx"),
            "solana_jr_strcusx_usd": inp.get("solana_jr_strcusx_usd"),
            "solana_counts_toward_hy": False,
            "rh_buying_power": inp.get("rh_buying_power"),
            "expenses_upcoming_monthly": es.get("upcoming_expense_monthly")
            or es.get("personal_monthly")
            or inp.get("expenses_upcoming_monthly"),
        },
        "sleeves": {
            k: {
                "target": (sleeves.get(k) or {}).get("target"),
                "filled": (sleeves.get(k) or {}).get("filled"),
                "gap": (sleeves.get(k) or {}).get("gap"),
            }
            for k in ("card_float", "loan_buffer", "bridge_dry_powder")
            if isinstance(sleeves.get(k), dict)
        },
        "actions": _slim_actions(actions if isinstance(actions, list) else []),
        "feeds": {
            "sources": dq.get("sources") or {},
            "warnings": (dq.get("warnings") or [])[:6],
            "status": dq.get("status"),
            "one_card_as_of": oc.get("as_of"),
            "x_money_as_of": xm.get("as_of"),
            "rh_checking_as_of": rhc.get("as_of"),
            "coinbase_as_of": (snap.get("coinbase") or {}).get("as_of"),
        },
        "one_card": {
            "balance_owed": oc.get("balance_owed") or oc.get("card_balance"),
            "spend_30d": oc.get("spend_30d"),
            "payments_30d": oc.get("payments_30d"),
            "source": oc.get("source"),
            "recent_txs": _slim_tx(oc.get("transactions") or []),
        },
        "x_money": {
            "cash": xm.get("cash"),
            "account_name": xm.get("account_name"),
            "spend_30d": xm.get("spend_30d"),
            "inflow_30d": xm.get("inflow_30d"),
            "source": xm.get("source"),
            "apy_est": xm.get("apy_est"),
        },
        "solana": {
            "wallet": (snap.get("solana") or {}).get("wallet") or inp.get("solana_wallet"),
            "book_usd": inp.get("solana_book_usd") or (snap.get("solana") or {}).get("book_usd"),
            "sol": inp.get("solana_sol") or (snap.get("solana") or {}).get("sol"),
            "usdc": inp.get("solana_usdc") or (snap.get("solana") or {}).get("usdc"),
            "jr_strcusx": inp.get("solana_jr_strcusx")
            or (snap.get("solana") or {}).get("jr_strcusx"),
            "jr_strcusx_usd": inp.get("solana_jr_strcusx_usd")
            or (snap.get("solana") or {}).get("jr_strcusx_usd"),
            "counts_toward_hy": False,
            "source": (snap.get("solana") or {}).get("source"),
        },
        "rh_checking": {
            "cash": rhc.get("cash"),
            "source": rhc.get("source"),
        },
        "expenses_summary": {
            "personal_monthly": es.get("personal_monthly") or es.get("upcoming_expense_monthly"),
            "coinbase_funded_monthly": es.get("coinbase_funded_monthly"),
            "x_money_funded_monthly": es.get("x_money_funded_monthly"),
        },
        "coach": coach_slim,
        "fund_manager": {
            "ok": fma.get("ok") or fm.get("ok"),
            "nav_usd": fma.get("nav_usd"),
            "cash_usd": fma.get("cash_usd"),
            "buying_power_usd": fma.get("buying_power_usd"),
            "pending_deposits_usd": fma.get("pending_deposits_usd"),
            "weights_of_deployed": fma.get("weights_of_deployed"),
            "policy_live": (fm.get("policy_summary") or {}).get("live"),
            "holdings": holdings,
            "last_decision_summary": (
                ((fm.get("decisions") or [{}])[0] or {}).get("summary")
                if isinstance(fm.get("decisions"), list) and fm.get("decisions")
                else None
            ),
        },
        "braiins": {
            "ok": br.get("ok"),
            "status": br.get("status"),
            "hash_rate_24h": br.get("hash_rate_24h"),
            "hash_rate_unit": br.get("hash_rate_unit"),
            "ok_workers": br.get("ok_workers"),
            "off_workers": br.get("off_workers"),
            "current_balance_btc": br.get("current_balance_btc"),
            "days_to_next_payout_est": br.get("days_to_next_payout_est"),
            "next_payout_est_at": br.get("next_payout_est_at"),
            "next_payout_progress_pct": br.get("next_payout_progress_pct"),
            "as_of": br.get("as_of"),
        }
        if br
        else None,
        "agent_brief": (ev.get("agent_brief") or "")[:1200] or None,
    }


def _shrink_context(ctx: dict[str, Any], max_chars: int = MAX_CONTEXT_CHARS) -> dict[str, Any]:
    raw = json.dumps(ctx, default=str, separators=(",", ":"))
    if len(raw) <= max_chars:
        return ctx
    # Drop heavy tails first
    if isinstance(ctx.get("actions"), list):
        ctx["actions"] = ctx["actions"][:5]
    coach = ctx.get("coach")
    if isinstance(coach, dict) and isinstance(coach.get("top_obligations"), list):
        coach["top_obligations"] = coach["top_obligations"][:6]
    fm = ctx.get("fund_manager")
    if isinstance(fm, dict) and isinstance(fm.get("holdings"), list):
        fm["holdings"] = fm["holdings"][:6]
    oc = ctx.get("one_card")
    if isinstance(oc, dict):
        oc["recent_txs"] = (oc.get("recent_txs") or [])[:3]
    if isinstance(ctx.get("agent_brief"), str):
        ctx["agent_brief"] = ctx["agent_brief"][:400]
    return ctx


def chat_completions(
    messages: list[dict],
    *,
    model: str | None = None,
    max_tokens: int = 1400,
    temperature: float = 0.35,
) -> dict[str, Any]:
    creds = resolve_xai_credentials()
    token = creds.get("token")
    if not token:
        raise AdvisorError(creds.get("error") or "No xAI credentials", status=401)
    if creds.get("expired"):
        raise AdvisorError(creds.get("error") or "Session expired", status=401)

    body = {
        "model": model or DEFAULT_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    req = urllib.request.Request(
        f"{XAI_API_BASE}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise AdvisorError(
            f"xAI API error HTTP {e.code}", status=e.code, body=err_body
        ) from e
    except urllib.error.URLError as e:
        raise AdvisorError(f"xAI request failed: {e}", status=0) from e


def ask_financial_advisor(
    question: str,
    treasury: dict[str, Any],
    *,
    coach: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Answer a user question grounded in the current FCC treasury snapshot."""
    q = (question or "").strip()
    if not q:
        raise AdvisorError("question is required", status=400)
    if len(q) > 4000:
        raise AdvisorError("question too long (max 4000 chars)", status=400)

    ctx = _shrink_context(build_treasury_context(treasury, coach=coach))
    user_content = (
        "TREASURY DATA JSON:\n"
        + json.dumps(ctx, indent=2, default=str)
        + "\n\nUSER QUESTION:\n"
        + q
    )
    raw = chat_completions(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
    )
    choice = (raw.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    answer = (message.get("content") or "").strip()
    usage = raw.get("usage") or {}
    return {
        "ok": True,
        "answer": answer or "(empty response)",
        "model": raw.get("model") or DEFAULT_MODEL,
        "usage": usage,
        "auth_source": resolve_xai_credentials().get("source"),
        "context_keys": list(ctx.keys()),
    }
