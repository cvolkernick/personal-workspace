"""Domain models for lift sessions and health metrics."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# Planner status copy. A closed day used to store this string as a lift (#1110).
_STATUS_LOG_NAME_RE = re.compile(r"^Already trained today\b", re.IGNORECASE)


def _fmt_weight_lbs(w: float) -> str:
    if abs(float(w) - round(float(w))) < 1e-9:
        return str(int(round(float(w))))
    return f"{w:g}"


@dataclass
class SetEntry:
    weight_lbs: float
    sets: int
    reps: int

    def _math_weight(self) -> Optional[float]:
        """Load used for tonnage and e1RM.

        Assisted sets store the pin on ``weight_lbs``. Read-time overlay puts
        bodyweight minus pin on ``tonnage_weight_lbs``. A missing weigh-in
        sets ``exclude_from_load_math`` and contributes nothing (#1156).
        """
        if getattr(self, "exclude_from_load_math", False):
            return None
        override = getattr(self, "tonnage_weight_lbs", None)
        if override is not None:
            return float(override)
        return float(self.weight_lbs)

    @property
    def volume(self) -> float:
        weight = self._math_weight()
        if weight is None:
            return 0.0
        return weight * int(self.sets) * int(self.reps)

    def epley_1rm(self) -> float:
        """Estimated 1RM (Epley). For reps=1 returns the weight itself."""
        weight = self._math_weight()
        if weight is None or self.reps <= 0:
            return 0.0
        if self.reps == 1:
            return float(weight)
        return float(weight) * (1.0 + self.reps / 30.0)

    def label(self) -> str:
        """Log-shaped triple: ``225 lbs x 3 x 8``. This stays the logged pin."""
        return f"{_fmt_weight_lbs(self.weight_lbs)} lbs x {int(self.sets)} x {int(self.reps)}"

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        source = getattr(self, "effective_load_source", None)
        if not source:
            return out
        out["assistance_lbs"] = float(self.weight_lbs)
        out["effective_load_source"] = source
        days = getattr(self, "weighin_days_away", None)
        if days is not None:
            out["weighin_days_away"] = int(days)
        if source == "nearest_weighin":
            eff = getattr(self, "tonnage_weight_lbs", None)
            if eff is not None:
                out["effective_load_lbs"] = float(eff)
        return out


@dataclass
class ExerciseEntry:
    name: str
    sets: List[SetEntry] = field(default_factory=list)
    is_pr: bool = False
    raw: str = ""
    quest_seeded: bool = False

    @property
    def volume(self) -> float:
        return sum(s.volume for s in self.sets)

    @property
    def best_e1rm(self) -> float:
        if not self.sets:
            return 0.0
        return max(s.epley_1rm() for s in self.sets)

    @property
    def best_working_weight(self) -> float:
        if not self.sets:
            return 0.0
        weights = [s._math_weight() for s in self.sets]
        present = [w for w in weights if w is not None]
        if not present:
            return 0.0
        return max(present)

    @property
    def sets_label(self) -> str:
        return ", ".join(s.label() for s in self.sets)

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "name": self.name,
            "sets": [s.to_dict() for s in self.sets],
            "is_pr": self.is_pr,
            "raw": self.raw,
            "quest_seeded": self.quest_seeded,
            "volume": self.volume,
            "best_e1rm": self.best_e1rm,
            "best_working_weight": self.best_working_weight,
            "sets_label": self.sets_label,
        }
        source = getattr(self, "effective_load_source", None)
        if source:
            out["effective_load_source"] = source
            if getattr(self, "assistance_lbs", None) is not None:
                out["assistance_lbs"] = float(self.assistance_lbs)
            days = getattr(self, "weighin_days_away", None)
            if days is not None:
                out["weighin_days_away"] = int(days)
            if source == "nearest_weighin" and getattr(self, "effective_load_lbs", None) is not None:
                out["effective_load_lbs"] = float(self.effective_load_lbs)
        return out


def is_planner_status_exercise_name(name: str) -> bool:
    """True when ``name`` is planner status copy, not a lift (#1110)."""
    return bool(_STATUS_LOG_NAME_RE.match(str(name or "").strip()))


def _status_row_volume(ex: Any) -> float:
    """Working volume for a log row. Missing sets count as zero."""
    if isinstance(ex, ExerciseEntry):
        return float(ex.volume)
    if isinstance(ex, dict):
        raw_sets = ex.get("sets")
        if isinstance(raw_sets, list):
            total = 0.0
            saw = False
            for st in raw_sets:
                if not isinstance(st, dict):
                    continue
                try:
                    total += (
                        float(st.get("weight_lbs") or 0)
                        * int(st.get("sets") or 0)
                        * int(st.get("reps") or 0)
                    )
                    saw = True
                except (TypeError, ValueError):
                    continue
            if saw or raw_sets == []:
                return total
        try:
            weight = ex.get("weight_lbs")
            count = ex.get("sets")
            reps = ex.get("reps")
            if (
                weight is not None
                and reps is not None
                and count is not None
                and not isinstance(count, list)
            ):
                return float(weight) * int(count) * int(reps)
        except (TypeError, ValueError):
            pass
        if ex.get("volume") is not None:
            try:
                return float(ex.get("volume") or 0)
            except (TypeError, ValueError):
                return 0.0
        return 0.0
    try:
        return float(getattr(ex, "volume", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def is_zero_volume_status_log(ex: Any) -> bool:
    """Read-time filter: ``^Already trained today`` with no working volume.

    The stored row is left in place (#1110). A same-named row that actually
    has load stays visible.
    """
    if isinstance(ex, ExerciseEntry):
        name = ex.name
    elif isinstance(ex, dict):
        name = str(ex.get("name") or ex.get("exercise") or "")
    else:
        name = str(getattr(ex, "name", "") or "")
    if not is_planner_status_exercise_name(name):
        return False
    return _status_row_volume(ex) <= 0


@dataclass
class Session:
    date: str  # ISO YYYY-MM-DD
    session_type: str  # push | pull | legs | other
    exercises: List[ExerciseEntry] = field(default_factory=list)
    notes: str = ""
    source_file: str = ""
    closed_at: Optional[str] = None  # ISO datetime of first persist (session-close)

    @property
    def volume(self) -> float:
        return sum(e.volume for e in self.exercises)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "date": self.date,
            "session_type": self.session_type,
            "exercises": [
                e.to_dict()
                for e in self.exercises
                if not is_zero_volume_status_log(e)
            ],
            "notes": self.notes,
            "source_file": self.source_file,
            "volume": self.volume,
        }
        if self.closed_at:
            out["closed_at"] = self.closed_at
        return out


@dataclass
class WeightSample:
    date: str  # ISO
    weight_lbs: float
    source: str = "google_fit"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SleepSample:
    date: str  # ISO night date
    sleep_hours: float
    efficiency_pct: Optional[float] = None
    source: str = "google_fit"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NutritionDay:
    """Daily calories + macros (protein/carbs/fat grams).

    ``nutrients`` is the existing Google Health nutrients{} map (enum → grams)
    when the day/rollup payload already has it. Absent keys stay absent.
    """

    date: str
    calories: Optional[float] = None
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fat_g: Optional[float] = None
    nutrients: Dict[str, float] = field(default_factory=dict)
    source: str = "google_health"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FoodLogEntry:
    """Meal-level food log from Google Health nutrition-log dataPoints."""

    date: str
    name: str
    calories: Optional[float] = None
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fat_g: Optional[float] = None
    meal_type: Optional[str] = None
    serving_label: Optional[str] = None
    time: Optional[str] = None  # HH:MM civil local when available
    nutrients: Dict[str, float] = field(default_factory=dict)  # nutrient → grams
    source: str = "google_health"
    # Google Health dataPoint resource name. Stable across edits, so an edited
    # entry replaces its old version instead of being counted twice.
    log_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HydrationDay:
    date: str
    water_ml: float
    source: str = "google_health"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CaloriesBurnedDay:
    """Activity total calories burned (not intake)."""

    date: str
    calories: float
    source: str = "google_health"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RestingHeartRateDay:
    """One civil day's resting heart rate from Google Health.

    Source type is ``daily-resting-heart-rate`` only. Never invent bpm from
    heart-rate samples, HRV, AZM, or calories.
    """

    date: str
    bpm: float
    source: str = "google_health"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ActiveZoneMinutesDay:
    """Daily Active Zone Minutes from Google Health dailyRollUp.

    Honest mapping from ActiveZoneMinutesRollupValue only — never invent
    numbers, and never substitute heart_minutes / active_minutes / steps /
    burned kcal as AZM.

      sumInFatBurnHeartZone  → fat_burn_minutes  (HeartRateZone.FAT_BURN)
      sumInCardioHeartZone   → cardio_minutes    (HeartRateZone.CARDIO)
      sumInPeakHeartZone     → peak_minutes      (HeartRateZone.PEAK)
    total_minutes is the sum of whichever zone fields were actually present.
    """

    date: str
    fat_burn_minutes: Optional[float] = None
    cardio_minutes: Optional[float] = None
    peak_minutes: Optional[float] = None
    total_minutes: Optional[float] = None
    source: str = "google_health"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StepSample:
    """Daily step count from Google Health dailyRollUp('steps').

    Honest ``countSum`` only — never invent from AZM, heart_minutes, or kcal.
    """

    date: str
    steps: int
    source: str = "google_health"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HealthSnapshot:
    weight: List[WeightSample] = field(default_factory=list)
    sleep: List[SleepSample] = field(default_factory=list)
    # Timed sleep sessions [{start, end, source}] for sleep battery (Time Allocator style)
    sleep_intervals: List[Dict[str, Any]] = field(default_factory=list)
    nutrition: List[NutritionDay] = field(default_factory=list)
    food_logs: List[FoodLogEntry] = field(default_factory=list)
    hydration: List[HydrationDay] = field(default_factory=list)
    calories_burned: List[CaloriesBurnedDay] = field(default_factory=list)
    active_zone_minutes: List[ActiveZoneMinutesDay] = field(default_factory=list)
    resting_heart_rate: List[RestingHeartRateDay] = field(default_factory=list)
    steps: List[StepSample] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "weight": [w.to_dict() for w in self.weight],
            "sleep": [s.to_dict() for s in self.sleep],
            "sleep_intervals": list(self.sleep_intervals or []),
            "nutrition": [n.to_dict() for n in self.nutrition],
            "food_logs": [f.to_dict() for f in self.food_logs],
            "hydration": [h.to_dict() for h in self.hydration],
            "calories_burned": [c.to_dict() for c in self.calories_burned],
            "active_zone_minutes": [a.to_dict() for a in self.active_zone_minutes],
            "resting_heart_rate": [r.to_dict() for r in self.resting_heart_rate],
            "steps": [s.to_dict() for s in self.steps],
            "error": self.error,
        }


@dataclass
class RecoveryStatus:
    label: str  # e.g. "Ready", "Moderate", "Needs Rest", "Unavailable"
    score: Optional[float] = None  # 0-100, or None when both inputs are stale
    reasons: List[str] = field(default_factory=list)
    inputs: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
