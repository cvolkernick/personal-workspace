"""Daily cardio quest from wearable Active Zone Minutes.

Prescription only — does not log a PPL session, write next_session_type,
or mint an ex-* lift leaf. Target is the median of recent AZM days, with
a walk / Zone 2 cut on rest, deload, or low recovery. Missing AZM is 0
today and is ignored in the median (not treated as a rest day).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

KIND_KEY = "cardio|azm"
SLUG = "azm"
GROUP = "cardio"

DEFAULT_AZM_TARGET = 20
AZM_LOOKBACK_DAYS = 14
AZM_TARGET_FLOOR = 10
AZM_TARGET_CAP = 45
EASY_MULT = 0.5
EASY_FLOOR = 10
EASY_CAP = 20

STANDARD_TITLE_PREFIX = "Cardio"
EASY_TITLE_PREFIX = "Walk · Zone 2"

MOTIVATION = (
    "AZM is the cardio prescription. Hit the day's minutes from the wearable; "
    "rest, deload, and low recovery drop this to a walk / Zone 2 — not HIIT "
    "and not a skip."
)


def _civil_day(raw: Any) -> str:
    return str(raw or "")[:10]


def _parse_day(raw: Any) -> Optional[datetime]:
    text = _civil_day(raw)
    if len(text) != 10:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None


def _row_dict(row: Any) -> Optional[dict]:
    if row is None:
        return None
    if hasattr(row, "to_dict"):
        try:
            row = row.to_dict()
        except Exception:  # noqa: BLE001
            return None
    if not isinstance(row, dict):
        return None
    day = _civil_day(row.get("date"))
    if not _parse_day(day):
        return None
    out = {"date": day}
    if "total_minutes" in row:
        out["total_minutes"] = row.get("total_minutes")
    return out


def azm_days(raw: Optional[Sequence[Any]]) -> List[dict]:
    out: List[dict] = []
    for row in raw or []:
        item = _row_dict(row)
        if item:
            out.append(item)
    return out


def azm_total(row: Any) -> Optional[float]:
    item = _row_dict(row) or (row if isinstance(row, dict) else None)
    if not isinstance(item, dict) or "total_minutes" not in item:
        return None
    raw = item.get("total_minutes")
    if raw is None or raw == "":
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def today_azm_minutes(days: Sequence[Any], as_of: str) -> float:
    """Today's wearable AZM. Missing day → 0, never invented from steps/kcal."""
    want = _civil_day(as_of)
    for row in azm_days(days):
        if row.get("date") == want:
            total = azm_total(row)
            return float(total) if total is not None else 0.0
    return 0.0


def recent_azm_minutes(
    days: Sequence[Any],
    *,
    as_of: str,
    lookback: int = AZM_LOOKBACK_DAYS,
) -> List[float]:
    """Present AZM totals in (as_of − lookback, as_of). Today is excluded.

    Missing days stay missing. Logged 0 is a real rest day and stays in the
    median. A 413 hike day is one sample — median, not mean, so it cannot
    set the daily quest.
    """
    end = _parse_day(as_of)
    if end is None:
        return []
    start = end - timedelta(days=max(1, int(lookback)))
    by_day: Dict[str, float] = {}
    for row in azm_days(days):
        day = _parse_day(row.get("date"))
        if day is None or day < start or day >= end:
            continue
        total = azm_total(row)
        if total is None:
            continue
        by_day[row["date"]] = total
    return [by_day[k] for k in sorted(by_day)]


def _median(values: Sequence[float]) -> float:
    ordered = sorted(float(v) for v in values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def cardio_target_minutes(
    recent: Sequence[float],
    *,
    easy: bool = False,
) -> int:
    """Minutes for today's quest. Capped so one outlier cannot become the Rx."""
    if recent:
        raw = _median(recent)
    else:
        raw = float(DEFAULT_AZM_TARGET)
    baseline = int(round(min(AZM_TARGET_CAP, max(AZM_TARGET_FLOOR, raw))))
    if not easy:
        return baseline
    return int(round(min(EASY_CAP, max(EASY_FLOOR, baseline * EASY_MULT))))


def is_easy_cardio(today: Optional[dict]) -> bool:
    """Rest / deload / low recovery → walk / Zone 2. Never a silent skip."""
    board = today if isinstance(today, dict) else {}
    rec = str(board.get("recommendation") or "").strip().lower()
    if rec in ("rest", "easy"):
        return True
    workout = board.get("workout") if isinstance(board.get("workout"), dict) else {}
    if workout.get("is_rest_day"):
        return True
    rec_w = str(workout.get("recommendation") or "").strip().lower()
    if rec_w in ("rest", "easy"):
        return True
    recovery = board.get("recovery") if isinstance(board.get("recovery"), dict) else {}
    label = str(recovery.get("label") or "").strip()
    if label in ("Needs Rest", "Caution"):
        return True
    try:
        score = float(recovery.get("score"))
    except (TypeError, ValueError):
        score = None
    if score is not None and score < 50:
        return True
    cont = (
        workout.get("training_continuity")
        if isinstance(workout.get("training_continuity"), dict)
        else {}
    )
    try:
        vol = float(cont.get("volume_band_scale"))
    except (TypeError, ValueError):
        vol = 1.0
    if vol < 1.0:
        return True
    phase = str(cont.get("phase") or "").strip().lower()
    if phase in ("deload", "return", "reentry", "restart"):
        return True
    return False


def cardio_title(*, current: int, target: int, easy: bool = False) -> str:
    cur = max(0, int(current))
    tgt = max(1, int(target))
    prefix = EASY_TITLE_PREFIX if easy else STANDARD_TITLE_PREFIX
    return f"{prefix} — {cur} / {tgt} AZM"


def _azm_from_board(today: dict) -> List[dict]:
    board = today if isinstance(today, dict) else {}
    cardio = board.get("cardio") if isinstance(board.get("cardio"), dict) else {}
    for key in ("active_zone_minutes",):
        raw = board.get(key)
        days = azm_days(raw if isinstance(raw, (list, tuple)) else None)
        if days:
            return days
    nested = cardio.get("days")
    days = azm_days(nested if isinstance(nested, (list, tuple)) else None)
    if days:
        return days
    health = board.get("health") if isinstance(board.get("health"), dict) else {}
    return azm_days(health.get("active_zone_minutes"))


def cardio_spec(
    today: Optional[dict] = None,
    *,
    azm: Optional[Sequence[Any]] = None,
    as_of: Optional[str] = None,
    easy: Optional[bool] = None,
) -> Dict[str, Any]:
    """Stable cardio|azm prescription for this civil day."""
    board = today if isinstance(today, dict) else {}
    day = _civil_day(as_of or board.get("date"))
    days = azm_days(azm) if azm is not None else _azm_from_board(board)
    easy_flag = is_easy_cardio(board) if easy is None else bool(easy)
    recent = recent_azm_minutes(days, as_of=day) if day else []
    target = cardio_target_minutes(recent, easy=easy_flag)
    current = int(round(today_azm_minutes(days, day))) if day else 0
    hit = current >= target
    return {
        "kind": KIND_KEY,
        "slug": SLUG,
        "group": GROUP,
        "date": day or None,
        "current_minutes": current,
        "target_minutes": target,
        "easy": easy_flag,
        "mode": "easy" if easy_flag else "standard",
        "hit": hit,
        "title": cardio_title(current=current, target=target, easy=easy_flag),
        "motivation": MOTIVATION,
        "days": days,
    }
