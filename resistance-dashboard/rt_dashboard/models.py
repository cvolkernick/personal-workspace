"""Domain models for lift sessions and health metrics."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SetEntry:
    weight_lbs: float
    sets: int
    reps: int

    @property
    def volume(self) -> float:
        return float(self.weight_lbs) * int(self.sets) * int(self.reps)

    def epley_1rm(self) -> float:
        """Estimated 1RM (Epley). For reps=1 returns the weight itself."""
        if self.reps <= 0:
            return 0.0
        if self.reps == 1:
            return float(self.weight_lbs)
        return float(self.weight_lbs) * (1.0 + self.reps / 30.0)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExerciseEntry:
    name: str
    sets: List[SetEntry] = field(default_factory=list)
    is_pr: bool = False
    raw: str = ""

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
        return max(s.weight_lbs for s in self.sets)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "sets": [s.to_dict() for s in self.sets],
            "is_pr": self.is_pr,
            "raw": self.raw,
            "volume": self.volume,
            "best_e1rm": self.best_e1rm,
            "best_working_weight": self.best_working_weight,
        }


@dataclass
class Session:
    date: str  # ISO YYYY-MM-DD
    session_type: str  # push | pull | legs | other
    exercises: List[ExerciseEntry] = field(default_factory=list)
    notes: str = ""
    source_file: str = ""

    @property
    def volume(self) -> float:
        return sum(e.volume for e in self.exercises)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "session_type": self.session_type,
            "exercises": [e.to_dict() for e in self.exercises],
            "notes": self.notes,
            "source_file": self.source_file,
            "volume": self.volume,
        }


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
    """Daily calories + macros (protein/carbs/fat grams)."""

    date: str
    calories: Optional[float] = None
    protein_g: Optional[float] = None
    carbs_g: Optional[float] = None
    fat_g: Optional[float] = None
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
class HealthSnapshot:
    weight: List[WeightSample] = field(default_factory=list)
    sleep: List[SleepSample] = field(default_factory=list)
    nutrition: List[NutritionDay] = field(default_factory=list)
    food_logs: List[FoodLogEntry] = field(default_factory=list)
    hydration: List[HydrationDay] = field(default_factory=list)
    calories_burned: List[CaloriesBurnedDay] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "weight": [w.to_dict() for w in self.weight],
            "sleep": [s.to_dict() for s in self.sleep],
            "nutrition": [n.to_dict() for n in self.nutrition],
            "food_logs": [f.to_dict() for f in self.food_logs],
            "hydration": [h.to_dict() for h in self.hydration],
            "calories_burned": [c.to_dict() for c in self.calories_burned],
            "error": self.error,
        }


@dataclass
class RecoveryStatus:
    label: str  # e.g. "Ready", "Moderate", "Needs Rest"
    score: float  # 0-100
    reasons: List[str] = field(default_factory=list)
    inputs: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
