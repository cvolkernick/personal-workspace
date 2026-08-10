#!/usr/bin/env python3
"""Local server for Financial Command Center.

Serves static UI + APIs:
  GET  /api/treasury   — latest evaluation JSON
  GET  /api/config     — treasury/config.json
  GET  /api/watchlist  — watchlist + deep-dive summaries
  GET  /api/watchlist/deep-dive?symbol=BE — full deep-dive markdown
  GET  /api/capital-flows — income → channel flow model (+ optional live enrich)
  GET  /api/braiins       — Braiins Pool mining snapshot summary
  GET  /api/coach         — financial coach allocation plan (pay on time)
  GET  /api/ask/status    — Ask Grok financial advisor auth + model
  POST /api/ask           — {question} ask Grok about FCC/treasury domain
  GET  /api/open-orchestra — probe Orchestrator (port 8790)
  POST /api/open-orchestra — ensure Orchestrator is running (start if needed)
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
import socket
import subprocess
import sys
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ensure_tool_path() -> None:
    """launchd/ensure often starts us with PATH=/usr/bin:/bin — restore homebrew CLIs.

    Without this, `coinbase` / scp extras are invisible and Refresh silently keeps
    stale CB ages while reporting coinbase_treasury=ok (file fallback).
    """
    extras = [
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

BRAIINS_SNAPSHOT = ROOT / "treasury" / "snapshots" / "braiins_latest.json"
ORCHESTRA_PORT = 8790
ORCHESTRA_URL = f"http://127.0.0.1:{ORCHESTRA_PORT}/"
_ORCHESTRA_PID: int | None = None
_YNAB_REFRESH_COOLDOWN_S = 300.0  # don't hammer YNAB more than once / 5 min
_last_ynab_refresh_ts = 0.0
# Set in main() from --offline / --consumer. Used for initial boot + consumer detect.
_SERVER_STARTED_OFFLINE = False
_SERVER_CONSUMER = False


def _is_snapshot_consumer() -> bool:
    """True when this host should not run live YNAB/CB (Pi phone FCC).

    --offline alone is only a *boot* hint (Mac ensure uses it for fast start).
    Consumer mode = explicit --consumer / FCC_OFFLINE_CONSUMER, or no live creds.
    """
    if _SERVER_CONSUMER:
        return True
    if os.environ.get("FCC_OFFLINE_CONSUMER", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return True
    try:
        from treasury.ynab_sync import load_ynab_token
        from treasury.adapters import _resolve_coinbase_bin

        tok, _ = load_ynab_token()
        if not tok and not _resolve_coinbase_bin():
            return True
    except Exception:
        pass
    return False


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
        from treasury.ynab_sync import main as ynab_main

        ynab_main([])
        _last_ynab_refresh_ts = time.time()
        sys.stderr.write("[fcc] coach: ynab_sync refreshed cash feeds\n")
        return True
    except SystemExit:
        _last_ynab_refresh_ts = time.time()
        return True
    except Exception as e:
        sys.stderr.write(f"[fcc] coach ynab_sync warning: {e}\n")
        return False


def _probe_port(port: int, host: str = "127.0.0.1", timeout: float = 0.35) -> bool:
    """True if something accepts TCP on host:port (same idea as orchestra launcher)."""
    if not port:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _orchestra_script() -> Path | None:
    """Prefer workspace orchestra/server.py under ROOT."""
    p = (ROOT / "orchestra" / "server.py").resolve()
    if p.is_file():
        return p
    return None


def ensure_orchestra(*, ready_timeout: float = 20.0) -> dict:
    """Probe Orchestrator on :8790; start orchestra/server.py if down.

    Mirrors orchestra/launcher.ensure_domain for the command-center itself so
    child dashboards (FCC, capital-flows, …) can return home reliably.
    """
    global _ORCHESTRA_PID
    ready_timeout = max(3.0, min(float(ready_timeout), 30.0))
    url = ORCHESTRA_URL
    port = ORCHESTRA_PORT

    if _probe_port(port):
        return {
            "ok": True,
            "id": "orchestra",
            "label": "Orchestrator",
            "live": True,
            "started": False,
            "already_running": True,
            "url": url,
            "port": port,
            "pid": _ORCHESTRA_PID,
            "message": f"Orchestrator already listening on {port}",
        }

    script = _orchestra_script()
    if not script:
        return {
            "ok": False,
            "id": "orchestra",
            "label": "Orchestrator",
            "live": False,
            "url": url,
            "port": port,
            "error": f"orchestra/server.py not found under {ROOT}",
        }

    log_dir = ROOT / "orchestra" / ".launch-logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    log_path = log_dir / "orchestra-from-fcc.log"
    try:
        log_f = open(log_path, "a", encoding="utf-8")
        log_f.write(
            f"\n--- launch {time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"port={port} script={script} ---\n"
        )
        log_f.flush()
    except OSError as e:
        return {
            "ok": False,
            "id": "orchestra",
            "error": f"Cannot open launch log: {e}",
            "url": url,
            "port": port,
        }

    cmd = [
        sys.executable,
        str(script),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-browser",
    ]
    log_f.write(f"cmd: {' '.join(cmd)}\n")
    log_f.flush()
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=log_f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    except OSError as e:
        try:
            log_f.close()
        except OSError:
            pass
        return {
            "ok": False,
            "id": "orchestra",
            "live": False,
            "url": url,
            "port": port,
            "error": f"Failed to spawn Orchestrator: {e}",
            "log": str(log_path),
        }

    _ORCHESTRA_PID = proc.pid
    deadline = time.time() + ready_timeout
    while time.time() < deadline:
        if _probe_port(port):
            try:
                log_f.close()
            except OSError:
                pass
            return {
                "ok": True,
                "id": "orchestra",
                "label": "Orchestrator",
                "live": True,
                "started": True,
                "already_running": False,
                "url": url,
                "port": port,
                "pid": proc.pid,
                "log": str(log_path),
                "message": f"Started Orchestrator on port {port}",
            }
        if proc.poll() is not None:
            try:
                log_f.close()
            except OSError:
                pass
            detail = ""
            try:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-600:]
                detail = " " + tail.strip().splitlines()[-1] if tail.strip() else ""
            except OSError:
                pass
            return {
                "ok": False,
                "id": "orchestra",
                "live": False,
                "url": url,
                "port": port,
                "pid": proc.pid,
                "error": (
                    f"Orchestrator exited early (code {proc.returncode})."
                    f"{detail}"
                ),
                "log": str(log_path),
            }
        time.sleep(0.2)

    try:
        log_f.close()
    except OSError:
        pass
    live = _probe_port(port)
    return {
        "ok": live,
        "id": "orchestra",
        "label": "Orchestrator",
        "live": live,
        "started": True,
        "already_running": False,
        "url": url,
        "port": port,
        "pid": proc.pid,
        "log": str(log_path),
        "error": None
        if live
        else f"Timed out waiting for Orchestrator on port {port}",
        "message": f"Spawned pid {proc.pid}; live={live}",
    }


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
            # Burn pay-from: summary.by_source (Personal+Fleet); fall back to Personal tab
            by_src = sm.get("by_source_monthly") or (
                ((ed.get("tabs") or {}).get("Personal") or {}).get("by_source_monthly")
                or {}
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


class FCCHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[fcc] " + (fmt % args) + "\n")

    def end_headers(self) -> None:
        # Phone browsers 304-cache index.html hard; always revalidate static UI.
        self.send_header("Cache-Control", "no-store, max-age=0")
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
                data["braiins"] = _braiins_live()
            except Exception as be:
                data["braiins"] = {
                    "ok": False,
                    "status": "error",
                    "error": f"braiins attach failed: {be}",
                }
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
        if path in ("/api/open-orchestra", "/api/orchestra-status"):
            live = _probe_port(ORCHESTRA_PORT)
            self._json(
                200,
                {
                    "ok": True,
                    "id": "orchestra",
                    "label": "Orchestrator",
                    "live": live,
                    "url": ORCHESTRA_URL,
                    "port": ORCHESTRA_PORT,
                    "already_running": live,
                    "message": (
                        f"Orchestrator listening on {ORCHESTRA_PORT}"
                        if live
                        else f"Orchestrator not running on {ORCHESTRA_PORT}"
                    ),
                },
            )
            return
        if path in ("/api/ask/status", "/api/advisor/status"):
            try:
                self._json(200, advisor_auth_status())
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
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
        return data

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
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
            # Prefer live YNAB + Coinbase + Braiins unless offline.
            args = ["--offline"] if offline else []
            report: dict = {
                "mode": "offline_consumer" if offline else "live_producer",
                "ynab": "skipped" if offline else "pending",
                "expenses": "skipped" if offline else "pending",
                "coinbase_treasury": "pending",
                "braiins": "skipped" if offline else "pending",
                "robinhood": "skipped" if offline else "pending",
            }
            code: int | None = 0
            try:
                if not offline:
                    try:
                        from treasury.ynab_sync import main as ynab_main

                        ynab_main([])
                        report["ynab"] = "ok"
                    except SystemExit as se:
                        report["ynab"] = "ok" if se.code in (0, None) else f"exit {se.code}"
                    except Exception as ye:
                        report["ynab"] = f"error: {ye}"
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
                report["coinbase_treasury"] = (
                    "ok" if code in (0, None) else f"exit {code}"
                )
                # Surface CB live_error when CLI missing / failed (ages stay stale)
                try:
                    p_cb = ROOT / "treasury" / "snapshots" / "coinbase_latest.json"
                    if p_cb.is_file():
                        cb_snap = json.loads(p_cb.read_text(encoding="utf-8"))
                    else:
                        cb_snap = {}
                    # Also read just-written treasury snapshot coinbase block
                    p_t = ROOT / "financial-command" / "treasury_latest.json"
                    if p_t.is_file():
                        t_data = json.loads(p_t.read_text(encoding="utf-8"))
                        cb_block = (t_data.get("snapshot") or {}).get("coinbase") or {}
                        if cb_block.get("live_error"):
                            report["coinbase_live_error"] = cb_block["live_error"]
                        if cb_block.get("as_of"):
                            report["coinbase_as_of"] = cb_block["as_of"]
                        if cb_block.get("source"):
                            report["coinbase_source"] = cb_block["source"]
                    elif cb_snap.get("as_of"):
                        report["coinbase_as_of"] = cb_snap.get("as_of")
                        report["coinbase_source"] = cb_snap.get("source")
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
                    data["braiins"] = _braiins_live()
                except Exception as be:
                    data["braiins"] = {
                        "ok": False,
                        "status": "error",
                        "error": f"braiins attach failed: {be}",
                    }
            self._json(200, {"ok": True, "treasury": data, "refreshed": report})
            return

        if path in ("/api/open-orchestra", "/api/launch-orchestra"):
            body = self._read_json()
            try:
                timeout = float(body.get("ready_timeout") or 20.0)
            except (TypeError, ValueError):
                timeout = 20.0
            try:
                result = ensure_orchestra(ready_timeout=timeout)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            code = 200 if result.get("ok") else 400
            self._json(code, result)
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
                from treasury.ynab_sync import main as ynab_main

                ynab_main([])
            except SystemExit:
                pass
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
