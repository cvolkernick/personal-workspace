#!/usr/bin/env python3
"""Allowlist + refuse rules for the finley→prism puller.

The puller runs ON finley-gateway (role b2-puller) and PULLS from
prism-gateway (role app-books). It writes only allowlisted paths into
the pull dest on finley. Venue keys and any dest that would put raw
treasury on Vercel or a Mac are live-blocked.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

# Relative to the prism user home unless noted.
# youtube-groom and nest-published live on the app box, not this monorepo.
# Caps scorecard only: ops/YOUTUBE_QUEUE.md (writer stays on Pi).
DEFAULT_PRISM_HOME = "/home/prism-agent"
DEFAULT_PRISM_WORKSPACE = "/home/prism-agent/personal-workspace"
DEFAULT_PULL_DEST = "/home/finley-agent/b2-pulls/prism"
DEFAULT_GRAPH_PATH = "/home/finley-agent/B2"

# Host / role tags (hostnames stay MagicDNS names).
PRISM_HOSTNAME = "prism-gateway"
FINLEY_HOSTNAME = "finley-gateway"
ROLE_APP_BOOKS = "app-books"
ROLE_B2_PULLER = "b2-puller"

# PULSE LOCK — one timer, one job, this cadence only.
# No prism self-backup. No units-only timer. No off-site clock. No replica.
PULL_TZ = "America/New_York"
PULL_ONCALENDAR = "*-*-* *:20:00 America/New_York"
PULL_TIMER_UNIT = "b2-puller.timer"
PULL_SERVICE_UNIT = "b2-puller.service"
PULSE_LOCK = "PULSE LOCK"

# Book / restore allowlist (paths relative to prism $HOME).
# Snapshots only — not treasury/config.json (account numbers + venue wiring).
PULL_RELATIVE: tuple[str, ...] = (
    "personal-workspace/treasury/snapshots/",
    "personal-workspace/financial-command/treasury_latest.json",
    "youtube-groom/state.json",
    "youtube-groom/never_readd",
    "youtube-groom/groom.log",
    ".config/systemd/user/",
    ".buzz/published/",
    "nest-published/",
)

# Live-block: venue keys, tokens, env dumps, Vercel treasury env, Mac homes.
# robinhood_latest.json is a book snapshot (allow). *token* / *secret* files are not.
_REFUSE_NAME_RE = re.compile(
    r"""
    (
        (^|/)(\.env|.*\.env)$
      | (^|/)secrets\.json$
      | workflow-scheduler\.env
      | (^|/)token$
      | ynab/token
      | (^|/).*\.pem$
      | (^|/)id_rsa
      | (^|/)id_ed25519
      | credential
      | credentials
      | api[_-]?key
      | apikey
      | api[_-]?secret
      | secret_key
      | private[_-]?key
      | fcc_treasury_json
      | coinbase[_-]?key
      | coinbase[_-]?secret
      | robinhood[_-]?token
      | robinhood[_-]?secret
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Destinations that must never receive a pull (raw treasury leak).
_REFUSE_DEST_RE = re.compile(
    r"""
    (
        /users/
      | (^|/)vercel
      | \.vercel
      | fcc_treasury_json
      | /library/application\ support/com\.vercel
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class Classify:
    path: str
    allowed: bool
    reason: str


def _norm(path: str | os.PathLike[str]) -> str:
    raw = str(path).strip().replace("\\", "/")
    if raw.startswith("~/"):
        raw = raw[2:]
    return raw.lstrip("/")


def is_refused_name(path: str | os.PathLike[str]) -> bool:
    """True when the path looks like a venue key / token / env / Vercel treasury."""
    return bool(_REFUSE_NAME_RE.search(_norm(path)))


def is_refused_dest(path: str | os.PathLike[str]) -> bool:
    """True when writing here would put raw treasury on Vercel or a Mac."""
    n = _norm(path)
    low = n.lower()
    if low.startswith("users/") or low == "users" or "/users/" in f"/{low}":
        return True
    return bool(_REFUSE_DEST_RE.search(n))


def classify_source(path: str | os.PathLike[str]) -> Classify:
    n = _norm(path)
    if is_refused_name(n):
        return Classify(n, False, "refuse: venue key / token / env / FCC_TREASURY_JSON")
    for rel in PULL_RELATIVE:
        rel_n = _norm(rel)
        if n == rel_n.rstrip("/") or n.startswith(rel_n):
            return Classify(n, True, "allow: pull list")
        # Allow workspace-relative forms used in tests / local fixtures.
        if rel_n.startswith("personal-workspace/"):
            tail = rel_n[len("personal-workspace/") :]
            if tail and (n == tail.rstrip("/") or n.startswith(tail)):
                return Classify(n, True, "allow: pull list")
    return Classify(n, False, "refuse: not on pull list")


def classify_dest(path: str | os.PathLike[str]) -> Classify:
    n = _norm(path)
    if is_refused_dest(n):
        return Classify(n, False, "refuse: dest is Vercel, Mac, or FCC_TREASURY_JSON")
    if is_refused_name(n):
        return Classify(n, False, "refuse: dest basename is a venue key path")
    return Classify(n, True, "allow: finley pull dest")


def assert_pull_list_clean(paths: Optional[Iterable[str]] = None) -> list[str]:
    """Raise if the configured (or given) pull list contains a refused path."""
    bad: list[str] = []
    for p in paths if paths is not None else PULL_RELATIVE:
        if is_refused_name(p):
            bad.append(_norm(p))
    if bad:
        raise RuntimeError(
            "pull list contains refused venue-key / Vercel paths: " + ", ".join(bad)
        )
    return list(paths if paths is not None else PULL_RELATIVE)


def pull_sources(*, prism_home: str = DEFAULT_PRISM_HOME) -> list[str]:
    """Absolute source paths on prism (missing sources are skipped at pull time)."""
    assert_pull_list_clean()
    home = Path(prism_home)
    return [str(home / rel) for rel in PULL_RELATIVE]


def dest_for_source(rel: str, dest_root: str | os.PathLike[str]) -> Path:
    """Map a pull-list relative path to a dest path under dest_root."""
    rel_n = _norm(rel)
    if rel_n.startswith("personal-workspace/"):
        rel_n = rel_n[len("personal-workspace/") :]
    dest = Path(dest_root) / rel_n
    c = classify_dest(dest)
    if not c.allowed:
        raise RuntimeError(c.reason + f" ({dest})")
    return dest
