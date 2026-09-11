#!/usr/bin/env python3
"""Secret / credential path + content refuse rules for agent-context backup (#580 E1).

Never copy Secure Vault contents, wallet data, ``.ssh/`` private keys, or
credential files into the backup remote. Used by backup *and* restore so a
poisoned remote cannot land secrets on a fresh VM.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Path refuse (basename / relative path). Keep aligned with deploy/b2-puller/paths.py
# plus the E1 list: vault, wallet, ssh keys, credential files.
_REFUSE_NAME_RE = re.compile(
    r"""
    (
        (^|/)(\.env(\..+)?|.*\.env)$
      | (^|/)secrets\.json$
      | (^|/)mcp_credentials\.json$
      | (^|/)auth\.json$
      | (^|/)token$
      | (^|/).*\.pem$
      | (^|/)id_rsa
      | (^|/)id_ed25519
      | (^|/)id_ecdsa
      | (^|/)id_dsa
      | (^|/).ssh/
      | (^|/)credentials(/|$)
      | credential
      | token
      | api[_-]?secret
      | secret_key
      | private[_-]?key
      | cdp_api_key
      | coinbase[_-]?secret
      | robinhood[_-]?token
      | robinhood[_-]?secret
      | ynab[/._-]?token
      | (^|/)jobs\.json(\.bak.*)?$
      | (^|/)wallet\.dat$
      | \.wallet$
      | (^|/)wallets(/|$)
      | (^|/)keystore(/|$)
      | (^|/)secure[_-]?vault(/|$)
      | (^|/)vault(/|$)
      | (^|/)mnemonic
      | (^|/)seed\.txt$
      | (^|/)nsec
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

_SKIP_DIR_NAMES = {
    ".git",
    ".ssh",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    "credentials",
    "secure-vault",
    "secure_vault",
    "wallets",
    "keystore",
}

_PRIVATE_KEY_MARKERS = (
    b"BEGIN OPENSSH PRIVATE KEY",
    b"BEGIN RSA PRIVATE KEY",
    b"BEGIN EC PRIVATE KEY",
    b"BEGIN ENCRYPTED PRIVATE KEY",
    b"BEGIN DSA PRIVATE KEY",
)

_NSEC_RE = re.compile(rb"\bnsec1[a-z0-9]{20,}\b")


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
    return bool(_REFUSE_NAME_RE.search(_norm(path)))


def is_skip_dir(name: str) -> bool:
    return name.lower() in _SKIP_DIR_NAMES


def sniff_secret_bytes(data: bytes) -> str | None:
    """Return a reason if file *contents* look like a private key / nsec."""
    head = data[:8192]
    for marker in _PRIVATE_KEY_MARKERS:
        if marker in head:
            return f"refuse: private-key PEM ({marker.decode('ascii', 'replace')})"
    if _NSEC_RE.search(head):
        return "refuse: nsec private key material"
    return None


def classify_path(path: str | os.PathLike[str]) -> Classify:
    n = _norm(path)
    if is_refused_name(n):
        return Classify(n, False, "refuse: secret / credential / wallet / vault / ssh key")
    return Classify(n, True, "allow")


def classify_file(path: Path, *, root: Path | None = None) -> Classify:
    rel = path
    if root is not None:
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = path
    c = classify_path(rel)
    if not c.allowed:
        return c
    if not path.is_file():
        return c
    try:
        data = path.read_bytes()
    except OSError as exc:
        return Classify(_norm(rel), False, f"refuse: unreadable ({exc})")
    reason = sniff_secret_bytes(data)
    if reason:
        return Classify(_norm(rel), False, reason)
    return Classify(_norm(rel), True, "allow")


def walk_allowed(src: Path) -> Iterable[tuple[Path, Classify]]:
    """Yield (path, classify) for files under src. Directories on the skip list are not descended."""
    if not src.exists():
        return
    if src.is_file():
        yield src, classify_file(src, root=src.parent)
        return
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if not is_skip_dir(d)]
        base = Path(dirpath)
        for name in filenames:
            p = base / name
            yield p, classify_file(p, root=src)
