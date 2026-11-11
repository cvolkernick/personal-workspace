"""Staged priority-volume ramp for lats, then chest, then quads.

The weekly ceiling for the active muscle is last Mon–Sun week's logged
hard-set credits plus one or two, and never a jump to the destination in
one step. Other major muscles stay in the 4–8 band. This does not use
``focus_muscles`` and does not drop them to maintenance.

Credits come from ``credit_sets_for_exercise`` via ``weekly_set_tally``.
That formula is not modified here.
"""

from __future__ import annotations

import copy
import logging
import re
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence

_LOG = logging.getLogger(__name__)

# Standing coaching only. Match the cue sentence, not the bare words
# "lowering" or "tempo" (those show up in "lowering volume" and notes).
_CUE_RE = re.compile(
    "|".join(
        (
            r"2\s*[\u2010-\u2015\-]\s*3\s*s(?:ec(?:ond)?s?)?(?!\w)",
            r"lower(?:ing)? the weight",
            r"controlled lower",
            r"extra pause at the bottom",
            r"\beccentric\s+tempo\b",
            r"\btempo\s+cue\b",
            r"\blowering\s+cue\b",
            r"\bcontrolled\s+eccentric\b",
        )
    ),
    re.IGNORECASE,
)

# Stale pre-ramp sentence. Replaced at read time when the equal ramp is live.
_FRAMEWORK_48_RE = re.compile(
    r"Framework:\s*≈?\s*4\s*[\u2010-\u2015\-]\s*8\s*sets/muscle/week",
    re.IGNORECASE,
)

_SESSION_CAP_RE = re.compile(r"^Session cap ~\d+\.?$")

# Logged credits at or above this gap under the ceiling count as finished.
_NEAR = 0.5

# Tile group → planner muscle. The lists are the same after the split.

# Previous balanced max. A muscle already near 10 rolls back toward this.
_OLD_WEEKLY_MAX = 8.0


def _day(value: Any) -> Optional[date]:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def week_monday(value: Any) -> Optional[date]:
    day = _day(value)
    if day is None:
        return None
    return day - timedelta(days=day.weekday())


def prior_sunday(value: Any) -> Optional[str]:
    monday = week_monday(value)
    if monday is None:
        return None
    return (monday - timedelta(days=1)).isoformat()


def ramp_week_number(as_of: Any, started_on: Any) -> int:
    """1-based Mon–Sun week index from ``priority_ramp_started_on``."""
    start = week_monday(started_on)
    current = week_monday(as_of)
    if start is None or current is None:
        return 1
    return max(1, (current - start).days // 7 + 1)


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if value is None:
        return default
    return bool(value)


def ramp_configured(goals: Optional[dict]) -> bool:
    raw = goals or {}
    if not _as_bool(raw.get("volume_ramp_equal"), False):
        return False
    return bool(str(raw.get("volume_ramp_started_on") or "")[:10])


def ramp_is_live(goals: Optional[dict], as_of: Any) -> bool:
    """True once ``as_of`` is on or after the configured Monday."""
    raw = goals or {}
    if not ramp_configured(raw):
        return False
    started = str(raw.get("volume_ramp_started_on") or "")[:10]
    day = str(as_of or "")[:10]
    return bool(day) and day >= started


def _in_cut(phase: Optional[str]) -> bool:
    # Omitted phase follows the approved cut. A named non-cut phase does not.
    if phase is None or str(phase).strip() == "":
        return True
    return str(phase).strip().lower() in ("cut", "recomp")


def _bad_lifts(status: Optional[str], in_cut: bool) -> bool:
    return bool(in_cut) and str(status or "").strip().lower() in ("yellow", "red")


def _band(staged: float, floor: float) -> Dict[str, float]:
    hi = float(staged)
    lo = max(float(floor), hi - 2.0)
    if lo > hi:
        lo = hi
    return {"min": lo, "max": hi, "priority": False}


def _plain(lo: float, hi: float) -> Dict[str, float]:
    return {"min": float(lo), "max": float(hi), "priority": False}


def _planner():
    from . import workout_planner as wp

    return wp


def _coerce_sessions(sessions: Optional[Sequence[Any]]) -> list:
    """Dict logs become Session rows so ``weekly_set_tally`` can read them."""
    from .models import ExerciseEntry, Session, SetEntry

    out = []
    for raw in sessions or []:
        if not isinstance(raw, dict):
            out.append(raw)
            continue
        exercises = []
        for ex in raw.get("exercises") or []:
            if not isinstance(ex, dict):
                exercises.append(ex)
                continue
            sets = []
            for st in ex.get("sets") or []:
                if not isinstance(st, dict):
                    continue
                try:
                    n_sets = int(st.get("sets") or 0)
                except (TypeError, ValueError):
                    n_sets = 0
                try:
                    reps = int(st.get("reps") or 0)
                except (TypeError, ValueError):
                    reps = 0
                weight = st.get("weight_lbs")
                try:
                    weight_f = float(weight) if weight is not None and weight != "" else 0.0
                except (TypeError, ValueError):
                    weight_f = 0.0
                sets.append(SetEntry(weight_lbs=weight_f, sets=n_sets, reps=reps))
            exercises.append(ExerciseEntry(name=str(ex.get("name") or ""), sets=sets))
        out.append(
            Session(
                date=str(raw.get("date") or "")[:10],
                session_type=str(raw.get("session_type") or ""),
                exercises=exercises,
                notes=str(raw.get("notes") or ""),
            )
        )
    return out


def _tally_credits(
    sessions: Optional[Sequence[Any]],
    catalog: Optional[dict],
    *,
    as_of: str,
    window_days: int,
    secondary_fraction: float,
) -> Dict[str, float]:
    wp = _planner()
    tally = wp.weekly_set_tally(
        _coerce_sessions(sessions),
        catalog or {"exercises": []},
        as_of=as_of,
        window_days=max(1, int(window_days)),
        secondary_fraction=float(secondary_fraction),
    )
    return {str(k): float(v) for k, v in (tally.get("by_muscle") or {}).items()}


def prior_week_credits(
    sessions: Optional[Sequence[Any]],
    catalog: Optional[dict],
    as_of: Any,
    secondary_fraction: float = 0.5,
) -> Dict[str, float]:
    """Hard-set credits for the previous America/New_York Mon–Sun week."""
    end = prior_sunday(as_of)
    if not end:
        return {}
    return _tally_credits(
        sessions,
        catalog,
        as_of=end,
        window_days=7,
        secondary_fraction=secondary_fraction,
    )


def this_week_credits(
    sessions: Optional[Sequence[Any]],
    catalog: Optional[dict],
    as_of: Any,
    secondary_fraction: float = 0.5,
) -> Dict[str, float]:
    """Hard-set credits from this week's Monday through ``as_of``."""
    day = _day(as_of)
    monday = week_monday(as_of)
    if day is None or monday is None:
        return {}
    window = (day - monday).days + 1
    return _tally_credits(
        sessions,
        catalog,
        as_of=day.isoformat(),
        window_days=window,
        secondary_fraction=secondary_fraction,
    )


def session_flags_restore(session: Any) -> bool:
    if isinstance(session, dict):
        notes = str(session.get("notes") or "")
        kind = str(session.get("session_type") or "")
        flagged = bool(
            session.get("deload")
            or session.get("deload_week")
            or session.get("recovery_week")
        )
    else:
        notes = str(getattr(session, "notes", "") or "")
        kind = str(getattr(session, "session_type", "") or "")
        flagged = bool(
            getattr(session, "deload", False)
            or getattr(session, "deload_week", False)
            or getattr(session, "recovery_week", False)
        )
    blob = f"{notes} {kind}".lower()
    return flagged or "deload" in blob or "recovery week" in blob


def _sessions_flag_restore(sessions: Optional[Sequence[Any]], start: date, end: date) -> bool:
    for session in sessions or []:
        raw = session.get("date") if isinstance(session, dict) else getattr(session, "date", "")
        day = _day(raw)
        if day is None or day < start or day > end:
            continue
        if session_flags_restore(session):
            return True
    return False


def prior_week_restore(sessions: Optional[Sequence[Any]], as_of: Any) -> bool:
    monday = week_monday(as_of)
    if monday is None:
        return False
    return _sessions_flag_restore(sessions, monday - timedelta(days=7), monday - timedelta(days=1))


def this_week_restore(sessions: Optional[Sequence[Any]], as_of: Any) -> bool:
    """A deload or recovery-week log already in this Mon–Sun week."""
    monday = week_monday(as_of)
    day = _day(as_of)
    if monday is None or day is None:
        return False
    return _sessions_flag_restore(sessions, monday, day)


def lifts_chip_status(sessions: Optional[Sequence[Any]], goals: Optional[dict], as_of: Any) -> str:
    day = _day(as_of)
    if day is None:
        return "insufficient"
    try:
        from datetime import datetime

        from .guardrails import _lift_tile

        tile = _lift_tile(list(sessions or []), goals or {}, datetime(day.year, day.month, day.day))
        return str((tile or {}).get("status") or "insufficient")
    except Exception:  # noqa: BLE001 - a missing chip must not blank the plan
        return "insufficient"


def collect_ramp_signals(
    goals: Optional[dict],
    sessions: Optional[Sequence[Any]],
    *,
    as_of: Optional[str] = None,
    recovery_label: Optional[str] = None,
    deload: bool = False,
    lifts_status: Optional[str] = None,
    prior_lifts_status: Optional[str] = None,
    restore_flag: Optional[bool] = None,
    prior_restore_flag: Optional[bool] = None,
) -> Dict[str, Any]:
    """Lifts chip and Restore flags, using the fallback planner's rules.

    Explicit overrides win. When a value is omitted and the ramp is live,
    the current and prior Mon–Sun weeks are read from the logs.
    """
    wp = _planner()
    normalized = wp.normalize_goals(goals)
    if as_of is None:
        from .timeutil import local_today_iso

        day = local_today_iso()
    else:
        day = str(as_of)[:10]
    chip = lifts_status
    prior_chip = prior_lifts_status
    restore_now = bool(restore_flag) if restore_flag is not None else False
    restore_prior = bool(prior_restore_flag) if prior_restore_flag is not None else False
    if ramp_is_live(normalized, day):
        if lifts_status is None:
            chip = lifts_chip_status(sessions, goals, day)
        if prior_lifts_status is None:
            sunday = prior_sunday(day)
            prior_chip = (
                lifts_chip_status(sessions, goals, sunday) if sunday else "insufficient"
            )
        if restore_flag is None:
            restore_now = (
                bool(deload)
                or goals_flag_restore(goals, recovery_label)
                or this_week_restore(sessions, day)
            )
        if prior_restore_flag is None:
            restore_prior = prior_week_restore(sessions, day)
    return {
        "lifts_status": chip,
        "prior_lifts_status": prior_chip,
        "restore_flag": bool(restore_now),
        "prior_restore_flag": bool(restore_prior),
    }


def goals_flag_restore(goals: Optional[dict], recovery_label: Optional[str] = None) -> bool:
    raw = goals or {}
    if raw.get("deload") or raw.get("deload_week") or raw.get("recovery_week"):
        return True
    label = str(recovery_label or "").lower()
    return "deload" in label or "recovery week" in label


def resolve_priority_ramp(
    goals: Optional[dict],
    *,
    as_of: Optional[str] = None,
    prior_credits: Optional[Dict[str, float]] = None,
    lifts_status: Optional[str] = None,
    prior_lifts_status: Optional[str] = None,
    restore_flag: bool = False,
    prior_restore_flag: bool = False,
    phase: Optional[str] = None,
    session_cap: Optional[int] = None,
) -> Dict[str, Any]:
    """Staged bands for one Mon–Sun week. ``live`` is false before the start date."""
    wp = _planner()
    raw = dict(goals or {})
    balanced_only = bool(raw.get("_balanced_bands_only"))
    normalized = wp.normalize_goals(raw)
    if as_of is None:
        from .timeutil import local_today_iso

        day = local_today_iso()
    else:
        day = str(as_of)[:10]
    if balanced_only or not ramp_is_live(normalized, day):
        return {"live": False, "as_of": day, "targets": {}}

    lo = float(normalized.get("sets_per_muscle_week_min") or 4)
    ceiling = float(
        normalized.get("volume_sets_target")
        or normalized.get("sets_per_muscle_week_priority_max")
        or 10
    )
    try:
        increment = int(normalized.get("volume_ramp_increment") or 2)
    except (TypeError, ValueError):
        increment = 2
    increment = 1 if increment <= 1 else 2
    prior = {
        wp.normalize_muscle(k): float(v or 0)
        for k, v in (prior_credits or {}).items()
    }
    in_cut = _in_cut(phase)
    current_bad = _bad_lifts(lifts_status, in_cut) or bool(restore_flag)
    prior_bad = _bad_lifts(prior_lifts_status, in_cut) or bool(prior_restore_flag)
    week = ramp_week_number(day, normalized.get("volume_ramp_started_on"))
    # Week 1 starts on approval. A yellow week after that holds the next week too.
    rollback = current_bad if week <= 1 else (current_bad or prior_bad)

    def reached(muscle: str) -> bool:
        return float(prior.get(muscle) or 0) + 1e-9 >= ceiling - _NEAR

    targets: Dict[str, Dict[str, float]] = {}
    for muscle in wp.MAJOR_MUSCLES:
        if rollback and reached(muscle):
            staged = _OLD_WEEKLY_MAX
        elif rollback:
            staged = float(prior.get(muscle) or 0)
        elif reached(muscle):
            staged = ceiling
        else:
            staged = min(ceiling, float(prior.get(muscle) or 0) + increment)
        targets[muscle] = _band(staged, lo)

    cap = int(session_cap or normalized.get("session_working_set_cap") or 14)
    today_line = _today_line(ceiling=ceiling, cap=cap, rollback=rollback)
    return {
        "live": True,
        "equal": True,
        "rollback": rollback,
        "as_of": day,
        "week": week,
        "increment": increment,
        "ceiling": ceiling,
        "targets": targets,
        "today_line": today_line,
        "session_cap": cap,
    }


def _today_line(*, ceiling: float, cap: int, rollback: bool) -> str:
    dest = int(round(ceiling))
    if rollback:
        return (
            f"Volume: ramp paused; major-muscle targets rolled back. Session cap ~{cap}."
        )
    return (
        f"Volume: ramping major muscles toward ~{dest} hard sets/week "
        f"(at most +1–2 credits each from last week’s logged sets). "
        f"Session cap ~{cap}."
    )


def ramp_public(ramp: Optional[dict]) -> Dict[str, Any]:
    ramp = ramp or {}
    if not ramp.get("live"):
        return {"live": False}
    targets = ramp.get("targets") or {}
    return {
        "live": True,
        "equal": True,
        "rollback": bool(ramp.get("rollback")),
        "week": ramp.get("week"),
        "increment": ramp.get("increment"),
        "ceiling": ramp.get("ceiling"),
        "today_line": ramp.get("today_line") or "",
        "targets": {
            m: {"min": band["min"], "max": band["max"], "priority": bool(band.get("priority"))}
            for m, band in targets.items()
            if isinstance(band, dict)
        },
    }


def _fmt_band(band: Dict[str, float]) -> str:
    lo = float(band.get("min") or 0)
    hi = float(band.get("max") or 0)
    if abs(lo - hi) < 0.05:
        return f"{hi:g}"
    return f"{lo:g}-{hi:g}"


def group_target_bands(
    goals: Optional[dict],
    sessions: Optional[Sequence[Any]],
    catalog: Optional[dict],
    *,
    as_of: Optional[str] = None,
    recovery_label: Optional[str] = None,
    deload: bool = False,
) -> Dict[str, Dict[str, float]]:
    """Equal-ramp min/max for every major group. Empty when the ramp is off.

    Same lifts-chip and Restore rollback as the planner. Lats and upper
    back are separate bands.
    """
    wp = _planner()
    normalized = wp.normalize_goals(goals)
    if as_of is None:
        from .timeutil import local_today_iso

        day = local_today_iso()
    else:
        day = str(as_of)[:10]
    if not ramp_is_live(normalized, day):
        return {}
    fraction = float(normalized.get("secondary_set_fraction") or 0.5)
    prior = prior_week_credits(sessions, catalog, day, fraction)
    signals = collect_ramp_signals(
        goals,
        sessions,
        as_of=day,
        recovery_label=recovery_label,
        deload=deload,
    )
    ramp = resolve_priority_ramp(
        normalized,
        as_of=day,
        prior_credits=prior,
        lifts_status=signals["lifts_status"],
        prior_lifts_status=signals["prior_lifts_status"],
        restore_flag=signals["restore_flag"],
        prior_restore_flag=signals["prior_restore_flag"],
    )
    if not ramp.get("live"):
        return {}
    targets = ramp.get("targets") or {}
    out: Dict[str, Dict[str, float]] = {}
    for muscle in wp.MAJOR_MUSCLES:
        band = targets.get(muscle)
        if isinstance(band, dict):
            out[muscle] = band
    return out


def display_targets(
    goals: Optional[dict],
    sessions: Optional[Sequence[Any]],
    catalog: Optional[dict],
    *,
    as_of: Optional[str] = None,
    recovery_label: Optional[str] = None,
    deload: bool = False,
) -> Dict[str, str]:
    """String bands for the Trends tile. Empty when the ramp is not live."""
    return {
        group: _fmt_band(band)
        for group, band in group_target_bands(
            goals,
            sessions,
            catalog,
            as_of=as_of,
            recovery_label=recovery_label,
            deload=deload,
        ).items()
    }


def _lower_ceiling(*bands: Optional[Dict[str, float]]) -> Optional[Dict[str, float]]:
    present = [band for band in bands if isinstance(band, dict)]
    if not present:
        return None
    return min(present, key=lambda band: float(band.get("max") or 0))


def prompt_volume_fields(
    goals: Optional[dict],
    sessions: Optional[Sequence[Any]] = None,
    catalog: Optional[dict] = None,
    *,
    as_of: Optional[str] = None,
    lifts_status: Optional[str] = None,
    prior_lifts_status: Optional[str] = None,
    restore_flag: bool = False,
    prior_restore_flag: bool = False,
    phase: Optional[str] = None,
) -> Dict[str, Any]:
    """Per-muscle targets and the workout-message rules for the model planner."""
    wp = _planner()
    normalized = wp.normalize_goals(goals)
    if as_of is None:
        from .timeutil import local_today_iso

        day = local_today_iso()
    else:
        day = str(as_of)[:10]
    fraction = float(normalized.get("secondary_set_fraction") or 0.5)
    prior: Dict[str, float] = {}
    if ramp_is_live(normalized, day):
        prior = prior_week_credits(sessions, catalog, day, fraction)
    ramp = resolve_priority_ramp(
        normalized,
        as_of=day,
        prior_credits=prior,
        lifts_status=lifts_status,
        prior_lifts_status=prior_lifts_status,
        restore_flag=restore_flag,
        prior_restore_flag=prior_restore_flag,
        phase=phase,
        session_cap=normalized.get("session_working_set_cap"),
    )
    if ramp.get("live"):
        targets = ramp.get("targets") or {}
        summary = ramp.get("today_line") or ""
    else:
        targets = wp.muscle_targets({**normalized, "_balanced_bands_only": True}, as_of=day)
        summary = "4-8 hard sets per major muscle per week"
    muscle_targets = {
        m: {
            "min": float(band["min"]),
            "max": float(band["max"]),
            "priority": bool(band.get("priority")),
        }
        for m, band in targets.items()
        if isinstance(band, dict)
    }
    clause = (
        f"{summary} Per-muscle weekly targets are in volume_caps.muscle_targets."
    )
    return {
        "sets_per_muscle_week": summary,
        "muscle_targets": muscle_targets,
        "volume_line": ramp.get("today_line") if ramp.get("live") else None,
        "volume_clause": clause,
        "ramp": ramp_public(ramp),
    }


def _is_ramp_sentence(sentence: str) -> bool:
    text = sentence.strip()
    return (
        text.startswith("Priority volume:")
        or text.startswith("Volume: ramping")
        or text.startswith("Volume: ramp paused")
        or _SESSION_CAP_RE.match(text) is not None
    )


def _without_priority_line(text: str) -> str:
    """Drop any Priority volume sentence so the computed line is the one shown."""
    kept: List[str] = []
    for chunk in re.split(r"\s*·\s*", text or ""):
        sentences = re.split(r"(?<=[.!?])\s+", chunk)
        piece = " ".join(
            sentence.strip()
            for sentence in sentences
            if sentence.strip() and not _is_ramp_sentence(sentence)
        )
        if piece:
            kept.append(piece)
    return " · ".join(kept)


def _split_sentences(text: str) -> List[str]:
    return re.split(r"(?<=[.!?])\s+", text or "")


def _join_kept(text: str, drop) -> str:
    """Drop sentences ``drop`` rejects. ``·`` chunks stay separate."""
    kept: List[str] = []
    for chunk in re.split(r"\s*·\s*", text or ""):
        piece = " ".join(
            sentence.strip()
            for sentence in _split_sentences(chunk)
            if sentence.strip() and not drop(sentence)
        )
        if piece:
            kept.append(piece)
    return " · ".join(kept)


def _is_cue_sentence(sentence: str) -> bool:
    return _CUE_RE.search(sentence) is not None


def _without_cue(text: str) -> str:
    """Drop lowering/tempo cue sentences. Bare 'lowering' and 'tempo' stay."""
    return _join_kept(text, _is_cue_sentence)


def _is_stale_framework(sentence: str) -> bool:
    return _FRAMEWORK_48_RE.search(sentence) is not None


def _without_stale_framework(text: str) -> str:
    """Drop 'Framework: ≈4–8 sets/muscle/week' once the equal ramp is live."""
    return _join_kept(text, _is_stale_framework)


def _has_volume_line(text: str) -> bool:
    return "Volume: ramping" in (text or "") or "Volume: ramp paused" in (text or "")


def ensure_workout_message(message: Optional[str], ramp: Optional[dict]) -> str:
    """Put the computed volume line on the workout message.

    A model sentence that still says the ramp is climbing is replaced by the
    rollback line when the lifts chip or Restore says to pause. A lowering
    or tempo cue is standing coaching only, so that sentence is dropped.
    When the ramp is live, the stale 'Framework: ≈4–8 sets/muscle/week'
    sentence is dropped so the computed line is the one shown.
    """
    text = _without_cue(str(message or "").strip())
    ramp = ramp or {}
    line = str(ramp.get("today_line") or "").strip()
    if ramp.get("live") and line:
        text = _without_stale_framework(text)
        text = _without_priority_line(text)
        text = f"{text} · {line}".strip(" ·") if text else line
    return text


_SERVE_GOALS_CACHE: Dict[str, Any] = {"key": None, "goals": None}


def _goals_mtime_key() -> tuple:
    """(path, mtime_ns) of the goals file the planner reads. ('default', None) if missing."""
    from .workout_planner import GOALS_PATH
    from .workout_store import _workspace_file_candidates

    for path in _workspace_file_candidates(GOALS_PATH):
        try:
            if not path.is_file():
                continue
            return (str(path), path.stat().st_mtime_ns)
        except OSError:
            continue
    return ("default", None)


def _load_serve_goals() -> dict:
    """Workspace goals, re-read only when the file mtime changes.

    The cache key also includes the loader object so a test patch of
    ``load_workspace_goals`` cannot keep a previous test's dict.
    """
    from .workout_store import load_workspace_goals

    key = (_goals_mtime_key(), id(load_workspace_goals))
    cached = _SERVE_GOALS_CACHE
    if cached.get("key") == key and isinstance(cached.get("goals"), dict):
        return copy.deepcopy(cached["goals"])
    try:
        loaded, _src = load_workspace_goals()
    except Exception:  # noqa: BLE001 - a missing file must not blank Today
        return {}
    goals = loaded if isinstance(loaded, dict) else {}
    _SERVE_GOALS_CACHE["key"] = key
    _SERVE_GOALS_CACHE["goals"] = copy.deepcopy(goals)
    return copy.deepcopy(goals)


def _goals_for_serve(goals: Optional[dict]) -> dict:
    """Ramp flags follow workspace goals. Other fields stay on the caller's dict.

    A stored payload can still say the ramp is off. Liveness uses the same
    file the planner reads. The caller's dict is not written.
    """
    workspace = _load_serve_goals()
    if not isinstance(goals, dict):
        return workspace
    merged = dict(goals)
    for key, value in workspace.items():
        if str(key).startswith("volume_ramp_"):
            merged[key] = value
    return merged


def _recovery_fields(recovery: Any) -> tuple:
    if isinstance(recovery, dict):
        label = recovery.get("label")
        deload = bool(
            recovery.get("deload")
            or recovery.get("deload_week")
            or recovery.get("recovery_week")
        )
        return label, deload
    if recovery is None:
        return None, False
    return getattr(recovery, "label", None), bool(getattr(recovery, "deload", False))


def _serve_ramp(
    goals: dict,
    sessions: Optional[Sequence[Any]],
    *,
    as_of: str,
    recovery: Any,
    phase: Optional[str],
) -> dict:
    """Same rollback rule as the planner. The line does not use prior credits."""
    label, deload = _recovery_fields(recovery)
    signals = collect_ramp_signals(
        goals,
        sessions,
        as_of=as_of,
        recovery_label=label,
        deload=deload,
    )
    return resolve_priority_ramp(
        goals,
        as_of=as_of,
        lifts_status=signals["lifts_status"],
        prior_lifts_status=signals["prior_lifts_status"],
        restore_flag=signals["restore_flag"],
        prior_restore_flag=signals["prior_restore_flag"],
        phase=phase,
        session_cap=(goals or {}).get("session_working_set_cap"),
    )


def _without_replaced_volume_copy(text: str) -> str:
    """Drop the stale 4–8 framework sentence and any old priority-volume line."""
    text = _without_stale_framework(text)
    return _join_kept(
        text, lambda sentence: sentence.strip().startswith("Priority volume:")
    )


def present_served_workout(
    plan: Optional[dict],
    *,
    goals: Optional[dict] = None,
    sessions: Optional[Sequence[Any]] = None,
    as_of: Optional[str] = None,
    recovery: Any = None,
    phase: Optional[str] = None,
) -> Optional[dict]:
    """Read-time workout message. Does not write the stored plan.

    Strips the standing-coaching cue from a saved or stamped message.
    ``volume_ramp_*`` liveness comes from workspace goals (the file the
    planner reads), so a stored payload that still has the ramp off cannot
    hide the line. Other goal fields stay on the dict the caller passed.
    When the equal ramp is live, the stale 4–8 framework sentence is
    replaced by ``today_line``. If that computation raises, the cue is
    still stripped and the framework sentence is left as saved.
    """
    if not isinstance(plan, dict) or not plan:
        return plan
    raw = plan.get("message")
    if raw is None or not str(raw).strip():
        return plan
    text = str(raw).strip()
    has_cue = _CUE_RE.search(text) is not None
    has_framework = _FRAMEWORK_48_RE.search(text) is not None
    if goals is None and not has_cue and not has_framework:
        return plan
    resolved = _goals_for_serve(goals)
    day = str(as_of or plan.get("date") or "")[:10]
    live = bool(day) and ramp_is_live(resolved, day)
    stripped = _without_cue(text)
    if live and _has_volume_line(stripped):
        stripped = _without_replaced_volume_copy(stripped)
    elif live:
        try:
            ramp = _serve_ramp(
                resolved, sessions, as_of=day, recovery=recovery, phase=phase
            )
        except Exception:  # noqa: BLE001 - a ramp miss must not fail Today
            _LOG.exception(
                "serve ramp failed; cue stripped, framework sentence left in place"
            )
        else:
            line = str(ramp.get("today_line") or "").strip()
            if ramp.get("live") and line:
                stripped = _without_replaced_volume_copy(stripped)
                stripped = f"{stripped} · {line}".strip(" ·") if stripped else line
            else:
                # Workspace flags say the ramp is live, but the computed
                # ramp is not live or has no line. Keep the Framework
                # sentence and still drop the stored cue.
                stripped = _without_cue(text)
    if stripped == text:
        return plan
    out = dict(plan)
    out["message"] = stripped
    return out


def _sets_of(row: dict) -> int:
    rx = row.get("prescription") if isinstance(row.get("prescription"), dict) else {}
    try:
        return int(rx.get("sets") or 0)
    except (TypeError, ValueError):
        return 0


def _set_sets(row: dict, n: int) -> None:
    rx = dict(row.get("prescription") or {})
    rx["sets"] = int(n)
    row["prescription"] = rx


def _refresh_credits(row: dict, secondary_fraction: float) -> None:
    wp = _planner()
    credits = wp.credit_sets_for_exercise(
        row.get("primary_muscles") or [],
        row.get("secondary_muscles") or [],
        _sets_of(row),
        secondary_fraction=secondary_fraction,
    )
    row["set_credits"] = {k: round(v, 2) for k, v in credits.items()}


def _planned_from(rows: Sequence[dict], secondary_fraction: float) -> Dict[str, float]:
    planned: Dict[str, float] = {}
    for row in rows:
        _refresh_credits(row, secondary_fraction)
        credits = row.get("set_credits") or {}
        for muscle, credit in credits.items():
            planned[muscle] = planned.get(muscle, 0.0) + float(credit)
    return planned


def _compounds_first(rows: Sequence[dict]) -> List[dict]:
    compounds = [r for r in rows if str(r.get("movement") or "") == "compound"]
    rest = [r for r in rows if str(r.get("movement") or "") != "compound"]
    return compounds + rest


def _marginal_credits(row: dict, secondary_fraction: float) -> Dict[str, float]:
    wp = _planner()
    return wp.credit_sets_for_exercise(
        row.get("primary_muscles") or [],
        row.get("secondary_muscles") or [],
        1,
        secondary_fraction=secondary_fraction,
    )


def _primaries(row: dict) -> set:
    wp = _planner()
    return {wp.normalize_muscle(m) for m in (row.get("primary_muscles") or [])}


def _add_score(
    row: dict,
    gaps: Dict[str, float],
    session_muscles: Sequence[str],
    secondary_fraction: float,
) -> Optional[tuple]:
    """Higher is better. None means this +1 set should not be added."""
    credits = _marginal_credits(row, secondary_fraction)
    prim = _primaries(row)
    helped: List[str] = []
    for muscle, credit in credits.items():
        if credit <= 1e-9:
            continue
        gap = float(gaps.get(muscle) or 0)
        if muscle in prim and gap + 0.05 < float(credit):
            return None
        if gap >= 0.49:
            helped.append(muscle)
    if not helped:
        return None
    session = set(session_muscles or [])
    session_gaps = [float(gaps[m]) for m in helped if m in session]
    furthest = max(session_gaps) if session_gaps else 0.0
    return (len(helped), furthest, sum(float(gaps[m]) for m in helped))


# One lift does not keep taking the ramp's extra sets. Four is the cap.
LIFT_SET_CAP = 4


def bump_equal_sets(
    plan_ex: Sequence[dict],
    *,
    targets: Dict[str, Dict[str, float]],
    logged: Optional[Dict[str, float]] = None,
    session_muscles: Sequence[str] = (),
    secondary_fraction: float = 0.5,
    session_cap: int = 14,
    max_exercises: Optional[int] = None,
    new_rows: Optional[Callable[[Sequence[dict]], Sequence[dict]]] = None,
) -> Dict[str, Any]:
    """Add sets toward each muscle's ceiling without a named priority.

    Prefer an existing compound that helps the most below-target muscles.
    Ties go to the session muscle furthest below its target. A new compound
    is added when no current compound can take a set. If the exercise list
    is already full, that compound takes the lowest-priority isolation's
    slot. A compound is never removed. No lift grows past four sets. The
    session cap stops the add; isolation set counts are not trimmed to feed
    another muscle.
    """
    rows: List[dict] = [dict(r) for r in plan_ex or []]
    for row in rows:
        if isinstance(row.get("prescription"), dict):
            row["prescription"] = dict(row["prescription"])
    logged = logged or {}
    cap = max(0, int(session_cap))
    added = 0
    note = "Volume ramp: +1 hard set."

    def total_sets() -> int:
        return sum(max(0, _sets_of(row)) for row in rows)

    def current_gaps() -> Dict[str, float]:
        planned = _planned_from(rows, secondary_fraction)
        gaps: Dict[str, float] = {}
        for muscle, band in (targets or {}).items():
            ceiling = float((band or {}).get("max") or 0)
            have = float(logged.get(muscle) or 0) + float(planned.get(muscle) or 0)
            gaps[muscle] = ceiling - have
        return gaps

    while total_sets() + 1 <= cap:
        gaps = current_gaps()
        best_i: Optional[int] = None
        best_score: Optional[tuple] = None
        for i, row in enumerate(rows):
            if str(row.get("movement") or "") != "compound":
                continue
            if _sets_of(row) >= LIFT_SET_CAP:
                continue
            score = _add_score(row, gaps, session_muscles, secondary_fraction)
            if score is None:
                continue
            if best_score is None or score > best_score:
                best_score = score
                best_i = i
        if best_i is not None:
            _set_sets(rows[best_i], _sets_of(rows[best_i]) + 1)
            rationale = str(rows[best_i].get("rationale") or "")
            if note not in rationale:
                rows[best_i]["rationale"] = f"{rationale} {note}".strip()
            added += 1
            continue
        if new_rows is None:
            break
        fresh_best: Optional[dict] = None
        fresh_score: Optional[tuple] = None
        for candidate in new_rows(rows) or []:
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("movement") or "") != "compound":
                continue
            score = _add_score(candidate, gaps, session_muscles, secondary_fraction)
            if score is None:
                continue
            if fresh_score is None or score > fresh_score:
                fresh_score = score
                fresh_best = candidate
        if fresh_best is None:
            break
        fresh = dict(fresh_best)
        if isinstance(fresh.get("prescription"), dict):
            fresh["prescription"] = dict(fresh["prescription"])
        if _sets_of(fresh) < 1:
            _set_sets(fresh, 1)
        if _sets_of(fresh) > LIFT_SET_CAP:
            _set_sets(fresh, LIFT_SET_CAP)
        # Compounds are added before an isolation keeps the last slot.
        # A full session gives the new compound an isolation's place.
        # A compound already in the session is never removed.
        if max_exercises is not None and len(rows) >= int(max_exercises):
            isolations = [
                i for i, row in enumerate(rows)
                if str(row.get("movement") or "") != "compound"
            ]
            if not isolations:
                break

            def _prio(index: int) -> int:
                try:
                    return int(rows[index].get("priority") or 0)
                except (TypeError, ValueError):
                    return 0

            # Lowest priority, then the later slot (the extra isolation).
            victim = min(isolations, key=lambda index: (_prio(index), -index))
            if total_sets() - _sets_of(rows[victim]) + _sets_of(fresh) > cap:
                break
            rows.pop(victim)
        elif total_sets() + _sets_of(fresh) > cap:
            break
        rationale = str(fresh.get("rationale") or "")
        if note not in rationale:
            fresh["rationale"] = f"{rationale} {note}".strip()
        insert_at = 0
        for i, row in enumerate(rows):
            if str(row.get("movement") or "") == "compound":
                insert_at = i + 1
        rows.insert(insert_at, fresh)
        added += _sets_of(fresh)
    rows = _compounds_first(rows)
    planned = _planned_from(rows, secondary_fraction)
    return {
        "exercises": rows,
        "planned_credits": planned,
        "session_sets": sum(max(0, _sets_of(row)) for row in rows),
        "added_sets": added,
    }
