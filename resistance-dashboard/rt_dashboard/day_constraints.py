"""Fit day constraint packet for Orchestra (P3-F).

Pure export of existing coach / recovery / meal signals — no second heuristics.
Writes ``fitness/data/day_constraints.json`` so ``orchestra.collect_fitness`` can
read without live FitDash HTTP.

Product freeze: PLANS/ORCHESTRATOR_UNITARY_DAILY_PLANNER.md §Fit constraint packet.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from .timeutil import local_now_iso, local_today_iso, local_tz

SCHEMA_VERSION = 1
DEFAULT_DEEP_LINK = "http://127.0.0.1:8787/"
SLEEP_BATTERY_MAX_AGE_HOURS = 2.0
SLEEP_OK_HOURS = 7.0


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def protein_gap_band(
    *,
    protein_target_g: Optional[float],
    protein_remaining_g: Optional[float],
    protein_consumed_g: Optional[float] = None,
    protein_adherence_7d_pct: Optional[float] = None,
    has_today_protein_log: bool = False,
    same_civil_day: bool = True,
) -> str:
    """Map today protein state to freeze bands: ok | watch | gap | unknown."""
    if not same_civil_day:
        return "unknown"
    tgt = _as_float(protein_target_g)
    if tgt is None or tgt <= 0:
        return "unknown"

    rem = _as_float(protein_remaining_g)
    consumed = _as_float(protein_consumed_g)
    if rem is None and consumed is not None:
        rem = max(0.0, tgt - consumed)
    if rem is None and not has_today_protein_log:
        # No today log — optional 7d fallback for gap severity only
        pct = _as_float(protein_adherence_7d_pct)
        if pct is not None and pct < 50.0:
            return "gap"
        return "unknown"

    if rem is None:
        rem = 0.0
    rem = max(0.0, rem)
    if consumed is None:
        consumed = max(0.0, tgt - rem)

    # ok: consumed ≥ 0.85 × target OR remaining ≤ 0.15 × target
    if consumed >= 0.85 * tgt or rem <= 0.15 * tgt:
        return "ok"
    # watch: remaining in (0.15, 0.40] × target
    if rem <= 0.40 * tgt:
        return "watch"
    # gap: remaining > 0.40 × target
    return "gap"


def _session_logged_today(sessions: Sequence[Any], civil_day: str) -> bool:
    day = str(civil_day)[:10]
    for s in sessions or []:
        d = getattr(s, "date", None)
        if d is None and isinstance(s, dict):
            d = s.get("date")
        if d is not None and str(d)[:10] == day:
            return True
    return False


def _sleep_last_night_h(
    sleep: Sequence[Any],
    civil_day: str,
) -> Optional[float]:
    """Prior-night hours: sample dated civil_day (FitDash night-ending date)."""
    day = str(civil_day)[:10]
    best: Optional[float] = None
    for s in sleep or []:
        d = getattr(s, "date", None)
        if d is None and isinstance(s, dict):
            d = s.get("date")
        if d is None or str(d)[:10] != day:
            continue
        hours = getattr(s, "sleep_hours", None)
        if hours is None and isinstance(s, dict):
            hours = s.get("sleep_hours") or s.get("hours")
        h = _as_float(hours)
        if h is None:
            continue
        source = getattr(s, "source", None)
        if source is None and isinstance(s, dict):
            source = s.get("source")
        if str(source or "") == "implied_zero" and h <= 0:
            continue
        if best is None or h > best:
            best = h
    return best


def _sleep_battery_export(
    sleep_battery: Optional[dict],
    *,
    compose_now: Optional[datetime] = None,
) -> Optional[dict]:
    """Return thin advisory battery or None if missing / stale / no_data."""
    if not isinstance(sleep_battery, dict):
        return None
    mode = str(sleep_battery.get("mode") or "")
    if mode in ("", "no_data"):
        return None
    # Live compute at compose time is fresh; if caller stamped as_of, enforce 2h.
    as_of = sleep_battery.get("as_of") or sleep_battery.get("computed_at")
    if as_of:
        try:
            s = str(as_of).strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ref = compose_now or datetime.now(timezone.utc)
            if ref.tzinfo is None:
                ref = ref.replace(tzinfo=timezone.utc)
            age_h = (ref.astimezone(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0
            if age_h > SLEEP_BATTERY_MAX_AGE_HOURS:
                return None
        except ValueError:
            return None
    pct = sleep_battery.get("pct_charged")
    empty_at = sleep_battery.get("empty_at")
    if pct is None and empty_at is None:
        return None
    out: Dict[str, Any] = {}
    if pct is not None:
        out["pct_charged"] = _as_float(pct)
    if empty_at is not None:
        out["empty_at"] = empty_at
    if sleep_battery.get("level") is not None:
        out["level"] = sleep_battery.get("level")
    if sleep_battery.get("summary"):
        out["summary"] = sleep_battery.get("summary")
    return out or None


def _default_deep_link() -> str:
    base = (os.environ.get("FITDASH_PUBLIC_URL") or "").strip().rstrip("/")
    if base:
        return base + "/"
    port = os.environ.get("PORT") or "8787"
    return f"http://127.0.0.1:{port}/"


def build_day_constraints_packet(
    *,
    today_board: Optional[dict] = None,
    recovery: Optional[Union[dict, Any]] = None,
    workout_plan: Optional[dict] = None,
    sessions: Optional[Sequence[Any]] = None,
    sleep: Optional[Sequence[Any]] = None,
    sleep_battery: Optional[dict] = None,
    adherence_7d: Optional[dict] = None,
    civil_day: Optional[str] = None,
    as_of: Optional[str] = None,
    deep_link: Optional[str] = None,
    coach_ok: bool = True,
    recovery_sparse: bool = False,
    fitness_down: bool = False,
) -> Dict[str, Any]:
    """Build freeze-shaped packet from existing FitDash coach/recovery/meal paths.

    Never invents Ready when coach failed, Fit is down, or recovery is sparse
    without real body signals.
    """
    day = civil_day or local_today_iso()
    now_iso = as_of or local_now_iso()
    board = today_board if isinstance(today_board, dict) else {}
    wp = workout_plan if isinstance(workout_plan, dict) else {}
    adh = adherence_7d if isinstance(adherence_7d, dict) else {}

    # Recovery fields — accept RecoveryStatus or dict
    if recovery is None:
        rec_label = board.get("recovery", {}).get("label") if isinstance(board.get("recovery"), dict) else None
        rec_score = board.get("recovery", {}).get("score") if isinstance(board.get("recovery"), dict) else None
    elif hasattr(recovery, "label"):
        rec_label = getattr(recovery, "label", None)
        rec_score = getattr(recovery, "score", None)
    elif isinstance(recovery, dict):
        rec_label = recovery.get("label")
        rec_score = recovery.get("score")
    else:
        rec_label = None
        rec_score = None
    rec_score_f = _as_float(rec_score)

    # Train recommendation from coach board (preferred) else recovery thresholds
    train_rec = board.get("recommendation")
    if train_rec not in ("train", "easy", "rest"):
        train_rec = None
    if train_rec is None:
        if wp.get("is_rest_day"):
            train_rec = "rest"
        elif rec_score_f is not None and rec_score_f < 40:
            train_rec = "rest"
        elif rec_score_f is not None and rec_score_f < 55:
            train_rec = "easy"
        else:
            train_rec = "train" if coach_ok and not fitness_down else None

    # Never invent Ready when we lack honest body signal
    if fitness_down or not coach_ok:
        if rec_label == "Ready":
            rec_label = None
        # Prefer rest gate honesty over invented train
        if train_rec == "train" and (rec_score_f is None or rec_score_f < 40):
            train_rec = "rest" if rec_score_f is not None and rec_score_f < 40 else None
    if recovery_sparse and rec_label == "Ready":
        # Sparse Health must not paint Ready (product honesty)
        rec_label = "Moderate" if rec_score_f is not None and rec_score_f >= 50 else rec_label
        if rec_label == "Ready":
            rec_label = None

    session_type = (
        (board.get("workout") or {}).get("session_type")
        if isinstance(board.get("workout"), dict)
        else None
    ) or wp.get("session_type")
    if session_type is not None:
        session_type = str(session_type).lower()

    logged = _session_logged_today(sessions or [], day)
    # Planned training day: not a pure rest rotation. Recovery-forced rest still
    # counts as "session due" (blocked by train_recommendation, not cancelled).
    is_rest_plan = bool(wp.get("is_rest_day"))
    recovery_forced_rest = (
        is_rest_plan
        and session_type == "rest"
        and rec_score_f is not None
        and rec_score_f < 40
    )
    if logged:
        session_due = False
    elif recovery_forced_rest:
        session_due = True
    elif is_rest_plan and session_type == "rest":
        session_due = False
    else:
        session_due = True

    # Protein — same civil day honesty
    nut = board.get("nutrition") if isinstance(board.get("nutrition"), dict) else {}
    targets = nut.get("targets") if isinstance(nut.get("targets"), dict) else {}
    consumed = nut.get("consumed") if isinstance(nut.get("consumed"), dict) else {}
    remaining = nut.get("remaining") if isinstance(nut.get("remaining"), dict) else {}
    protein_target = _as_float(targets.get("protein_g"))
    protein_consumed = _as_float(consumed.get("protein_g"))
    protein_remaining = _as_float(remaining.get("protein_g"))
    if protein_remaining is None and protein_target is not None and protein_consumed is not None:
        protein_remaining = max(0.0, protein_target - protein_consumed)

    has_today_log = bool(
        protein_consumed is not None and protein_consumed > 0
    ) or any(
        _as_float((consumed or {}).get(k)) not in (None, 0.0)
        for k in ("calories", "protein_g", "carbs_g", "fat_g")
    )
    adh_p = adh.get("protein") if isinstance(adh.get("protein"), dict) else {}
    adh_pct = _as_float(adh_p.get("pct")) if adh_p else _as_float(
        (board.get("adherence_7d") or {}).get("protein_pct")
        if isinstance(board.get("adherence_7d"), dict)
        else None
    )

    protein_band = protein_gap_band(
        protein_target_g=protein_target,
        protein_remaining_g=protein_remaining,
        protein_consumed_g=protein_consumed,
        protein_adherence_7d_pct=adh_pct,
        has_today_protein_log=has_today_log,
        same_civil_day=True,  # board is always local civil today at compose
    )
    if fitness_down or not coach_ok:
        # Do not claim protein honesty without live Fit
        if not has_today_log:
            protein_band = "unknown"

    sleep_h = _sleep_last_night_h(sleep or [], day)
    # Also try board / battery last_sleep
    if sleep_h is None and isinstance(sleep_battery, dict):
        sleep_h = _as_float(sleep_battery.get("last_sleep_hours"))
    sleep_ok: Optional[bool]
    if sleep_h is None:
        sleep_ok = None
    else:
        sleep_ok = sleep_h >= SLEEP_OK_HOURS

    bat_export = None if fitness_down else _sleep_battery_export(sleep_battery)

    stale = bool(fitness_down or not coach_ok)
    if recovery_sparse and rec_score_f is None:
        stale = True

    conf = 0.0
    if not stale and coach_ok:
        conf = 0.55 if recovery_sparse else 0.85
        if rec_score_f is not None and train_rec:
            conf = min(0.95, conf + 0.05)

    # Summary one-liner
    bits: List[str] = []
    if train_rec:
        bits.append(f"rec={train_rec}")
    if rec_label is not None and rec_score_f is not None:
        bits.append(f"{rec_label} {rec_score_f:.0f}")
    elif rec_score_f is not None:
        bits.append(f"score={rec_score_f:.0f}")
    if protein_band and protein_band != "unknown":
        rem_s = f"{protein_remaining:.0f}g left" if protein_remaining is not None else ""
        bits.append(f"protein {protein_band}" + (f" ({rem_s})" if rem_s else ""))
    if session_due and train_rec != "rest":
        bits.append(f"session due ({session_type or 'planned'})")
    elif session_due and train_rec == "rest":
        bits.append("session blocked (rest)")
    if stale:
        bits.insert(0, "stale/unknown")
    summary = "; ".join(bits) if bits else "fitness day packet"

    packet: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "domain": "fitness",
        "as_of": now_iso,
        "civil_day": day,
        "fresh_for_hours": 24,
        "stale": stale,
        "confidence": round(conf, 2),
        "summary": summary,
        "deep_link": deep_link or _default_deep_link(),
        "session_due": bool(session_due),
        "session_type": session_type,
        "train_recommendation": train_rec,
        "recovery_label": rec_label,
        "recovery_score": rec_score_f,
        "protein_gap_band": protein_band,
        "protein_remaining_g": (
            round(protein_remaining, 1) if protein_remaining is not None else None
        ),
        "protein_target_g": (
            round(protein_target, 1) if protein_target is not None else None
        ),
        "protein_as_of": now_iso if (has_today_log or protein_band != "unknown") else None,
        "sleep_last_night_h": round(sleep_h, 2) if sleep_h is not None else None,
        "sleep_ok": sleep_ok,
    }
    if bat_export is not None:
        packet["sleep_battery"] = bat_export
    return packet


def day_constraints_path(workspace: Union[str, Path]) -> Path:
    return Path(workspace) / "fitness" / "data" / "day_constraints.json"


def write_day_constraints(
    workspace: Union[str, Path],
    packet: dict,
) -> Path:
    """Atomic write of packet to fitness/data/day_constraints.json."""
    path = day_constraints_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(packet, indent=2, sort_keys=False) + "\n"
    # Atomic replace within same directory
    fd, tmp_name = tempfile.mkstemp(
        prefix=".day_constraints.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def export_day_constraints_from_dashboard(
    dashboard: dict,
    *,
    workspace: Optional[Union[str, Path]] = None,
    sessions: Optional[Sequence[Any]] = None,
    sleep: Optional[Sequence[Any]] = None,
    write: bool = True,
) -> Dict[str, Any]:
    """Build (and optionally write) packet from a load_dashboard_data payload."""
    coach = dashboard.get("coach") if isinstance(dashboard.get("coach"), dict) else {}
    today = coach.get("today") if isinstance(coach.get("today"), dict) else {}
    meta = dashboard.get("meta") if isinstance(dashboard.get("meta"), dict) else {}
    recovery = dashboard.get("recovery")
    workout_plan = (dashboard.get("workout_store") or {}).get("plan") or {}
    sleep_battery = dashboard.get("sleep_battery")
    civil = meta.get("local_today") or local_today_iso()
    as_of = meta.get("generated_at") or local_now_iso()
    coach_err = False
    if isinstance(coach.get("weekly_review"), dict):
        bullets = coach["weekly_review"].get("bullets") or []
        coach_err = any("Coach layer error" in str(b) for b in bullets)
    if coach.get("brief", {}).get("markdown", "").startswith("Coach unavailable"):
        coach_err = True
    sparse = bool(isinstance(recovery, dict) and recovery.get("sparse"))
    health_creds = meta.get("health_credentials")
    fitness_down = bool(meta.get("error") and "nutrition_store" in str(meta.get("error")))
    # Soft: if dashboard completely missing recovery + coach today, treat as down
    if not today and recovery is None:
        fitness_down = True

    packet = build_day_constraints_packet(
        today_board=today,
        recovery=recovery,
        workout_plan=workout_plan if isinstance(workout_plan, dict) else {},
        sessions=sessions or [],
        sleep=sleep or [],
        sleep_battery=sleep_battery if isinstance(sleep_battery, dict) else None,
        adherence_7d=coach.get("adherence_7d") if isinstance(coach.get("adherence_7d"), dict) else {},
        civil_day=str(civil)[:10],
        as_of=as_of if isinstance(as_of, str) else local_now_iso(),
        coach_ok=not coach_err,
        recovery_sparse=sparse,
        fitness_down=fitness_down,
    )
    # Prefer not to claim Ready when Health credentials missing and no recovery inputs
    if health_creds is False and packet.get("recovery_label") == "Ready":
        packet["recovery_label"] = None
        packet["stale"] = True
        packet["confidence"] = min(float(packet.get("confidence") or 0), 0.3)
        packet["summary"] = "stale/unknown; " + str(packet.get("summary") or "")

    ws = workspace
    if ws is None:
        ws = os.environ.get("LOCAL_WORKSPACE_DIR") or ""
    if write and ws:
        try:
            write_day_constraints(ws, packet)
            packet["_written_to"] = str(day_constraints_path(ws))
        except OSError as e:
            packet["_write_error"] = str(e)
    return packet
