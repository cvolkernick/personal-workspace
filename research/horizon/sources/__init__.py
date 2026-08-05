"""Source adapters for Horizon ingestion."""

from __future__ import annotations

from research.horizon.sources.base import SourceAdapter, SourceEvent
from research.horizon.sources.fixture import FixtureSource
from research.horizon.sources.rss import RssSource

__all__ = [
    "SourceAdapter",
    "SourceEvent",
    "FixtureSource",
    "RssSource",
]
