"""Canonical FitDash nutrition day: wake → sleep onset (issue #828).

One function (`resolve_nutrition_day` / `list_nutrition_days`) owns "today"
for every nutrition surface. Civil midnight and the sleep-battery eating
window are not day boundaries.

Rules
=====
* A nutrition day opens at actual wake (end of an overnight sleep session)
  and closes at the next overnight sleep *onset*. Food logged at 1:30 AM
  before that onset belongs to the day that began that morning. Food after
  onset belongs to the new day.
* Naps do not flip the day (same overnight classifier as sleep_quest).
* The eating window (wake → empty_at) is a pacing overlay only. Food
  outside the window still counts toward the current waking day.
* Day id is the viewer-local civil date of the wake that opened the day
  (storage / trends axis). Historical rebucketing is expected.

Backstop (explicit — no silent default)
=======================================
When the athlete is still awake and no overnight sleep onset is recorded:

1. ``wake_plus_20h`` — roll a new day at ``last_wake + 20h``.
2. ``next_logged_wake`` — if a later wake exists with no intervening
   overnight sleep, roll at that wake (takes precedence when earlier).
3. ``missing_sleep_civil`` — no sleep/wake data at all: civil midnight
   of the viewer-local day.

Burn for CICO uses the same day ids. Civil-day burned totals are split
across days by hour overlap. The nominal window is ``[wake, next_wake)``
(~24h of TDEE, including the following sleep) so sleep BMR stays on the
day that just closed. ``burn_domains`` clips each window to the next
span's start. An open in-sleep span leaves that sleep on the previous
day when the previous window already runs through the coming wake.
The windows are a partition: one civil day's burn is counted once.
Intake uses ``[wake, sleep_onset)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .sleep_battery import intervals_from_daily_sleep, normalize_intervals
from .sleep_quest import _is_overnight
from .timeutil import local_now, local_tz

# Explicit backstop when still awake with no sleep onset recorded.
WAKE_BACKSTOP_HOURS = 20.0

SOURCE_SLEEP = "sleep_intervals"
SOURCE_BATTERY_WAKE = "sleep_battery_wake"
SOURCE_BACKSTOP_20H = "wake_plus_20h"
SOURCE_NEXT_WAKE = "next_logged_wake"
SOURCE_MISSING_CIVIL = "missing_sleep_civil"


def parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def food_log_as_dict(log: Any) -> Optional[dict]:
    if log is None:
        return None
    if isinstance(log, dict):
        return log
    if hasattr(log, "to_dict"):
        try:
            d = log.to_dict()
            return d if isinstance(d, dict) else None
        except Exception:
            pass
    date = getattr(log, "date", None)
    if not date:
        return None
    return {
        "date": date,
        "time": getattr(log, "time", None),
        "calories": getattr(log, "calories", None),
        "protein_g": getattr(log, "protein_g", None),
        "carbs_g": getattr(log, "carbs_g", None),
        "fat_g": getattr(log, "fat_g", None),
        "name": getattr(log, "name", None),
        "nutrients": getattr(log, "nutrients", None) or {},
        "meal_type": getattr(log, "meal_type", None),
        "serving_label": getattr(log, "serving_label", None),
        "source": getattr(log, "source", None),
    }


def food_log_event_time(
    log: Any, *, default_tz: Optional[timezone] = None
) -> Optional[datetime]:
    """Local civil datetime for a food log (date + HH:MM when present).

    Logs without a time stamp are placed at local noon so full-day rows still
    land inside a typical multi-day wake window.
    """
    d = food_log_as_dict(log)
    if not d:
        return None
    day = str(d.get("date") or "")[:10]
    if len(day) < 10:
        return None
    try:
        y, m, dd = int(day[0:4]), int(day[5:7]), int(day[8:10])
    except ValueError:
        return None
    tz = default_tz if default_tz is not None else local_tz()
    hh, mm = 12, 0
    traw = d.get("time")
    if traw:
        ts = str(traw).strip()
        parts = ts.replace(".", ":").split(":")
        try:
            if len(parts) >= 2:
                hh = int(parts[0])
                mm = int(parts[1])
        except (TypeError, ValueError):
            hh, mm = 12, 0
    try:
        return datetime(y, m, dd, hh, mm, 0, tzinfo=tz)
    except ValueError:
        return None


def _as_local(dt: datetime, tz) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).astimezone(tz)
    return dt.astimezone(tz)


def _day_id(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


@dataclass
class NutritionDaySpan:
    """One wake-to-sleep nutrition day."""

    day_id: str
    start: datetime
    end: datetime
    sleep_onset: Optional[datetime]
    next_wake: Optional[datetime]
    open: bool
    source: str
    backstop: Optional[str] = None

    def contains(self, dt: datetime) -> bool:
        if dt is None:
            return False
        local = _as_local(dt, self.start.tzinfo)
        return self.start <= local < self.end

    @property
    def burn_end(self) -> datetime:
        """Nominal CICO end: next wake when known, else this day's end.

        Not disjoint by itself. ``burn_domains`` clips these windows so
        spans do not share hours.
        """
        if self.next_wake is not None and self.next_wake > self.start:
            return self.next_wake
        return self.end

    def as_dict(self) -> Dict[str, Any]:
        return {
            "day_id": self.day_id,
            "start": self.start.isoformat(timespec="seconds"),
            "end": self.end.isoformat(timespec="seconds"),
            "sleep_onset": (
                self.sleep_onset.isoformat(timespec="seconds")
                if self.sleep_onset
                else None
            ),
            "next_wake": (
                self.next_wake.isoformat(timespec="seconds")
                if self.next_wake
                else None
            ),
            "open": self.open,
            "source": self.source,
            "backstop": self.backstop,
            "clock": "waking_day",
            "wake_backstop_hours": WAKE_BACKSTOP_HOURS,
            "note": (
                "Nutrition day is wake → overnight sleep onset. "
                "Eating window is pacing only. "
                "Awake with no sleep recorded rolls at wake+20h "
                "or the next logged wake; no sleep data uses civil midnight."
            ),
        }


def _overnight_sessions(
    intervals: Sequence[dict], now: datetime
) -> List[Tuple[datetime, datetime]]:
    """Overnight (start, end) pairs. End may be in the future if currently sleeping."""
    tz = now.tzinfo
    out: List[Tuple[datetime, datetime]] = []
    for row in normalize_intervals(list(intervals or [])):
        st = parse_dt(row.get("start"))
        en = parse_dt(row.get("end"))
        if not st or not en or en <= st:
            continue
        st_l = _as_local(st, tz)
        en_l = _as_local(en, tz)
        hours = (en_l - st_l).total_seconds() / 3600.0
        if hours <= 0:
            continue
        if not _is_overnight(st_l, en_l, hours):
            continue
        if st_l > now:
            continue
        out.append((st_l, en_l))
    out.sort(key=lambda pair: pair[0])
    return out


def _append_day(
    days: List[NutritionDaySpan],
    *,
    start: datetime,
    end: datetime,
    sleep_onset: Optional[datetime],
    next_wake: Optional[datetime],
    open: bool,
    source: str,
    backstop: Optional[str] = None,
) -> None:
    if end <= start:
        return
    days.append(
        NutritionDaySpan(
            day_id=_day_id(start),
            start=start,
            end=end,
            sleep_onset=sleep_onset,
            next_wake=next_wake,
            open=open,
            source=source,
            backstop=backstop,
        )
    )


def _open_end(now: datetime, start: datetime) -> datetime:
    """Exclusive end that still contains ``now``."""
    end = now + timedelta(microseconds=1)
    if end <= start:
        return start + timedelta(seconds=1)
    return end


def _fill_open_from_wake(
    days: List[NutritionDaySpan],
    *,
    wake: datetime,
    now: datetime,
    source: str,
) -> None:
    """Open [wake, now] with 20h backstop rolls if still awake."""
    cap = wake + timedelta(hours=WAKE_BACKSTOP_HOURS)
    if now <= cap:
        _append_day(
            days,
            start=wake,
            end=_open_end(now, wake),
            sleep_onset=None,
            next_wake=None,
            open=True,
            source=source,
            backstop=None,
        )
        return
    _append_day(
        days,
        start=wake,
        end=cap,
        sleep_onset=cap,
        next_wake=cap,
        open=False,
        source=source,
        backstop=SOURCE_BACKSTOP_20H,
    )
    cursor = cap
    while cursor <= now:
        nxt_cap = cursor + timedelta(hours=WAKE_BACKSTOP_HOURS)
        if now <= nxt_cap:
            _append_day(
                days,
                start=cursor,
                end=_open_end(now, cursor),
                sleep_onset=None,
                next_wake=None,
                open=True,
                source=SOURCE_BACKSTOP_20H,
                backstop=SOURCE_BACKSTOP_20H,
            )
            return
        _append_day(
            days,
            start=cursor,
            end=nxt_cap,
            sleep_onset=nxt_cap,
            next_wake=nxt_cap,
            open=False,
            source=SOURCE_BACKSTOP_20H,
            backstop=SOURCE_BACKSTOP_20H,
        )
        cursor = nxt_cap


def _merged_sleep_intervals(
    sleep_intervals: Optional[Sequence[dict]],
    daily_sleep: Optional[Sequence[Any]],
    now: datetime,
) -> List[dict]:
    intervals = normalize_intervals(list(sleep_intervals or []))
    if not daily_sleep:
        return intervals
    try:
        daily = intervals_from_daily_sleep(daily_sleep, tz=now.tzinfo, now=now)
    except Exception:
        daily = []
    if not daily:
        return intervals
    return normalize_intervals(list(intervals) + list(daily))


def list_nutrition_days(
    *,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
    sleep_intervals: Optional[Sequence[dict]] = None,
    sleep_battery: Optional[dict] = None,
    daily_sleep: Optional[Sequence[Any]] = None,
) -> List[NutritionDaySpan]:
    """All nutrition days from sleep evidence up to ``now`` (inclusive current)."""
    if now is None or tz_name:
        now = local_now(tz_name, now=now)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone(local_tz(tz_name))
    tz = now.tzinfo

    nights = _overnight_sessions(
        _merged_sleep_intervals(sleep_intervals, daily_sleep, now), now
    )
    days: List[NutritionDaySpan] = []

    if nights:
        for i, (onset, wake) in enumerate(nights):
            next_pair = nights[i + 1] if i + 1 < len(nights) else None
            if wake > now:
                if onset <= now:
                    # Currently in this overnight sleep: new day opened at onset.
                    _append_day(
                        days,
                        start=onset,
                        end=_open_end(now, onset),
                        sleep_onset=None,
                        next_wake=wake,
                        open=True,
                        source=SOURCE_SLEEP,
                    )
                continue
            if next_pair is None:
                _fill_open_from_wake(days, wake=wake, now=now, source=SOURCE_SLEEP)
            else:
                next_onset, next_wake = next_pair
                close_at = next_onset
                backstop = None
                src = SOURCE_SLEEP
                cap = wake + timedelta(hours=WAKE_BACKSTOP_HOURS)
                if next_onset > cap and (next_wake - wake).total_seconds() / 3600.0 > WAKE_BACKSTOP_HOURS:
                    # Gap: no overnight between wakes — roll at next wake if
                    # sooner than 20h, else 20h then remainder until next onset.
                    close_at = min(next_wake, cap) if next_wake <= cap else cap
                    if next_wake <= cap:
                        close_at = next_wake
                        backstop = SOURCE_NEXT_WAKE
                        src = SOURCE_NEXT_WAKE
                    else:
                        close_at = cap
                        backstop = SOURCE_BACKSTOP_20H
                _append_day(
                    days,
                    start=wake,
                    end=close_at,
                    sleep_onset=next_onset if close_at == next_onset else close_at,
                    next_wake=next_wake,
                    open=False,
                    source=src,
                    backstop=backstop,
                )
                if close_at < next_onset and close_at == cap:
                    # Synthetic days in the gap until next overnight onset.
                    cursor = cap
                    while cursor < next_onset:
                        nxt = min(next_onset, cursor + timedelta(hours=WAKE_BACKSTOP_HOURS))
                        _append_day(
                            days,
                            start=cursor,
                            end=nxt,
                            sleep_onset=next_onset if nxt == next_onset else nxt,
                            next_wake=next_wake,
                            open=False,
                            source=SOURCE_BACKSTOP_20H,
                            backstop=SOURCE_BACKSTOP_20H,
                        )
                        cursor = nxt
        if days:
            return days

    bat = sleep_battery if isinstance(sleep_battery, dict) else {}
    wake = parse_dt(bat.get("last_wake_at"))
    if wake is not None:
        wake_l = _as_local(wake, tz)
        if wake_l <= now:
            _fill_open_from_wake(
                days, wake=wake_l, now=now, source=SOURCE_BATTERY_WAKE
            )
            if days:
                return days

    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now if now > start else start + timedelta(seconds=1)
    _append_day(
        days,
        start=start,
        end=end,
        sleep_onset=None,
        next_wake=None,
        open=True,
        source=SOURCE_MISSING_CIVIL,
        backstop=SOURCE_MISSING_CIVIL,
    )
    return days


def resolve_nutrition_day(
    *,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
    sleep_intervals: Optional[Sequence[dict]] = None,
    sleep_battery: Optional[dict] = None,
    daily_sleep: Optional[Sequence[Any]] = None,
) -> NutritionDaySpan:
    """The nutrition day that contains ``now``."""
    if now is None or tz_name:
        now = local_now(tz_name, now=now)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone(local_tz(tz_name))
    days = list_nutrition_days(
        now=now,
        tz_name=tz_name,
        sleep_intervals=sleep_intervals,
        sleep_battery=sleep_battery,
        daily_sleep=daily_sleep,
    )
    for span in reversed(days):
        if span.contains(now) or (span.open and span.start <= now):
            return span
    if days:
        return days[-1]
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return NutritionDaySpan(
        day_id=_day_id(start),
        start=start,
        end=now if now > start else start + timedelta(seconds=1),
        sleep_onset=None,
        next_wake=None,
        open=True,
        source=SOURCE_MISSING_CIVIL,
        backstop=SOURCE_MISSING_CIVIL,
    )


def assign_event_day(
    event_at: Any, days: Sequence[NutritionDaySpan]
) -> Optional[NutritionDaySpan]:
    dt = parse_dt(event_at) if not isinstance(event_at, datetime) else event_at
    if dt is None:
        return None
    for span in days:
        if span.contains(dt):
            return span
    return None


def sum_intake_for_span(
    food_logs: Optional[Sequence[Any]],
    span: NutritionDaySpan,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Sum macros for logs with event time in [span.start, span.end).

    Includes food outside the eating window. cutoff = min(now, span.end)
    so future-dated logs past now are excluded on an open day.
    """
    clock = now if now is not None else span.end
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=span.start.tzinfo)
    else:
        clock = clock.astimezone(span.start.tzinfo)
    cutoff = min(clock, span.end)
    totals = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    count = 0
    nutrient_maps: List[Any] = []
    rows: List[dict] = []
    tz = span.start.tzinfo
    for log in food_logs or []:
        dt = food_log_event_time(log, default_tz=tz)  # type: ignore[arg-type]
        if dt is None:
            continue
        dt = dt.astimezone(tz)
        if dt < span.start or dt >= cutoff:
            continue
        d = food_log_as_dict(log) or {}
        try:
            totals["calories"] += float(d.get("calories") or 0)
            totals["protein_g"] += float(d.get("protein_g") or 0)
            totals["carbs_g"] += float(d.get("carbs_g") or 0)
            totals["fat_g"] += float(d.get("fat_g") or 0)
            count += 1
            nutrient_maps.append(d.get("nutrients"))
            rows.append(d)
        except (TypeError, ValueError):
            continue
    from .nutrition_micros import sum_micros

    micros = sum_micros(nutrient_maps) if count else {}
    out = {
        "calories": round(totals["calories"], 1),
        "protein_g": round(totals["protein_g"], 1),
        "carbs_g": round(totals["carbs_g"], 1),
        "fat_g": round(totals["fat_g"], 1),
        "log_count": count,
        "source": "waking_day_logs" if count else "none",
        "date": span.day_id,
        "window_start": span.start.isoformat(timespec="seconds"),
        "window_end": span.end.isoformat(timespec="seconds"),
        "cutoff": cutoff.isoformat(timespec="seconds"),
        "food_log_count": count,
    }
    if micros:
        out["micros"] = micros
    out["logs"] = rows
    return out


def food_logs_for_span(
    food_logs: Optional[Sequence[Any]], span: NutritionDaySpan
) -> List[dict]:
    tz = span.start.tzinfo
    out: List[dict] = []
    for log in food_logs or []:
        dt = food_log_event_time(log, default_tz=tz)  # type: ignore[arg-type]
        if dt is None:
            continue
        if not span.contains(dt):
            continue
        d = food_log_as_dict(log)
        if d:
            out.append(d)
    return out


def _overlap_hours(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> float:
    lo = max(a0, b0)
    hi = min(a1, b1)
    if hi <= lo:
        return 0.0
    return (hi - lo).total_seconds() / 3600.0


def bucket_intake_by_nutrition_day(
    food_logs: Optional[Sequence[Any]],
    days: Sequence[NutritionDaySpan],
    *,
    nutrition_rollups: Optional[Sequence[Any]] = None,
) -> List[Dict[str, Any]]:
    """Trends intake series keyed by nutrition day_id.

    Timed logs win. A civil rollup fills a day_id only when that day has
    no timed logs (cannot rebucket a midnight-midnight total).
    """
    by_id: Dict[str, Dict[str, Any]] = {}
    for span in days:
        got = sum_intake_for_span(food_logs, span, now=span.end)
        by_id[span.day_id] = {
            "date": span.day_id,
            "calories": got["calories"] if got["log_count"] else None,
            "protein_g": got["protein_g"] if got["log_count"] else None,
            "carbs_g": got["carbs_g"] if got["log_count"] else None,
            "fat_g": got["fat_g"] if got["log_count"] else None,
            "source": got["source"],
            "food_log_count": got["log_count"],
        }
        if got.get("micros"):
            by_id[span.day_id]["micros"] = got["micros"]
    if nutrition_rollups:
        for row in nutrition_rollups:
            if hasattr(row, "to_dict"):
                d = row.to_dict()
            elif isinstance(row, dict):
                d = row
            else:
                d = {
                    "date": getattr(row, "date", None),
                    "calories": getattr(row, "calories", None),
                    "protein_g": getattr(row, "protein_g", None),
                    "carbs_g": getattr(row, "carbs_g", None),
                    "fat_g": getattr(row, "fat_g", None),
                    "source": getattr(row, "source", None),
                }
            day = str(d.get("date") or "")[:10]
            if len(day) < 10:
                continue
            slot = by_id.get(day)
            if slot is None:
                by_id[day] = {
                    "date": day,
                    "calories": d.get("calories"),
                    "protein_g": d.get("protein_g"),
                    "carbs_g": d.get("carbs_g"),
                    "fat_g": d.get("fat_g"),
                    "source": d.get("source") or "daily_rollup",
                    "food_log_count": 0,
                }
                continue
            if int(slot.get("food_log_count") or 0) > 0:
                continue
            if slot.get("calories") is None and d.get("calories") is not None:
                slot["calories"] = d.get("calories")
                slot["protein_g"] = d.get("protein_g")
                slot["carbs_g"] = d.get("carbs_g")
                slot["fat_g"] = d.get("fat_g")
                slot["source"] = d.get("source") or "daily_rollup"
    return [by_id[k] for k in sorted(by_id.keys())]


def _is_open_in_sleep(span: NutritionDaySpan) -> bool:
    """New day opened at overnight onset; that sleep has not ended yet."""
    return bool(
        span.open
        and span.source == SOURCE_SLEEP
        and span.sleep_onset is None
        and span.next_wake is not None
        and span.next_wake > span.start
    )


def burn_domains(
    days: Sequence[NutritionDaySpan],
) -> List[Tuple[datetime, datetime]]:
    """Disjoint half-open burn intervals, one per span, in input order.

    Nominal window is ``[start, burn_end)`` (through the next wake) so
    sleep BMR stays on the day that just closed. Windows are then cut:

    * A later span that is not an open in-sleep span clips this window
      at its start. Sleep-gap synthetics no longer nest inside the real
      span's ``next_wake``.
    * An open in-sleep span yields when the previous window already runs
      through the coming wake, and its interval is empty. With no previous
      window covering that wake, it keeps ``[start, burn_end)`` so the
      hours are not dropped.

    Computed at request time. Stored civil-day burn rows are not rewritten.
    """
    spans = list(days)
    n = len(spans)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: (spans[i].start, i))
    placed: List[Optional[Tuple[datetime, datetime]]] = [None] * n
    prev_end: Optional[datetime] = None
    for pos, idx in enumerate(order):
        span = spans[idx]
        nxt = spans[order[pos + 1]] if pos + 1 < n else None
        if (
            _is_open_in_sleep(span)
            and prev_end is not None
            and span.next_wake is not None
            and prev_end >= span.next_wake
        ):
            wake = span.next_wake
            placed[idx] = (wake, wake)
            continue
        end = span.burn_end
        if end < span.start:
            end = span.start
        if nxt is not None and not _is_open_in_sleep(nxt) and nxt.start < end:
            end = nxt.start
        if end < span.start:
            end = span.start
        placed[idx] = (span.start, end)
        prev_end = end
    return [
        placed[i] if placed[i] is not None else (spans[i].start, spans[i].start)
        for i in range(n)
    ]


def bucket_burn_by_nutrition_day(
    burned: Optional[Sequence[Any]],
    days: Sequence[NutritionDaySpan],
) -> List[Dict[str, Any]]:
    """Split civil-day burned kcal onto nutrition days by hour overlap.

    Domains come from ``burn_domains`` and do not overlap, so the fractions
    of one civil day sum to the share of that day the spans cover. Sleep
    BMR stays on the day that closed at that night's onset. One row per
    nutrition day id: shared ids are already summed in the accumulator.
    """
    if not days:
        return []
    tz = days[0].start.tzinfo
    civil: List[Tuple[datetime, datetime, float, str]] = []
    for row in burned or []:
        if hasattr(row, "to_dict"):
            d = row.to_dict()
        elif isinstance(row, dict):
            d = row
        else:
            d = {
                "date": getattr(row, "date", None),
                "calories": getattr(row, "calories", None),
                "source": getattr(row, "source", None),
            }
        day = str(d.get("date") or "")[:10]
        if len(day) < 10:
            continue
        try:
            y, m, dd = int(day[0:4]), int(day[5:7]), int(day[8:10])
            start = datetime(y, m, dd, 0, 0, 0, tzinfo=tz)
        except ValueError:
            continue
        try:
            kcal = float(d.get("calories") or 0)
        except (TypeError, ValueError):
            continue
        civil.append(
            (start, start + timedelta(hours=24), kcal, str(d.get("source") or "google_health"))
        )
    domains = burn_domains(days)
    acc: Dict[str, float] = {span.day_id: 0.0 for span in days}
    hit: Dict[str, bool] = {span.day_id: False for span in days}
    for c0, c1, kcal, _src in civil:
        for span, (d0, d1) in zip(days, domains):
            hours = _overlap_hours(c0, c1, d0, d1)
            if hours <= 0:
                continue
            acc[span.day_id] += kcal * (hours / 24.0)
            hit[span.day_id] = True
    # One row per day id. The accumulator already summed disjoint spans.
    # Emitting once per span and adding again double-counts a shared id.
    return [
        {
            "date": day_id,
            "calories": round(acc[day_id], 1),
            "source": "waking_day_split",
        }
        for day_id in sorted(acc)
        if hit.get(day_id)
    ]


def burned_for_span(
    burned: Optional[Sequence[Any]],
    span: NutritionDaySpan,
) -> Optional[float]:
    """Burn for one span with no neighbor context.

    Prefer ``bucket_burn_by_nutrition_day`` on the full day list. A lone
    in-sleep span cannot see that the previous day already owns the sleep.
    """
    rows = bucket_burn_by_nutrition_day(burned, [span])
    if not rows:
        return None
    try:
        return float(rows[0]["calories"])
    except (TypeError, ValueError, KeyError):
        return None


def compose_nutrition_today(
    *,
    now: Optional[datetime] = None,
    tz_name: Optional[str] = None,
    sleep_intervals: Optional[Sequence[dict]] = None,
    sleep_battery: Optional[dict] = None,
    daily_sleep: Optional[Sequence[Any]] = None,
    food_logs: Optional[Sequence[Any]] = None,
    nutrition_rollups: Optional[Sequence[Any]] = None,
    calories_burned: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    """Single composition used by dashboard, planner, bars, and trends."""
    span = resolve_nutrition_day(
        now=now,
        tz_name=tz_name,
        sleep_intervals=sleep_intervals,
        sleep_battery=sleep_battery,
        daily_sleep=daily_sleep,
    )
    days = list_nutrition_days(
        now=now,
        tz_name=tz_name,
        sleep_intervals=sleep_intervals,
        sleep_battery=sleep_battery,
        daily_sleep=daily_sleep,
    )
    intake = sum_intake_for_span(food_logs, span, now=now)
    logs_today = food_logs_for_span(food_logs, span)
    if intake["log_count"] <= 0 and nutrition_rollups:
        # Honest fallback: civil rollup for this day_id only when the span
        # does not cross midnight (otherwise the rollup is the wrong clock).
        same_civil = span.start.date() == (span.end - timedelta(seconds=1)).date()
        if same_civil:
            for row in nutrition_rollups:
                date = str(getattr(row, "date", None) or (row.get("date") if isinstance(row, dict) else "") or "")[:10]
                if date != span.day_id:
                    continue
                def _g(obj: Any, key: str) -> Any:
                    if isinstance(obj, dict):
                        return obj.get(key)
                    return getattr(obj, key, None)

                intake["calories"] = float(_g(row, "calories") or 0)
                intake["protein_g"] = float(_g(row, "protein_g") or 0)
                intake["carbs_g"] = float(_g(row, "carbs_g") or 0)
                intake["fat_g"] = float(_g(row, "fat_g") or 0)
                intake["source"] = "daily_rollup"
                break
    trends_in = bucket_intake_by_nutrition_day(
        food_logs, days, nutrition_rollups=nutrition_rollups
    )
    trends_out = bucket_burn_by_nutrition_day(calories_burned, days)
    # Same partition as the chart. Isolated burned_for_span would recount
    # sleep onto an open in-sleep span.
    burned_today = None
    for row in trends_out:
        if row.get("date") != span.day_id:
            continue
        try:
            burned_today = float(row["calories"])
        except (TypeError, ValueError):
            burned_today = None
        break
    consumed = {
        "calories": round(float(intake.get("calories") or 0), 1),
        "protein_g": round(float(intake.get("protein_g") or 0), 1),
        "carbs_g": round(float(intake.get("carbs_g") or 0), 1),
        "fat_g": round(float(intake.get("fat_g") or 0), 1),
        "date": span.day_id,
        "source": intake.get("source") or "none",
        "food_log_count": int(intake.get("log_count") or 0),
    }
    if intake.get("micros"):
        consumed["micros"] = intake["micros"]
    return {
        "nutrition_day": span.as_dict(),
        "span": span,
        "days": days,
        "today_consumed": consumed,
        "food_logs_today": logs_today,
        "calories_burned_today": burned_today,
        "trends_nutrition": trends_in,
        "trends_calories_burned": trends_out,
    }
