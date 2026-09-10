"""Always-on Keep vs Pivot phase barometer.

Reads only fields already on the FitDash dashboard JSON. Weekly KPI
snapshots live on disk so two consecutive weeks is not RAM.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .timeutil import local_today_iso

VERSION = 1
MAX_WEEKS = 8
MAX_AUDIT = 20
MIN_WEEK_GAP_DAYS = 7

THRESHOLDS: Dict[str, Any] = {
    "cut_to_bulk": {
        "weekly_loss_max_lb": 0.2,
        "protein_adherence_min_pct": 70.0,
        "recovery_score_max": 65.0,
        "sleep_battery_max_pct": 45.0,
        "labs": {
            "free_t3": 3.0,
            "total_testosterone": 500.0,
            "free_testosterone": 100.0,
        },
        "deficit_min_kcal": 300,
        "volume_7d_max_lb": 22000.0,
        "projected_sets_max": 12.0,
        "consecutive_weeks": 2,
    },
    "bulk_to_cut": {
        "weekly_gain_min_lb": 0.5,
        "recovery_score_max": 60.0,
        "sleep_battery_max_pct": 40.0,
        "consecutive_weeks": 2,
    },
    "maintain": {
        "weekly_abs_max_lb": 0.3,
        "consecutive_weeks": 3,
        "recovery_collapse_score": 50.0,
        "recovery_collapse_sleep_pct": 35.0,
    },
}

PHASE_LABELS = {
    "cut": "Cut",
    "slow_bulk": "Bulk",
    "bulk": "Bulk",
    "maintain": "Maintenance",
    "maintenance": "Maintenance",
    "recomp": "Recomp",
}

CANONICAL_PHASES = ("cut", "slow_bulk", "maintain", "recomp")

_WEEKLY_RE = re.compile(
    r"14d scale weekly\s*([+-]?\d+(?:\.\d+)?)\s*lb/week", re.I
)
_DEFICIT_RE = re.compile(r"deficit\s+(\d+)\s*kcal", re.I)
_TDEE_GAP_RE = re.compile(r"(?:tdee|gap)\D{0,24}(\d{3,4})\s*kcal", re.I)


def _parse_day(value: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    return n


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        out = to_dict()
        return out if isinstance(out, dict) else {}
    return {}


def iso_week_key(as_of: str) -> str:
    day = _parse_day(as_of)
    if not day:
        return str(as_of or "")[:10]
    iso = day.isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def canonical_phase(raw: Any) -> str:
    v = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if v in ("bulk", "gain", "slow_bulk"):
        return "slow_bulk"
    if v in ("maintenance", "maintain"):
        return "maintain"
    if v in ("cut", "recomp"):
        return v
    return v or "cut"


def phase_label(phase: str) -> str:
    return PHASE_LABELS.get(canonical_phase(phase), (phase or "—").title())


def store_path(user_id: Optional[str] = None) -> Path:
    root = os.environ.get("FITDASH_PHASE_BAROMETER_DIR")
    if root:
        base = Path(root)
    else:
        base = Path.home() / ".config" / "resistance-dashboard"
    uid = str(user_id or "").strip()
    name = f"phase_barometer_{uid}.json" if uid else "phase_barometer.json"
    return base / name


def empty_store() -> dict:
    return {
        "version": VERSION,
        "weekly_snapshots": [],
        "last_decision": None,
        "dismiss_banner_until": None,
        "dismissed_verdict": None,
        "audit": [],
    }


def load_store(user_id: Optional[str] = None) -> dict:
    path = store_path(user_id)
    if not path.is_file():
        return empty_store()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_store()
    if not isinstance(raw, dict):
        return empty_store()
    out = empty_store()
    out.update({k: raw.get(k, out.get(k)) for k in out})
    if not isinstance(out.get("weekly_snapshots"), list):
        out["weekly_snapshots"] = []
    if not isinstance(out.get("audit"), list):
        out["audit"] = []
    return out


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="phase_baro_", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_store(store: dict, user_id: Optional[str] = None) -> dict:
    path = store_path(user_id)
    payload = deepcopy(store) if isinstance(store, dict) else empty_store()
    payload["version"] = VERSION
    snaps = list(payload.get("weekly_snapshots") or [])[-MAX_WEEKS:]
    payload["weekly_snapshots"] = snaps
    payload["audit"] = list(payload.get("audit") or [])[-MAX_AUDIT:]
    _atomic_write(path, payload)
    payload["_path"] = str(path)
    return payload


def verdict_fingerprint(obj: Optional[dict]) -> str:
    blob = obj if isinstance(obj, dict) else {}
    return "|".join(
        [
            str(blob.get("reading") or ""),
            str(blob.get("status") or ""),
            str(blob.get("next_phase") or ""),
            str(blob.get("explanation") or ""),
        ]
    )


def dismiss_banner(*, as_of: Optional[str] = None, user_id: Optional[str] = None) -> dict:
    day = as_of or local_today_iso()
    until_dt = _parse_day(day)
    until = (until_dt + timedelta(days=7)).strftime("%Y-%m-%d") if until_dt else day
    store = load_store(user_id)
    store["dismiss_banner_until"] = until
    last = store.get("last_decision") if isinstance(store.get("last_decision"), dict) else {}
    store["dismissed_verdict"] = verdict_fingerprint(last)
    store.setdefault("audit", []).append(
        {"at": day, "action": "dismiss_banner", "until": until}
    )
    return save_store(store, user_id)


def undismiss_banner(*, as_of: Optional[str] = None, user_id: Optional[str] = None) -> dict:
    day = as_of or local_today_iso()
    store = load_store(user_id)
    store["dismiss_banner_until"] = None
    store["dismissed_verdict"] = None
    store.setdefault("audit", []).append({"at": day, "action": "undismiss_banner"})
    return save_store(store, user_id)


def _marker_value(markers: Any, *ids: str) -> Optional[float]:
    if not isinstance(markers, dict):
        return None
    wanted = {str(i).lower() for i in ids}
    for key, raw in markers.items():
        kid = str(key or "").lower()
        blob = raw if isinstance(raw, dict) else {"value": raw, "id": key}
        mid = str(blob.get("id") or kid).lower()
        if kid not in wanted and mid not in wanted:
            continue
        n = _as_float(blob.get("value") if "value" in blob else raw)
        if n is not None:
            return n
    return None


def _marker_band(markers: Any, *ids: str) -> str:
    if not isinstance(markers, dict):
        return ""
    wanted = {str(i).lower() for i in ids}
    for key, raw in markers.items():
        if not isinstance(raw, dict):
            continue
        kid = str(key or "").lower()
        mid = str(raw.get("id") or kid).lower()
        if kid in wanted or mid in wanted:
            return str(raw.get("band") or raw.get("performance_status") or "")
    return ""


def _labs_cluster_low(labs: dict, markers: dict) -> bool:
    cluster = labs.get("cluster") if isinstance(labs, dict) else None
    if isinstance(cluster, dict) and str(cluster.get("id") or "") == "energy_availability":
        return True
    th = THRESHOLDS["cut_to_bulk"]["labs"]
    ft3 = _marker_value(markers, "free_t3", "ft3")
    tt = _marker_value(markers, "total_testosterone", "testosterone_ng_dl", "testosterone")
    ft = _marker_value(markers, "free_testosterone")
    e2 = _marker_value(markers, "estrogen", "e2", "estradiol")
    e2_band = _marker_band(markers, "estrogen", "e2", "estradiol")
    if ft3 is not None and ft3 < float(th["free_t3"]):
        return True
    if tt is not None and tt < float(th["total_testosterone"]):
        return True
    if ft is not None and ft < float(th["free_testosterone"]):
        return True
    if e2 is not None and e2 < 15.0:
        return True
    if "low" in e2_band.lower():
        return True
    return False


def _fat_gain_labs(labs: dict, markers: dict) -> bool:
    if not isinstance(markers, dict) and not isinstance(labs, dict):
        return False
    for cid in ("fructosamine", "triglycerides", "apob"):
        band = _marker_band(markers, cid)
        if "out_of_clinical" in band or band in ("high", "out_of_performance"):
            return True
        n = _marker_value(markers, cid)
        if cid == "triglycerides" and n is not None and n >= 150:
            return True
        if cid == "apob" and n is not None and n >= 90:
            return True
    hist = (labs or {}).get("history") if isinstance(labs, dict) else None
    rows = (hist or {}).get("markers") if isinstance(hist, dict) else None
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            mid = str(row.get("id") or row.get("marker") or "").lower()
            if mid not in ("fructosamine", "triglycerides", "apob"):
                continue
            delta = _as_float(row.get("delta") or row.get("change"))
            if delta is not None and delta > 0:
                return True
    return False


def _weekly_from_weight(weight: Sequence[Any], *, as_of: str) -> Optional[float]:
    try:
        from .nutrition_targets import _weight_trend
        from .models import WeightSample
    except Exception:
        return None
    samples: List[WeightSample] = []
    for w in weight or []:
        if isinstance(w, WeightSample):
            samples.append(w)
            continue
        blob = _as_dict(w)
        lbs = _as_float(blob.get("weight_lbs") or blob.get("lbs"))
        day = str(blob.get("date") or "")[:10]
        if lbs and day:
            samples.append(WeightSample(date=day, weight_lbs=lbs))
    if len(samples) < 2:
        return None
    _delta, weekly, _span = _weight_trend(samples, as_of=as_of, window=14)
    return weekly


def _weekly_from_reasons(reasons: Sequence[Any]) -> Optional[float]:
    for raw in reasons or []:
        m = _WEEKLY_RE.search(str(raw or ""))
        if m:
            return _as_float(m.group(1))
    return None


def _deficit_kcal(nt: dict, reasons: Sequence[Any]) -> Optional[float]:
    """Actual cut size: TDEE − applied calories. Reasons are last resort.

    Cut recommendations always emit `phase=cut; deficit N kcal`, so matching
    that string first would pass the deficit gate even when applied is
    maintenance (or a <300 kcal gap).
    """
    tdee = _as_float(nt.get("tdee_kcal"))
    applied = _as_dict(nt.get("applied"))
    cal = _as_float(applied.get("calories"))
    if tdee is not None and cal is not None:
        return tdee - cal
    rec = _as_dict(nt.get("recommended"))
    rec_cal = _as_float(rec.get("calories"))
    if tdee is not None and rec_cal is not None:
        return tdee - rec_cal
    for raw in reasons or []:
        m = _DEFICIT_RE.search(str(raw or ""))
        if m:
            return _as_float(m.group(1))
    return None


def _volume_muscles(plan: dict) -> List[dict]:
    vol = plan.get("volume") if isinstance(plan, dict) else None
    rows = (vol or {}).get("muscles") if isinstance(vol, dict) else None
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def collect_kpis(
    *,
    as_of: str,
    recovery: Any = None,
    sleep_battery: Any = None,
    coach: Optional[dict] = None,
    nutrition_store: Optional[dict] = None,
    workout_store: Optional[dict] = None,
    health: Optional[dict] = None,
) -> dict:
    rec = _as_dict(recovery)
    inputs = rec.get("inputs") if isinstance(rec.get("inputs"), dict) else {}
    bat = _as_dict(sleep_battery)
    if not bat:
        bat = _as_dict((coach or {}).get("today") and ((coach or {}).get("today") or {}).get("sleep_battery"))
    coach = coach or {}
    nt = _as_dict(coach.get("nutrition_targets"))
    adh = _as_dict(coach.get("adherence_7d"))
    labs_store = _as_dict((nutrition_store or {}).get("labs"))
    fc_labs = _as_dict((_as_dict(coach.get("food_commentary")).get("labs")))
    labs = labs_store or fc_labs
    markers = labs.get("markers") if isinstance(labs.get("markers"), dict) else {}
    wo = workout_store or {}
    plan = _as_dict(wo.get("plan"))
    muscles = _volume_muscles(plan)
    reasons = list(nt.get("reasons") or [])
    weekly = _weekly_from_weight(
        (health or {}).get("weight") or [], as_of=as_of
    )
    if weekly is None:
        weekly = _weekly_from_reasons(reasons)
    protein = _as_float((_as_dict(adh.get("protein"))).get("pct"))
    calories_pct = _as_float((_as_dict(adh.get("calories"))).get("pct"))
    sleep_pct = _as_float(bat.get("pct_charged"))
    if sleep_pct is None:
        sleep_pct = _as_float((_as_dict(rec.get("sleep_battery"))).get("pct_charged"))
    vol_7d = _as_float(inputs.get("training_volume_7d"))
    projected = []
    over_12 = []
    under = high = over = 0
    for row in muscles:
        sets = _as_float(row.get("projected"))
        if sets is None:
            sets = _as_float(row.get("done"))
        status = str(row.get("status") or "")
        name = str(row.get("muscle") or "")
        projected.append({"muscle": name, "projected": sets, "status": status})
        if sets is not None and sets > THRESHOLDS["cut_to_bulk"]["projected_sets_max"]:
            over_12.append(name)
        if status in ("under", "low"):
            under += 1
        elif status == "high":
            high += 1
        elif status == "over":
            over += 1
    goals = _as_dict(wo.get("goals"))
    focus = [
        str(m)
        for m in (goals.get("focus_muscles") or [])
        if str(m).strip()
    ]
    phase = canonical_phase(nt.get("phase") or _as_dict((nutrition_store or {}).get("targets")).get("phase") or "cut")
    return {
        "as_of": as_of,
        "week": iso_week_key(as_of),
        "phase": phase,
        "weight_delta_7d_lbs": _as_float(inputs.get("weight_delta_7d_lbs")),
        "latest_weight_lbs": _as_float(inputs.get("latest_weight_lbs")),
        "weekly_14d_lb": round(weekly, 2) if weekly is not None else None,
        "protein_pct": protein,
        "calories_pct": calories_pct,
        "recovery_score": _as_float(rec.get("score")),
        "sleep_battery_pct": round(sleep_pct, 1) if sleep_pct is not None else None,
        "avg_sleep_hours_7d": _as_float(inputs.get("avg_sleep_hours_7d")),
        "labs_cluster_id": (
            str((_as_dict(labs.get("cluster"))).get("id") or "") or None
        ),
        "labs_low": _labs_cluster_low(labs, markers),
        "fat_gain_labs": _fat_gain_labs(labs, markers),
        "markers": {
            "free_t3": _marker_value(markers, "free_t3", "ft3"),
            "total_testosterone": _marker_value(
                markers, "total_testosterone", "testosterone_ng_dl", "testosterone"
            ),
            "free_testosterone": _marker_value(markers, "free_testosterone"),
            "estrogen": _marker_value(markers, "estrogen", "e2", "estradiol"),
        },
        "training_volume_7d": vol_7d,
        "muscles_over_12": over_12,
        "muscle_status_counts": {"under": under, "high": high, "over": over},
        "volume_manageable": (
            (vol_7d is None or vol_7d < THRESHOLDS["cut_to_bulk"]["volume_7d_max_lb"])
            and not over_12
        ),
        "deficit_kcal": _deficit_kcal(nt, reasons),
        "recommended": _as_dict(nt.get("recommended")) or None,
        "applied": _as_dict(nt.get("applied")) or None,
        "focus_muscles": focus,
    }


def _fmt(n: Optional[float], digits: int = 1, signed: bool = False) -> str:
    if n is None:
        return "—"
    if signed:
        return f"{n:+.{digits}f}"
    return f"{n:.{digits}f}"


def _weight_stalled_or_gaining(weekly: Optional[float], max_loss: float) -> bool:
    if weekly is None:
        return False
    return weekly >= -abs(max_loss)


def _cut_week_gates(kpis: dict) -> dict:
    th = THRESHOLDS["cut_to_bulk"]
    weekly = _as_float(kpis.get("weekly_14d_lb"))
    protein = _as_float(kpis.get("protein_pct"))
    score = _as_float(kpis.get("recovery_score"))
    sleep = _as_float(kpis.get("sleep_battery_pct"))
    deficit = _as_float(kpis.get("deficit_kcal"))
    weight_ok = _weight_stalled_or_gaining(weekly, th["weekly_loss_max_lb"])
    protein_ok = protein is not None and protein >= th["protein_adherence_min_pct"]
    recov_ok = (
        (score is not None and score <= th["recovery_score_max"])
        or (sleep is not None and sleep <= th["sleep_battery_max_pct"])
        or bool(kpis.get("labs_low"))
    )
    deficit_ok = deficit is not None and deficit >= th["deficit_min_kcal"]
    volume_ok = bool(kpis.get("volume_manageable"))
    return {
        "weight_stalled": weight_ok,
        "protein": protein_ok,
        "recovery_or_labs": recov_ok,
        "deficit": deficit_ok,
        "volume": volume_ok,
        "all": bool(weight_ok and protein_ok and recov_ok and deficit_ok and volume_ok),
    }


def _bulk_week_gates(kpis: dict) -> dict:
    th = THRESHOLDS["bulk_to_cut"]
    weekly = _as_float(kpis.get("weekly_14d_lb"))
    score = _as_float(kpis.get("recovery_score"))
    sleep = _as_float(kpis.get("sleep_battery_pct"))
    gain_ok = weekly is not None and weekly >= th["weekly_gain_min_lb"]
    recov_ok = (
        score is not None
        and score < th["recovery_score_max"]
        and sleep is not None
        and sleep < th["sleep_battery_max_pct"]
    )
    labs_ok = bool(kpis.get("fat_gain_labs"))
    return {
        "gain": gain_ok,
        "recovery": recov_ok,
        "fat_gain_labs": labs_ok,
        "all": bool(gain_ok and recov_ok and labs_ok),
    }


def _maintain_week_drift(kpis: dict) -> bool:
    weekly = _as_float(kpis.get("weekly_14d_lb"))
    if weekly is None:
        return False
    return abs(weekly) > THRESHOLDS["maintain"]["weekly_abs_max_lb"]


def _recovery_collapsed(kpis: dict) -> bool:
    th = THRESHOLDS["maintain"]
    score = _as_float(kpis.get("recovery_score"))
    sleep = _as_float(kpis.get("sleep_battery_pct"))
    if score is not None and score < th["recovery_collapse_score"]:
        return True
    if sleep is not None and sleep < th["recovery_collapse_sleep_pct"]:
        return True
    return False


def _snapshot_for_week(history: Sequence[dict], week: str) -> Optional[dict]:
    for row in reversed(list(history or [])):
        if isinstance(row, dict) and str(row.get("week") or "") == week:
            return row
    return None


def _snapshot_as_of(snap: dict) -> str:
    if not isinstance(snap, dict):
        return ""
    day = str(snap.get("as_of") or "")[:10]
    if day:
        return day
    kpis = snap.get("kpis") if isinstance(snap.get("kpis"), dict) else None
    if isinstance(kpis, dict):
        return str(kpis.get("as_of") or "")[:10]
    return ""


def _as_of_gap_days(prior_as_of: str, current_as_of: str) -> Optional[int]:
    a = _parse_day(prior_as_of)
    b = _parse_day(current_as_of)
    if not a or not b:
        return None
    return (b - a).days


def _prior_weeks(as_of: str, n: int) -> List[str]:
    day = _parse_day(as_of)
    if not day:
        return []
    out = []
    for i in range(1, n + 1):
        out.append(iso_week_key((day - timedelta(days=7 * i)).strftime("%Y-%m-%d")))
    return out


def _prior_elapsed_snapshots(
    current: dict, history: Sequence[dict], n: int
) -> Optional[List[dict]]:
    """Prior ISO-week rows whose as_of is at least 7 days before current.

    ISO week labels flip Sunday→Monday, so as_of−7d keys alone would treat
    2026-09-06 (W36) and 2026-09-07 (W37) as two consecutive weeks.
    """
    as_of = str(current.get("as_of") or "")[:10]
    priors = _prior_weeks(as_of, n)
    if len(priors) < n:
        return None
    out: List[dict] = []
    for week in priors:
        snap = _snapshot_for_week(history, week)
        if not snap:
            return None
        gap = _as_of_gap_days(_snapshot_as_of(snap), as_of)
        if gap is None or gap < MIN_WEEK_GAP_DAYS:
            return None
        out.append(snap)
    return out


def _kpis_from_snap(snap: dict) -> dict:
    if isinstance(snap.get("kpis"), dict):
        return snap["kpis"]
    return snap


def _consecutive_cut(current: dict, history: Sequence[dict]) -> bool:
    need = int(THRESHOLDS["cut_to_bulk"]["consecutive_weeks"])
    if not _cut_week_gates(current)["all"]:
        return False
    priors = _prior_elapsed_snapshots(current, history, need - 1)
    if not priors:
        return False
    return all(_cut_week_gates(_kpis_from_snap(snap))["all"] for snap in priors)


def _consecutive_bulk(current: dict, history: Sequence[dict]) -> bool:
    need = int(THRESHOLDS["bulk_to_cut"]["consecutive_weeks"])
    if not _bulk_week_gates(current)["all"]:
        return False
    priors = _prior_elapsed_snapshots(current, history, need - 1)
    if not priors:
        return False
    return all(_bulk_week_gates(_kpis_from_snap(snap))["all"] for snap in priors)


def _consecutive_maintain_drift(current: dict, history: Sequence[dict]) -> bool:
    need = int(THRESHOLDS["maintain"]["consecutive_weeks"])
    if not _maintain_week_drift(current):
        return False
    priors = _prior_elapsed_snapshots(current, history, need - 1)
    if not priors:
        return False
    return all(_maintain_week_drift(_kpis_from_snap(snap)) for snap in priors)


def _keep_status(phase: str) -> str:
    if phase == "cut":
        return "Stable – Keep Cutting"
    if phase == "slow_bulk":
        return "Stable – Keep Current Phase"
    return "Stable – Keep Current Phase"


def _pivot_status(next_phase: str) -> str:
    if next_phase == "slow_bulk":
        return "Pivot Signal – Consider Bulk"
    if next_phase == "cut":
        return "Pivot Signal – Consider Cut"
    if next_phase == "recomp":
        return "Pivot Signal – Consider Recomp"
    return f"Pivot Signal – Consider {phase_label(next_phase)}"


def _explanation(kpis: dict, *, reading: str, phase: str, next_phase: str, protein_block: bool) -> str:
    bits: List[str] = []
    weekly = kpis.get("weekly_14d_lb")
    protein = kpis.get("protein_pct")
    sleep = kpis.get("sleep_battery_pct")
    score = kpis.get("recovery_score")
    vol = kpis.get("training_volume_7d")
    if weekly is not None:
        if weekly >= 0:
            bits.append(f"Weight loss slowed to {_fmt(weekly, 2, True)} lb/wk for 14d")
        else:
            bits.append(f"14d scale {_fmt(weekly, 2, True)} lb/wk")
    if protein is not None:
        if protein < THRESHOLDS["cut_to_bulk"]["protein_adherence_min_pct"]:
            bits.append(f"protein adherence only {_fmt(protein, 1)}%")
        else:
            bits.append(f"protein adherence {_fmt(protein, 1)}%")
    if kpis.get("labs_low") or kpis.get("labs_cluster_id") == "energy_availability":
        bits.append("energy-availability labs remain suppressed")
    if sleep is not None:
        bits.append(f"sleep_battery {_fmt(sleep, 1)}%")
    elif score is not None:
        bits.append(f"recovery {score:.0f}")
    if protein_block:
        bits.append("pivot blocked until protein ≥70%")
    if reading == "keep" and not bits:
        bits.append(
            f"Phase {phase_label(phase)} holds — no 2-week pivot threshold crossed"
        )
    if vol is not None and reading == "keep":
        bits.append(f"7d volume {_fmt(vol, 0)} lb (DeanT 4–8, no muscle >12 projected)")
    text = ", ".join(bits)
    if not text.endswith("."):
        text += "."
    if reading == "pivot":
        text += f" Consider {phase_label(next_phase)}."
    return text[0].upper() + text[1:] if text else text


def evaluate_decision(
    kpis: dict,
    history: Optional[Sequence[dict]] = None,
    *,
    dismiss_until: Optional[str] = None,
) -> dict:
    history = list(history or [])
    phase = canonical_phase(kpis.get("phase") or "cut")
    protein = _as_float(kpis.get("protein_pct"))
    protein_block = protein is None or protein < THRESHOLDS["cut_to_bulk"]["protein_adherence_min_pct"]
    next_phase = phase
    reading = "keep"
    consecutive = False
    gates: Dict[str, Any] = {}

    if phase == "cut":
        gates = _cut_week_gates(kpis)
        consecutive = _consecutive_cut(kpis, history)
        # Hard block: never pivot on protein < 70% even if weight stalled.
        if consecutive and not protein_block:
            reading = "pivot"
            next_phase = "slow_bulk"
        else:
            reading = "keep"
            next_phase = "slow_bulk"
    elif phase == "slow_bulk":
        gates = _bulk_week_gates(kpis)
        consecutive = _consecutive_bulk(kpis, history)
        if consecutive:
            reading = "pivot"
            next_phase = "cut"
        else:
            next_phase = "cut"
    else:
        drift = _consecutive_maintain_drift(kpis, history)
        collapsed = _recovery_collapsed(kpis)
        consecutive = bool(drift or collapsed)
        weekly = _as_float(kpis.get("weekly_14d_lb"))
        gates = {
            "drift_3w": drift,
            "recovery_collapse": collapsed,
            "all": consecutive,
        }
        if consecutive:
            reading = "pivot"
            if weekly is not None and weekly > 0:
                next_phase = "cut"
            elif weekly is not None and weekly < 0:
                next_phase = "slow_bulk"
            else:
                next_phase = "cut"
        else:
            next_phase = phase

    if reading == "pivot" and protein_block and phase == "cut":
        reading = "keep"

    tone = "green" if reading == "keep" else ("red" if phase == "cut" else "amber")
    status = _keep_status(phase) if reading == "keep" else _pivot_status(next_phase)
    as_of = str(kpis.get("as_of") or "")
    dismissed = False
    until = str(dismiss_until or "")[:10] or None
    if until and as_of and as_of <= until:
        dismissed = True
    banner = reading == "pivot" and not dismissed
    recd = kpis.get("recommended") if isinstance(kpis.get("recommended"), dict) else None
    actions = [
        {
            "id": "apply_recommended",
            "label": "Apply coach recommended targets",
            "enabled": bool(recd),
        },
        {
            "id": "switch_phase",
            "label": f"Switch phase to {phase_label(next_phase)}",
            "phase": next_phase,
            "enabled": next_phase != phase,
        },
        {"id": "dismiss", "label": "Dismiss for 7d", "enabled": True},
        {"id": "log_labs", "label": "Log new labs", "enabled": True},
    ]
    return {
        "as_of": as_of,
        "phase": phase,
        "phase_label": phase_label(phase),
        "reading": reading,
        "status": status,
        "tone": tone,
        "explanation": _explanation(
            kpis,
            reading=reading,
            phase=phase,
            next_phase=next_phase,
            protein_block=bool(protein_block and phase == "cut"),
        ),
        "next_phase": next_phase,
        "next_phase_label": phase_label(next_phase),
        "gates": gates,
        "kpis": kpis,
        "thresholds": deepcopy(THRESHOLDS),
        "consecutive_weeks_met": bool(consecutive and reading == "pivot"),
        "protein_blocks_pivot": bool(protein_block and phase == "cut"),
        "banner": banner,
        "dismissed_until": until if dismissed else None,
        "actions": actions,
        "recommended": recd,
        "focus_muscles": list(kpis.get("focus_muscles") or []),
        "volume_framework": {
            "id": "dean_t_balanced_4_8",
            "target_sets_per_muscle_week": "4-8",
        },
        "available": True,
    }


def upsert_week(store: dict, kpis: dict) -> dict:
    out = deepcopy(store) if isinstance(store, dict) else empty_store()
    week = str(kpis.get("week") or iso_week_key(str(kpis.get("as_of") or "")))
    snap = {
        "week": week,
        "as_of": kpis.get("as_of"),
        "kpis": deepcopy(kpis),
        "gates": _cut_week_gates(kpis)
        if canonical_phase(kpis.get("phase")) == "cut"
        else (
            _bulk_week_gates(kpis)
            if canonical_phase(kpis.get("phase")) == "slow_bulk"
            else {
                "drift": _maintain_week_drift(kpis),
                "collapse": _recovery_collapsed(kpis),
            }
        ),
    }
    rows = [r for r in (out.get("weekly_snapshots") or []) if isinstance(r, dict)]
    rows = [r for r in rows if str(r.get("week") or "") != week]
    rows.append(snap)
    rows.sort(key=lambda r: str(r.get("week") or ""))
    out["weekly_snapshots"] = rows[-MAX_WEEKS:]
    return out


def build_phase_barometer(
    payload: Optional[dict] = None,
    *,
    user_id: Optional[str] = None,
    persist: bool = True,
    as_of: Optional[str] = None,
) -> dict:
    data = payload if isinstance(payload, dict) else {}
    day = as_of or str((data.get("meta") or {}).get("local_today") or "")[:10] or local_today_iso()
    kpis = collect_kpis(
        as_of=day,
        recovery=data.get("recovery"),
        sleep_battery=data.get("sleep_battery"),
        coach=data.get("coach") if isinstance(data.get("coach"), dict) else {},
        nutrition_store=data.get("nutrition_store") if isinstance(data.get("nutrition_store"), dict) else {},
        workout_store=data.get("workout_store") if isinstance(data.get("workout_store"), dict) else {},
        health=data.get("health") if isinstance(data.get("health"), dict) else {},
    )
    store = load_store(user_id)
    prior = list(store.get("weekly_snapshots") or [])
    decision = evaluate_decision(
        kpis, prior, dismiss_until=store.get("dismiss_banner_until")
    )
    fp = verdict_fingerprint(decision)
    stored_fp = str(store.get("dismissed_verdict") or "")
    if store.get("dismiss_banner_until") and stored_fp and stored_fp != fp:
        store["dismiss_banner_until"] = None
        store["dismissed_verdict"] = None
        decision = evaluate_decision(kpis, prior, dismiss_until=None)
    elif store.get("dismiss_banner_until") and not stored_fp:
        store["dismissed_verdict"] = fp
    store = upsert_week(store, kpis)
    weeks = [
        {
            "week": r.get("week"),
            "as_of": r.get("as_of"),
            "kpis": r.get("kpis"),
            "gates": r.get("gates"),
        }
        for r in (store.get("weekly_snapshots") or [])
        if isinstance(r, dict)
    ]
    decision["weeks"] = weeks[-3:]
    decision["last_decision"] = {
        "as_of": decision["as_of"],
        "reading": decision["reading"],
        "status": decision["status"],
        "phase": decision["phase"],
        "next_phase": decision["next_phase"],
        "explanation": decision["explanation"],
    }
    store["last_decision"] = decision["last_decision"]
    store.setdefault("audit", []).append(
        {
            "at": day,
            "reading": decision["reading"],
            "status": decision["status"],
            "protein_pct": kpis.get("protein_pct"),
            "weekly_14d_lb": kpis.get("weekly_14d_lb"),
        }
    )
    if persist:
        try:
            save_store(store, user_id)
            decision["persisted"] = True
        except Exception as exc:  # noqa: BLE001
            decision["persisted"] = False
            decision["persist_error"] = str(exc)
    else:
        decision["persisted"] = False
    return decision


def attach_phase_barometer(payload: dict, *, user_id: Optional[str] = None) -> dict:
    """Mutate dashboard payload: top-level + coach.today + weekly_review + nutrition_store."""
    uid = user_id or str(((payload.get("meta") or {}).get("user_id") or "")).strip() or None
    try:
        obj = build_phase_barometer(payload, user_id=uid, persist=True)
    except Exception as exc:  # noqa: BLE001
        obj = {
            "available": False,
            "error": str(exc),
            "status": "Stable – Keep Current Phase",
            "tone": "green",
            "explanation": "Phase barometer unavailable.",
            "reading": "keep",
        }
    payload["phase_barometer"] = obj
    coach = payload.get("coach")
    if isinstance(coach, dict):
        coach["phase_barometer"] = obj
        today = coach.get("today")
        if isinstance(today, dict):
            today["phase_barometer"] = obj
        weekly = coach.get("weekly_review")
        if isinstance(weekly, dict):
            weekly["phase_barometer"] = obj
        else:
            coach["weekly_review"] = {"bullets": [], "phase_barometer": obj}
    nut = payload.get("nutrition_store")
    if isinstance(nut, dict):
        nut["phase_barometer"] = obj
    return payload
