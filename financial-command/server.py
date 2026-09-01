#!/usr/bin/env python3
"""Local server for Financial Command Center.

Serves static UI + APIs:
  GET  /api/treasury   — latest evaluation JSON
  GET  /api/config     — treasury/config.json
  GET  /api/watchlist  — watchlist + deep-dive summaries
  GET  /api/watchlist/deep-dive?symbol=BE — full deep-dive markdown
  GET  /api/capital-flows — income → channel flow model (+ optional live enrich)
  GET  /api/interest-spectrum — APR/APY visual spectrum (no invented rates)
  GET  /api/bias-spectrum — new-money consider-share (core + ready watchlist; not book weight)
  GET  /api/braiins       — Braiins Pool mining snapshot summary
  GET  /api/coach         — financial coach allocation plan (pay on time)
  GET  /api/planned-actual — display-only sheet planned vs YNAB actual flags
  GET  /api/ask/status    — Ask Grok financial advisor auth + model
  POST /api/ask/login     — start existing `grok login --device-auth` (no secrets)
  GET  /api/ask/login     — poll grok CLI login (public fields only)
  POST /api/ask           — {question} ask Grok about FCC/treasury domain
  POST /api/config     — merge-save manual fields / policy
  POST /api/refresh    — re-run treasury evaluation (live Coinbase)

Usage:
  python3 financial-command/server.py
  python3 financial-command/server.py --port 8000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ensure_tool_path() -> None:
    """launchd/systemd often start us with PATH=/usr/bin:/bin.

    Restore Grok Build (`~/.grok/bin`) and Homebrew CLIs. Without this,
    `grok login` fails as "CLI not found" on Pi, and `coinbase` / scp extras
    are invisible so Refresh silently keeps stale CB ages.
    """
    extras = [
        str(Path.home() / ".grok" / "bin"),
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        "/usr/local/bin",
        str(Path.home() / ".local" / "bin"),
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    cur = (os.environ.get("PATH") or "").split(":")
    merged: list[str] = []
    seen: set[str] = set()
    for p in extras + cur:
        p = (p or "").strip()
        if not p or p in seen:
            continue
        if p in extras or Path(p).is_dir():
            seen.add(p)
            merged.append(p)
    os.environ["PATH"] = ":".join(merged)


_ensure_tool_path()

from treasury.adapters import load_config, save_config  # noqa: E402
from treasury.financial_advisor import (  # noqa: E402
    AdvisorError,
    ask_financial_advisor,
    auth_status as advisor_auth_status,
    grok_login_status,
    start_grok_login,
)
from treasury.financial_coach import (  # noqa: E402
    build_coach_plan,
    load_snapshots as load_coach_snapshots,
)
from treasury.run_treasury import main as run_treasury_main  # noqa: E402
from treasury.watchlist_dashboard import (  # noqa: E402
    build_watchlist_dashboard,
    get_deep_dive_markdown,
)
from treasury.interest_spectrum import build_interest_spectrum  # noqa: E402
from treasury.bias_spectrum import build_bias_spectrum  # noqa: E402
from treasury.planned_actual import load_planned_actual  # noqa: E402

BRAIINS_SNAPSHOT = ROOT / "treasury" / "snapshots" / "braiins_latest.json"
SOLANA_SNAPSHOT = ROOT / "treasury" / "snapshots" / "solana_latest.json"
FM_SNAPSHOT = ROOT / "treasury" / "snapshots" / "fund_manager_latest.json"
XM_SNAPSHOT = ROOT / "treasury" / "snapshots" / "x_money_latest.json"
_YNAB_REFRESH_COOLDOWN_S = 300.0  # don't hammer YNAB more than once / 5 min
_last_ynab_refresh_ts = 0.0
# Set in main() from --offline / --consumer. Used for initial boot + consumer detect.
_SERVER_STARTED_OFFLINE = False
_SERVER_CONSUMER = False


def _coinbase_cli_available() -> bool:
    """Fail closed: missing helper / import → no CLI."""
    try:
        from treasury.adapters import _resolve_coinbase_bin

        return bool(_resolve_coinbase_bin())
    except Exception:
        return False


def _has_ynab_token() -> bool:
    try:
        from treasury.ynab_sync import load_ynab_token

        tok, _ = load_ynab_token()
        return bool(tok)
    except Exception:
        return False


def _is_snapshot_consumer() -> bool:
    """True when this host should not run live YNAB/CB (full re-read only).

    --offline alone is only a *boot* hint (Mac ensure uses it for fast start).
    Full consumer = explicit --consumer / FCC_OFFLINE_CONSUMER, or no YNAB
    token *and* no Coinbase CLI. Probe/import failures fail closed (no creds).

    A host with YNAB but no Coinbase CLI is *not* a full consumer (Pi): Refresh
    still live-fetches YNAB/Sheet/Solana; Coinbase stays on the Mac snapshot.
    """
    from treasury.adapters import should_force_offline_consumer

    env_on = os.environ.get("FCC_OFFLINE_CONSUMER", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    return should_force_offline_consumer(
        explicit=_SERVER_CONSUMER,
        env_consumer=env_on,
        has_ynab_token=_has_ynab_token(),
        has_coinbase_cli=_coinbase_cli_available(),
    )


def _snapshot_age_hours(path: Path) -> float | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        iso = data.get("as_of")
        if not iso:
            return None
        from datetime import datetime, timezone

        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 3600.0
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _ynab_refresh_ok(ynab_report: object) -> bool:
    """True only when every YNAB feed is live-clean. Soft-preserve is never 'ok'."""
    from treasury.ynab_sync import ynab_feeds_clean

    return ynab_feeds_clean(ynab_report)


def _ynab_soft_preserved(ynab_report: object, feed: str = "x_money") -> bool:
    """FCC flag: a feed kept its prior snapshot after a live pick/sync miss."""
    from treasury.ynab_sync import ynab_feed_soft_preserved

    return ynab_feed_soft_preserved(ynab_report, feed)


def _ynab_cash_stale(max_hours: float = 6.0) -> bool:
    """True if any YNAB cash feed is missing or older than max_hours."""
    for name in ("x_money_latest.json", "one_card_latest.json", "rh_checking_latest.json"):
        age = _snapshot_age_hours(ROOT / "treasury" / "snapshots" / name)
        if age is None or age > max_hours:
            return True
    return False


def _maybe_refresh_ynab_for_coach(*, force: bool = False) -> bool:
    """Run ynab_sync when cash feeds are stale (or force). Returns True if sync ran."""
    global _last_ynab_refresh_ts
    import time

    now = time.time()
    if not force and now - _last_ynab_refresh_ts < _YNAB_REFRESH_COOLDOWN_S:
        return False
    if not force and not _ynab_cash_stale(6.0):
        return False
    try:
        from treasury.ynab_sync import sync_and_write_report, ynab_feeds_clean

        ynab_report = sync_and_write_report()
        _last_ynab_refresh_ts = time.time()
        if ynab_feeds_clean(ynab_report):
            sys.stderr.write("[fcc] coach: ynab_sync refreshed cash feeds\n")
        else:
            sys.stderr.write(f"[fcc] coach: ynab_sync not clean {ynab_report}\n")
        return True
    except Exception as e:
        sys.stderr.write(f"[fcc] coach ynab_sync warning: {e}\n")
        return False


def _solana_snapshot() -> dict:
    """Read-only Solana book from the pushed snapshot (no RPC on prod serve)."""
    if not SOLANA_SNAPSHOT.is_file():
        return {
            "ok": False,
            "source": "missing",
            "error": "no solana_latest.json — run: python3 -m treasury.solana_sync",
        }
    try:
        data = json.loads(SOLANA_SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "source": "error", "error": str(e)}
    if not isinstance(data, dict):
        return {"ok": False, "source": "invalid", "error": "solana_latest.json is not an object"}
    return data


def _attach_solana(data: dict) -> dict:
    """Prod composites may lack snapshot.solana; the sleeve file is pushed separately."""
    snap = data.get("snapshot")
    if not isinstance(snap, dict):
        snap = {}
        data["snapshot"] = snap
    existing = snap.get("solana")
    if isinstance(existing, dict) and (
        existing.get("wallet") or existing.get("book_usd") is not None
    ):
        return data
    sol = _solana_snapshot()
    if sol.get("wallet") or sol.get("book_usd") is not None:
        snap["solana"] = sol
    return data


def _fund_manager_snapshot() -> dict:
    """Read-only fund manager snapshot (Mac-pushed; no live RH on prod serve)."""
    if not FM_SNAPSHOT.is_file():
        return {
            "ok": False,
            "error": "no fund_manager_latest.json — refresh RH snapshot on Mac",
        }
    try:
        data = json.loads(FM_SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "error": str(e)}
    if not isinstance(data, dict):
        return {"ok": False, "error": "fund_manager_latest.json is not an object"}
    return data


def _fm_has_analysis(fm: object) -> bool:
    if not isinstance(fm, dict):
        return False
    an = fm.get("analysis")
    if isinstance(an, dict) and (an.get("ok") or an.get("positions")):
        return True
    return bool(fm.get("ok") and (fm.get("positions") or fm.get("analysis")))


def _attach_fund_manager(data: dict) -> dict:
    """Prod composites often omit fund_manager; the sidecar file is pushed separately."""
    if _fm_has_analysis(data.get("fund_manager")):
        return data
    ev = data.get("evaluation")
    if isinstance(ev, dict) and _fm_has_analysis(ev.get("fund_manager")):
        data["fund_manager"] = ev.get("fund_manager")
        return data
    fm = _fund_manager_snapshot()
    if _fm_has_analysis(fm):
        data["fund_manager"] = fm
    return data


def _x_money_usable(xm: object) -> bool:
    if not isinstance(xm, dict):
        return False
    if xm.get("source") in (None, "empty"):
        return False
    return (
        xm.get("as_of") is not None
        or xm.get("cash") is not None
        or xm.get("available") is not None
    )


def _x_money_snapshot() -> dict:
    """Read-only X Money sidecar (Mac-pushed YNAB; Pi may omit it from the composite)."""
    if not XM_SNAPSHOT.is_file():
        return {"source": "empty", "error": "no x_money_latest.json"}
    try:
        data = json.loads(XM_SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"source": "error", "error": str(e)}
    if not isinstance(data, dict):
        return {"source": "invalid", "error": "x_money_latest.json is not an object"}
    return data


def _attach_x_money(data: dict) -> dict:
    """Prod composites often omit snapshot.x_money; the sidecar is pushed separately."""
    snap = data.get("snapshot")
    if not isinstance(snap, dict):
        snap = {}
        data["snapshot"] = snap
    if _x_money_usable(snap.get("x_money")):
        return data
    xm = _x_money_snapshot()
    if not _x_money_usable(xm):
        return data
    snap["x_money"] = xm
    ev = data.get("evaluation")
    if isinstance(ev, dict):
        dq = ev.get("data_quality")
        if isinstance(dq, dict):
            srcs = dq.get("sources")
            if not isinstance(srcs, dict):
                srcs = {}
                dq["sources"] = srcs
            if not srcs.get("x_money"):
                srcs["x_money"] = xm.get("source")
                srcs["x_money_as_of"] = xm.get("as_of")
        inp = ev.get("inputs")
        if isinstance(inp, dict) and inp.get("x_money_cash") is None:
            cash = xm.get("cash")
            if cash is None:
                cash = xm.get("available")
            if cash is not None:
                inp["x_money_cash"] = cash
            if xm.get("account_name"):
                inp.setdefault("x_money_account", xm.get("account_name"))
    return data


def _attach_morpho_position(data: dict) -> dict:
    """GET: live GraphQL rewrite + policy re-eval. Stale sidecar must not win."""
    try:
        from treasury.morpho_position_sync import overlay_morpho_position_onto_treasury

        return overlay_morpho_position_onto_treasury(data, config=load_config())
    except Exception:
        return data


def _enrich_treasury(data: dict) -> dict:
    return _attach_fund_manager(
        _attach_x_money(_attach_solana(_attach_morpho_position(data)))
    )


def _braiins_live() -> dict:
    """Public Braiins summary for FCC main dash + capital-flows (no secrets)."""
    if not BRAIINS_SNAPSHOT.is_file():
        return {
            "ok": False,
            "status": "missing",
            "error": (
                "no braiins_latest.json — run: python3 treasury/braiins_sync.py "
                "(token at ~/.config/braiins/token)"
            ),
        }
    try:
        bd = json.loads(BRAIINS_SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "status": "error", "error": str(e)}
    if not isinstance(bd, dict):
        return {"ok": False, "status": "error", "error": "invalid braiins snapshot"}
    if not bd.get("ok"):
        err = bd.get("error") or "sync failed"
        if err == "token_missing":
            err = (
                "token missing — ~/.config/braiins/token or BRAIINS_POOL_TOKEN, "
                "then python3 treasury/braiins_sync.py"
            )
        return {
            "ok": False,
            "status": "token_missing" if bd.get("error") == "token_missing" else "error",
            "error": err,
            "as_of": bd.get("as_of"),
        }
    workers = bd.get("workers") or []
    # Compact worker list for UI
    worker_rows = []
    for w in workers[:20]:
        if not isinstance(w, dict):
            continue
        worker_rows.append(
            {
                "name": w.get("name"),
                "state": w.get("state"),
                "hash_rate_24h": w.get("hash_rate_24h"),
                "hash_rate_5m": w.get("hash_rate_5m"),
                "hash_rate_unit": w.get("hash_rate_unit") or bd.get("hash_rate_unit"),
            }
        )
    outlook = bd.get("payout_outlook") if isinstance(bd.get("payout_outlook"), dict) else {}
    return {
        "ok": True,
        "status": "live",
        "username": bd.get("username"),
        "hash_rate_5m": bd.get("hash_rate_5m"),
        "hash_rate_60m": bd.get("hash_rate_60m"),
        "hash_rate_24h": bd.get("hash_rate_24h"),
        "hash_rate_unit": bd.get("hash_rate_unit") or "Gh/s",
        "ok_workers": bd.get("ok_workers"),
        "low_workers": bd.get("low_workers"),
        "off_workers": bd.get("off_workers"),
        "dis_workers": bd.get("dis_workers"),
        "today_reward_btc": bd.get("today_reward_btc"),
        "estimated_reward_btc": bd.get("estimated_reward_btc"),
        "current_balance_btc": bd.get("current_balance_btc"),
        "all_time_reward_btc": bd.get("all_time_reward_btc"),
        "last_payout_btc": bd.get("last_payout_btc"),
        "last_payout_at": bd.get("last_payout_at"),
        "worker_count": bd.get("worker_count"),
        "workers": worker_rows,
        "daily_reward_avg_btc": bd.get("daily_reward_avg_btc") or outlook.get("daily_reward_avg_btc"),
        "payout_outlook": outlook or None,
        "next_payout_est_at": bd.get("next_payout_est_at") or outlook.get("next_payout_est_at"),
        "next_payout_threshold_btc": bd.get("next_payout_threshold_btc")
        or outlook.get("threshold_btc"),
        "next_payout_progress_pct": bd.get("next_payout_progress_pct")
        or outlook.get("progress_pct"),
        "days_to_next_payout_est": bd.get("days_to_next_payout_est")
        or outlook.get("days_to_threshold_est"),
        "as_of": bd.get("as_of"),
        "source": "braiins_pool_api",
    }


def _capital_flows_payload() -> dict:
    """Load investment/capital_flows.json and lightly enrich from YNAB snapshots."""
    path = ROOT / "investment" / "capital_flows.json"
    if not path.is_file():
        return {"ok": False, "error": "investment/capital_flows.json missing"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"ok": False, "error": str(e)}
    if not isinstance(data, dict):
        return {"ok": False, "error": "capital_flows.json must be an object"}
    data = dict(data)
    data["ok"] = True
    live: dict = {}
    # X Money snapshot may include Lyft inflows
    xm = ROOT / "treasury" / "snapshots" / "x_money_latest.json"
    if xm.is_file():
        try:
            xmd = json.loads(xm.read_text(encoding="utf-8"))
            lyft = 0.0
            for t in xmd.get("transactions") or []:
                payee = str(t.get("payee") or "").lower()
                amt = t.get("amount_display")
                if amt is None:
                    amt = t.get("amount")
                try:
                    amt_f = float(amt or 0)
                except (TypeError, ValueError):
                    amt_f = 0.0
                if "lyft" in payee and amt_f > 0:
                    lyft += amt_f
            live["lyft_inflow_from_x_money_txs"] = round(lyft, 2)
            live["x_money_inflow_30d"] = xmd.get("inflow_30d")
            live["x_money_as_of"] = xmd.get("as_of")
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    exp = ROOT / "treasury" / "snapshots" / "expenses_latest.json"
    if exp.is_file():
        try:
            ed = json.loads(exp.read_text(encoding="utf-8"))
            sm = ed.get("summary") or {}
            live["coinbase_funded_monthly_est"] = sm.get("coinbase_funded_monthly")
            live["rh_checking_funded_monthly_est"] = sm.get(
                "rh_checking_funded_monthly"
            ) or sm.get("rh_funded_monthly")
            live["fleet_monthly_est"] = sm.get("fleet_monthly")
            live["collateral_monthly_est"] = sm.get(
                "collateral_investments_monthly"
            ) or sm.get("collateral_monthly")
            live["personal_monthly_est"] = sm.get("personal_monthly")
            live["upcoming_expense_monthly_est"] = sm.get(
                "upcoming_expense_monthly"
            ) or sm.get("combined_monthly")
            # Burn pay-from: summary.by_source (Essential + funded unique Fleet)
            tabs = ed.get("tabs") or {}
            ess_tab = tabs.get("Essential") or tabs.get("Personal") or {}
            by_src = sm.get("by_source_monthly") or (
                ess_tab.get("by_source_monthly") or {}
            )
            if sm.get("x_money_funded_monthly") is not None:
                live["x_money_funded_monthly_est"] = sm.get("x_money_funded_monthly")
            elif by_src.get("X Money") is not None:
                live["x_money_funded_monthly_est"] = by_src.get("X Money")
            if by_src.get("Coinbase") is not None:
                live["coinbase_funded_monthly_est"] = by_src.get(
                    "Coinbase", live.get("coinbase_funded_monthly_est")
                )
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    # Braiins Pool mining snapshot (treasury/braiins_sync.py)
    br = _braiins_live()
    live["braiins"] = br
    integ = data.get("integrations")
    if isinstance(integ, dict) and isinstance(integ.get("braiins_pool"), dict):
        bp = dict(integ["braiins_pool"])
        bp["status"] = br.get("status") or ("live" if br.get("ok") else "error")
        if br.get("as_of"):
            bp["last_sync"] = br.get("as_of")
        data["integrations"] = {**integ, "braiins_pool": bp}
    data["live"] = live
    # Prefer simpler key for SVG caption (express-pay Lyft on X Money)
    if live.get("lyft_inflow_from_x_money_txs") is not None:
        data["live"]["lyft_inflow_30d"] = live["lyft_inflow_from_x_money_txs"]
    return data


def _root_fcc_js_remap(path: str) -> str | None:
    """Map GET /foo.js → /financial-command/foo.js when that sibling exists.

    Live FCC is served with directory=ROOT. GET / remaps to
    /financial-command/index.html, so relative <script src="nav-fleet.js">
    hits /nav-fleet.js (404) unless we remap — same hole as /favicon.ico.
    Only a basename under financial-command/ is eligible (no path traversal).
    """
    name = path.lstrip("/")
    if not name or "/" in name or not name.endswith(".js"):
        return None
    if not (ROOT / "financial-command" / name).is_file():
        return None
    return f"/financial-command/{name}"


class FCCHandler(SimpleHTTPRequestHandler):
    # PWA manifest MIME (stdlib map often serves .webmanifest as octet-stream)
    extensions_map = {
        **getattr(SimpleHTTPRequestHandler, "extensions_map", {}),
        ".webmanifest": "application/manifest+json",
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".png": "image/png",
        ".html": "text/html; charset=utf-8",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[fcc] " + (fmt % args) + "\n")

    def end_headers(self) -> None:
        # Phone browsers 304-cache index.html hard; always revalidate static UI.
        self.send_header("Cache-Control", "no-store, max-age=0")
        path = urlparse(getattr(self, "path", "") or "").path
        if path.endswith("/sw.js") or path == "/sw.js":
            self.send_header("Service-Worker-Allowed", "/")
        super().end_headers()

    def _json(self, code: int, payload: dict) -> None:
        if not isinstance(payload, dict):
            payload = {"ok": False, "error": "invalid payload type"}
        try:
            body = json.dumps(payload, default=str).encode("utf-8")
        except (TypeError, ValueError) as e:
            body = json.dumps(
                {"ok": False, "error": f"json encode failed: {e}"}
            ).encode("utf-8")
            code = 500
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/treasury":
            p = ROOT / "financial-command" / "treasury_latest.json"
            if not p.is_file():
                # Fallback to treasury/snapshots copy
                p = ROOT / "treasury" / "snapshots" / "treasury_latest.json"
            if not p.is_file():
                self._json(
                    404,
                    {
                        "ok": False,
                        "error": "no treasury_latest.json — POST /api/refresh",
                    },
                )
                return
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            if not isinstance(data, dict):
                self._json(500, {"ok": False, "error": "treasury_latest.json is not an object"})
                return
            data = dict(data)
            data.setdefault("ok", True)
            try:
                from treasury.solana_sync import overlay_solana_snapshot

                overlay_solana_snapshot(data)
            except Exception:
                pass
            try:
                data["braiins"] = _braiins_live()
            except Exception as be:
                data["braiins"] = {
                    "ok": False,
                    "status": "error",
                    "error": f"braiins attach failed: {be}",
                }
            data = _enrich_treasury(data)
            self._json(200, data)
            return
        if path == "/api/braiins":
            try:
                self._json(200, _braiins_live())
            except Exception as e:
                self._json(500, {"ok": False, "status": "error", "error": str(e)})
            return
        if path in ("/api/health", "/api/fcc-identity"):
            # Orchestrator / launchers use this to refuse wrong monorepo FCC instances
            self._json(
                200,
                {
                    "ok": True,
                    "service": "financial-command",
                    "canonical": True,
                    "workspace_root": str(ROOT),
                    "config_path": str(ROOT / "treasury" / "config.json"),
                    "branch_expected": "work/treasury",
                    "features": [
                        "braiins",
                        "x_money",
                        "coach",
                        "watchlist",
                        "capital_flows",
                        "interest_spectrum",
                        "bias_spectrum",
                        "planned_actual",
                    ],
                },
            )
            return
        if path == "/api/config":
            self._json(200, {"ok": True, "config": load_config()})
            return
        if path == "/api/watchlist":
            try:
                self._json(200, build_watchlist_dashboard())
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path == "/api/watchlist/deep-dive":
            qs = parse_qs(parsed.query or "")
            sym = (qs.get("symbol") or [""])[0]
            try:
                payload = get_deep_dive_markdown(sym)
                code = 200 if payload.get("ok") else 404
                self._json(code, payload)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path == "/api/capital-flows":
            try:
                self._json(200, _capital_flows_payload())
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path == "/api/interest-spectrum":
            try:
                self._json(200, build_interest_spectrum())
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path == "/api/bias-spectrum":
            try:
                self._json(200, build_bias_spectrum())
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path == "/api/planned-actual":
            try:
                self._json(200, load_planned_actual())
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e), "display_only": True})
            return
        if path == "/api/coach":
            try:
                qs = parse_qs(parsed.query or "")
                force = (qs.get("refresh") or ["0"])[0] in ("1", "true", "yes")
                # Auto-refresh YNAB cash feeds when stale (soft UI poll never hits YNAB)
                refreshed = _maybe_refresh_ynab_for_coach(force=force)
                tre_fcc = ROOT / "financial-command" / "treasury_latest.json"
                if refreshed:
                    try:
                        run_treasury_main(["--offline"])
                    except Exception as te:
                        sys.stderr.write(f"[fcc] coach treasury recompute: {te}\n")
                snaps = load_coach_snapshots(ROOT / "treasury" / "snapshots")
                if not snaps.get("expenses"):
                    self._json(
                        200,
                        {
                            "ok": False,
                            "error": "expenses_latest.json missing — run expenses_sync or FCC Refresh",
                            "obligations": [],
                            "summary": {},
                            "data_requests": [
                                {
                                    "field": "expenses_snapshot",
                                    "why": "No expense sheet snapshot.",
                                    "how": "python3 treasury/expenses_sync.py",
                                }
                            ],
                        },
                    )
                    return
                if tre_fcc.is_file():
                    try:
                        snaps["treasury"] = json.loads(
                            tre_fcc.read_text(encoding="utf-8")
                        )
                    except (OSError, json.JSONDecodeError):
                        pass
                plan = build_coach_plan(snaps)
                if not isinstance(plan, dict):
                    plan = {"ok": False, "error": "coach builder returned non-object"}
                plan.setdefault("ok", True)
                if refreshed:
                    plan["ynab_refreshed"] = True
                # never invent cash: residuals must be non-negative
                for v in (plan.get("residuals") or {}).values():
                    if isinstance(v, (int, float)) and v < -0.01:
                        plan["ok"] = False
                        plan["error"] = "invalid negative residual cash"
                        break
                self._json(200 if plan.get("ok") is not False else 500, plan)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e), "obligations": []})
            return
        if path in ("/api/ask/status", "/api/advisor/status"):
            try:
                self._json(200, advisor_auth_status())
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path in ("/api/ask/login", "/api/advisor/login"):
            try:
                self._json(200, grok_login_status())
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e), "phase": "fail"})
            return
        if path in ("/", "/financial-command", "/financial-command/"):
            self.path = "/financial-command/index.html"
        elif path in ("/financial-command/watchlist", "/financial-command/watchlist/"):
            self.path = "/financial-command/watchlist.html"
        elif path in (
            "/financial-command/capital-flows",
            "/financial-command/capital-flows/",
        ):
            self.path = "/financial-command/capital-flows.html"
        elif path in (
            "/financial-command/interest-spectrum",
            "/financial-command/interest-spectrum/",
        ):
            self.path = "/financial-command/interest-spectrum.html"
        elif path in (
            "/financial-command/bias-spectrum",
            "/financial-command/bias-spectrum/",
        ):
            self.path = "/financial-command/bias-spectrum.html"
        elif path in ("/favicon.ico", "/financial-command/favicon.ico"):
            # iOS Safari fetches /favicon.ico at the origin root, not the page dir.
            self.path = "/financial-command/favicon.ico"
        elif path in ("/favicon.svg", "/financial-command/favicon.svg"):
            self.path = "/financial-command/favicon.svg"
        elif path in ("/favicon-32.png", "/financial-command/favicon-32.png"):
            self.path = "/financial-command/favicon-32.png"
        elif path in (
            "/apple-touch-icon.png",
            "/apple-touch-icon-precomposed.png",
            "/apple-touch-icon-120x120.png",
            "/apple-touch-icon-120x120-precomposed.png",
            "/apple-touch-icon-180x180.png",
            "/apple-touch-icon-180x180-precomposed.png",
        ):
            self.path = "/financial-command/apple-touch-icon.png"
        elif path in (
            "/manifest.webmanifest",
            "/financial-command/manifest.webmanifest",
        ):
            # Chromium probes /manifest.webmanifest at the origin root.
            self.path = "/financial-command/manifest.webmanifest"
        elif path in ("/sw.js", "/financial-command/sw.js"):
            self.path = "/financial-command/sw.js"
        elif path in ("/icon-192.png", "/financial-command/icon-192.png"):
            self.path = "/financial-command/icon-192.png"
        elif path in ("/icon-512.png", "/financial-command/icon-512.png"):
            self.path = "/financial-command/icon-512.png"
        else:
            remapped = _root_fcc_js_remap(path)
            if remapped:
                self.path = remapped
        return super().do_GET()

    def _load_treasury_payload(self) -> dict:
        p = ROOT / "financial-command" / "treasury_latest.json"
        if not p.is_file():
            p = ROOT / "treasury" / "snapshots" / "treasury_latest.json"
        if not p.is_file():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        data = dict(data)
        try:
            data["braiins"] = _braiins_live()
        except Exception as be:
            data["braiins"] = {
                "ok": False,
                "status": "error",
                "error": f"braiins attach failed: {be}",
            }
        return _enrich_treasury(data)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/api/ask/login", "/api/advisor/login"):
            try:
                result = start_grok_login()
                self._json(200, result)
            except Exception as e:
                self._json(500, {"ok": False, "phase": "fail", "error": str(e)})
            return
        if path in ("/api/ask", "/api/advisor"):
            body = self._read_json()
            question = (body.get("question") or body.get("q") or "").strip()
            try:
                treasury = self._load_treasury_payload()
                if not treasury:
                    self._json(
                        404,
                        {
                            "ok": False,
                            "error": "no treasury snapshot — run Refresh first",
                        },
                    )
                    return
                coach = None
                try:
                    snaps = load_coach_snapshots(ROOT / "treasury" / "snapshots")
                    coach = build_coach_plan(treasury, snapshots=snaps)
                except Exception:
                    coach = None
                result = ask_financial_advisor(question, treasury, coach=coach)
                self._json(200, result)
            except AdvisorError as e:
                code = e.status if e.status and 400 <= e.status < 600 else 500
                if e.status == 0:
                    code = 502
                self._json(
                    code,
                    {
                        "ok": False,
                        "error": str(e),
                        "body": (e.body or "")[:400],
                    },
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        if path == "/api/config":
            body = self._read_json()
            try:
                # Mirror security deposit into policy when saved from UI
                man = body.get("coinbase_manual") or {}
                if man.get("one_card_security_deposit_usdc") is not None:
                    body.setdefault("policy", {})
                    body["policy"]["one_card_security_deposit_usdc"] = man[
                        "one_card_security_deposit_usdc"
                    ]
                save_config(body)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            # Re-evaluate offline (use latest snapshots + new manual)
            try:
                run_treasury_main(["--offline"])
            except SystemExit as e:
                if e.code not in (0, None):
                    self._json(500, {"ok": False, "error": f"treasury exit {e.code}"})
                    return
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, {"ok": True, "config": load_config()})
            return

        if path == "/api/refresh":
            body = self._read_json()
            # Pi phone FCC: no YNAB/CB creds — live Refresh would wipe Mac-pushed files.
            # UI still posts offline:false; consumer mode forces snapshot re-read only.
            # force_live=true opts into producer path when credentials exist.
            offline = bool(body.get("offline"))
            if _is_snapshot_consumer() and not body.get("force_live"):
                offline = True
            # Interactive UI Refresh defaults to Pi-only RH (fast). Local Grok+MCP
            # can take 2–4 min and made the button look dead while YNAB/CB already
            # finished. Opt in: body.rh_mcp=true or env FCC_REFRESH_RH_MCP=1.
            rh_mcp = bool(body.get("rh_mcp")) or os.environ.get(
                "FCC_REFRESH_RH_MCP", ""
            ).strip() in ("1", "true", "yes")
            # Prefer live YNAB + Solana + Braiins unless offline.
            # Coinbase CLI is Mac-only; skip live CB on Pi instead of failing
            # the whole producer path (that used to drop snapshot.solana).
            args = ["--offline"] if offline else []
            skip_cb = (not offline) and (not _coinbase_cli_available())
            if skip_cb:
                args.append("--skip-coinbase")
            report: dict = {
                "mode": "offline_consumer" if offline else "live_producer",
                "ynab": "skipped" if offline else "pending",
                "expenses": "skipped" if offline else "pending",
                "coinbase_treasury": "skipped" if skip_cb else "pending",
                "braiins": "skipped" if offline else "pending",
                "robinhood": "skipped" if offline else "pending",
                "solana": "skipped" if offline else "pending",
            }
            code: int | None = 0
            try:
                if not offline:
                    try:
                        from treasury.ynab_sync import sync_and_write_report

                        # Per-feed {as_of, token_source, live_error|preserved} — never bare "ok"
                        # on soft-preserve (X Money pick miss used to hide the Mac snapshot).
                        report["ynab"] = sync_and_write_report()
                    except Exception as ye:
                        report["ynab"] = {
                            "one_card": {"as_of": None, "token_source": None, "live_error": str(ye)},
                            "rh_checking": {"as_of": None, "token_source": None, "live_error": str(ye)},
                            "x_money": {"as_of": None, "token_source": None, "live_error": str(ye)},
                        }
                        sys.stderr.write(f"[fcc] ynab_sync warning: {ye}\n")
                    try:
                        from treasury.expenses_sync import main as exp_main

                        exp_main([])
                        report["expenses"] = "ok"
                    except SystemExit as se:
                        report["expenses"] = (
                            "ok" if se.code in (0, None) else f"exit {se.code}"
                        )
                    except Exception as ee:
                        report["expenses"] = f"error: {ee}"
                        sys.stderr.write(f"[fcc] expenses_sync warning: {ee}\n")
                    try:
                        from treasury.braiins_sync import main as braiins_main

                        # braiins_sync sleeps ~5s between API calls
                        bc = braiins_main([])
                        report["braiins"] = "ok" if bc in (0, None) else f"exit {bc}"
                    except SystemExit as se:
                        report["braiins"] = (
                            "ok" if se.code in (0, None) else f"exit {se.code}"
                        )
                    except Exception as be:
                        report["braiins"] = f"error: {be}"
                        sys.stderr.write(f"[fcc] braiins_sync warning: {be}\n")
                # Coinbase + assemble treasury BEFORE RH — RH MCP can take 1–2+ min
                # and used to block CB/Sheet ages from updating if Refresh was aborted.
                code = run_treasury_main(args)
                if not skip_cb:
                    report["coinbase_treasury"] = (
                        "ok" if code in (0, None) else f"exit {code}"
                    )
                elif code in (0, None):
                    report["coinbase_treasury"] = "skipped"
                # Surface CB / Solana ages. Overlay sidecar if assemble omitted SOL.
                try:
                    from treasury.solana_sync import overlay_solana_snapshot

                    p_cb = ROOT / "treasury" / "snapshots" / "coinbase_latest.json"
                    cb_snap = (
                        json.loads(p_cb.read_text(encoding="utf-8"))
                        if p_cb.is_file()
                        else {}
                    )
                    p_t = ROOT / "financial-command" / "treasury_latest.json"
                    t_data = (
                        json.loads(p_t.read_text(encoding="utf-8"))
                        if p_t.is_file()
                        else {}
                    )
                    if isinstance(t_data, dict):
                        overlay_solana_snapshot(t_data)
                        if p_t.is_file():
                            p_t.write_text(
                                json.dumps(t_data, indent=2) + "\n", encoding="utf-8"
                            )
                        snap = t_data.get("snapshot") or {}
                        cb_block = snap.get("coinbase") or {}
                        sol_block = snap.get("solana") or {}
                    else:
                        cb_block, sol_block = {}, {}
                    if cb_block.get("live_error"):
                        report["coinbase_live_error"] = cb_block["live_error"]
                    if cb_block.get("as_of"):
                        report["coinbase_as_of"] = cb_block["as_of"]
                    if cb_block.get("source"):
                        report["coinbase_source"] = cb_block["source"]
                    elif cb_snap.get("as_of"):
                        report["coinbase_as_of"] = cb_snap.get("as_of")
                        report["coinbase_source"] = cb_snap.get("source")
                    if sol_block.get("live_error"):
                        report["solana_live_error"] = sol_block["live_error"]
                        report["solana"] = f"error: {sol_block['live_error']}"
                    elif sol_block.get("as_of"):
                        report["solana"] = "ok"
                        report["solana_as_of"] = sol_block.get("as_of")
                        report["solana_source"] = sol_block.get("source")
                    elif not offline:
                        report["solana"] = "missing"
                except Exception:
                    pass
                if not offline:
                    # RH: Pi snapshot first. Local MCP only when explicitly requested
                    # (launchd rh_refresh owns the slow path by default).
                    try:
                        from treasury.rh_snapshot_sync import sync_rh_snapshot

                        rh = sync_rh_snapshot(
                            prefer_pi=True,
                            allow_local_mcp=rh_mcp,
                            reevaluate=False,
                        )
                        if rh.get("ok"):
                            report["robinhood"] = f"ok:{rh.get('source')}"
                            report["robinhood_as_of"] = rh.get("as_of")
                            # Re-merge RH into dashboard JSON without live CB again
                            try:
                                run_treasury_main(["--offline"])
                            except SystemExit:
                                pass
                        else:
                            report["robinhood"] = (
                                f"error:{rh.get('error') or 'failed'}"
                            )
                            report["robinhood_detail"] = {
                                "pi": (rh.get("pi") or {}).get("error"),
                                "local_mcp": (rh.get("local_mcp") or {}).get("error"),
                                "rh_mcp_enabled": rh_mcp,
                            }
                            sys.stderr.write(
                                f"[fcc] rh_snapshot_sync failed: {report['robinhood']}\n"
                            )
                    except Exception as rexc:
                        report["robinhood"] = f"error: {rexc}"
                        sys.stderr.write(f"[fcc] rh_snapshot_sync warning: {rexc}\n")
                    # Always push venue snapshots to Pi so iPad offline FCC ages move
                    try:
                        from treasury.rh_snapshot_sync import push_snapshots_to_pi

                        report["push_pi"] = push_snapshots_to_pi()
                    except Exception as pe:
                        report["push_pi"] = {"ok": False, "error": str(pe)}
                        sys.stderr.write(f"[fcc] push_pi warning: {pe}\n")
                else:
                    report["note"] = (
                        "offline_consumer: re-read snapshots only "
                        "(live feeds produced on Mac and pushed to this host)"
                    )
            except SystemExit as e:
                code = e.code if e.code is not None else 0
                if not skip_cb:
                    report["coinbase_treasury"] = (
                        "ok" if code in (0, None) else f"exit {code}"
                    )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e), "refreshed": report})
                return
            if code not in (0, None):
                self._json(
                    500,
                    {
                        "ok": False,
                        "error": f"treasury exit {code}",
                        "refreshed": report,
                    },
                )
                return
            p = ROOT / "financial-command" / "treasury_latest.json"
            data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
            if isinstance(data, dict):
                try:
                    from treasury.solana_sync import overlay_solana_snapshot

                    overlay_solana_snapshot(data)
                except Exception:
                    pass
                try:
                    data["braiins"] = _braiins_live()
                except Exception as be:
                    data["braiins"] = {
                        "ok": False,
                        "status": "error",
                        "error": f"braiins attach failed: {be}",
                    }
                data = _enrich_treasury(data)
            self._json(200, {"ok": True, "treasury": data, "refreshed": report})
            return

        self._json(404, {"ok": False, "error": "unknown endpoint"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Financial Command Center server")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (use 0.0.0.0 for LAN/Tailscale on Pi)",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Initial boot refresh offline only (does not force all POSTs offline)",
    )
    parser.add_argument(
        "--consumer",
        action="store_true",
        help="Snapshot consumer (Pi/phone): Refresh re-reads files only, never live YNAB/CB",
    )
    args = parser.parse_args(argv)

    global _SERVER_STARTED_OFFLINE, _SERVER_CONSUMER
    _SERVER_STARTED_OFFLINE = bool(args.offline)
    _SERVER_CONSUMER = bool(args.consumer)

    print(f"[fcc] workspace_root={ROOT}", file=sys.stderr)
    print(f"[fcc] config={ROOT / 'treasury' / 'config.json'}", file=sys.stderr)
    consumer = _is_snapshot_consumer()
    print(
        f"[fcc] mode={'offline_consumer' if consumer else 'live_producer'}"
        f" (boot_offline={_SERVER_STARTED_OFFLINE}, flag_consumer={_SERVER_CONSUMER})",
        file=sys.stderr,
    )

    # Initial YNAB + treasury refresh
    try:
        if not args.offline:
            try:
                from treasury.ynab_sync import sync_and_write_report

                ynab_boot = sync_and_write_report()
                print(f"ynab_sync report: {ynab_boot}", file=sys.stderr)
            except Exception as ye:
                print(f"ynab_sync warning: {ye}", file=sys.stderr)
            try:
                from treasury.expenses_sync import main as exp_main

                exp_main([])
            except SystemExit:
                pass
            except Exception as ee:
                print(f"expenses_sync warning: {ee}", file=sys.stderr)
        run_treasury_main(["--offline"] if args.offline else [])
    except SystemExit:
        pass
    except Exception as e:
        print(f"initial treasury refresh warning: {e}", file=sys.stderr)

    bind_host = (args.host or "127.0.0.1").strip() or "127.0.0.1"
    url = f"http://127.0.0.1:{args.port}/financial-command/index.html"
    wl = f"http://127.0.0.1:{args.port}/financial-command/watchlist.html"
    print(f"Financial Command Center → {url}")
    print(f"Watchlist research        → {wl}")
    print(
        f"Capital Flows             → http://127.0.0.1:{args.port}/financial-command/capital-flows.html"
    )
    print(
        f"Interest Spectrum         → http://127.0.0.1:{args.port}/financial-command/interest-spectrum.html"
    )
    print(
        f"Bias Spectrum             → http://127.0.0.1:{args.port}/financial-command/bias-spectrum.html"
    )
    print(f"[fcc] bind {bind_host}:{args.port}", file=sys.stderr)
    httpd = ThreadingHTTPServer((bind_host, args.port), FCCHandler)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
