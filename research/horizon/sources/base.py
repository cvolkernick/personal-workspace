"""Narrow source adapter interface."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass
class SourceEvent:
    id: str
    domain: str
    title: str
    facts: list[str] = field(default_factory=list)
    interpretation: str = ""
    confidence: float = 0.5
    impact: str = "medium"
    tags: list[str] = field(default_factory=list)
    related_domains: list[str] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@runtime_checkable
class SourceAdapter(Protocol):
    name: str

    def fetch(self) -> list[dict[str, Any]]:
        """Return list of event dicts compatible with world_state.apply_events."""
        ...
