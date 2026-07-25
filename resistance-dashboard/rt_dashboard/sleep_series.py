"""Calendar-complete sleep series: unlogged nights count as zero sleep.

Intent: no Google Health / Fitbit sample for a night usually means no sleep
(or equivalent sleep debt). Missing nights are material for recovery and
must pull averages down — not be omitted from charts or 7d stats.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence

from .models import SleepSample


def _parse_day(d: str) -> Optional[datetime]:
    try:
        return datetime.strptime(d[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def expand_sleep_calendar(
    sleep: Sequence[SleepSample],
    *,
    as_of: str,
    window_days: int = 90,
    fill_hours: float = 0.0,
    fill_source: str = "implied_zero",
) -> List[SleepSample]:
    """Return one sample per civil day in ``[as_of - (window-1), as_of]``.

    Days with no logged sleep get ``sleep_hours=fill_hours`` (default 0) and
    ``source=fill_source`` so charts/averages treat them as real zero nights.
    When multiple logs exist for one date, hours are summed (same as parsers).
    """
    end = _parse_day(as_of)
    if not end:
        return list(sleep)
    window_days = max(1, int(window_days))

    by_date: Dict[str, float] = {}
    source_by: Dict[str, str] = {}
    for s in sleep or []:
        d = (s.date or "")[:10]
        if not d:
            continue
        by_date[d] = by_date.get(d, 0.0) + float(s.sleep_hours or 0.0)
        # Prefer real device source over implied for that night
        if s.source and s.source != fill_source:
            source_by[d] = s.source
        elif d not in source_by:
            source_by[d] = s.source or fill_source

    out: List[SleepSample] = []
    for i in range(window_days - 1, -1, -1):
        day = (end - timedelta(days=i)).strftime("%Y-%m-%d")
        if day in by_date:
            out.append(
                SleepSample(
                    date=day,
                    sleep_hours=round(by_date[day], 2),
                    efficiency_pct=None,
                    source=source_by.get(day, "google_health"),
                )
            )
        else:
            out.append(
                SleepSample(
                    date=day,
                    sleep_hours=float(fill_hours),
                    efficiency_pct=None,
                    source=fill_source,
                )
            )
    return out


def calendar_avg_sleep_hours(
    sleep: Sequence[SleepSample],
    *,
    as_of: str,
    days: int = 7,
) -> Optional[float]:
    """Mean sleep over the last ``days`` civil days (missing = 0)."""
    if days <= 0:
        return None
    filled = expand_sleep_calendar(sleep, as_of=as_of, window_days=days)
    if not filled:
        return None
    return round(sum(s.sleep_hours for s in filled) / len(filled), 2)
