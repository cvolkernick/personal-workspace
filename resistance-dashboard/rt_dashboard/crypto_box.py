"""Authenticated encryption for FitDash at-rest data (stdlib-only).

Uses HMAC-SHA256 keystream + encrypt-then-MAC. Master key from
``FITDASH_MASTER_KEY`` (urlsafe base64 32 bytes) or auto-generated
``~/.config/resistance-dashboard/master.key``.

Ciphertext is only unwrapped while handling an authenticated request;
unauthenticated handlers never call decrypt.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
from pathlib import Path
from typing import Optional, Union

BytesLike = Union[bytes, str]

_KEY: Optional[bytes] = None


def master_key_path() -> Path:
    env = (os.environ.get("FITDASH_MASTER_KEY_FILE") or "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "resistance-dashboard" / "master.key"


def load_or_create_master_key() -> bytes:
    global _KEY
    if _KEY is not None:
        return _KEY
    env = (os.environ.get("FITDASH_MASTER_KEY") or "").strip()
    if env:
        try:
            raw = base64.urlsafe_b64decode(env + "==")
        except Exception:
            raw = hashlib.sha256(env.encode("utf-8")).digest()
        if len(raw) < 32:
            raw = hashlib.sha256(raw).digest()
        _KEY = raw[:32]
        return _KEY
    path = master_key_path()
    if path.is_file():
        raw = path.read_bytes().strip()
        try:
            key = base64.urlsafe_b64decode(raw + b"==")
        except Exception:
            key = hashlib.sha256(raw).digest()
        _KEY = key[:32] if len(key) >= 32 else hashlib.sha256(key).digest()
        return _KEY
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    path.write_text(base64.urlsafe_b64encode(key).decode("ascii") + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    _KEY = key
    return _KEY


def _as_bytes(v: BytesLike) -> bytes:
    if isinstance(v, bytes):
        return v
    return str(v).encode("utf-8")


def _keystream(key: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < n:
        out.extend(hmac.new(key, nonce + struct.pack(">I", i), hashlib.sha256).digest())
        i += 1
    return bytes(out[:n])


def seal(plaintext: BytesLike, *, aad: BytesLike = b"", key: Optional[bytes] = None) -> str:
    """Encrypt + authenticate. Returns urlsafe base64 token."""
    key = key or load_or_create_master_key()
    pt = _as_bytes(plaintext)
    ad = _as_bytes(aad)
    nonce = secrets.token_bytes(16)
    ct = bytes(a ^ b for a, b in zip(pt, _keystream(key, nonce, len(pt))))
    tag = hmac.new(key, ad + nonce + ct, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(nonce + tag + ct).decode("ascii")


def open_box(token: str, *, aad: BytesLike = b"", key: Optional[bytes] = None) -> bytes:
    """Decrypt token; raises ValueError if tampered."""
    key = key or load_or_create_master_key()
    ad = _as_bytes(aad)
    raw = base64.urlsafe_b64decode(token.encode("ascii") + b"==")
    if len(raw) < 32:
        raise ValueError("ciphertext too short")
    nonce, tag, ct = raw[:16], raw[16:32], raw[32:]
    expect = hmac.new(key, ad + nonce + ct, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(tag, expect):
        raise ValueError("authentication failed")
    return bytes(a ^ b for a, b in zip(ct, _keystream(key, nonce, len(ct))))


def seal_str(plaintext: str, *, aad: BytesLike = b"") -> str:
    return seal(plaintext, aad=aad)


def open_str(token: str, *, aad: BytesLike = b"") -> str:
    return open_box(token, aad=aad).decode("utf-8")
