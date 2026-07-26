#!/usr/bin/env python3
"""Local server for Financial Command Center.

Serves static UI + APIs:
  GET  /api/treasury   — latest evaluation JSON
  GET  /api/config     — treasury/config.json
  GET  /api/watchlist  — watchlist + deep-dive summaries
  GET  /api/watchlist/deep-dive?symbol=BE — full deep-dive markdown
  GET  /api/capital-flows — income → channel flow model (+ optional live enrich)
  GET  /api/braiins       — Braiins Pool mining snapshot summary
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

from treasury.adapters import load_config, save_config  # noqa: E402
from treasury.run_treasury import main as run_treasury_main  # noqa: E402
from treasury.watchlist_dashboard import (  # noqa: E402
    build_watchlist_dashboard,
    get_deep_dive_markdown,
)

BRAIINS_SNAPSHOT = ROOT / "treasury" / "snapshots" / "braiins_latest.json"
ORCHESTRA_PORT = 8790
ORCHESTRA_URL = f"http://127.0.0.1:{ORCHESTRA_PORT}/"
_ORCHESTRA_PID: int | None = None


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
            # Sheet pay-from breakdown (Personal tab)
            by_src = (
                ((ed.get("tabs") or {}).get("Personal") or {}).get("by_source_monthly")
                or {}
            )
            if by_src.get("X Money") is not None:
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

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
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
                self._json(404, {"ok": False, "error": "no treasury_latest.json — POST /api/refresh"})
                return
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            if isinstance(data, dict):
                data = dict(data)
                data["braiins"] = _braiins_live()
            self._json(200, data)
            return
        if path == "/api/braiins":
            self._json(200, _braiins_live())
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

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
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
            offline = False
            body = self._read_json()
            if body.get("offline"):
                offline = True
            # Prefer live YNAB + Coinbase unless offline
            args = ["--offline"] if offline else []
            try:
                if not offline:
                    try:
                        from treasury.ynab_sync import main as ynab_main

                        ynab_main([])
                    except SystemExit:
                        pass
                    except Exception as ye:
                        sys.stderr.write(f"[fcc] ynab_sync warning: {ye}\n")
                    try:
                        from treasury.expenses_sync import main as exp_main

                        exp_main([])
                    except SystemExit:
                        pass
                    except Exception as ee:
                        sys.stderr.write(f"[fcc] expenses_sync warning: {ee}\n")
                code = run_treasury_main(args)
            except SystemExit as e:
                code = e.code if e.code is not None else 0
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            if code not in (0, None):
                self._json(500, {"ok": False, "error": f"treasury exit {code}"})
                return
            p = ROOT / "financial-command" / "treasury_latest.json"
            data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
            self._json(200, {"ok": True, "treasury": data})
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
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Initial refresh offline")
    args = parser.parse_args(argv)

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

    url = f"http://127.0.0.1:{args.port}/financial-command/index.html"
    wl = f"http://127.0.0.1:{args.port}/financial-command/watchlist.html"
    print(f"Financial Command Center → {url}")
    print(f"Watchlist research        → {wl}")
    print(
        f"Capital flows             → http://127.0.0.1:{args.port}/financial-command/capital-flows.html"
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), FCCHandler)
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
