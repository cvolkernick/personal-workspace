#!/usr/bin/env python3
"""Local server for Financial Command Center.

Serves static UI + APIs:
  GET  /api/treasury   — latest evaluation JSON
  GET  /api/config     — treasury/config.json
  GET  /api/watchlist  — watchlist + deep-dive summaries
  GET  /api/watchlist/deep-dive?symbol=BE — full deep-dive markdown
  GET  /api/capital-flows — income → channel flow model (+ optional live enrich)
  GET  /api/braiins       — Braiins Pool mining snapshot summary
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
import subprocess
import sys
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
