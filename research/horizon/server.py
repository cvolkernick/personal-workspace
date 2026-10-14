#!/usr/bin/env python3
"""Horizon visual dashboard server.

  GET  /                 — front page: the Daily Brief (FRONT_PAGE flag; query → /model)
  GET  /model            — macro-model dashboard UI (index.html, unchanged)
  GET  /api/health
  GET  /api/brief        — latest synthesis brief JSON
  GET  /api/world-state  — latest world-state JSON
  GET  /api/dashboard    — combined payload for UI
  POST /api/refresh      — re-run pipeline (body: {"offline": true})
  GET  /daily-brief/*    — Daily Brief newspaper (research/daily_brief; not model output)
  GET  /manifest.webmanifest, /sw.js, /pwa.js, /icon-*.png, /apple-touch-icon.png
                         — install shell ("Add to Home Screen"); prefix-aware

Usage:
  python3 research/horizon/server.py
  python3 research/horizon/server.py --port 8795 --no-browser
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import webbrowser
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HORIZON_DIR = Path(__file__).resolve().parent
ROOT = HORIZON_DIR.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.horizon.pipeline import run_pipeline  # noqa: E402
from research.horizon.swarm import SWARM_PATH, swarm_html  # noqa: E402
from research.horizon.store import (  # noqa: E402
    DEFAULT_DATA_DIR,
    brief_latest_paths,
    load_json,
    load_world_state,
    world_state_latest_path,
)

DEFAULT_PORT = 8795
DEFAULT_BIND_HOST = "127.0.0.1"


def resolve_bind_host(
    explicit: str | None = None,
    environ: dict[str, str] | None = None,
) -> str:
    """Bind address. Explicit --host wins, else HORIZON_BIND_HOST, else 127.0.0.1.

    HORIZON_BIND_HOST=0.0.0.0 restores the previous all-interfaces listen.
    FCC's /horizon/* proxy always dials 127.0.0.1, which 0.0.0.0 still accepts.
    """
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    env = os.environ if environ is None else environ
    raw = (env.get("HORIZON_BIND_HOST") or "").strip()
    return raw or DEFAULT_BIND_HOST


def build_dashboard_payload(workspace: Path | None = None, data_dir: Path | None = None) -> dict:
    data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    workspace = Path(workspace or ROOT)
    state = load_world_state(data_dir)
    brief_path, _ = brief_latest_paths(data_dir)
    brief = None
    if brief_path.is_file():
        try:
            brief = load_json(brief_path)
        except (OSError, json.JSONDecodeError):
            brief = None

    domains = (state or {}).get("domains") or {}
    domain_stats = []
    for d, bucket in domains.items():
        nodes = bucket.get("nodes") or []
        top_score = max((float(n.get("priority_score") or 0) for n in nodes), default=0.0)
        avg_conf = (
            sum(float(n.get("confidence") or 0) for n in nodes) / len(nodes) if nodes else 0.0
        )
        domain_stats.append(
            {
                "id": d,
                "label": bucket.get("label") or d,
                "node_count": len(nodes),
                "top_score": round(top_score, 3),
                "avg_confidence": round(avg_conf, 3),
                "summary": bucket.get("summary") or "",
                "intensity": min(1.0, top_score / 3.5) if top_score else 0.0,
            }
        )
    domain_stats.sort(key=lambda x: x["top_score"], reverse=True)

    return {
        "ok": True,
        "service": "horizon",
        "workspace": str(workspace),
        "data_dir": str(data_dir),
        "has_world_state": state is not None,
        "has_brief": brief is not None,
        "version_id": (brief or state or {}).get("version_id"),
        "generated_at": (brief or {}).get("generated_at") or (state or {}).get("updated_at"),
        "domain_stats": domain_stats,
        "world_state": state,
        "brief": brief,
        "paths": {
            "world_state": str(world_state_latest_path(data_dir)),
            "brief_json": str(brief_path),
        },
    }


def daily_brief_route(path: str):
    """(status, content_type, body) for /daily-brief*, else None.

    Sibling page, not part of the Horizon model: research/daily_brief never
    reads world-state or model briefs. Lazy and error-isolated so a bad
    edition can never take the dashboard down.
    """
    if not (path == "/daily-brief" or path.startswith("/daily-brief/")):
        return None
    try:
        from research.daily_brief.render import route

        return route(path)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[horizon] daily-brief error: {exc!r}\n")
        body = (
            "<!doctype html><meta charset=utf-8><title>The Daily Brief</title>"
            "<p>The Daily Brief could not be rendered. Check the Horizon log.</p>"
        )
        return 500, "text/html; charset=utf-8", body


# ---------------------------------------------------------------- front page
# One flag. "daily-brief": / serves the Daily Brief and the dashboard lives at
# /model. "model": / serves the dashboard as before (/model keeps working).
# Only HTML landing routes move; /api/*, /data/* and static assets are untouched.
FRONT_PAGE = "daily-brief"
MODEL_PATH = "/model"

# Dashboard element ids an old "/#<id>" deep link may target (index.html).
DASHBOARD_ANCHORS = (
    "kpis", "tabs", "domain-heat", "overview-top", "overview-strat",
    "panel-overview", "panel-brief", "panel-world", "panel-strategy",
    "panel-watchlist", "panel-graph", "brief-list", "world-domains",
    "strategy-head", "strategy-grid", "watch-body", "graph-svg", "edge-list",
)

# Relative href: resolves under the /horizon prefix (FCC injects <base href="/horizon/">).
_MODEL_LINK = '<a href="model" id="nav-model" title="Horizon macro-model dashboard">Macro Model</a>'

# Old "/#panel-…" links: hashes never reach the server, so hop client-side.
# Prefix-aware: builds the target from location.pathname (/ or /horizon/).
_ANCHOR_HOP = (
    "<script>(function(){var ids=" + json.dumps(list(DASHBOARD_ANCHORS)) + ";"
    "var h=(location.hash||'').slice(1);if(!h||ids.indexOf(h)<0)return;"
    "var p=location.pathname||'';if(p.slice(-1)!=='/')p+='/';"
    "location.replace(p+'model'+location.search+location.hash);})();</script>"
)


def front_page_html(body: str) -> str:
    """Daily Brief HTML for the root: adds the Macro Model link + anchor hop.

    /daily-brief itself is served byte-for-byte from the renderer.
    """
    if '<div class="topbar">' in body:
        body = body.replace('<div class="topbar">', '<div class="topbar">' + _MODEL_LINK, 1)
    else:
        body = body.replace('<div class="paper">', '<div class="paper"><div class="topbar">' + _MODEL_LINK + "</div>", 1)
    if "</head>" in body:
        body = body.replace("</head>", _ANCHOR_HOP + "</head>", 1)
    else:
        body = _ANCHOR_HOP + body
    return body


def landing_route(path: str, query: str):
    """Map an HTML landing path to an action, or None to fall through.

    ("redirect", location) | ("brief", None) | ("model", None)
    """
    if FRONT_PAGE != "daily-brief":
        if path in ("/", "/index.html", MODEL_PATH):
            return ("model", None)
        if path == MODEL_PATH + "/":
            return ("redirect", MODEL_PATH + (f"?{query}" if query else ""))
        return None
    q = f"?{query}" if query else ""
    if path == "/":
        # The brief takes no query params; any query is an old dashboard deep link.
        return ("redirect", MODEL_PATH + q) if query else ("brief", None)
    if path in ("/index.html", MODEL_PATH + "/"):
        return ("redirect", MODEL_PATH + q)
    if path == MODEL_PATH:
        return ("model", None)
    return None


# ---------------------------------------------------------------- install shell
# Same pattern as FCC (manifest + icons + worker + apple-touch meta), but
# Horizon-owned files so FCC's origin-scoped pwa.js/sw.js stay untouched.
# Manifest URLs are relative, so behind FCC's /horizon lens the app's
# start_url and scope resolve to /horizon/; directly on :8795 they are /.
PWA_ICONS_PATH = HORIZON_DIR / "pwa_icons.json"
PWA_ICON_NAMES = (
    "icon-192.png",
    "icon-512.png",
    "icon-maskable-192.png",
    "icon-maskable-512.png",
    "apple-touch-icon.png",
    "favicon-32.png",
)
# iOS probes these at the app root when a page has no apple-touch-icon link.
_ICON_ALIASES = {
    "apple-touch-icon-precomposed.png": "apple-touch-icon.png",
    "apple-touch-icon-120x120.png": "apple-touch-icon.png",
    "apple-touch-icon-120x120-precomposed.png": "apple-touch-icon.png",
    "apple-touch-icon-180x180.png": "apple-touch-icon.png",
    "apple-touch-icon-180x180-precomposed.png": "apple-touch-icon.png",
}
_ICON_CACHE: dict[str, bytes] | None = None
_NO_CACHE_SHELL = ("/sw.js", "/manifest.webmanifest", "/pwa.js")

# Root-absolute so nested /daily-brief/<date>/<slot> pages resolve correctly;
# FCC's lens rewrites these to /horizon/... like every other Horizon asset.
PWA_HEAD = (
    '<link rel="manifest" href="/manifest.webmanifest">'
    '<meta name="theme-color" content="#060a10">'
    '<meta name="mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-status-bar-style" content="black">'
    '<meta name="apple-mobile-web-app-title" content="Horizon">'
    '<link rel="apple-touch-icon" href="/apple-touch-icon.png">'
    '<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">'
    '<script src="/pwa.js" defer></script>'
)


def pwa_icon(name: str) -> bytes | None:
    """PNG bytes for an icon path name (decoded once from pwa_icons.json)."""
    global _ICON_CACHE
    name = _ICON_ALIASES.get(name, name)
    if name not in PWA_ICON_NAMES:
        return None
    if _ICON_CACHE is None:
        try:
            raw = json.loads(PWA_ICONS_PATH.read_text(encoding="utf-8"))
            _ICON_CACHE = {k: base64.b64decode(v) for k, v in raw.items() if k in PWA_ICON_NAMES}
        except (OSError, ValueError) as exc:
            sys.stderr.write(f"[horizon] pwa icons unavailable: {exc!r}\n")
            _ICON_CACHE = {}
    return _ICON_CACHE.get(name)


def with_pwa_head(html: str) -> str:
    """Add the install-shell head tags to a Horizon-served HTML page (idempotent)."""
    if 'rel="manifest"' in html:
        return html
    if "</head>" in html:
        return html.replace("</head>", PWA_HEAD + "</head>", 1)
    return PWA_HEAD + html


# ---------------------------------------------------------------- site nav
# One persistent top nav on every Horizon HTML page (front page, /daily-brief,
# the archive, edition permalinks, /model). Links are relative so they work at
# the root of Horizon's own origin and under FCC's /horizon/ lens:
# - Behind the lens (X-Forwarded-Prefix set) FCC injects <base href="/horizon/">,
#   so links are relative to the app base ("daily-brief", "model").
# - On Horizon's own origin there is no <base>, so links climb from the page's
#   directory ("../../daily-brief" on /daily-brief/2026-10-11/pm).
# Additive only: the brief keeps its "Daily Brief, not Horizon model output"
# label and the renderer's own topbar/tabbar.
SITE_NAV_ID = "hz-site-nav"
SITE_NAV_LINKS = (
    ("home", "Home", ""),
    ("daily-brief", "Daily Brief", "daily-brief"),
    ("archive", "Archive", "daily-brief/archive"),
    ("model", "Model", "model"),
    ("directory", "Directory", "directory"),
    ("swarm", "Swarm", "swarm"),
)
_SITE_NAV_CSS = (
    "<style>#hz-site-nav{position:sticky;top:0;z-index:50;display:flex;gap:.35rem;"
    "flex-wrap:wrap;align-items:center;padding:.45rem max(.75rem,env(safe-area-inset-left));"
    "background:#060a10;border-bottom:1px solid #1c2633;font:600 .82rem system-ui,-apple-system,sans-serif}"
    "#hz-site-nav a{color:#c9d6e3;text-decoration:none;padding:.22rem .65rem;border-radius:999px;"
    "border:1px solid #1c2633}"
    "#hz-site-nav a[aria-current=page]{color:#060a10;background:#c9d6e3;border-color:#c9d6e3}</style>"
)
_BODY_OPEN = re.compile(r"<body\b[^>]*>", re.I)


def site_nav_active(path: str) -> str:
    p = (path or "/").rstrip("/") or "/"
    if p == "/":
        return "home"
    if p == MODEL_PATH:
        return "model"
    if p == DIRECTORY_PATH or p.startswith(DIRECTORY_PATH + "/"):
        return "directory"
    if p == SWARM_PATH:
        return "swarm"
    if p == "/daily-brief/archive":
        return "archive"
    if p == "/daily-brief" or p.startswith("/daily-brief/"):
        return "daily-brief"
    return ""


def site_nav_prefix(path: str, forwarded_prefix: str = "") -> str:
    """Relative climb from the page to the app base ("" or "../" * depth)."""
    if (forwarded_prefix or "").strip():
        return ""
    depth = max(0, (path or "/").count("/") - 1)
    return "../" * depth


def site_nav_html(path: str, forwarded_prefix: str = "") -> str:
    prefix = site_nav_prefix(path, forwarded_prefix)
    active = site_nav_active(path)
    links = []
    for key, label, rel in SITE_NAV_LINKS:
        href = (prefix + rel) or "./"
        cur = ' aria-current="page"' if key == active else ""
        links.append(f'<a href="{href}" data-nav="{key}"{cur}>{label}</a>')
    return (
        _SITE_NAV_CSS
        + f'<nav id="{SITE_NAV_ID}" aria-label="Horizon">'
        + "".join(links)
        + "</nav>"
    )


def with_site_nav(html: str, path: str, forwarded_prefix: str = "") -> str:
    """Insert the site nav right after <body> (idempotent)."""
    if f'id="{SITE_NAV_ID}"' in html:
        return html
    nav = site_nav_html(path, forwarded_prefix)
    out, n = _BODY_OPEN.subn(lambda m: m.group(0) + nav, html, count=1)
    return out if n else nav + html


# ---------------------------------------------------------------- directory
# Team & Systems Directory sub-page (not the front page). This repo is public,
# so the directory content is NOT in git: the route reads index.html and the
# PDF from a prism-local directory (gitignored research/horizon/directory/_local/
# by default, or HORIZON_DIRECTORY_DIR). make_pdf.sh builds the PDF there.
# Missing files -> 404 with a short hint.
DIRECTORY_PATH = "/directory"
DIRECTORY_DIR = Path(
    os.environ.get("HORIZON_DIRECTORY_DIR") or (HORIZON_DIR / "directory" / "_local")
).expanduser()
DIRECTORY_PDF_NAME = "team-directory.pdf"
SWARM_NODES_MD = Path(os.environ.get("HORIZON_NODES_MD") or (ROOT / "ops" / "NODES.md")).expanduser()
DIRECTORY_PDF_PATH = DIRECTORY_PATH + "/" + DIRECTORY_PDF_NAME
_DIRECTORY_MISSING_HTML = (
    b"<!doctype html><h1>Directory not installed</h1>"
    b"<p>Place index.html in the prism-local directory folder "
    b"(HORIZON_DIRECTORY_DIR) and run research/horizon/directory/make_pdf.sh.</p>"
)
_DIRECTORY_BAR = (
    '<div id="hz-dir-pdf" style="display:flex;gap:.5rem;justify-content:flex-end;'
    'padding:.35rem max(.75rem,env(safe-area-inset-left));background:#0b121b;'
    'font:600 .78rem system-ui,-apple-system,sans-serif">'
    '<a href="{base}team-directory.pdf" target="_blank" rel="noopener" '
    'style="color:#c9d6e3">View PDF</a>'
    '<a href="{base}team-directory.pdf?download=1" download '
    'style="color:#c9d6e3">Download PDF</a></div>'
)


def directory_route(path: str, query: str):
    """("redirect", loc) | ("html", None) | ("pdf", attachment: bool) | None."""
    if path == DIRECTORY_PATH + "/" or path == DIRECTORY_PATH + "/index.html":
        return ("redirect", DIRECTORY_PATH)
    if path == DIRECTORY_PATH:
        return ("html", None)
    if path == DIRECTORY_PDF_PATH:
        dl = parse_qs(query or "").get("download", [""])[0].lower() in ("1", "true", "yes")
        return ("pdf", dl)
    return None


def directory_html(path: str, forwarded_prefix: str = "") -> str | None:
    try:
        raw = (DIRECTORY_DIR / "index.html").read_text(encoding="utf-8")
    except OSError:
        return None
    base = "directory/" if (forwarded_prefix or "").strip() else site_nav_prefix(path) + "directory/"
    page = with_site_nav(with_pwa_head(raw), path, forwarded_prefix)
    bar = _DIRECTORY_BAR.format(base=base)
    return page.replace("</nav>", "</nav>" + bar, 1)


class HorizonHandler(SimpleHTTPRequestHandler):
    # Stdlib maps .webmanifest to octet-stream on some Pythons; pin the types.
    extensions_map = {
        **getattr(SimpleHTTPRequestHandler, "extensions_map", {}),
        ".webmanifest": "application/manifest+json",
        ".js": "text/javascript; charset=utf-8",
        ".png": "image/png",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HORIZON_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[horizon] " + (fmt % args) + "\n")

    def end_headers(self) -> None:
        # Worker and manifest must revalidate so a shell update reaches the phone.
        if urlparse(getattr(self, "path", "") or "").path in _NO_CACHE_SHELL:
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _maybe_serve_pwa_icon(self) -> bool:
        path = urlparse(self.path).path
        if path.count("/") != 1 or not path.endswith(".png"):
            return False
        raw = pwa_icon(path[1:])
        if raw is None:
            return False
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)
        return True

    def _forwarded_prefix(self) -> str:
        # Set only by FCC's /horizon lens (which also injects <base href="/horizon/">).
        return (self.headers.get("X-Forwarded-Prefix") or "").strip()

    def _json(self, code: int, payload: dict) -> None:
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
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _maybe_serve_daily_brief(self) -> bool:
        res = daily_brief_route(urlparse(self.path).path)
        if res is None:
            return False
        code, ctype, body = res
        if code == 200 and ctype.startswith("text/html"):
            body = with_site_nav(with_pwa_head(body), urlparse(self.path).path, self._forwarded_prefix())
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)
        return True

    def _send_html(self, code: int, raw: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _maybe_serve_landing(self) -> bool:
        parsed = urlparse(self.path)
        res = landing_route(parsed.path, parsed.query)
        if res is None:
            return False
        kind, location = res
        if kind == "redirect":
            # Root-absolute: FCC's /horizon proxy rewrites Location under its prefix.
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return True
        if kind == "brief":
            code, _ctype, body = daily_brief_route("/daily-brief")
            if code == 200:
                body = with_site_nav(
                    with_pwa_head(front_page_html(body)), parsed.path, self._forwarded_prefix()
                )
            self._send_html(code, body.encode("utf-8"))
            return True
        model = (HORIZON_DIR / "index.html").read_text(encoding="utf-8")
        self._send_html(200, with_site_nav(model, parsed.path, self._forwarded_prefix()).encode("utf-8"))
        return True

    def _maybe_serve_swarm(self) -> bool:
        parsed = urlparse(self.path)
        if parsed.path == SWARM_PATH + "/":
            self.send_response(302)
            self.send_header("Location", SWARM_PATH)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        if parsed.path != SWARM_PATH:
            return False
        page = swarm_html(SWARM_NODES_MD)
        if page is None:
            self._send_html(404, b"<!doctype html><h1>Swarm unavailable</h1><p>ops/NODES.md not found.</p>")
        else:
            self._send_html(200, with_site_nav(with_pwa_head(page), parsed.path, self._forwarded_prefix()).encode("utf-8"))
        return True

    def _maybe_serve_directory(self) -> bool:
        if self._maybe_serve_swarm():
            return True
        parsed = urlparse(self.path)
        res = directory_route(parsed.path, parsed.query)
        if res is None:
            return False
        kind, arg = res
        if kind == "redirect":
            self.send_response(302)
            self.send_header("Location", arg)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        if kind == "html":
            page = directory_html(parsed.path, self._forwarded_prefix())
            if page is None:
                self._send_html(404, _DIRECTORY_MISSING_HTML)
            else:
                self._send_html(200, page.encode("utf-8"))
            return True
        try:
            raw = (DIRECTORY_DIR / DIRECTORY_PDF_NAME).read_bytes()
        except OSError:
            self._send_html(404, _DIRECTORY_MISSING_HTML)
            return True
        disp = "attachment" if arg else "inline"
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", f'{disp}; filename="{DIRECTORY_PDF_NAME}"')
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)
        return True

    def do_HEAD(self) -> None:  # noqa: N802
        if self._maybe_serve_directory():
            return
        if self._maybe_serve_daily_brief() or self._maybe_serve_landing() or self._maybe_serve_pwa_icon():
            return
        return super().do_HEAD()

    def do_GET(self) -> None:  # noqa: N802
        if self._maybe_serve_directory():
            return
        if self._maybe_serve_daily_brief():
            return
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "horizon-macro",
                    "label": "Horizon Macro",
                    "port": DEFAULT_PORT,
                    "note": "Global macro intelligence — seasonal plan is horizon/ on :8791",
                },
            )
            return

        if path == "/api/dashboard":
            try:
                self._json(200, build_dashboard_payload(ROOT, DEFAULT_DATA_DIR))
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/brief":
            brief_path, _ = brief_latest_paths(DEFAULT_DATA_DIR)
            if not brief_path.is_file():
                self._json(404, {"ok": False, "error": "no brief yet — run refresh"})
                return
            try:
                self._json(200, {"ok": True, "brief": load_json(brief_path)})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if path in ("/api/world-state", "/api/world_state"):
            state = load_world_state(DEFAULT_DATA_DIR)
            if state is None:
                self._json(404, {"ok": False, "error": "no world-state yet — run refresh"})
                return
            self._json(200, {"ok": True, "world_state": state})
            return

        if self._maybe_serve_landing() or self._maybe_serve_pwa_icon():
            return
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/refresh":
            self._json(404, {"ok": False, "error": "not found"})
            return
        body = self._read_json_body()
        offline = body.get("offline", True)
        link_only = bool(body.get("link_only", False))
        try:
            result = run_pipeline(
                workspace=ROOT,
                data_dir=DEFAULT_DATA_DIR,
                offline=bool(offline),
                link_only=link_only,
            )
            payload = build_dashboard_payload(ROOT, DEFAULT_DATA_DIR)
            payload["refresh"] = {
                "ok": result.get("ok"),
                "version_id": result.get("version_id"),
                "source_modes": result.get("source_modes"),
                "sections": result.get("sections"),
                "linkage_count": result.get("linkage_count"),
            }
            self._json(200, payload)
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Horizon visual dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--host",
        default=None,
        help=(
            "Bind address. Default 127.0.0.1, or HORIZON_BIND_HOST when set. "
            "HORIZON_BIND_HOST=0.0.0.0 listens on all interfaces (previous behavior). "
            "An explicit --host wins over the env var."
        ),
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Run offline pipeline once before serving if no brief exists",
    )
    args = parser.parse_args(argv)
    bind_host = resolve_bind_host(args.host)

    brief_path, _ = brief_latest_paths(DEFAULT_DATA_DIR)
    if args.bootstrap or not brief_path.is_file():
        print("[horizon] bootstrapping offline world-state + brief…", flush=True)
        run_pipeline(workspace=ROOT, data_dir=DEFAULT_DATA_DIR, offline=True)

    server = ThreadingHTTPServer((bind_host, args.port), HorizonHandler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"[horizon] bind {bind_host}:{args.port}", flush=True)
    print(f"[horizon] front page at {url} (macro model at {url.rstrip('/')}{MODEL_PATH})", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[horizon] stopped", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
