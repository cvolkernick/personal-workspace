"""Typed record returned by every source adapter."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass
class SourceRecord:
    source_id: str
    source_name: str
    source_url: str
    as_of: str | None
    payload: dict[str, Any] | None
    raw_sha256: str | None
    unavailable: str | None

    @property
    def ok(self) -> bool:
        return self.unavailable is None and self.payload is not None


def unavailable(source_id: str, source_name: str, source_url: str, reason: str) -> SourceRecord:
    return SourceRecord(source_id, source_name, source_url, None, None, None, reason)


def ok_record(
    source_id: str,
    source_name: str,
    source_url: str,
    as_of: str | None,
    payload: dict[str, Any],
    raw: bytes,
) -> SourceRecord:
    return SourceRecord(
        source_id,
        source_name,
        source_url,
        as_of,
        payload,
        hashlib.sha256(raw).hexdigest(),
        None,
    )
