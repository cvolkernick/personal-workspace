"""Minimal PPL markdown parser for Vercel preview lifts. Not the Pi store."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

SESSION_HEADER_RE = re.compile(
    r"^##\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})\s*-\s*(.+)$"
)
EXERCISE_LINE_RE = re.compile(r"^-\s*-?\s*(.+?):\s*(.+)$")
SET_GROUP_RE = re.compile(
    r"(?P<w>\d+(?:\.\d+)?)\s*(?:lbs)?\s*[xX×]\s*"
    r"(?:(?P<a>\d+)\s*[xX×]\s*(?P<b>\d+)|(?P<reps_only>\d+))",
    re.IGNORECASE,
)
ARROW_RE = re.compile(
    r"(?P<weights>(?:\d+(?:\.\d+)?\s*(?:→|->)\s*)+\d+(?:\.\d+)?)\s*lbs?\s*[xX×]\s*(?P<reps>\d+)",
    re.IGNORECASE,
)
SKIP_PREFIXES = ("note:", "notes:", "next session", "add ", "trendline")
SESSION_TYPES = ("push", "pull", "legs")
WORKOUT_PATHS = {
    "push": "fitness/workouts/push.md",
    "pull": "fitness/workouts/pull.md",
    "legs": "fitness/workouts/legs.md",
}


@dataclass
class SetEntry:
    weight_lbs: float
    sets: int
    reps: int

    @property
    def volume(self) -> float:
        return float(self.weight_lbs) * int(self.sets) * int(self.reps)


@dataclass
class ExerciseEntry:
    name: str
    sets: List[SetEntry] = field(default_factory=list)
    is_pr: bool = False
    raw: str = ""

    @property
    def volume(self) -> float:
        return sum(s.volume for s in self.sets)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "sets": [
                {"weight_lbs": s.weight_lbs, "sets": s.sets, "reps": s.reps}
                for s in self.sets
            ],
            "is_pr": self.is_pr,
            "raw": self.raw,
            "volume": self.volume,
        }


@dataclass
class Session:
    date: str
    session_type: str
    exercises: List[ExerciseEntry] = field(default_factory=list)
    notes: str = ""
    source_file: str = ""

    @property
    def volume(self) -> float:
        return sum(e.volume for e in self.exercises)

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "session_type": self.session_type,
            "exercises": [e.to_dict() for e in self.exercises],
            "notes": self.notes,
            "source_file": self.source_file,
            "volume": self.volume,
        }


def _parse_date(month: str, day: str, year: str) -> str:
    return datetime.strptime(f"{month} {day} {year}", "%B %d %Y").strftime("%Y-%m-%d")


def _session_type_from_path(path: str) -> str:
    lower = path.lower()
    for st in SESSION_TYPES:
        if st in lower:
            return st
    return "other"


def _parse_set_groups(payload: str) -> Tuple[List[SetEntry], bool]:
    is_pr = "PR!" in payload.upper() or "(PR)" in payload.upper()
    clean = re.sub(r"\([^)]*\)", "", payload).strip()
    sets: List[SetEntry] = []
    for m in ARROW_RE.finditer(clean):
        reps = int(m.group("reps"))
        for p in re.split(r"\s*(?:→|->)\s*", m.group("weights")):
            p = p.strip()
            if not p:
                continue
            try:
                sets.append(SetEntry(weight_lbs=float(p), sets=1, reps=reps))
            except ValueError:
                continue
        clean = clean[: m.start()] + " " + clean[m.end() :]
    for m in SET_GROUP_RE.finditer(clean):
        w = float(m.group("w"))
        if m.group("a") is not None and m.group("b") is not None:
            a, b = int(m.group("a")), int(m.group("b"))
            sets_n, reps_n = a, b
            if a > 15 and b <= 6:
                sets_n, reps_n = b, a
            sets.append(SetEntry(weight_lbs=w, sets=sets_n, reps=reps_n))
        elif m.group("reps_only") is not None:
            sets.append(SetEntry(weight_lbs=w, sets=1, reps=int(m.group("reps_only"))))
    return sets, is_pr


def parse_exercise_line(line: str) -> Optional[ExerciseEntry]:
    line = line.strip()
    if not line.startswith("-"):
        return None
    low = line.lower().lstrip("-").strip()
    if any(low.startswith(p) for p in SKIP_PREFIXES):
        return None
    if "not done" in low or "skipped" in low:
        return None
    m = EXERCISE_LINE_RE.match(line)
    if not m:
        return None
    name = m.group(1).strip().lstrip("-").strip()
    if not name or name in {":", "-"}:
        return None
    payload = m.group(2).strip()
    if not re.search(r"\d", payload):
        return None
    sets, is_pr = _parse_set_groups(payload)
    if not sets:
        return None
    name = re.sub(r"\bDB\s+DB\b", "DB", name)
    return ExerciseEntry(name=name, sets=sets, is_pr=is_pr, raw=line)


def parse_workout_markdown(
    text: str, session_type: str = "other", source_file: str = ""
) -> List[Session]:
    if source_file and session_type == "other":
        session_type = _session_type_from_path(source_file)
    sessions: List[Session] = []
    current: Optional[Session] = None
    notes_buf: List[str] = []

    def close_current() -> None:
        nonlocal current, notes_buf
        if current is not None:
            if notes_buf:
                current.notes = "\n".join(notes_buf).strip()
            if current.exercises:
                sessions.append(current)
        current = None
        notes_buf = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        hm = SESSION_HEADER_RE.match(line.strip())
        if hm:
            close_current()
            try:
                iso = _parse_date(hm.group(1), hm.group(2), hm.group(3))
            except ValueError:
                current = None
                continue
            current = Session(date=iso, session_type=session_type, source_file=source_file)
            notes_buf = []
            continue
        if current is None:
            continue
        if line.startswith("## ") and not SESSION_HEADER_RE.match(line.strip()):
            close_current()
            continue
        if line.startswith("---"):
            close_current()
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("note") or stripped.lower().startswith("**note"):
            notes_buf.append(re.sub(r"^\*?\*?notes?:\*?\*?\s*", "", stripped, flags=re.I))
            continue
        ex = parse_exercise_line(stripped)
        if ex:
            current.exercises.append(ex)
    close_current()
    sessions.sort(key=lambda s: s.date, reverse=True)
    return sessions
