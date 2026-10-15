"""Parse Naka's daily fund notes file (``investment/brief/YYYY-MM-DD.md``).

The file is written by another agent on another box, so everything in it is
untrusted text: it is size-capped, control characters are removed, links must be
http(s), and nothing is ever treated as HTML (the renderer escapes). The parser
is tolerant: it keeps what it can parse and records ``problems`` (missing
required sections, unparsable lines) instead of failing. Nothing is invented;
if the day's file is absent the state is ``missing`` and no content is shown.
The format is specified in ``investment/brief/FORMAT.md``.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.daily_brief.fund import _is_http, _parse_ts

REQUIRED_SECTIONS = ("Developments", "Relevance", "Naka: direction (advisory)", "Naka: actions taken")
RELEVANCE_WORDS = ("pinned", "watch only")
MAX_FILE_BYTES = 64 * 1024
MAX_SUMMARY = 400
MAX_BLOCK = 4000
MAX_UNKNOWN = 6
MAX_UNPARSED = 10
BRIEF_DIR = Path("investment") / "brief"

_TICKER = r"[A-Z][A-Z0-9.\-]{0,9}"
_DEV_RE = re.compile(r"^[-*] \*\*(" + _TICKER + r")\*\*\s+[\u2014\u2013-]\s+(.+?)\s+Source:\s+(\S+)\s*$")
_REL_RE = re.compile(r"^[-*] \*\*(" + _TICKER + r")\*\*:\s*(.+?)\s*$")
_HEAD_RE = re.compile(r"^## (.+?)\s*$")
_FM_RE = re.compile(r"^([a-z_]+):\s*(.*?)\s*$")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def clean_text(s: str, limit: int) -> str:
    """Remove control characters, normalize newlines, cap the length."""
    s = _CTRL_RE.sub("", s.replace("\r\n", "\n").replace("\r", "\n"))
    s = s.strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "\u2026"


def notes_path(root: Path | str, date_s: str) -> Path:
    return Path(root) / BRIEF_DIR / f"{date_s}.md"


def _split_sections(body: str) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """([(heading, lines)], preamble lines). Fenced code blocks never start a section."""
    sections: list[tuple[str, list[str]]] = []
    pre: list[str] = []
    cur: list[str] | None = None
    fenced = False
    for line in body.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        m = None if fenced else _HEAD_RE.match(line)
        if m:
            cur = []
            sections.append((m.group(1).strip(), cur))
        elif cur is not None:
            cur.append(line)
        else:
            pre.append(line)
    return sections, pre


def parse_notes(text: str, *, expect_date: str | None = None) -> dict[str, Any]:
    """Parse the file text. Returns the ``notes`` payload (without file metadata)."""
    problems: list[str] = []
    raw = text if isinstance(text, str) else ""
    if len(raw.encode("utf-8", "replace")) > MAX_FILE_BYTES:
        problems.append(f"file larger than {MAX_FILE_BYTES // 1024} KB; truncated")
        raw = raw.encode("utf-8", "replace")[:MAX_FILE_BYTES].decode("utf-8", "replace")
    raw = _CTRL_RE.sub("", raw.replace("\r\n", "\n").replace("\r", "\n"))
    lines = raw.split("\n")
    meta: dict[str, str] = {}
    body_start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                body_start = i + 1
                break
            m = _FM_RE.match(lines[i])
            if m:
                meta[m.group(1)] = m.group(2)
        else:
            problems.append("front matter is not closed with ---")
            body_start = len(lines)
    else:
        problems.append("missing front matter (---, date, filed_at, ---)")

    date_s = meta.get("date") if _DATE_RE.match(meta.get("date", "")) else None
    if "date" in meta and date_s is None:
        problems.append("front matter date is not YYYY-MM-DD")
    filed_at = meta.get("filed_at") if _parse_ts(meta.get("filed_at")) else None
    if "filed_at" not in meta:
        problems.append("front matter has no filed_at")
    elif filed_at is None:
        problems.append("front matter filed_at is not an ISO-8601 timestamp")

    sections, _pre = _split_sections("\n".join(lines[body_start:]))
    by_name: dict[str, list[str]] = {}
    unknown: list[dict[str, str]] = []
    for name, body in sections:
        if name in REQUIRED_SECTIONS:
            if name in by_name:
                problems.append(f"duplicate section: {name}")
            else:
                by_name[name] = body
        elif len(unknown) < MAX_UNKNOWN:
            unknown.append({"heading": clean_text(name, 80), "text": clean_text("\n".join(body), MAX_BLOCK)})
    missing = [s for s in REQUIRED_SECTIONS if s not in by_name]
    order = [n for n, _ in sections if n in REQUIRED_SECTIONS]
    if not missing and order != list(REQUIRED_SECTIONS):
        problems.append("required sections are out of order")

    developments: list[dict[str, str | None]] = []
    unparsed: list[str] = []

    def _unp(line: str) -> None:
        if len(unparsed) < MAX_UNPARSED:
            unparsed.append(clean_text(line, 200))

    for line in by_name.get("Developments", []):
        if not line.strip():
            continue
        m = _DEV_RE.match(line.rstrip())
        if not m:
            _unp(line)
            continue
        url = m.group(3)
        if url.lower() == "none":
            url_ok = None
        elif _is_http(url) and len(url) <= 500:
            url_ok = url
        else:
            _unp(line)
            continue
        developments.append({"ticker": m.group(1), "text": clean_text(m.group(2), MAX_SUMMARY), "url": url_ok})

    relevance: list[dict[str, str]] = []
    seen_rel: set[str] = set()
    for line in by_name.get("Relevance", []):
        if not line.strip():
            continue
        m = _REL_RE.match(line.rstrip())
        if not m or m.group(2).strip().lower() not in RELEVANCE_WORDS or m.group(1) in seen_rel:
            _unp(line)
            continue
        seen_rel.add(m.group(1))
        relevance.append({"ticker": m.group(1), "word": m.group(2).strip().lower()})
    if unparsed:
        problems.append(f"{len(unparsed)} line(s) in Developments/Relevance did not match the format")

    if date_s is None and not by_name and not meta:
        state = "invalid"
        problems.append("not in the agreed format")
    else:
        state = "filed"
    return {
        "state": state,
        "date": date_s,
        "filed_at": filed_at,
        "author": clean_text(meta.get("author", ""), 40) or None,
        "developments": developments,
        "relevance": relevance,
        "direction": clean_text("\n".join(by_name["Naka: direction (advisory)"]), MAX_BLOCK) if "Naka: direction (advisory)" in by_name else None,
        "actions": clean_text("\n".join(by_name["Naka: actions taken"]), MAX_BLOCK) if "Naka: actions taken" in by_name else None,
        "missing_sections": missing,
        "unparsed": unparsed,
        "unknown_sections": unknown,
        "problems": problems,
    }


def _commit_time(root: Path, rel: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%cI", "--", str(rel)],
            capture_output=True, text=True, timeout=8, check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out if _parse_ts(out) else None


def load_notes(root: Path | str, date_s: str, *, with_git: bool = True) -> dict[str, Any]:
    """Notes for the edition date. Never falls back to another day's file."""
    root = Path(root)
    rel = BRIEF_DIR / f"{date_s}.md"
    path = root / rel
    base: dict[str, Any] = {"edition_date": date_s, "file": str(rel)}
    if not path.is_file():
        earlier = sorted(p.stem for p in (root / BRIEF_DIR).glob("????-??-??.md") if p.stem < date_s) if (root / BRIEF_DIR).is_dir() else []
        return {**base, "state": "missing",
                "note": f"Naka's fund notes not yet filed for {date_s}.",
                "latest_on_disk": earlier[-1] if earlier else None}
    try:
        text = path.read_bytes()[: MAX_FILE_BYTES + 1024].decode("utf-8", "replace")
    except OSError:
        return {**base, "state": "missing", "note": f"Naka's fund notes not yet filed for {date_s}.", "latest_on_disk": None}
    parsed = parse_notes(text, expect_date=date_s)
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
    out = {**base, **parsed, "mtime": mtime, "commit_time": _commit_time(root, rel) if with_git else None}
    if parsed["state"] == "filed" and parsed["date"] is not None and parsed["date"] != date_s:
        out["state"] = "mismatch"
        out["note"] = (f"The file for {date_s} says it is dated {parsed['date']}; not shown as today's notes. "
                       f"Naka's fund notes not yet filed for {date_s}.")
        for k in ("developments", "relevance", "direction", "actions", "unknown_sections", "unparsed"):
            out[k] = [] if isinstance(out.get(k), list) else None
    elif parsed["state"] == "invalid":
        out["note"] = f"A file exists for {date_s} but is not in the agreed format (see investment/brief/FORMAT.md)."
    return out
