#!/usr/bin/env python3
"""Local server for the Orchestra top-level command center.

  GET  /api/health
  GET  /api/orchestra   — full payload (recommendations primary; domains, synergies, …)
  GET  /api/domains
  GET  /api/synergies
  GET  /api/priorities
  GET  /api/attention   — attention digest + freshness
  GET  /api/recommendations — automated recommended next actions (primary)
  GET  /api/strategy        — strategy brief (themes, goals, directives)
  GET  /api/conductor/status — Grok auth ready for Conductor
  POST /api/conductor       — {question} ask Grok about orchestration
  GET  /api/launch/status   — which domain servers are live
  POST /api/launch          — {domain} start server if down, return url
  GET  /                — unified UI

Usage:
  python3 orchestra/server.py
  python3 orchestra/server.py --port 8790 --no-browser
  python3 launch.py
"""

from __future__ import annotations

import argparse
import html as html_module
import json
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ORCHESTRA_DIR = Path(__file__).resolve().parent
ROOT = ORCHESTRA_DIR.parent
if str(ORCHESTRA_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRA_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from action_plan_template import (  # noqa: E402
    collect_action_plans,
    ensure_domain_action_plan,
    ensure_macro_action_plan,
    ensure_template_file,
    read_plan_body,
    sanitize_domain_id,
)
from conductor import (  # noqa: E402
    CONDUCTOR_SUGGESTIONS,
    ConductorError,
    ask_conductor,
    auth_status,
)
from ikigai import load_ikigai, save_ikigai  # noqa: E402
from intent import FOCUS_BRIEF_PROMPT, load_intent, save_intent  # noqa: E402
from launcher import ensure_domain, status_all  # noqa: E402
from payload import DEFAULT_PORT, WORKSPACE_ROOT, build_orchestra_payload  # noqa: E402
from public_base import public_hostname, rewrite_payload_urls  # noqa: E402


class OrchestraHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ORCHESTRA_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[orchestra] " + (fmt % args) + "\n")

    def _json(self, code: int, payload: dict) -> None:
        # Mac → Pi: rewrite 127.0.0.1 domain deep-links to the public host
        if isinstance(payload, dict) and payload.get("ok") is not False:
            host = public_hostname(request_host_header=self.headers.get("Host"))
            if host:
                payload = rewrite_payload_urls(payload, host)
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        probe = (qs.get("probe") or ["0"])[0] in ("1", "true", "yes")

        if path == "/api/health":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "orchestra",
                    "workspace": str(WORKSPACE_ROOT),
                },
            )
            return

        if path in ("/api/conductor/status", "/api/ask/status"):
            st = auth_status()
            self._json(
                200,
                {
                    "ok": True,
                    "conductor": st,
                    "suggestions": CONDUCTOR_SUGGESTIONS,
                },
            )
            return

        if path in ("/api/intent", "/api/focus/intent"):
            try:
                data = load_intent(WORKSPACE_ROOT)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, {"ok": True, "intent": data})
            return

        if path in ("/api/ikigai", "/api/identity"):
            try:
                data = load_ikigai(WORKSPACE_ROOT)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, {"ok": True, "ikigai": data})
            return

        if path == "/api/strategy":
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=False
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(
                200,
                {"ok": True, "strategy": payload.get("strategy") or {}},
            )
            return

        if path in ("/api/launch/status", "/api/servers"):
            try:
                self._json(200, status_all(workspace=WORKSPACE_ROOT))
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path in (
            "/api/orchestra",
            "/api/status",
            "/api/payload",
        ):
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=probe
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, payload)
            return

        if path == "/api/domains":
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=probe
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(
                200,
                {
                    "ok": True,
                    "domains": payload.get("domains"),
                    "links": payload.get("links"),
                },
            )
            return

        if path == "/api/synergies":
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=False
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(
                200,
                {"ok": True, "synergies": payload.get("synergies") or []},
            )
            return

        if path in ("/api/priorities", "/api/action-plan"):
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=False
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(
                200,
                {
                    "ok": True,
                    "priorities": payload.get("priorities") or [],
                    "action_plan": payload.get("action_plan") or [],
                    "action_plans": payload.get("action_plans") or {},
                },
            )
            return

        if path in ("/api/action-plans", "/api/action_plans"):
            try:
                plans = collect_action_plans(WORKSPACE_ROOT)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, {"ok": True, "action_plans": plans})
            return

        if path in ("/api/action-plans/content", "/api/action_plans/body"):
            layer = (qs.get("layer") or ["macro"])[0].strip().lower()
            raw_domain = (qs.get("domain") or qs.get("id") or [""])[0]
            domain = sanitize_domain_id(raw_domain) if raw_domain.strip() else None
            if raw_domain.strip() and not domain:
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": f"invalid domain_id: use [a-z0-9_-] only (got {raw_domain!r})",
                    },
                )
                return
            try:
                if domain:
                    result = read_plan_body(
                        WORKSPACE_ROOT, layer="domain", domain_id=domain
                    )
                else:
                    result = read_plan_body(WORKSPACE_ROOT, layer=layer or "macro")
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            code = 200 if result.get("ok") else 404
            self._json(code, result)
            return

        if path in ("/api/action-plans/view",):
            layer = (qs.get("layer") or ["macro"])[0].strip().lower()
            raw_domain = (qs.get("domain") or qs.get("id") or [""])[0]
            domain = sanitize_domain_id(raw_domain) if raw_domain.strip() else None
            if raw_domain.strip() and not domain:
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": f"invalid domain_id: use [a-z0-9_-] only (got {raw_domain!r})",
                    },
                )
                return
            try:
                if domain:
                    ensure_domain_action_plan(WORKSPACE_ROOT, domain)
                    result = read_plan_body(
                        WORKSPACE_ROOT, layer="domain", domain_id=domain
                    )
                else:
                    ensure_macro_action_plan(WORKSPACE_ROOT)
                    result = read_plan_body(WORKSPACE_ROOT, layer="macro")
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            if not result.get("ok"):
                self._json(404, result)
                return
            # Rendered Markdown view (marked.js) — title/rel escaped; body via JSON
            title = html_module.escape(
                str(result.get("label") or result.get("id") or "Action Plan")
            )
            rel = html_module.escape(str(result.get("rel_path") or ""))
            body_md = result.get("body") or ""
            body_json = json.dumps(body_md, ensure_ascii=False)
            html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --bg: #0b0f14; --panel: #121820; --border: #243044; --text: #e7eef5;
    --muted: #8aa0b5; --accent: #5b9fd4; --cyan: #3ecfbf;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; font-family: system-ui, -apple-system, Segoe UI, sans-serif;
    background: var(--bg); color: var(--text); padding: 1.25rem 1.5rem 2.5rem;
    line-height: 1.55; max-width: 52rem;
  }}
  .top h1 {{ font-size: 1.25rem; margin: 0 0 .35rem; font-weight: 650; }}
  .path {{ color: var(--muted); font-size: .85rem; margin-bottom: 1rem; }}
  .path code {{ color: var(--cyan); font-size: .8rem; }}
  a {{ color: var(--accent); }}
  .md {{
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 1.1rem 1.25rem 1.35rem;
  }}
  .md h1, .md h2, .md h3, .md h4 {{
    line-height: 1.25; margin: 1.1rem 0 .45rem; font-weight: 650;
  }}
  .md h1 {{ font-size: 1.35rem; border-bottom: 1px solid var(--border); padding-bottom: .35rem; }}
  .md h2 {{ font-size: 1.15rem; color: #c5d4e6; }}
  .md h3 {{ font-size: 1.02rem; color: var(--cyan); }}
  .md h4 {{ font-size: .95rem; color: var(--muted); }}
  .md p {{ margin: .5rem 0; }}
  .md ul, .md ol {{ margin: .4rem 0 .6rem; padding-left: 1.35rem; }}
  .md li {{ margin: .25rem 0; }}
  .md strong {{ color: #fff; font-weight: 650; }}
  .md em {{ color: #c8d6e5; }}
  .md code {{
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: .86em; background: #0b0f14; border: 1px solid var(--border);
    border-radius: 4px; padding: .1em .35em;
  }}
  .md pre {{
    background: #0b0f14; border: 1px solid var(--border); border-radius: 8px;
    padding: .75rem 1rem; overflow-x: auto;
  }}
  .md pre code {{ border: 0; background: transparent; padding: 0; }}
  .md hr {{ border: 0; border-top: 1px solid var(--border); margin: 1.1rem 0; }}
  .md blockquote {{
    margin: .6rem 0; padding: .35rem .9rem; border-left: 3px solid var(--accent);
    color: var(--muted); background: rgba(91,159,212,.06);
  }}
  .raw-toggle {{
    margin-top: .75rem; font-size: .8rem; color: var(--muted); cursor: pointer;
    background: none; border: 0; padding: 0; text-decoration: underline;
  }}
  #raw {{
    display: none; margin-top: .5rem; white-space: pre-wrap;
    background: #0b0f14; border: 1px solid var(--border); border-radius: 8px;
    padding: .85rem 1rem; font-size: .85rem; color: var(--muted);
  }}
  #raw.show {{ display: block; }}
</style>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>
</head><body>
<div class="top">
  <h1>{title}</h1>
  <div class="path">Source: <code>{rel}</code> · <a href="/">← Orchestrator</a></div>
</div>
<article class="md" id="md-out">Loading…</article>
<button type="button" class="raw-toggle" id="btn-raw">Show raw Markdown</button>
<pre id="raw"></pre>
<script type="application/json" id="md-src">{body_json}</script>
<script>
(function () {{
  var raw = "";
  try {{
    raw = JSON.parse(document.getElementById("md-src").textContent || '""');
  }} catch (e) {{
    raw = "";
  }}
  var out = document.getElementById("md-out");
  var rawEl = document.getElementById("raw");
  rawEl.textContent = raw || "";
  function escHtml(s) {{
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }}
  function render(md) {{
    var escaped = escHtml(md);
    try {{
      if (typeof marked !== "undefined" && typeof marked.parse === "function") {{
        if (typeof marked.setOptions === "function") {{
          marked.setOptions({{ gfm: true, breaks: true }});
        }}
        return marked.parse(escaped);
      }}
    }} catch (e) {{ /* fall through */ }}
    // minimal fallback
    return "<pre>" + escaped + "</pre>";
  }}
  out.innerHTML = render(raw);
  document.getElementById("btn-raw").addEventListener("click", function () {{
    rawEl.classList.toggle("show");
    this.textContent = rawEl.classList.contains("show")
      ? "Hide raw Markdown"
      : "Show raw Markdown";
  }});
}})();
</script>
</body></html>"""
            raw = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
            return

        if path == "/api/attention":
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=probe
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(
                200,
                {
                    "ok": True,
                    "attention": payload.get("attention") or [],
                    "freshness": payload.get("freshness") or {},
                    "counts": {
                        "attention": (payload.get("counts") or {}).get("attention"),
                        "stale_sources": (payload.get("counts") or {}).get(
                            "stale_sources"
                        ),
                    },
                },
            )
            return

        if path in ("/api/recommendations", "/api/actions", "/api/next"):
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=probe
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            rec = payload.get("recommendations") or {}
            self._json(
                200,
                {
                    "ok": True,
                    "next_action": payload.get("next_action") or rec.get("next_action"),
                    "recommendations": rec,
                    "recommended_actions": payload.get("recommended_actions") or [],
                    "summary": rec.get("summary"),
                    "mode": rec.get("mode"),
                    "focus": rec.get("focus") or [],
                },
            )
            return

        if path in ("/api/next-action", "/api/next_action"):
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=probe
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            nxt = payload.get("next_action")
            self._json(
                200,
                {
                    "ok": True,
                    "next_action": nxt,
                    "mode": (payload.get("recommendations") or {}).get("mode"),
                    "summary": (payload.get("recommendations") or {}).get("summary"),
                },
            )
            return

        if path in ("/", "/index.html", "/orchestra", "/orchestra/"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/api/intent", "/api/focus/intent"):
            body = self._read_json_body()
            try:
                saved = save_intent(body, WORKSPACE_ROOT)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, {"ok": True, "intent": saved})
            return

        if path in ("/api/ikigai", "/api/identity"):
            body = self._read_json_body()
            try:
                saved = save_ikigai(body, WORKSPACE_ROOT)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, {"ok": True, "ikigai": saved})
            return

        if path in ("/api/focus-brief", "/api/conductor/focus-brief"):
            # Proactive focus brief grounded in intent + orchestration data
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=False
                )
                result = ask_conductor(FOCUS_BRIEF_PROMPT, payload)
                result["kind"] = "focus_brief"
            except ConductorError as e:
                code = e.status if e.status in (400, 401, 403, 429) else 502
                if e.status and 400 <= e.status < 600:
                    code = e.status
                self._json(
                    code if code >= 400 else 502,
                    {
                        "ok": False,
                        "error": str(e),
                        "detail": (e.body or "")[:800],
                    },
                )
                return
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, result)
            return

        if path in ("/api/conductor", "/api/ask", "/api/conductor/ask"):
            body = self._read_json_body()
            question = (body.get("question") or body.get("prompt") or "").strip()
            if not question:
                self._json(400, {"ok": False, "error": "question is required"})
                return
            try:
                payload = build_orchestra_payload(
                    WORKSPACE_ROOT, probe_ports=False
                )
                result = ask_conductor(question, payload)
            except ConductorError as e:
                code = e.status if e.status in (400, 401, 403, 429) else 502
                if e.status and 400 <= e.status < 600:
                    code = e.status
                self._json(
                    code if code >= 400 else 502,
                    {
                        "ok": False,
                        "error": str(e),
                        "detail": (e.body or "")[:800],
                    },
                )
                return
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, result)
            return

        if path in ("/api/launch", "/api/start", "/api/servers/start"):
            body = self._read_json_body()
            domain = (
                body.get("domain")
                or body.get("id")
                or body.get("service")
                or ""
            ).strip()
            if not domain:
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "domain is required (workflow|finance|fitness|holistic|iot)",
                    },
                )
                return
            try:
                result = ensure_domain(
                    domain,
                    workspace=WORKSPACE_ROOT,
                    force_restart=bool(body.get("force")),
                )
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            code = 200 if result.get("ok") else 400
            self._json(code, result)
            return

        if path in ("/api/action-plans/ensure", "/api/action_plans/ensure"):
            body = self._read_json_body()
            layer = (body.get("layer") or "").strip().lower()
            raw_domain = (
                body.get("domain") or body.get("id") or body.get("domain_id") or ""
            )
            domain = sanitize_domain_id(str(raw_domain)) if str(raw_domain).strip() else None
            if str(raw_domain).strip() and not domain:
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": f"invalid domain_id: use [a-z0-9_-] only (got {raw_domain!r})",
                    },
                )
                return
            try:
                ensure_template_file(WORKSPACE_ROOT)
                # domain wins unless caller forces macro layer without a domain id
                if domain and layer not in ("macro", "orchestrator"):
                    result = ensure_domain_action_plan(WORKSPACE_ROOT, domain)
                elif layer in ("macro", "orchestrator") or not domain:
                    result = ensure_macro_action_plan(WORKSPACE_ROOT)
                else:
                    result = ensure_domain_action_plan(WORKSPACE_ROOT, domain)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
                return
            code = 200 if result.get("ok") else 400
            self._json(code, result)
            return

        self._json(404, {"ok": False, "error": f"unknown path {path}"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Orchestrator top-level dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address. Use 0.0.0.0 on the Pi for LAN/Tailscale access.",
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Force local API (Pi systemd unit flag).",
    )
    parser.add_argument("--backend", default=None, help="Reserved for frontend proxy mode.")
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), OrchestraHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Orchestrator: {url}")
    print(f"API: {url}api/orchestra · Conductor: {url}api/conductor")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        try:
            webbrowser.open(url.replace("0.0.0.0", "127.0.0.1"))
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Orchestrator…")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
