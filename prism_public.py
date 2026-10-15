"""Shared check for PRISM_PUBLIC_BASE and DAILY_BRIEF_PUBLIC_BASE.

FCC, orchestra, and Daily Brief all import this module. A value is accepted
only when it is https, names a real DNS host, and has no userinfo, port,
query, fragment, or path. DAILY_BRIEF_PUBLIC_BASE may also be that origin
plus the path /horizon.

Unset and invalid values are not replaced with a placeholder origin. Callers
warn on stderr. Long-running servers exit non-zero when systemd set
INVOCATION_ID. The Daily Brief CLI warns and keeps the relative path.
"""

from __future__ import annotations

import ipaddress
import os
import sys
from urllib.parse import urlsplit

_RESERVED_TLDS = frozenset({"example", "invalid", "test", "localhost"})
_HOST_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789.-")
_warned: set[str] = set()


def reset_warnings() -> None:
    """Allow the same warning to print again. Tests use this."""
    _warned.clear()


def _dns_host_ok(host: str) -> bool:
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        return False
    name = host.lower()
    if not name or name.endswith(".") or ".." in name:
        return False
    if any(ch not in _HOST_CHARS for ch in name):
        return False
    try:
        ipaddress.ip_address(name)
        return False
    except ValueError:
        pass
    labels = name.split(".")
    if len(labels) < 2:
        return False
    if any(
        not lab or len(lab) > 63 or lab.startswith("-") or lab.endswith("-")
        for lab in labels
    ):
        return False
    if all(lab.isdigit() for lab in labels):
        return False
    if "example" in labels or "localhost" in labels:
        return False
    if labels[-1] in _RESERVED_TLDS:
        return False
    return True


def parse_public_base(raw: str, *, allow_horizon: bool = False) -> str | None:
    """Return a normalized base, or None when raw is empty or not allowed.

    Trailing slash on the origin is ignored. With allow_horizon, the only
    extra path is /horizon (one trailing slash on that path is ignored).
    Scheme is never inferred: a bare host is rejected.
    """
    text = (raw or "").strip()
    if not text or any(ch.isspace() for ch in text):
        return None
    parts = urlsplit(text)
    if parts.scheme.lower() != "https" or not parts.netloc:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if parts.port is not None or parts.query or parts.fragment:
        return None
    host = parts.hostname
    if not host or not _dns_host_ok(host):
        return None
    origin = "https://" + host.lower()
    path = parts.path or ""
    if path in ("", "/"):
        return origin
    if allow_horizon and path.rstrip("/") == "/horizon":
        return origin + "/horizon"
    return None


def prism_public_origin(environ: dict[str, str] | None = None) -> str:
    """Validated PRISM_PUBLIC_BASE origin, or "" when unset or invalid."""
    env = os.environ if environ is None else environ
    raw = (env.get("PRISM_PUBLIC_BASE") or "").strip()
    if not raw:
        return ""
    return parse_public_base(raw, allow_horizon=False) or ""


_HORIZON_SERVE_PORTS = (443, 8443, 10000)


def horizon_public_origin(environ: dict[str, str] | None = None) -> str:
    """Validated HORIZON_PUBLIC_ORIGIN, or "" when unset or invalid.

    Horizon's own tailnet origin (Tailscale Serve on its own HTTPS port, e.g.
    https://host:10000). When set, FCC redirects /horizon/* there instead of
    proxying, so Horizon installs as its own app. Only https, a real DNS host,
    and a Serve port (443, 8443, 10000) are accepted. No path, query, or
    fragment. A value equal to PRISM_PUBLIC_BASE is refused (redirect loop).
    """
    env = os.environ if environ is None else environ
    text = (env.get("HORIZON_PUBLIC_ORIGIN") or "").strip().rstrip("/")
    if not text or any(ch.isspace() for ch in text):
        return ""
    parts = urlsplit(text)
    if parts.scheme.lower() != "https" or not parts.netloc:
        return ""
    if parts.username is not None or parts.password is not None:
        return ""
    if parts.path or parts.query or parts.fragment:
        return ""
    try:
        port = parts.port
    except ValueError:
        return ""
    if port is not None and port not in _HORIZON_SERVE_PORTS:
        return ""
    host = parts.hostname
    if not host or not _dns_host_ok(host):
        return ""
    origin = "https://" + host.lower()
    if port not in (None, 443):
        origin += f":{port}"
    if origin == prism_public_origin(env):
        return ""
    return origin


def canonical_https_host(environ: dict[str, str] | None = None) -> str:
    """Hostname of the validated origin, or "" when there is none."""
    origin = prism_public_origin(environ)
    if not origin:
        return ""
    return origin.split("://", 1)[-1].split("/", 1)[0].lower()


def _warn(reason: str, env: dict[str, str], *, fatal_under_systemd: bool) -> None:
    message = "ERROR: " + reason
    if message not in _warned:
        _warned.add(message)
        print(message, file=sys.stderr)
    if fatal_under_systemd and (env.get("INVOCATION_ID") or "").strip():
        raise SystemExit(1)


def guard_prism_public_base(
    environ: dict[str, str] | None = None,
    *,
    fatal_under_systemd: bool = True,
) -> str:
    """Validate PRISM_PUBLIC_BASE at process start.

    Returns the origin, or "" after a stderr warning. When fatal_under_systemd
    is true and INVOCATION_ID is set, the warning is followed by SystemExit(1).
    """
    env = os.environ if environ is None else environ
    raw = (env.get("PRISM_PUBLIC_BASE") or "").strip()
    if not raw:
        _warn(
            "PRISM_PUBLIC_BASE is unset. Refusing the placeholder origin. "
            "Set PRISM_PUBLIC_BASE to an https origin with a real host and no path.",
            env,
            fatal_under_systemd=fatal_under_systemd,
        )
        return ""
    origin = parse_public_base(raw, allow_horizon=False)
    if not origin:
        _warn(
            "PRISM_PUBLIC_BASE is invalid. Expected https://host with a real "
            "DNS name and no userinfo, port, path, query, or fragment. "
            "Refusing the placeholder origin.",
            env,
            fatal_under_systemd=fatal_under_systemd,
        )
        return ""
    return origin


def daily_brief_public_base(
    environ: dict[str, str] | None = None,
    *,
    fatal_under_systemd: bool = False,
) -> str | None:
    """Validated Daily Brief prefix, or None when publish should stay relative.

    DAILY_BRIEF_PUBLIC_BASE wins when it is set. An invalid explicit value
    does not fall through to PRISM_PUBLIC_BASE. When the explicit var is
    unset, a valid PRISM_PUBLIC_BASE is used with /horizon appended.
    """
    env = os.environ if environ is None else environ
    explicit = (env.get("DAILY_BRIEF_PUBLIC_BASE") or "").strip()
    if explicit:
        parsed = parse_public_base(explicit, allow_horizon=True)
        if parsed:
            return parsed
        _warn(
            "DAILY_BRIEF_PUBLIC_BASE is invalid. Expected https://host or "
            "https://host/horizon. Not falling through to PRISM_PUBLIC_BASE. "
            "Printing the relative path.",
            env,
            fatal_under_systemd=fatal_under_systemd,
        )
        return None
    raw = (env.get("PRISM_PUBLIC_BASE") or "").strip()
    if not raw:
        _warn(
            "PRISM_PUBLIC_BASE is unset and DAILY_BRIEF_PUBLIC_BASE is unset. "
            "Refusing the placeholder origin. Printing the relative path.",
            env,
            fatal_under_systemd=fatal_under_systemd,
        )
        return None
    origin = parse_public_base(raw, allow_horizon=False)
    if not origin:
        _warn(
            "PRISM_PUBLIC_BASE is invalid. Expected an https origin with a real "
            "host and no path. Refusing the placeholder origin. "
            "Printing the relative path.",
            env,
            fatal_under_systemd=fatal_under_systemd,
        )
        return None
    return origin + "/horizon"
