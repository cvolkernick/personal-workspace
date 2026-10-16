"""Weekly lifts-only training log PDF (#1145).

Reads the same session rows the dashboard reads. No second log format.
Week bounds are America/New_York civil dates. Quest seeds and non-lift
session types stay out of the days and the week totals.

FitDash stores when a session was saved (``closed_at`` / ``created_at``).
It does not store a duration. The PDF's Time line is that save clock.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from .models import ExerciseEntry, SetEntry, is_zero_volume_status_log
from .quest_workout_log import is_unedited_seed

TRAINING_TZ = "America/New_York"
LIFT_TYPES = ("push", "pull", "legs")
_TZ = ZoneInfo(TRAINING_TZ)


def filename_for(week_start: date) -> str:
    return f"fitdash-training-log-{week_start.isoformat()}.pdf"


def _as_utc(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def civil_today(now: Optional[datetime] = None) -> date:
    return _as_utc(now).astimezone(_TZ).date()


def week_containing(day: date) -> Tuple[date, date]:
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def previous_completed_week(today: date) -> Tuple[date, date]:
    """Monday-Sunday that ended before the week containing ``today``."""
    this_monday, _end = week_containing(today)
    start = this_monday - timedelta(days=7)
    return start, start + timedelta(days=6)


def _week_param(query: str) -> str:
    vals = parse_qs(query or "", keep_blank_values=False).get("week") or []
    if not vals:
        return ""
    return str(vals[0] or "").strip()


def _bad_week(message: str) -> Dict[str, Any]:
    return {"ok": False, "error": "bad_week", "message": message}


def resolve_week(
    query: str, *, now: Optional[datetime] = None
) -> Tuple[Optional[Tuple[date, date]], Optional[Dict[str, Any]]]:
    """Default is the previous completed week. ``week=YYYY-MM-DD`` snaps to that Monday.

    A week that has not started is rejected. The current week is allowed
    when the caller names a day inside it.
    """
    raw = _week_param(query)
    today = civil_today(now)
    current_start, _current_end = week_containing(today)
    if not raw:
        return previous_completed_week(today), None
    try:
        day = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None, _bad_week(f"week must be YYYY-MM-DD. Got {raw!r}.")
    start, end = week_containing(day)
    if start > current_start:
        return None, _bad_week(
            f"week {start.isoformat()} has not started yet "
            f"(America/New_York). Got {raw!r}."
        )
    return (start, end), None


def _session_get(session: Any, key: str) -> Any:
    if isinstance(session, dict):
        return session.get(key)
    return getattr(session, key, None)


def _session_type(session: Any) -> str:
    return str(_session_get(session, "session_type") or "").strip().lower()


def _session_date(session: Any) -> str:
    return str(_session_get(session, "date") or "")[:10]


def _session_notes(session: Any) -> str:
    return str(_session_get(session, "notes") or "").strip()


def _session_closed(session: Any) -> str:
    raw = _session_get(session, "closed_at") or _session_get(session, "created_at")
    return str(raw or "").strip()


def _exercises(session: Any) -> list:
    raw = _session_get(session, "exercises") or []
    return list(raw) if isinstance(raw, (list, tuple)) else []


def _movement_only(ex: Any) -> bool:
    if isinstance(ex, dict):
        return bool(ex.get("movement_only"))
    return bool(getattr(ex, "movement_only", False))


def _as_entry(ex: Any) -> Optional[ExerciseEntry]:
    if isinstance(ex, ExerciseEntry):
        return ex
    if not isinstance(ex, dict):
        return None
    name = str(ex.get("name") or ex.get("exercise") or "").strip()
    if not name:
        return None
    sets: List[SetEntry] = []
    raw_sets = ex.get("sets")
    if isinstance(raw_sets, list):
        for st in raw_sets:
            if not isinstance(st, dict):
                continue
            try:
                weight = float(st.get("weight_lbs") or 0)
                count = int(st.get("sets") or 0)
                reps = int(st.get("reps") or 0)
            except (TypeError, ValueError):
                continue
            if isinstance(st.get("sets"), bool) or isinstance(st.get("reps"), bool):
                continue
            sets.append(SetEntry(weight_lbs=weight, sets=count, reps=reps))
    return ExerciseEntry(
        name=name,
        sets=sets,
        is_pr=bool(ex.get("is_pr")),
        raw=str(ex.get("raw") or ""),
        quest_seeded=bool(ex.get("quest_seeded")),
    )


def _real_sets(entry: ExerciseEntry) -> List[SetEntry]:
    kept: List[SetEntry] = []
    for st in entry.sets or []:
        if isinstance(st.sets, bool) or isinstance(st.reps, bool):
            continue
        try:
            count = int(st.sets)
            reps = int(st.reps)
            weight = float(st.weight_lbs)
        except (TypeError, ValueError):
            continue
        if count <= 0 or reps <= 0 or weight < 0 or not math.isfinite(weight):
            continue
        kept.append(SetEntry(weight_lbs=weight, sets=count, reps=reps))
    return kept


def logged_lift_exercise(ex: Any) -> Optional[ExerciseEntry]:
    """One real logged lift, or None when the row is a seed, status, or empty."""
    if _movement_only(ex) or is_zero_volume_status_log(ex):
        return None
    entry = _as_entry(ex)
    if entry is None or is_unedited_seed(entry) or is_zero_volume_status_log(entry):
        return None
    sets = _real_sets(entry)
    if not sets:
        return None
    return ExerciseEntry(
        name=entry.name,
        sets=sets,
        is_pr=bool(entry.is_pr),
        raw="",
        quest_seeded=False,
    )


def _parse_clock(raw: str) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(_TZ)


def format_clock(raw: str) -> str:
    parsed = _parse_clock(raw)
    if parsed is None:
        return ""
    hour = parsed.hour % 12 or 12
    ampm = "AM" if parsed.hour < 12 else "PM"
    return f"{parsed.strftime('%a')} {hour}:{parsed.minute:02d} {ampm} ET"


def _fmt_weight(weight: float) -> str:
    if abs(weight - round(weight)) < 1e-9:
        return str(int(round(weight)))
    return f"{weight:.2f}".rstrip("0").rstrip(".")


def _fmt_lb(volume: float) -> str:
    if abs(volume - round(volume)) < 1e-6:
        return f"{int(round(volume)):,} lb"
    return f"{volume:,.1f} lb"


def format_sets(sets: Sequence[SetEntry]) -> str:
    """sets x reps x weight, one group at a time."""
    parts = []
    for st in sets:
        parts.append(
            f"{int(st.sets)} x {int(st.reps)} @ {_fmt_weight(float(st.weight_lbs))} lb"
        )
    return ", ".join(parts)


def _format_day(iso: str) -> str:
    parsed = datetime.strptime(iso, "%Y-%m-%d")
    return parsed.strftime("%A, %B ") + str(parsed.day) + parsed.strftime(", %Y")


def collect_week(
    sessions: Sequence[Any], start: date, end: date
) -> List[Dict[str, Any]]:
    """Lift sessions inside the inclusive civil window, real sets only."""
    start_s = start.isoformat()
    end_s = end.isoformat()
    rows: List[Dict[str, Any]] = []
    for session in sessions or []:
        st = _session_type(session)
        if st not in LIFT_TYPES:
            continue
        day = _session_date(session)
        if len(day) != 10 or day < start_s or day > end_s:
            continue
        exercises = []
        for ex in _exercises(session):
            kept = logged_lift_exercise(ex)
            if kept is None:
                continue
            exercises.append(kept)
        if not exercises:
            continue
        volume = sum(float(ex.volume) for ex in exercises)
        rows.append(
            {
                "date": day,
                "session_type": st,
                "exercises": exercises,
                "notes": _session_notes(session),
                "clock": format_clock(_session_closed(session)),
                "volume": volume,
            }
        )
    rows.sort(key=lambda row: (row["date"], LIFT_TYPES.index(row["session_type"])))
    return rows


def _pdf_escape(text: str) -> str:
    cleaned = (
        str(text or "")
        .replace("\u00d7", "x")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2026", "...")
    )
    out: List[str] = []
    for ch in cleaned:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == "(":
            out.append("\\(")
        elif ch == ")":
            out.append("\\)")
        elif ch in "\r\n\t":
            out.append(" ")
        elif 32 <= code <= 126 or 160 <= code <= 255:
            out.append(ch)
        else:
            out.append("?")
    return "".join(out)


def _wrap(text: str, width: int = 88) -> List[str]:
    words = str(text or "").split()
    if not words:
        return [""]
    lines: List[str] = []
    cur = ""
    for word in words:
        while len(word) > width:
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:width])
            word = word[width:]
        trial = word if not cur else cur + " " + word
        if len(trial) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _text_op(x: int, y: int, size: int, font: str, text: str) -> bytes:
    escaped = _pdf_escape(text)
    return (
        f"BT /{font} {size} Tf {x} {y} Td ({escaped}) Tj ET"
    ).encode("latin-1")


class _Doc:
    def __init__(self) -> None:
        self.pages: List[List[bytes]] = []
        self.ops: List[bytes] = []
        self.y = 0
        self._open(continued=False)

    def _open(self, *, continued: bool) -> None:
        if self.ops:
            self.pages.append(self.ops)
        self.ops = []
        self.y = 750
        if continued:
            self.line("FitDash training log (continued)", size=12, bold=True)
            self.blank()

    def line(self, text: str, *, size: int = 11, bold: bool = False) -> None:
        font = "F2" if bold else "F1"
        step = size + 4
        for part in _wrap(text):
            if self.y < 54 + size:
                self._open(continued=True)
                font = "F2" if bold else "F1"
            self.ops.append(_text_op(54, self.y, size, font, part))
            self.y -= step

    def blank(self) -> None:
        self.y -= 8
        if self.y < 54:
            self._open(continued=True)

    def finish(self) -> List[List[bytes]]:
        if self.ops:
            self.pages.append(self.ops)
        if not self.pages:
            self.pages.append([])
        return self.pages


def _render_pdf(pages: Sequence[Sequence[bytes]]) -> bytes:
    bodies: List[bytes] = [b""]

    def reserve() -> int:
        bodies.append(b"")
        return len(bodies) - 1

    catalog_id = reserve()
    pages_id = reserve()
    font_id = reserve()
    bold_id = reserve()
    kids: List[str] = []
    for ops in pages:
        stream = b"\n".join(ops) + b"\n"
        content_id = reserve()
        page_id = reserve()
        bodies[content_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"endstream"
        )
        bodies[page_id] = (
            f"<< /Type /Page /Parent {pages_id} 0 R "
            f"/MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R /F2 {bold_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode("ascii")
        kids.append(f"{page_id} 0 R")
    bodies[font_id] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    bodies[bold_id] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"
    bodies[pages_id] = (
        f"<< /Type /Pages /Count {len(kids)} /Kids [{' '.join(kids)}] >>"
    ).encode("ascii")
    bodies[catalog_id] = (
        f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("ascii")
    )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, body in enumerate(bodies[1:], start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("ascii")
        out += body
        out += b"\nendobj\n"
    xref = len(out)
    count = len(bodies) - 1
    out += f"xref\n0 {count + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer << /Size {count + 1} /Root {catalog_id} 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


def render_week_pdf(
    rows: Sequence[Dict[str, Any]], start: date, end: date
) -> bytes:
    doc = _Doc()
    doc.line("FitDash training log", size=16, bold=True)
    doc.line(
        f"Week of {_format_day(start.isoformat())} through {_format_day(end.isoformat())}",
        size=12,
        bold=True,
    )
    doc.line(
        "America/New_York. Resistance sessions only. Quest seeds are omitted.",
        size=11,
    )
    doc.blank()
    volume = sum(float(row["volume"]) for row in rows)
    clocks = [str(row["clock"]) for row in rows if row.get("clock")]
    doc.line("Week totals", size=13, bold=True)
    doc.line(f"Sessions: {len(rows)}")
    doc.line(f"Volume: {_fmt_lb(volume)}")
    doc.line("Time: " + ("; ".join(clocks) if clocks else "-"))
    doc.line("Time is the save clock. FitDash does not store a duration.")
    doc.blank()
    if not rows:
        doc.line("No lifts logged.")
    else:
        current = ""
        for row in rows:
            if row["date"] != current:
                if current:
                    doc.blank()
                current = row["date"]
                doc.line(_format_day(current), size=13, bold=True)
            head = row["session_type"].upper()
            if row.get("clock"):
                head = f"{head} · logged {row['clock']}"
            doc.line(head, bold=True)
            for ex in row["exercises"]:
                pr = " (PR)" if ex.is_pr else ""
                doc.line(f"{ex.name}: {format_sets(ex.sets)}{pr}")
            if row.get("notes"):
                doc.line(f"Notes: {row['notes']}")
    return _render_pdf(doc.finish())


def training_log_pdf_result(
    sessions: Sequence[Any],
    query: str = "",
    *,
    now: Optional[datetime] = None,
) -> Tuple[int, Dict[str, Any]]:
    window, err = resolve_week(query, now=now)
    if err or window is None:
        return 400, err or _bad_week("week is missing.")
    start, end = window
    rows = collect_week(sessions, start, end)
    pdf = render_week_pdf(rows, start, end)
    return 200, {
        "ok": True,
        "pdf": pdf,
        "filename": filename_for(start),
        "content_type": "application/pdf",
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "sessions": len(rows),
    }


def training_log_from_store(
    sessions: Sequence[Any],
    query: str = "",
    *,
    store_error: str = "",
    now: Optional[datetime] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Empty store plus an error is a failure, not a 'no lifts' PDF."""
    rows = list(sessions or [])
    if str(store_error or "").strip() and not rows:
        return 503, {
            "ok": False,
            "error": "workout_store_unavailable",
            "message": str(store_error).strip(),
        }
    return training_log_pdf_result(rows, query, now=now)


def write_pdf_response(handler: Any, pdf: bytes, filename: str) -> None:
    safe = (
        str(filename or "fitdash-training-log.pdf")
        .replace("\\", "")
        .replace('"', "")
        .replace("\r", "")
        .replace("\n", "")
    )
    handler.send_response(200)
    handler.send_header("Content-Type", "application/pdf")
    handler.send_header("Content-Disposition", f'attachment; filename="{safe}"')
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Length", str(len(pdf)))
    handler.end_headers()
    handler.wfile.write(pdf)
