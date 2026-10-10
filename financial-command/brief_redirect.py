"""FCC /brief* -> Daily Brief on the Horizon host (#1091).

The newspaper is served by the Horizon host at ``/daily-brief``
(``research/daily_brief``). FCC keeps only this redirect. ``server.py`` is
unchanged: its ``brief_route`` loads ``brief.py`` and calls
``route(path) -> (status, content_type, body)``, then writes that tuple with
``send_response`` / ``send_header("Content-Type", ...)`` /
``send_header("Content-Length", ...)``. That contract has no slot for a
``Location`` header and passes only the path (no query), so:

* ``Location`` rides on the content-type value as a second header line
  (``http.server``'s ``send_header`` writes the value verbatim). The target
  is stripped of control characters and percent-encoded first, so nothing
  but that one header can be added.
* The query string is read back from the calling request handler
  (``self.path``) when its path matches; otherwise it is dropped.
* The body is a tiny HTML page with a link, for clients that ignore 3xx.

302, not 301, so browsers do not pin it. Revert the PR to get the in-FCC
renderer back.
"""

from __future__ import annotations

import html
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import quote, urlparse

DEFAULT_BRIEF_BASE_URL = "/horizon/daily-brief"
_SAFE = "/:?&=%#;@!$'()*+,~-._[]"


def brief_base_url() -> str:
    raw = (os.environ.get("BRIEF_BASE_URL") or "").strip().rstrip("/")
    return raw or DEFAULT_BRIEF_BASE_URL


def is_brief_path(path: str) -> bool:
    return path == "/brief" or path.startswith("/brief/") or path.startswith("/api/brief/")


def redirect_target(raw_path: str, base: str | None = None) -> str | None:
    """Location for /brief, /brief/*, /api/brief/* (subpath and query kept), else None.

    /brief/2026-10-09/am?x=1  -> {base}/2026-10-09/am?x=1
    /api/brief/latest         -> {base}/api/latest
    """
    parsed = urlparse(raw_path)
    path = parsed.path
    base = (base or brief_base_url()).rstrip("/")
    if path == "/brief" or path.startswith("/brief/"):
        rest = path[len("/brief"):]
    elif path.startswith("/api/brief/"):
        rest = "/api/" + path[len("/api/brief/"):]
    else:
        return None
    target = base + (rest if rest not in ("", "/") else "")
    if parsed.query:
        target += "?" + parsed.query
    return safe_location(target)


def safe_location(target: str) -> str:
    """Drop control characters (no CR/LF can reach the header) and percent-encode the rest."""
    cleaned = "".join(ch for ch in target if ch >= " " and ch != "\x7f")
    return quote(cleaned, safe=_SAFE)


def _request_raw_path(path: str) -> str:
    """The calling handler's ``self.path`` (with query) if it matches ``path``, else ``path``."""
    try:
        f = sys._getframe(1)
    except Exception:  # noqa: BLE001
        return path
    for _ in range(8):
        if f is None:
            break
        h = f.f_locals.get("self")
        if isinstance(h, BaseHTTPRequestHandler):
            raw = getattr(h, "path", None)
            if isinstance(raw, str) and urlparse(raw).path == path:
                return raw
            break
        f = f.f_back
    return path


def route(path: str, raw_path: str | None = None) -> tuple[int, str, str] | None:
    """(302, content_type + Location, body) for brief paths, else None."""
    if not is_brief_path(urlparse(path).path):
        return None
    if raw_path is None:
        raw_path = path if "?" in path else _request_raw_path(path)
    target = redirect_target(raw_path)
    if target is None:
        return None
    ctype = "text/html; charset=utf-8\r\nLocation: " + target
    esc = html.escape(target, quote=True)
    body = (
        "<!doctype html><meta charset=utf-8><title>The Daily Brief has moved</title>"
        f'<p>The Daily Brief moved to <a href="{esc}">{esc}</a>.</p>'
    )
    return 302, ctype, body
