#!/usr/bin/env python3
"""Deterministic order gate for the agentic fund manager (#1093).

Chairman standing order (2026-09-22/23): the stocks book is held and the 40%
BTC / digital-credit target is restored with **new cash only**. The fund
manager must never produce or execute a SELL to rebalance.

This module is the single choke point between "an analysis / LLM proposal"
and "an order":

* ``evaluate_intent`` / ``gate_orders``  -- pure verdicts, fail closed.
* ``execute_intents``                    -- the ONLY order-submit path. A broker
  adapter is called only with a ``GatedOrder`` that this module minted.
* ``guard_decision_entry``               -- decision-log level check used by
  ``fund_manager.append_decision`` (detects/marks sells in any record).
* ``grok_deny_args``                     -- permission deny rules the shell
  runners (and ``rh_snapshot_sync.py``) pass to ``grok --yolo`` so the LLM cannot
  place orders around us. Single shared helper: edit the globs in one place.
* ``load_override`` / ``SpendLedger``    -- a sell override counts only if the file
  is committed by Chris (matches git HEAD); spend is tracked per override id
  across runs, with a total cap across all overrides (rolling 24h).

Fail closed means: an unreadable/disabled policy, a malformed intent, an
unknown side, an unknown purpose, an unknown amount or an unknown cash balance
blocks the order. Nothing here can be loosened by config: if
``guardrails.no_sell_to_rebalance`` is not literally ``true`` the gate blocks
EVERYTHING (it does not allow sells).

No network and no broker call happens in this module on its own. Alerts and
decision logging are lazy-imported from ``treasury.fund_manager``.
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

POLICY_PATH = ROOT / "investment" / "fund_manager.json"
OVERRIDE_PATH = ROOT / "investment" / "fund_manager_sell_override.json"
INTENTS_PATH = ROOT / "treasury" / "snapshots" / "fund_manager_intents.json"
RH_SNAPSHOT_PATH = ROOT / "treasury" / "snapshots" / "robinhood_latest.json"
SPEND_LEDGER_PATH = ROOT / "treasury" / "snapshots" / "fund_manager_override_spend.json"

BLOCK_STATUS = "blocked_no_sell_guard"
EXECUTED_STATUSES = frozenset(
    {
        "filled",
        "partially_filled",
        "placed",
        "submitted",
        "executed",
        "confirmed",
        "queued",
        "open",
        "pending",
        "accepted",
        "sent",
    }
)
MAX_OVERRIDE_HOURS = 24
# Override authentication (#1093 review): the override file is honored only if it
# is byte-identical to git HEAD, the working tree is clean for it, and the last
# commit that touched it was AUTHORED by Chris. The repo identifies Chris by
# author email (every Chris commit: ``Chris Volkernick <cvolkern@gmail.com>``);
# a GitHub web-UI commit has committer ``noreply@github.com``. Code constants on
# purpose: config files can only narrow the gate, never widen who may approve.
CHRIS_AUTHOR_EMAILS = frozenset({"cvolkern@gmail.com"})
TRUSTED_COMMITTER_EMAILS = CHRIS_AUTHOR_EMAILS | {"noreply@github.com"}
# Optional extra hardening: if set (e.g. "origin/work/treasury"), the commit that
# last touched the override must be an ancestor of this ref, i.e. already pushed.
OVERRIDE_TRUSTED_REF_ENV = "FM_OVERRIDE_TRUSTED_REF"
# Hard ceiling on cumulative sell notional across ALL overrides in any rolling
# MAX_OVERRIDE_HOURS window. The override file / policy can only lower it.
DEFAULT_TOTAL_CAP_USD = 100.0
BROKER_ADAPTER_ENV = "FM_BROKER_ADAPTER"  # "pkg.module:callable"; none = place nothing

# Reason codes (stable; tests and #701 comments key on them).
R_POLICY = "policy_unavailable"
R_BAD_INTENT = "malformed_intent"
R_UNCLASSIFIABLE = "unclassifiable_side"
R_REBALANCE_SELL = "rebalance_sell"
R_UNCLASSIFIED_SELL = "sell_purpose_unclassified"
R_SELL_NO_OVERRIDE = "sell_without_valid_override"
R_SHORT = "short_or_option_write"
R_OPTION = "option_order"
R_CRYPTO_SELL = "crypto_sell"
R_OVERRIDE_UNAUTH = "override_not_authenticated"
R_BLOCKED_SYMBOL = "blocked_symbol"
R_BAD_AMOUNT = "invalid_or_unknown_amount"
R_CASH_UNKNOWN = "deployable_cash_unknown"
R_OVER_CASH = "over_deployable_cash"

# A sell is only ever *eligible* for an override when its purpose is one of
# these explicit, non-rebalance purposes. Anything else is blocked.
NON_REBALANCE_PURPOSES = frozenset(
    {"risk_exit", "stop_loss", "tax", "withdrawal", "chairman_manual"}
)
_REBALANCE_RE = re.compile(
    r"(re-?balanc|rotat|\btrim|sell[ _-]?to[ _-]?(buy|fund)|reallocat|raise[ _-]?cash|"
    r"free[ _-]?(up[ _-]?)?cash|\btidy|\bdrift|toward[s]? (the )?(40|60)|"
    r"fund (the )?buy|exit[ _-]?(other|non))",
    re.IGNORECASE,
)
_SIDE_KEYS = ("side", "order_side", "action", "direction", "transaction_type")
_AMOUNT_KEYS = ("notional_usd", "amount_usd", "dollar_amount", "notional")
_TEXT_KEYS = ("purpose", "reason", "rationale", "note", "notes", "summary", "kind")
_OPTION_KEYS = ("strike", "strike_price", "expiration", "expiration_date", "legs", "contract", "option", "option_type")
_BUY = {"buy", "bid", "long", "buy_to_open"}
_SELL_WORDS = ("sell", "short", "trim", "reduce", "exit", "close", "liquidat", "write", "dump", "rotate", "unwind")


# --------------------------------------------------------------------------
# Data types
# --------------------------------------------------------------------------


@dataclass
class Verdict:
    allowed: bool
    code: str  # "ok" or a reason code
    reason: str
    side: str = "unknown"  # buy | sell | short | unknown
    override_used: bool = False
    status: str = ""
    # Set only when an authenticated override approved this sell: what to charge
    # to the spend ledger (override id, qty, usd, override expires_at).
    spend: Optional[Tuple[str, float, float, str]] = None

    def __post_init__(self) -> None:
        if not self.status:
            if self.allowed:
                self.status = "gate_approved"
            else:
                # sells / shorts / unclassifiable => the no-sell status; a plain
                # buy rejected for cap/amount/symbol reasons is not a sell block.
                self.status = BLOCK_STATUS if self.side != "buy" else "blocked_order_gate"


_MINT = object()


class GatedOrder:
    """An order the gate approved. Only ``_mint_gated`` creates one."""

    __slots__ = ("intent", "verdict")

    def __init__(self, intent: Dict[str, Any], verdict: Verdict, _token: object = None):
        if _token is not _MINT:
            raise TypeError("GatedOrder can only be created by the order gate")
        self.intent = dict(intent)
        self.verdict = verdict

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.intent)


def _mint_gated(intent: Dict[str, Any], verdict: Verdict) -> GatedOrder:
    return GatedOrder(intent, verdict, _MINT)


@dataclass
class GatePolicy:
    ok: bool
    error: str = ""
    allowed_sides: Tuple[str, ...] = ()
    blocked_symbols: Tuple[str, ...] = ()
    live: bool = False
    min_trade_usd: float = 1.0
    # guardrails.sell_override_total_cap_usd: can only lower DEFAULT_TOTAL_CAP_USD.
    override_total_cap_usd: Optional[float] = None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_env_enabled() -> bool:
    """Path/clock overrides exist only for the dry-run suite. Prod never sets this."""
    return os.environ.get("FM_GATE_TEST") == "1"


def test_path(env_name: str, default: Path) -> Path:
    if test_env_enabled():
        raw = (os.environ.get(env_name) or "").strip()
        if raw:
            return Path(raw)
    return default


# --------------------------------------------------------------------------
# Advisory-only AI review (2026-10-26). The grok review step may write intents
# but must never execute them. Only the deterministic step of the daily / bp-poll
# runner runs --execute-intents. Two independent checks, either one refuses:
#   1. FM_ADVISORY_ONLY set (the runners set it, with the broker env stripped,
#      on the grok invocation only);
#   2. any ancestor process is grok (catches a review that unsets the marker
#      or exports the broker env itself, as the 2026-10-26 14:05 run did).
# --------------------------------------------------------------------------
ADVISORY_ENV = "FM_ADVISORY_ONLY"
_GROK_PROC_RE = re.compile(r"^grok(?:[-_.].*)?$")


def _proc_names(pid: int, proc_root: Path) -> List[str]:
    d = proc_root / str(pid)
    names: List[str] = []
    try:
        names.append((d / "comm").read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        pass
    try:
        argv0 = (d / "cmdline").read_bytes().split(b"\0", 1)[0].decode("utf-8", "replace")
        if argv0:
            names.append(os.path.basename(argv0))
    except OSError:
        pass
    try:
        names.append(os.path.basename(os.readlink(d / "exe")))
    except OSError:
        pass
    return [n for n in names if n]


def _ppid(pid: int, proc_root: Path) -> Optional[int]:
    try:
        stat = (proc_root / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
        return int(stat.rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def grok_ancestor(start_pid: Optional[int] = None, proc_root: Path = Path("/proc")) -> Optional[str]:
    """``"<pid>:<name>"`` of the nearest grok ancestor (self excluded), else None."""
    pid = _ppid(start_pid or os.getpid(), proc_root)
    for _ in range(64):
        if pid is None or pid <= 1:
            return None
        for name in _proc_names(pid, proc_root):
            if _GROK_PROC_RE.match(name):
                return f"{pid}:{name}"
        pid = _ppid(pid, proc_root)
    return None


def execution_block_reason() -> Optional[str]:
    """Why order execution must be refused here, or None for the deterministic step."""
    marker = (os.environ.get(ADVISORY_ENV) or "").strip()
    if marker and marker != "0":
        return f"{ADVISORY_ENV}={marker}: the AI review is advisory only; execution refused"
    anc = grok_ancestor()
    if anc:
        return f"running under grok ({anc}); only the deterministic runner step may execute"
    return None


def _num(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool) or val == "":
        return None
    try:
        out = float(val)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _norm_token(val: Any) -> str:
    return re.sub(r"[\s\-]+", "_", str(val).strip().lower())


def _parse_ts(val: Any) -> Optional[datetime]:
    if not isinstance(val, str) or not val.strip():
        return None
    s = val.strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:  # naive = ambiguous = malformed
        return None
    return dt.astimezone(timezone.utc)


def test_now(fallback: Optional[datetime] = None) -> datetime:
    """Test clock (``FM_NOW``). Ignored unless ``FM_GATE_TEST=1``."""
    if test_env_enabled():
        parsed = _parse_ts(os.environ.get("FM_NOW") or "")
        if parsed is not None:
            return parsed
    return fallback or _now()


def load_gate_policy(path: Optional[Path] = None) -> GatePolicy:
    """Read ``guardrails`` from fund_manager.json. Any problem => ok=False."""
    p = path or test_path("FM_POLICY_PATH", POLICY_PATH)
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return GatePolicy(False, f"cannot load policy: {type(exc).__name__}")
    if not isinstance(data, dict):
        return GatePolicy(False, "policy is not a JSON object")
    g = data.get("guardrails")
    if not isinstance(g, dict):
        return GatePolicy(False, "guardrails block missing")
    if g.get("no_sell_to_rebalance") is not True:
        return GatePolicy(
            False, "guardrails.no_sell_to_rebalance must be true (gate refuses to run otherwise)"
        )
    sides = g.get("allowed_sides")
    if not isinstance(sides, list) or not all(isinstance(s, str) for s in sides):
        return GatePolicy(False, "guardrails.allowed_sides missing or malformed")
    # Config can only NARROW the gate. Sell/short can never be added here.
    allowed = tuple(sorted({_norm_token(s) for s in sides} & {"buy"}))
    blocked = g.get("blocked_symbols") or []
    if not isinstance(blocked, list):
        return GatePolicy(False, "guardrails.blocked_symbols malformed")
    limits = data.get("limits") if isinstance(data.get("limits"), dict) else {}
    min_trade = _num(limits.get("min_trade_notional_usd"))
    cap_raw = g.get("sell_override_total_cap_usd")
    cap = _num(cap_raw)
    if cap_raw is not None and (cap is None or cap <= 0):
        return GatePolicy(False, "guardrails.sell_override_total_cap_usd malformed")
    return GatePolicy(
        True,
        "",
        allowed,
        tuple(str(s).strip().upper() for s in blocked),
        live=data.get("live") is True,
        min_trade_usd=min_trade if min_trade and min_trade > 0 else 1.0,
        override_total_cap_usd=cap,
    )


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def classify_side(intent: Any) -> Tuple[str, str]:
    """Return (side, detail); side in buy | sell | short | unknown. Fail closed.

    * every side-like field present must agree; a mix of buy and sell is unknown
    * anything that is not exactly a buy token and not recognisably a sell/short
      word is unknown (also blocked)
    """
    if not isinstance(intent, dict):
        return "unknown", "intent is not an object"
    tokens = [_norm_token(intent[k]) for k in _SIDE_KEYS if intent.get(k) not in (None, "")]
    if not tokens:
        return "unknown", "no side field"
    kinds = set()
    for t in tokens:
        if t in _BUY:
            kinds.add("buy")
        elif "short" in t or "write" in t or t in ("sell_to_open",):
            kinds.add("short")
        elif any(w in t for w in _SELL_WORDS):
            kinds.add("sell")
        else:
            kinds.add("unknown")
    if kinds == {"buy"}:
        return "buy", ""
    if "unknown" in kinds or ("buy" in kinds and len(kinds) > 1):
        return "unknown", f"conflicting or unrecognised side {sorted(set(tokens))}"
    return ("short" if "short" in kinds else "sell"), ""


def _is_option(intent: Dict[str, Any]) -> bool:
    for k in ("asset_class", "instrument", "instrument_type", "security_type", "type"):
        if "option" in _norm_token(intent.get(k, "")):
            return True
    return any(intent.get(k) not in (None, "", [], {}) for k in _OPTION_KEYS)


def _is_crypto(intent: Dict[str, Any]) -> bool:
    for k in ("asset_class", "instrument", "instrument_type", "security_type"):
        if "crypto" in _norm_token(intent.get(k, "")):
            return True
    return False


def classify_purpose(intent: Dict[str, Any]) -> str:
    """rebalance | <non-rebalance purpose> | unclassified.

    Any rebalance-ish wording anywhere in the free text wins over an explicit
    purpose (conservative: ``purpose=risk_exit`` + "to rebalance" => rebalance).
    """
    for k in _TEXT_KEYS:
        v = intent.get(k)
        if isinstance(v, str) and _REBALANCE_RE.search(v):
            return "rebalance"
    purpose = _norm_token(intent.get("purpose", "")) if intent.get("purpose") else ""
    if purpose in NON_REBALANCE_PURPOSES:
        return purpose
    return "unclassified"


def intent_symbol(intent: Dict[str, Any]) -> str:
    return str(intent.get("symbol") or intent.get("ticker") or "").strip().upper()


def intent_qty(intent: Dict[str, Any]) -> Optional[float]:
    for k in ("quantity", "qty", "shares"):
        n = _num(intent.get(k))
        if n is not None:
            return n
    return None


def intent_notional(intent: Dict[str, Any]) -> Optional[float]:
    for k in _AMOUNT_KEYS:
        n = _num(intent.get(k))
        if n is not None:
            return n
    qty = intent_qty(intent)
    px = None
    for k in ("limit_price", "price"):
        px = _num(intent.get(k))
        if px is not None:
            break
    if qty is not None and px is not None:
        return qty * px
    return None


# --------------------------------------------------------------------------
# Override (explicit Chris approval; <= 24h; committed; spend-tracked)
# --------------------------------------------------------------------------
#
# File format (``investment/fund_manager_sell_override.json``), committed by Chris:
#   {"overrides": [{"id": "...", "approver": "Chris", "symbols": [...],
#                   "max_qty": .., "max_notional_usd": .., "reason": "..",
#                   "approved_at": "..Z", "expires_at": "..Z"}],
#    "total_max_notional_usd": 50}          # optional, can only lower the ceiling
# A bare single-override object (with an ``id``) is accepted as a one-item list.
# Caps are CUMULATIVE per override id across runs (spend ledger) and a total
# notional ceiling applies across all overrides in any rolling 24h window.


class LedgerError(Exception):
    """Spend ledger unreadable/corrupt: sells fail closed."""


_AUTH_MINT = object()


class AuthedOverride:
    """Override contents that passed git authentication. Minted only by
    ``load_override`` (or ``_mint_authed_override`` for tests): a plain dict
    can never authorise a sell."""

    __slots__ = ("overrides", "total_cap_usd", "commit")

    def __init__(self, overrides: List[Dict[str, Any]], total_cap_usd: float, commit: str, _token: object = None):
        if _token is not _AUTH_MINT:
            raise TypeError("AuthedOverride can only be created by load_override")
        self.overrides = list(overrides)
        self.total_cap_usd = float(total_cap_usd)
        self.commit = commit


def _mint_authed_override(
    data: Any, *, commit: str = "test", total_cap_usd: Optional[float] = None
) -> AuthedOverride:
    """Shape-parse override JSON into an AuthedOverride. Does NOT authenticate
    (``load_override`` does that first); unit tests use it to skip git."""
    if not isinstance(data, dict):
        raise ValueError("override is not a JSON object")
    if "overrides" in data:
        items = data.get("overrides")
        if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
            raise ValueError("overrides must be a non-empty list of objects")
    else:
        items = [data]
    cap = DEFAULT_TOTAL_CAP_USD
    if total_cap_usd is not None:
        cap = min(cap, total_cap_usd)
    if "total_max_notional_usd" in data:
        file_cap = _num(data.get("total_max_notional_usd"))
        if file_cap is None or file_cap <= 0:
            raise ValueError("total_max_notional_usd malformed")
        cap = min(cap, file_cap)
    return AuthedOverride(items, cap, commit, _AUTH_MINT)


def _git(root: Path, *args: str) -> "subprocess.CompletedProcess[bytes]":
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, env=env, timeout=15, check=False
    )


def _git_text(root: Path, *args: str) -> Tuple[bool, str]:
    try:
        r = _git(root, *args)
    except (OSError, subprocess.SubprocessError):
        return False, "git unavailable"
    return r.returncode == 0, r.stdout.decode("utf-8", "replace").strip()


def verify_committed(path: Path, repo_root: Optional[Path] = None) -> Tuple[bool, str, str, bytes]:
    """Authenticate an override file against git. Returns (ok, why, commit, bytes).

    Requires: inside a git repo; tracked; clean (no staged/unstaged/untracked
    change); bytes identical to the blob at HEAD; the last commit touching it
    authored by Chris (``CHRIS_AUTHOR_EMAILS``) with a trusted committer; and,
    when ``FM_OVERRIDE_TRUSTED_REF`` is set, already contained in that ref.
    Anything that cannot be verified fails closed.
    """
    p = Path(path)
    try:
        if p.is_symlink():
            return False, "override file is a symlink", "", b""
        data = p.read_bytes()
        p = p.resolve()
        root = Path(repo_root or ROOT).resolve()
    except OSError:
        return False, "override file unreadable", "", b""
    ok, top = _git_text(root, "rev-parse", "--show-toplevel")
    if not ok or not top:
        return False, "not a git repository (cannot verify override)", "", b""
    top_p = Path(top).resolve()
    try:
        rel = p.relative_to(top_p).as_posix()
    except ValueError:
        return False, "override file is outside the git repository", "", b""
    if not _git_text(top_p, "ls-files", "--error-unmatch", "--", rel)[0]:
        return False, "override file is not committed (untracked)", "", b""
    ok, st = _git_text(top_p, "status", "--porcelain=v1", "--untracked-files=all", "--", rel)
    if not ok or st:
        return False, "override file has uncommitted changes", "", b""
    try:
        blob = _git(top_p, "cat-file", "blob", f"HEAD:{rel}")
    except (OSError, subprocess.SubprocessError):
        return False, "git unavailable", "", b""
    if blob.returncode != 0:
        return False, "override file is not in git HEAD", "", b""
    if blob.stdout != data:
        return False, "override file differs from git HEAD (modified after commit)", "", b""
    ok, line = _git_text(top_p, "log", "-1", "--format=%H%x00%ae%x00%ce", "HEAD", "--", rel)
    parts = line.split("\x00")
    if not ok or len(parts) != 3 or not parts[0]:
        return False, "cannot determine the commit that last changed the override", "", b""
    commit, author, committer = parts[0], parts[1].strip().lower(), parts[2].strip().lower()
    if author not in CHRIS_AUTHOR_EMAILS:
        return False, "override commit author is not Chris", "", b""
    if committer not in TRUSTED_COMMITTER_EMAILS:
        return False, "override commit committer is not Chris or GitHub", "", b""
    ref = (os.environ.get(OVERRIDE_TRUSTED_REF_ENV) or "").strip()
    if ref:
        ok, _ = _git_text(top_p, "merge-base", "--is-ancestor", commit, ref)
        if not ok:
            return False, f"override commit is not contained in {ref}", "", b""
    return True, "committed by Chris", commit, data


def load_override(
    path: Optional[Path] = None,
    *,
    repo_root: Optional[Path] = None,
    policy: Optional[GatePolicy] = None,
) -> Tuple[Optional[AuthedOverride], str]:
    """(authenticated override, error). Missing file => (None, "") i.e. no override.

    A file that exists but is uncommitted, modified, wrongly authored, or
    malformed returns (None, reason): it authorises nothing.
    """
    p = Path(path or OVERRIDE_PATH)
    if not p.exists() and not p.is_symlink():
        return None, ""
    ok, why, commit, raw = verify_committed(p, repo_root)
    if not ok:
        return None, f"override not authenticated: {why}"
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "override file unreadable/malformed"
    try:
        cap = policy.override_total_cap_usd if policy is not None else None
        return _mint_authed_override(data, commit=commit, total_cap_usd=cap), ""
    except ValueError as exc:
        return None, f"override file malformed: {exc}"


# ---- spend ledger -------------------------------------------------------


class SpendLedger:
    """Cumulative sell spend per override id, persisted atomically.

    ``transaction()`` holds an exclusive flock for the whole gate pass, so two
    concurrent runs cannot both spend the same headroom. Corrupt or unreadable
    state raises ``LedgerError`` (callers fail closed); a missing file is empty.
    """

    def __init__(self, path: Optional[Path] = None, data: Optional[Dict[str, Any]] = None, persist: bool = True):
        self.path = Path(path or SPEND_LEDGER_PATH)
        self.data: Dict[str, Any] = data if data is not None else {"version": 1, "overrides": {}}
        self.persist = persist
        self.dirty = False

    @classmethod
    def _load(cls, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {"version": 1, "overrides": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LedgerError(f"spend ledger unreadable ({type(exc).__name__})") from exc
        if not isinstance(data, dict) or not isinstance(data.get("overrides"), dict):
            raise LedgerError("spend ledger malformed")
        for oid, e in data["overrides"].items():
            if not isinstance(e, dict) or _num(e.get("qty")) is None or _num(e.get("usd")) is None:
                raise LedgerError(f"spend ledger entry {oid!r} malformed")
        return data

    @classmethod
    @contextlib.contextmanager
    def transaction(cls, path: Optional[Path] = None, *, persist: bool = True):
        """Lock, load, yield a ledger, and (if persist and changed) save atomically."""
        import fcntl

        p = Path(path or SPEND_LEDGER_PATH)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            lock = open(str(p) + ".lock", "a+")
        except OSError as exc:
            raise LedgerError(f"spend ledger lock unavailable ({type(exc).__name__})") from exc
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            led = cls(p, cls._load(p), persist)
            yield led
            if persist and led.dirty:
                led.save()
        finally:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            finally:
                lock.close()

    def save(self) -> None:
        """Atomic write: temp file in the same dir, fsync, rename."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".spend-", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        self.dirty = False

    def used(self, override_id: str) -> Tuple[float, float]:
        e = self.data["overrides"].get(override_id)
        if not e:
            return 0.0, 0.0
        return float(_num(e.get("qty")) or 0.0), float(_num(e.get("usd")) or 0.0)

    def total_used_usd(self, now: datetime) -> float:
        """Notional spent by ANY override in the rolling MAX_OVERRIDE_HOURS window."""
        cutoff = now - timedelta(hours=MAX_OVERRIDE_HOURS)
        total = 0.0
        for e in self.data["overrides"].values():
            last = _parse_ts(e.get("last_used_at"))
            if last is None or last > cutoff:  # unparseable => count it (fail closed)
                total += float(_num(e.get("usd")) or 0.0)
        return total

    def add(self, override_id: str, qty: float, usd: float, now: datetime, expires_at: str = "") -> None:
        e = self.data["overrides"].setdefault(
            override_id,
            {"qty": 0.0, "usd": 0.0, "uses": 0, "first_used_at": now.isoformat(), "expires_at": expires_at},
        )
        e["qty"] = float(_num(e.get("qty")) or 0.0) + float(qty or 0.0)
        e["usd"] = float(_num(e.get("usd")) or 0.0) + float(usd or 0.0)
        e["uses"] = int(e.get("uses") or 0) + 1
        e["last_used_at"] = now.isoformat()
        if expires_at:
            e["expires_at"] = expires_at
        self.dirty = True


def _override_id(override: Dict[str, Any]) -> Optional[str]:
    oid = override.get("id")
    if isinstance(oid, str) and oid.strip() and len(oid.strip()) <= 120:
        return oid.strip()
    return None


def validate_override(
    override: Any,
    *,
    symbol: str,
    qty: Optional[float],
    notional: Optional[float],
    now: datetime,
    ledger: Optional[SpendLedger] = None,
    total_cap_usd: float = DEFAULT_TOTAL_CAP_USD,
    enforce_spend: bool = True,
) -> Tuple[bool, str]:
    """Is this single override entry valid, unexpired, and does it cover THIS sell
    on top of what it (and all overrides) already spent? ``enforce_spend=False``
    is for the detective decision-log check: per-order limits only."""
    if not isinstance(override, dict):
        return False, "no override"
    oid = _override_id(override)
    if oid is None:
        return False, "override has no id (needed for spend tracking)"
    if str(override.get("approver") or "").strip().lower() != "chris":
        return False, "override approver is not Chris"
    reason = override.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return False, "override has no reason"
    syms = override.get("symbols")
    if not isinstance(syms, list) or not syms or not all(isinstance(s, str) and s.strip() for s in syms):
        return False, "override symbols missing/malformed"
    exp = _parse_ts(override.get("expires_at"))
    if exp is None:
        return False, "override expires_at missing/malformed (timezone required)"
    if exp <= now:
        return False, "override expired"
    if exp > now + timedelta(hours=MAX_OVERRIDE_HOURS):
        return False, f"override expires more than {MAX_OVERRIDE_HOURS}h out"
    if override.get("approved_at") is not None:
        appr = _parse_ts(override.get("approved_at"))
        if appr is None or appr > now or exp - appr > timedelta(hours=MAX_OVERRIDE_HOURS):
            return False, "override approved_at invalid or window > 24h"
    max_qty = _num(override.get("max_qty"))
    max_notional = _num(override.get("max_notional_usd"))
    if (max_qty is None or max_qty <= 0) and (max_notional is None or max_notional <= 0):
        return False, "override needs a positive max_qty or max_notional_usd"
    if symbol not in {s.strip().upper() for s in syms}:
        return False, "symbol not covered by override"
    used_qty = used_usd = 0.0
    if enforce_spend:
        if ledger is None:
            return False, "spend ledger unavailable (cannot enforce cumulative caps)"
        used_qty, used_usd = ledger.used(oid)
        if notional is None or notional <= 0:
            return False, "sell notional unknown (needed for the total cap)"
    if max_qty is not None and max_qty > 0:
        if qty is None or qty <= 0 or qty + used_qty > max_qty + 1e-12:
            return False, (
                f"qty missing or above override max_qty (used {used_qty:g} of {max_qty:g})"
                if enforce_spend
                else "qty missing or above override max_qty"
            )
    if max_notional is not None and max_notional > 0:
        if notional is None or notional <= 0 or notional + used_usd > max_notional + 1e-9:
            return False, (
                f"notional missing or above override max_notional_usd (used ${used_usd:.2f} of ${max_notional:.2f})"
                if enforce_spend
                else "notional missing or above override max_notional_usd"
            )
    if enforce_spend:
        assert ledger is not None and notional is not None
        total_used = ledger.total_used_usd(now)
        if total_used + notional > total_cap_usd + 1e-9:
            return False, (
                f"total override cap exceeded (${total_used:.2f} used + ${notional:.2f} > "
                f"${total_cap_usd:.2f} per {MAX_OVERRIDE_HOURS}h across all overrides)"
            )
    return True, "override ok"


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------


def _block(code: str, reason: str, side: str = "unknown") -> Verdict:
    return Verdict(False, code, reason, side=side)


def evaluate_sell(
    intent: Dict[str, Any],
    side: str,
    *,
    override: Any = None,
    now: Optional[datetime] = None,
    ledger: Optional[SpendLedger] = None,
    policy_total_cap_usd: Optional[float] = None,
    enforce_spend: bool = True,
) -> Verdict:
    """Verdict for something already classified as sell/short.

    Only an ``AuthedOverride`` (git-authenticated, see ``load_override``) can
    approve a sell, and only within its cumulative per-override caps and the
    total cap tracked in ``ledger``. A pure function: the caller charges the
    returned ``Verdict.spend`` to the ledger (``gate_orders`` does).
    """
    now = now or _now()
    sym = intent_symbol(intent)
    if side == "short" or intent.get("short") is True:
        return _block(R_SHORT, f"short/option-write {sym or '?'} is never allowed", "short")
    if _is_option(intent):
        return _block(R_OPTION, f"option order {sym or '?'} is never allowed", "sell")
    if _is_crypto(intent):
        return _block(R_CRYPTO_SELL, f"crypto sell {sym or '?'} is never allowed", "sell")
    purpose = classify_purpose(intent)
    if purpose == "rebalance":
        return _block(
            R_REBALANCE_SELL,
            f"sell {sym or '?'} to rebalance is forbidden (no-sell-to-rebalance; "
            "rebalance with new cash only). Not overridable.",
            "sell",
        )
    if purpose == "unclassified":
        return _block(
            R_UNCLASSIFIED_SELL,
            f"sell {sym or '?'} has no explicit non-rebalance purpose "
            f"({', '.join(sorted(NON_REBALANCE_PURPOSES))}); failing closed",
            "sell",
        )
    if not sym:
        return _block(R_BAD_INTENT, "sell without a symbol", "sell")
    if override is None:
        return _block(R_SELL_NO_OVERRIDE, f"sell {sym} blocked: no override", "sell")
    if not isinstance(override, AuthedOverride):
        return _block(
            R_OVERRIDE_UNAUTH,
            f"sell {sym} blocked: override is not authenticated (must be committed by Chris; load_override)",
            "sell",
        )
    cap = override.total_cap_usd
    if policy_total_cap_usd is not None:
        cap = min(cap, policy_total_cap_usd)
    qty, notional = intent_qty(intent), intent_notional(intent)
    why = "no override entries"
    # earliest-expiring first, so a nearly-expired approval is used up first
    for o in sorted(override.overrides, key=lambda o: str(o.get("expires_at"))):
        ok, why = validate_override(
            o, symbol=sym, qty=qty, notional=notional, now=now,
            ledger=ledger, total_cap_usd=cap, enforce_spend=enforce_spend,
        )
        if ok:
            oid = _override_id(o) or "?"
            spend = (oid, qty or 0.0, notional or 0.0, str(o.get("expires_at"))) if enforce_spend else None
            return Verdict(
                True, "ok",
                f"sell {sym} allowed by Chris override {oid} ({purpose})", "sell", True, spend=spend,
            )
    return _block(R_SELL_NO_OVERRIDE, f"sell {sym} blocked: {why}", "sell")


def evaluate_intent(
    intent: Any,
    *,
    policy: Optional[GatePolicy] = None,
    override: Any = None,
    now: Optional[datetime] = None,
    deployable_cash: Optional[float] = None,
    committed_buy_usd: float = 0.0,
    ledger: Optional[SpendLedger] = None,
) -> Verdict:
    """Single-order verdict. Never raises: any error is a block."""
    try:
        return _evaluate_intent(intent, policy, override, now, deployable_cash, committed_buy_usd, ledger)
    except Exception as exc:  # fail closed
        return _block(R_BAD_INTENT, f"gate error {type(exc).__name__}: failing closed")


def _evaluate_intent(intent, policy, override, now, deployable_cash, committed_buy_usd, ledger=None) -> Verdict:
    policy = policy or load_gate_policy()
    if not policy.ok:
        return _block(R_POLICY, f"gate policy unavailable ({policy.error}); placing nothing")
    if not isinstance(intent, dict):
        return _block(R_BAD_INTENT, "intent is not an object")
    side, detail = classify_side(intent)
    if side == "unknown":
        return _block(R_UNCLASSIFIABLE, f"cannot classify order side ({detail}); failing closed")
    if side in ("sell", "short"):
        return evaluate_sell(
            intent, side, override=override, now=now, ledger=ledger,
            policy_total_cap_usd=policy.override_total_cap_usd,
        )
    # ---- buys ----
    if "buy" not in policy.allowed_sides:
        return _block(R_POLICY, "policy allows no sides; placing nothing", "buy")
    if _is_option(intent):
        return _block(R_OPTION, "option order is never allowed", "buy")
    sym = intent_symbol(intent)
    if not sym:
        return _block(R_BAD_INTENT, "buy without a symbol", "buy")
    if sym in policy.blocked_symbols:
        return _block(R_BLOCKED_SYMBOL, f"{sym} is in guardrails.blocked_symbols", "buy")
    amt = intent_notional(intent)
    if amt is None or amt <= 0:
        return _block(R_BAD_AMOUNT, f"buy {sym} has no positive known notional", "buy")
    if deployable_cash is None:
        return _block(R_CASH_UNKNOWN, "deployable cash unknown; buys fail closed", "buy")
    if committed_buy_usd + amt > deployable_cash + 1e-9:
        return _block(
            R_OVER_CASH,
            f"buy {sym} ${amt:.2f} would exceed deployable cash (no margin, no sell-to-fund)",
            "buy",
        )
    return Verdict(True, "ok", f"buy {sym} ${amt:.2f} within deployable cash", "buy")


@dataclass
class BatchResult:
    allowed: List[GatedOrder] = field(default_factory=list)
    blocked: List[Tuple[Any, Verdict]] = field(default_factory=list)

    @property
    def blocked_sells(self) -> List[Tuple[Any, Verdict]]:
        return [(i, v) for i, v in self.blocked if v.side in ("sell", "short") or v.code in (R_UNCLASSIFIABLE,)]


def gate_orders(
    intents: Any,
    *,
    deployable_cash: Optional[float],
    policy: Optional[GatePolicy] = None,
    override: Any = None,
    now: Optional[datetime] = None,
    ledger: Optional[SpendLedger] = None,
) -> BatchResult:
    """Gate a batch. Sum of allowed buys is capped at deployable cash. Every
    approved sell is charged to ``ledger`` (in memory; the caller persists) so
    later sells in the batch and later runs see the spend."""
    res = BatchResult()
    policy = policy or load_gate_policy()
    now = now or _now()
    if not isinstance(intents, list):
        res.blocked.append((intents, _block(R_BAD_INTENT, "intents payload is not a list")))
        return res
    committed = 0.0
    for it in intents:
        v = evaluate_intent(
            it, policy=policy, override=override, now=now,
            deployable_cash=deployable_cash, committed_buy_usd=committed, ledger=ledger,
        )
        if v.allowed and v.side == "buy":
            committed += intent_notional(it) or 0.0
        if v.allowed and v.side == "sell":
            if ledger is None or v.spend is None:  # cannot account for it => cannot allow it
                v = _block(R_SELL_NO_OVERRIDE, "sell blocked: spend could not be recorded", "sell")
            else:
                oid, q, usd, exp = v.spend
                ledger.add(oid, q, usd, now, exp)
        if v.allowed:
            res.allowed.append(_mint_gated(it, v))
        else:
            res.blocked.append((it, v))
    return res


# --------------------------------------------------------------------------
# Execution (the only submit path)
# --------------------------------------------------------------------------

Submit = Callable[[GatedOrder], Dict[str, Any]]
_BROKER_ROW_KEYS = ("broker_reason", "order_id", "idempotency_key", "broker_state", "filled_quantity", "fill_price")


def load_broker_adapter() -> Optional[Submit]:
    """Optional ``module:callable`` from FM_BROKER_ADAPTER. None => place nothing."""
    spec = (os.environ.get(BROKER_ADAPTER_ENV) or "").strip()
    if not spec or ":" not in spec:
        return None
    mod, _, fn = spec.partition(":")
    try:
        return getattr(importlib.import_module(mod), fn)
    except Exception:
        return None


def _action_row(intent: Any, verdict: Verdict, status: str) -> Dict[str, Any]:
    it = intent if isinstance(intent, dict) else {"raw": str(intent)[:80]}
    row: Dict[str, Any] = {
        "symbol": intent_symbol(it) or "?",
        "side": str(it.get("side") or it.get("action") or "?"),
        "status": status,
        "reason_code": verdict.code,
        "reason": verdict.reason,
    }
    n = intent_notional(it)
    q = intent_qty(it)
    if n is not None:
        row["notional_usd"] = round(n, 4)
    if q is not None:
        row["quantity"] = q
    if it.get("purpose"):
        row["purpose"] = str(it.get("purpose"))[:60]
    return row


def execute_intents(
    intents: Any,
    *,
    deployable_cash: Optional[float],
    submit: Optional[Submit] = None,
    policy: Optional[GatePolicy] = None,
    override: Any = None,
    now: Optional[datetime] = None,
    dry_run: Optional[bool] = None,
    record: bool = True,
    record_fn: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ledger_path: Optional[Path] = None,
    plan: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Gate, log, and (only for approved orders) submit.

    ``plan`` is the broker run planner's info (``submit.plan``); placed orders
    and planner repairs/rejections are posted as one decision entry (#701).

    ``submit`` is never called with anything but a ``GatedOrder``. With
    ``live:false`` in policy (or ``dry_run``) nothing is submitted; approved
    orders are reported as ``would_place``. No adapter => nothing submitted.
    """
    policy = policy or load_gate_policy()
    live = bool(policy.ok and policy.live)
    if dry_run is None:
        dry_run = not live
    ledger_error = ""
    if override is None:
        res = gate_orders(intents, deployable_cash=deployable_cash, policy=policy, override=None, now=now)
    else:
        # Hold the ledger lock for the whole pass and persist the spend BEFORE any
        # submit, so concurrent runs cannot spend the same headroom and a crash
        # after approval never refunds it. A dry run reads but never writes.
        try:
            with SpendLedger.transaction(ledger_path, persist=not dry_run) as led:
                res = gate_orders(
                    intents, deployable_cash=deployable_cash, policy=policy,
                    override=override, now=now, ledger=led,
                )
        except LedgerError as exc:
            ledger_error = str(exc)
            res = gate_orders(
                intents, deployable_cash=deployable_cash, policy=policy, override=override, now=now, ledger=None
            )
    out: Dict[str, Any] = {
        "live": live,
        "dry_run": bool(dry_run),
        "placed": [],
        "would_place": [],
        "blocked": [],
        "broker_calls": 0,
    }
    if ledger_error:
        out["ledger_error"] = ledger_error
    actions: List[Dict[str, Any]] = []
    for gated in res.allowed:
        broker: Dict[str, Any] = {}
        if dry_run:
            status = "would_place"
        elif submit is None:
            status = "not_submitted_no_broker_adapter"
        else:
            try:
                result = submit(gated)
                status = str((result or {}).get("status") or "submitted")
                out["broker_calls"] += 1
                if isinstance(result, dict):  # adapter detail for the run log / #701 (no secrets)
                    broker = {k: result[k] for k in _BROKER_ROW_KEYS if result.get(k) not in (None, "")}
            except Exception as exc:
                status = f"submit_error_{type(exc).__name__}"
        row = _action_row(gated.intent, gated.verdict, status)
        row.update(broker)
        actions.append(row)
        out["would_place" if status == "would_place" else "placed"].append(row)
    for it, v in res.blocked:
        row = _action_row(it, v, v.status)
        actions.append(row)
        out["blocked"].append(row)
        _log_blocked_line(row)
    if record and res.blocked:
        any_sell = bool(res.blocked_sells)
        entry = {
            "kind": "blocked_sell" if any_sell else "blocked_order",
            "outcome": "blocked",
            "path": "order_gate",
            "need_llm": False,
            "summary": (
                f"Order gate blocked {len(res.blocked)} order(s): "
                + "; ".join(f"{r['symbol']} {r['side']} [{r['reason_code']}]" for r in out["blocked"])
            ),
            "rationale": {
                "why_now": "Fund-manager intents passed through the no-sell-to-rebalance order gate (#1093).",
                "why_not_alternatives": "Rebalance with new cash only; sells need an explicit unexpired Chris override.",
                "thesis_tags": ["no_sell_guard", "order_gate"],
            },
            "actions": actions,
            "blocked_sell": any_sell,
        }
        try:
            (record_fn or _default_record)(entry)
        except Exception as exc:  # logging must never turn into an order
            out["record_error"] = type(exc).__name__
    if record and not dry_run:
        entry = _execution_entry(out, plan)
        if entry is not None:
            try:
                (record_fn or _default_record)(entry)
            except Exception as exc:  # logging must never turn into an order
                out["record_error"] = type(exc).__name__
    return out


def _execution_entry(out: Dict[str, Any], plan: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Decision entry (#701) for placed orders + planner repairs/rejections.
    None when there is nothing to report (no placement, no repair/drop)."""
    placed = [r for r in out.get("placed") or [] if str(r.get("status")) in EXECUTED_STATUSES]
    others = [r for r in out.get("placed") or [] if r not in placed]
    plan = plan or {}
    dropped = list(plan.get("dropped") or [])
    repairs = list(plan.get("repairs") or [])
    if not placed and not dropped and not repairs:
        return None
    total = sum(float(r.get("notional_usd") or 0) for r in placed)
    legs = ", ".join(f"{r['symbol']} ${float(r.get('notional_usd') or 0):.2f}" for r in placed) or "none"
    valuation = str(plan.get("valuation") or "")
    summary = (
        f"Run {plan.get('run_id') or '?'} [{plan.get('status') or 'unplanned'}]: placed {len(placed)} buy(s) "
        f"${total:.2f} ({legs}); not placed {len(others)}; sleeve/weight drops {len(dropped)}"
    )
    if "cost_basis" in valuation:
        summary += f"; 40% test valued at cost basis ({valuation})"
    if str(plan.get("floating_source") or "").find("fallback") >= 0:
        summary += f"; floating {plan.get('floating_source')}"
    actions: List[Dict[str, Any]] = []
    for r in placed + others:
        a = {k: r.get(k) for k in ("symbol", "side", "notional_usd", "status", "order_id", "broker_reason")}
        a["qty"] = r.get("filled_quantity")
        a["fill_price"] = r.get("fill_price")
        actions.append(a)
    for d in dropped:
        actions.append({"symbol": d.get("symbol"), "side": "buy", "notional_usd": d.get("notional_usd"),
                        "status": d.get("status"), "broker_reason": d.get("reason")})
    return {
        "kind": "deploy" if placed else "deploy_not_placed",
        "outcome": "placed" if placed else "not_placed",
        "path": "order_gate+broker_adapter",
        "need_llm": False,
        "summary": summary,
        "rationale": {
            "why_now": "Gate-approved buys from the deterministic run plan (BTC + digital credit first to 40%, "
                       "STRC 25 / SATA 25 / STRK 15 / floating 35, TSLA/SPCX 50/50 after).",
            "why_not_alternatives": "Buy-only; any sell is a manual action Chris takes himself.",
            "thesis_tags": ["broker_adapter", "run_plan", "no_sell_guard"],
        },
        "plan": {k: plan.get(k) for k in ("run_id", "status", "run_total_usd", "settled_cash", "btc_share_before",
                                           "valuation", "floating_source", "ai_proposed_usd",
                                           "repairs", "notes", "legs") if k in plan},
        "actions": actions,
    }


def _log_blocked_line(row: Dict[str, Any]) -> None:
    print(
        f"[fund-manager order gate] {BLOCK_STATUS}: {row.get('side')} {row.get('symbol')} "
        f"[{row.get('reason_code')}] {row.get('reason')}",
        file=sys.stderr,
    )


def _default_record(entry: Dict[str, Any]) -> Any:
    from treasury.fund_manager import append_decision

    return append_decision(entry)


# --------------------------------------------------------------------------
# Decision-log level guard (used by fund_manager.append_decision)
# --------------------------------------------------------------------------

_QUIET_STATUS = frozenset({"", "none", "hold", "skipped", "not_sent", "no_action", "cancelled", "canceled"})


def guard_decision_entry(
    entry: Dict[str, Any], *, override: Any = None, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Return a copy of a decision record with sell-type actions marked/blocked.

    Detective for anything already executed (``guard_violation``), preventive
    marking for proposals. Cancels and non-order rows are left alone. Never
    raises; on internal error the record gains ``guard_error`` and is kept.
    """
    out = dict(entry)
    try:
        violations: List[Dict[str, Any]] = []
        new_actions: List[Any] = []
        for a in list(entry.get("actions") or []):
            if not isinstance(a, dict):
                new_actions.append(a)
                continue
            kind_tok = _norm_token(a.get("action") or a.get("type") or "")
            status = _norm_token(a.get("status") or "")
            if kind_tok.startswith("cancel") or status in ("cancelled", "canceled"):
                new_actions.append(a)
                continue
            side, detail = classify_side(a)
            if side == "buy":
                new_actions.append(a)
                continue
            if side == "unknown" and not any(a.get(k) for k in _SIDE_KEYS) and status in _QUIET_STATUS:
                new_actions.append(a)
                continue
            verdict = (
                evaluate_sell(a, side, override=override, now=now, enforce_spend=False)
                if side in ("sell", "short")
                else _block(R_UNCLASSIFIABLE, f"cannot classify order side ({detail})")
            )
            if verdict.allowed:
                new_actions.append(a)
                continue
            b = dict(a)
            executed = status in EXECUTED_STATUSES
            b["guard_prev_status"] = a.get("status")
            b["status"] = BLOCK_STATUS if not executed else a.get("status")
            b["reason_code"] = verdict.code
            b["reason"] = verdict.reason
            if executed:
                b["guard_violation"] = True
            violations.append({"symbol": intent_symbol(a) or "?", "code": verdict.code, "executed": executed})
            new_actions.append(b)
        if violations:
            out["actions"] = new_actions
            out["blocked_sell"] = True
            out["guard"] = {"status": BLOCK_STATUS, "violations": violations}
            tag = "SELL EXECUTED PAST GUARD" if any(v["executed"] for v in violations) else "BLOCKED SELL"
            summ = str(out.get("summary") or "")
            if not summ.startswith("[" + tag):
                out["summary"] = f"[{tag}] {summ}".strip()
    except Exception as exc:  # pragma: no cover - defensive
        out["guard_error"] = type(exc).__name__
    return out


# --------------------------------------------------------------------------
# grok runner hardening
# --------------------------------------------------------------------------

RH_SERVER = "robinhood-trading"
# Order-mutating RH MCP tool name globs. cancel_* is deliberately NOT here.
RH_ORDER_TOOL_GLOBS = (
    "place_*",
    "replace_*",
    "modify_*",
    "submit_*",
    "create_*order*",
    "*sell*",
    "*short*",
    "*transfer*",
    "*withdraw*",
    "exercise_option",  # state-changing trade action; cancel_option_exercise stays allowed
)


# Read-only RH MCP tools the snapshot refresh (treasury/rh_refresh_prompt.txt,
# run by rh_snapshot_sync.py) needs. The deny globs above must never match these;
# a test pins both this list and the prompt. Change globs/tools HERE only.
RH_READ_TOOLS = ("get_accounts", "get_portfolio", "get_equity_positions")


def is_tool_denied(tool: str) -> bool:
    """Would ``grok_deny_args()`` deny RH MCP tool ``tool``? (same globs, one place)"""
    return any(fnmatch.fnmatchcase(tool, g) for g in RH_ORDER_TOOL_GLOBS)


# The ONLY order tool that may ever be carved out of the deny list, and only by
# the buy-only broker adapter's place step (treasury/fund_manager_broker.py),
# which runs strictly after this gate approved the order.
RH_CARVE_OUT_TOOLS = ("place_equity_order",)
# Known siblings re-denied explicitly when a glob is carved open.
RH_KNOWN_ORDER_TOOLS = ("place_equity_order", "place_option_order", "place_crypto_order")


def grok_deny_args(allow_tools: Iterable[str] = ()) -> List[str]:
    """Flags for ``grok -p … --yolo``. Deny rules still apply under --yolo.

    The ONE shared helper: the fund-manager runners (via ``--print-grok-deny-args``)
    and ``rh_snapshot_sync.py`` (direct import) both take their deny list from here.

    ``allow_tools`` may only name tools in ``RH_CARVE_OUT_TOOLS`` (anything else is
    ignored). A glob matching a carved-out tool is replaced by explicit denies of
    its known siblings; every other glob (``*sell*``, ``*short*``, ...) stays.
    """
    carve = {t for t in allow_tools if t in RH_CARVE_OUT_TOOLS}
    args: List[str] = []
    for g in RH_ORDER_TOOL_GLOBS:
        if any(fnmatch.fnmatchcase(t, g) for t in carve):
            for t in RH_KNOWN_ORDER_TOOLS:
                if t not in carve and fnmatch.fnmatchcase(t, g):
                    args += ["--deny", f"MCPTool({RH_SERVER}__{t})"]
            continue
        args += ["--deny", f"MCPTool({RH_SERVER}__{g})"]
    return args


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _deployable_cash_from_snapshot(path: Optional[Path] = None) -> Optional[float]:
    p = Path(path or test_path("FM_RH_SNAPSHOT_PATH", RH_SNAPSHOT_PATH))
    try:
        snap = json.loads(p.read_text(encoding="utf-8"))
        ag = snap.get("agentic") if isinstance(snap.get("agentic"), dict) else None
        if ag is None:
            return None
        cash, bp = _num(ag.get("cash")), _num(ag.get("buying_power"))
        if cash is None or bp is None:
            return None
        return max(0.0, min(cash, bp))
    except (OSError, ValueError, AttributeError):
        return None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print-grok-deny-args", action="store_true", help="one deny arg per line")
    ap.add_argument("--execute-intents", metavar="FILE", help="gate + (maybe) submit intents JSON")
    ap.add_argument("--dry-run", action="store_true", help="never submit, even when live")
    ap.add_argument("--no-record", action="store_true", help="skip decision log/#701 (tests)")
    args = ap.parse_args(argv)
    if args.print_grok_deny_args:
        print("\n".join(grok_deny_args()))
        return 0
    if args.execute_intents:
        refused = execution_block_reason()
        if refused:
            # Leave the intents file for the deterministic step; submit nothing.
            print(json.dumps({"ok": False, "refused": "advisory_only", "reason": refused,
                              "placed": [], "broker_calls": 0}))
            return 4
        p = Path(args.execute_intents)
        submit = load_broker_adapter()
        planner = getattr(submit, "plan", None) if submit is not None else None
        ai_note = ""
        if not p.is_file():
            if planner is None:
                print(json.dumps({"ok": True, "intents": 0, "note": "no intents file; nothing to do"}))
                return 0
            # AI step errored / timed out / wrote nothing: the planner still runs
            # (run size is deterministic; floating 35% -> IBIT).
            payload, ai_note = [], "no AI intents file (AI step errored/timed out)"
        else:
            try:
                payload = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and "orders" in payload:
                    payload = payload["orders"]
            except (OSError, ValueError):
                payload = "unreadable"
                ai_note = "AI intents file unreadable"
        policy = load_gate_policy()
        override, oerr = load_override(policy=policy)
        plan: Optional[Dict[str, Any]] = None
        if planner is not None:
            # Deterministic run planner (run size, sleeve gate, fixed weights, caps,
            # window, calendar, once-per-ET-day). Sells pass through to be blocked.
            # plan_intents returns plain intent dicts. It must not submit. Every
            # planned buy is minted by gate_orders (below, via execute_intents)
            # and only that GatedOrder is passed to submit.
            try:
                payload, plan = planner(payload, persist=not args.dry_run, ai_note=ai_note)
            except Exception as exc:  # fail closed: drop every buy
                plan = {"status": "plan_error", "reason": type(exc).__name__}
                if isinstance(payload, list):
                    payload = [it for it in payload if classify_side(it)[0] != "buy"]
        result = execute_intents(
            payload,
            deployable_cash=_deployable_cash_from_snapshot(),
            submit=submit,
            policy=policy,
            override=override,
            dry_run=args.dry_run or None,
            record=not args.no_record,
            plan=plan,
        )
        if plan is not None:
            result["plan"] = plan
        if p.is_file():
            try:  # consumed: never replay a stale intents file
                p.rename(p.with_suffix(".consumed.json"))
            except OSError:
                pass
        if oerr:
            result["override_error"] = oerr
        print(json.dumps(result, indent=2, default=str))
        return 3 if result["blocked"] else 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    # `python -m treasury.fund_manager_order_gate` (daily + bp-poll) executes this
    # file as __main__, a different module object from
    # treasury.fund_manager_order_gate. The broker adapter imports the latter and
    # submit() rejects every other GatedOrder ("not a GatedOrder minted by the
    # order gate"). Minting here sent every planned leg (the 2026-10-26 12:30 ET
    # STRC/SATA/STRK/IBIT run) straight into that rejection. Delegate so the gate
    # that mints is the gate submit checks, then submit receives those orders.
    from treasury.fund_manager_order_gate import main as _module_main

    raise SystemExit(_module_main())
