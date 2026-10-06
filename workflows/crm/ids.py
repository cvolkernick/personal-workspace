"""ULID strings. 48-bit time plus 80 bits of randomness, Crockford base32."""

from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid() -> str:
    value = ((int(time.time() * 1000) & ((1 << 48) - 1)) << 80) | int.from_bytes(
        os.urandom(10), "big"
    )
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))
