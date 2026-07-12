"""Parse and serialize PPL markdown lift logs (fitness/workouts/*.md format)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .models import ExerciseEntry, Session, SetEntry

SESSION_HEADER_RE = re.compile(
    r"^##\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})\s*-\s*(.+)$"
)
# Exercise line: "- Name: 50 lbs x 1 x 12, 45 lbs x 1 x 12 (PR!)"
EXERCISE_LINE_RE = re.compile(
    r"^-\s*-?\s*(.+?):\s*(.+)$"
)
# Single set group: "50 lbs x 1 x 12" or "50 lbs x 12" or "155 x 10"
SET_GROUP_RE = re.compile(
    r"(?P<w>\d+(?:\.\d+)?)\s*(?:lbs)?\s*[xX×]\s*"
    r"(?:(?P<a>\d+)\s*[xX×]\s*(?P<b>\d+)|(?P<reps_only>\d+))",
    re.IGNORECASE,
)
ARROW_RE = re.compile(
    r"(?P<weights>(?:\d+(?:\.\d+)?\s*(?:→|->)\s*)+\d+(?:\.\d+)?)\s*lbs?\s*[xX×]\s*(?P<reps>\d+)",
    re.IGNORECASE,
)
SKIP_PREFIXES = (
    "note:",
    "notes:",
    "next session",
    "add ",
    "trendline",
)
SESSION_TYPES = ("push", "pull", "legs")


def _parse_date(month: str, day: str, year: str) -> str:
    dt = datetime.strptime(f"{month} {day} {year}", "%B %d %Y")
    return dt.strftime("%Y-%m-%d")


def _format_date_header(iso_date: str) -> str:
    dt = datetime.strptime(iso_date, "%Y-%m-%d")
    # "May 26, 2026"
    return dt.strftime("%B %-d, %Y").replace(" 0", " ") if False else dt.strftime("%B ") + str(dt.day) + dt.strftime(", %Y")


def _session_type_from_path(path: str) -> str:
    lower = path.lower()
    for st in SESSION_TYPES:
        if st in lower:
            return st
    return "other"


def _parse_set_groups(payload: str) -> Tuple[List[SetEntry], bool]:
    is_pr = "PR!" in payload.upper() or "(PR)" in payload.upper()
    # strip trailing notes in parens except we already captured PR
    clean = re.sub(r"\([^)]*\)", "", payload).strip()
    sets: List[SetEntry] = []

    # Arrow drop sets: "40 → 35 → 30 lbs x 10" → three sets of 1x10
    for m in ARROW_RE.finditer(clean):
        weights_raw = m.group("weights")
        reps = int(m.group("reps"))
        parts = re.split(r"\s*(?:→|->)\s*", weights_raw)
        for p in parts:
            p = p.strip()
            if not p:
                continue
            try:
                w = float(p)
            except ValueError:
                continue
            sets.append(SetEntry(weight_lbs=w, sets=1, reps=reps))
        # remove matched region so SET_GROUP_RE doesn't double-count
        clean = clean[: m.start()] + " " + clean[m.end() :]

    for m in SET_GROUP_RE.finditer(clean):
        w = float(m.group("w"))
        if m.group("a") is not None and m.group("b") is not None:
            a, b = int(m.group("a")), int(m.group("b"))
            # Convention in these logs: almost always "weight x sets x reps".
            # Ambiguous cases like "45 lbs x 10 x 3" (May 9 legs RDLs) appear
            # occasionally; prefer sets-then-reps when a <= 10 and b >= a,
            # else if a > 12 and b <= 6 treat as reps x sets (rare).
            sets_n, reps_n = a, b
            if a > 15 and b <= 6:
                # e.g. hypothetical "weight x 20 x 3" meaning 20 reps x 3 sets
                sets_n, reps_n = b, a
            sets.append(SetEntry(weight_lbs=w, sets=sets_n, reps=reps_n))
        elif m.group("reps_only") is not None:
            sets.append(
                SetEntry(weight_lbs=w, sets=1, reps=int(m.group("reps_only")))
            )

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
    # broken lines like "- : 25 lbs..."
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
    # normalize duplicate "DB DB"
    name = re.sub(r"\bDB\s+DB\b", "DB", name)
    return ExerciseEntry(name=name, sets=sets, is_pr=is_pr, raw=line)


def parse_workout_markdown(
    text: str, session_type: str = "other", source_file: str = ""
) -> List[Session]:
    """Parse a full push/pull/legs markdown file into sessions."""
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
            month, day, year, _rest = hm.group(1), hm.group(2), hm.group(3), hm.group(4)
            # skip non-session headers (Trendline Analysis, Exercise Directory)
            rest_l = _rest.lower()
            if "session" not in rest_l and "complete" not in rest_l:
                # still allow bare date headers that look like sessions
                if not re.search(r"session|complete|workout|day", rest_l):
                    # e.g. "## Trendline Analysis" won't match date pattern anyway
                    pass
            try:
                iso = _parse_date(month, day, year)
            except ValueError:
                current = None
                continue
            current = Session(
                date=iso,
                session_type=session_type,
                source_file=source_file,
            )
            notes_buf = []
            continue

        if current is None:
            continue

        # stop consuming once we hit a non-session H2 that isn't a date
        if line.startswith("## ") and not SESSION_HEADER_RE.match(line.strip()):
            close_current()
            continue
        if line.startswith("# ") or line.startswith("---"):
            # section boundary inside file after sessions
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
            continue

        # freeform note bullets under a session
        if stripped.startswith("-") and ":" not in stripped.split("http")[0]:
            notes_buf.append(stripped.lstrip("- ").strip())

    close_current()
    # sort newest first for display consistency
    sessions.sort(key=lambda s: s.date, reverse=True)
    return sessions


def parse_all_workouts(files: Dict[str, str]) -> List[Session]:
    """files: path -> markdown content"""
    all_sessions: List[Session] = []
    for path, text in files.items():
        st = _session_type_from_path(path)
        all_sessions.extend(
            parse_workout_markdown(text, session_type=st, source_file=path)
        )
    all_sessions.sort(key=lambda s: (s.date, s.session_type), reverse=True)
    return all_sessions


def format_set_entry(s: SetEntry) -> str:
    if s.sets == 1:
        # still use full triple for round-trip consistency with dominant style
        return f"{_fmt_w(s.weight_lbs)} lbs x {s.sets} x {s.reps}"
    return f"{_fmt_w(s.weight_lbs)} lbs x {s.sets} x {s.reps}"


def _fmt_w(w: float) -> str:
    if abs(w - round(w)) < 1e-9:
        return str(int(round(w)))
    return f"{w:g}"


def format_exercise_line(ex: ExerciseEntry) -> str:
    body = ", ".join(format_set_entry(s) for s in ex.sets)
    pr = " (PR!)" if ex.is_pr else ""
    return f"- {ex.name}: {body}{pr}"


def format_session_block(session: Session) -> str:
    header = f"## {_format_date_header(session.date)} - Session Complete"
    lines = [header]
    for ex in session.exercises:
        lines.append(format_exercise_line(ex))
    if session.notes:
        lines.append("")
        lines.append(f"Notes: {session.notes}")
    lines.append("")
    return "\n".join(lines)


def append_session_to_markdown(existing: str, session: Session) -> str:
    """Insert a new session block after the title/chart header, before older sessions."""
    block = format_session_block(session)
    lines = existing.splitlines(keepends=True)
    if not lines:
        return block

    insert_at = 0
    # after first H1 and any immediate chart/blank lines, before first session header
    seen_title = False
    for i, line in enumerate(lines):
        if line.startswith("# "):
            seen_title = True
            insert_at = i + 1
            continue
        if not seen_title:
            continue
        if SESSION_HEADER_RE.match(line.strip()):
            insert_at = i
            break
        if line.startswith("## "):
            insert_at = i
            break
        insert_at = i + 1

    # ensure trailing newline before insert
    prefix = "".join(lines[:insert_at])
    suffix = "".join(lines[insert_at:])
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if not prefix.endswith("\n\n"):
        if prefix.endswith("\n"):
            prefix += "\n"
        else:
            prefix += "\n\n"
    return prefix + block + ("\n" if not block.endswith("\n") else "") + suffix


WORKOUT_PATHS = {
    "push": "fitness/workouts/push.md",
    "pull": "fitness/workouts/pull.md",
    "legs": "fitness/workouts/legs.md",
}
