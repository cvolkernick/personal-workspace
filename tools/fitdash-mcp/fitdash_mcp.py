"""FitDash MCP server: thin, read-only stdio wrapper over the FitDash agent API.

Slice 2 of #1281. Every tool issues a single GET against /api/agent/<section>
and returns the JSON body unchanged (including generated_at / as_of).

Env:
  FITDASH_AGENT_TOKEN       bearer token (else read from FITDASH_AGENT_TOKEN_FILE)
  FITDASH_AGENT_TOKEN_FILE  default /workspace/.fitdash_agent_token
  FITDASH_API_BASE          default https://prism-gateway.tailb1085a.ts.net:10001
  FITDASH_PROXY             e.g. socks5h://127.0.0.1:1055 (remote DNS); empty = direct
  FITDASH_TIMEOUT           seconds, default 20
"""
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE = "https://prism-gateway.tailb1085a.ts.net:10001"
DEFAULT_TOKEN_FILE = "/workspace/.fitdash_agent_token"
SECTIONS = ("targets", "inventory", "logs", "plan", "training", "recovery")
MAX_LOG_DAYS = 92


class FitDashError(RuntimeError):
    """Error safe to surface to the MCP client (never contains the token)."""


def _base_url() -> str:
    return os.environ.get("FITDASH_API_BASE", DEFAULT_BASE).rstrip("/")


def _proxy() -> str | None:
    p = os.environ.get("FITDASH_PROXY", "").strip()
    if not p:
        return None
    # httpx's SOCKS transport already resolves hostnames on the proxy side,
    # so socks5h:// (curl --socks5-hostname) maps to socks5://.
    if p.startswith("socks5h://"):
        p = "socks5://" + p[len("socks5h://"):]
    return p


def _token() -> str:
    tok = os.environ.get("FITDASH_AGENT_TOKEN", "").strip()
    if tok:
        return tok
    path = Path(os.environ.get("FITDASH_AGENT_TOKEN_FILE", DEFAULT_TOKEN_FILE))
    try:
        tok = path.read_text().strip()
    except OSError:
        tok = ""
    if not tok:
        raise FitDashError(
            "No FitDash agent token: set FITDASH_AGENT_TOKEN or FITDASH_AGENT_TOKEN_FILE"
        )
    return tok


def _check_date(name: str, value: str | None) -> _dt.date | None:
    if value in (None, ""):
        return None
    try:
        return _dt.date.fromisoformat(value)
    except ValueError:
        raise FitDashError(f"{name} must be YYYY-MM-DD, got {value!r}") from None


def fetch(section: str, params: dict[str, str] | None = None,
          transport: httpx.BaseTransport | None = None) -> Any:
    """GET /api/agent/<section> and return the decoded JSON as-is."""
    if section not in SECTIONS:
        raise FitDashError(f"unknown section {section!r}")
    url = f"{_base_url()}/api/agent/{section}"
    headers = {"Authorization": f"Bearer {_token()}", "Accept": "application/json"}
    kwargs: dict[str, Any] = {"timeout": float(os.environ.get("FITDASH_TIMEOUT", "20"))}
    if transport is not None:
        kwargs["transport"] = transport
    elif _proxy():
        kwargs["proxy"] = _proxy()
    try:
        with httpx.Client(**kwargs) as client:
            resp = client.get(url, params=params or None, headers=headers)
    except httpx.TimeoutException:
        raise FitDashError(f"FitDash API timed out: {url}") from None
    except (httpx.TransportError, OSError) as exc:
        raise FitDashError(
            f"FitDash API unreachable at {url} ({type(exc).__name__}); "
            "check FITDASH_API_BASE / FITDASH_PROXY and that the tailnet proxy is up"
        ) from None
    if resp.status_code == 401:
        raise FitDashError(
            "FitDash API returned 401 Unauthorized: agent token missing, wrong or rotated"
        )
    if resp.status_code == 403:
        raise FitDashError("FitDash API returned 403 Forbidden")
    if resp.status_code >= 400:
        detail = resp.text[:300].replace(headers["Authorization"], "Bearer ***")
        raise FitDashError(f"FitDash API {section} returned HTTP {resp.status_code}: {detail}")
    try:
        return resp.json()
    except ValueError:
        raise FitDashError(f"FitDash API {section} returned non-JSON body") from None


def fetch_logs(from_: str | None = None, to: str | None = None,
               transport: httpx.BaseTransport | None = None) -> Any:
    f, t = _check_date("from", from_), _check_date("to", to)
    if f and t:
        if f > t:
            raise FitDashError("from must be on or before to")
        if (t - f).days + 1 > MAX_LOG_DAYS:
            raise FitDashError(f"logs range is at most {MAX_LOG_DAYS} days")
    params = {k: v.isoformat() for k, v in (("from", f), ("to", t)) if v}
    return fetch("logs", params, transport=transport)


def build_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("fitdash")

    @mcp.tool()
    def fitdash_targets() -> dict:
        """FitDash nutrition/training targets (read-only)."""
        return fetch("targets")

    @mcp.tool()
    def fitdash_inventory() -> dict:
        """FitDash food/supplement inventory (read-only)."""
        return fetch("inventory")

    @mcp.tool()
    def fitdash_logs(from_date: str | None = None, to_date: str | None = None) -> dict:
        """FitDash daily logs. Dates YYYY-MM-DD; default last 7 days, max 92 days."""
        return fetch_logs(from_date, to_date)

    @mcp.tool()
    def fitdash_plan() -> dict:
        """FitDash current plan (read-only)."""
        return fetch("plan")

    @mcp.tool()
    def fitdash_training() -> dict:
        """FitDash training state (read-only)."""
        return fetch("training")

    @mcp.tool()
    def fitdash_recovery() -> dict:
        """FitDash recovery state (read-only)."""
        return fetch("recovery")

    return mcp


def main() -> None:
    build_server().run()  # stdio


if __name__ == "__main__":
    main()
