"""Daily Brief edition store: schema, publish, reads, To-Do bucketing.

Moved out of ``financial-command/brief.py`` (#1091). One JSON file per edition
under the store (default ``~/.local/share/fcc/brief-editions/YYYY-MM-DD-{am|pm}.json``,
Eastern date). Stdlib only, no network, no credentials.

The page itself is served by the Horizon host under ``/daily-brief`` (see
``render.py``). Nothing here reads FCC books or Horizon model data.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BASE_PATH = "/daily-brief"
ET = ZoneInfo("America/New_York")
EDITIONS = ("am", "pm")
TODO_STATUSES = ("Not started", "In progress", "Done")
BUCKETS = ("overdue", "today", "week", "later", "nodate")
BUCKET_TITLES = {
    "overdue": "Overdue",
    "today": "Due today",
    "week": "Due this week",
    "later": "Later",
    "nodate": "No date",
}
STALE_HOURS = 14.0
MAC_TASKS_TITLE = "Mac tasks"
MAC_TASKS_EMPTY = "Nothing waiting at the Mac"
MAC_TASKS_URL = "https://app.notion.com/p/3efcba2ad31b814aa4ece8389594cdc5"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(am|pm)\.json$")
_SECTION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
# Notion enhanced-Markdown to-do line: "- [ ] text" / "- [x] text", any indent (nested).
_TODO_LINE_RE = re.compile(r"^(?P<indent>[ \t]*)[-*+] \[(?P<mark>[ xX])\][ \t]+(?P<text>.*\S)[ \t]*$")


class BriefError(ValueError):
    """Schema or store error with a list of human-readable problems."""

    def __init__(self, problems: list[str] | str):
        if isinstance(problems, str):
            problems = [problems]
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


# --------------------------------------------------------------------------- store


def store_dir() -> Path:
    """Edition store. DAILY_BRIEF_DIR, then the legacy FCC_BRIEF_DIR, then the default.

    The default path is unchanged from the FCC era, so no migration is needed.
    """
    for var in ("DAILY_BRIEF_DIR", "FCC_BRIEF_DIR"):
        raw = os.environ.get(var, "").strip()
        if raw:
            return Path(raw).expanduser()
    return Path.home() / ".local" / "share" / "fcc" / "brief-editions"


def edition_url(key: tuple[str, str]) -> str:
    """Path of an edition permalink on the Horizon host."""
    return f"{BASE_PATH}/{key[0]}/{key[1]}"


def edition_filename(d: str, edition: str) -> str:
    if not _DATE_RE.match(d or "") or edition not in EDITIONS:
        raise BriefError(f"bad edition key {d!r}/{edition!r}")
    return f"{d}-{edition}.json"


def parse_filename(name: str) -> tuple[str, str] | None:
    m = _FILE_RE.match(name)
    if not m:
        return None
    try:
        date.fromisoformat(m.group(1))
    except ValueError:
        return None
    return m.group(1), m.group(2)


def eastern_now(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ET)


def eastern_date_for(ts: datetime | None = None) -> str:
    return eastern_now(ts).date().isoformat()


def edition_for(ts: datetime | None = None) -> str:
    """am before 15:00 ET, pm after (the ~8 AM / ~8 PM routines)."""
    return "am" if eastern_now(ts).hour < 15 else "pm"


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt


# ----------------------------------------------------------------------- schema


def _is_http(url: Any) -> bool:
    return isinstance(url, str) and url.startswith(("https://", "http://"))


def _check_link(problems: list[str], where: str, url: Any) -> None:
    if url in (None, ""):
        return
    if not _is_http(url):
        problems.append(f"{where}: link must be an http(s) URL")


def _check_due(raw: Any) -> bool:
    if raw in (None, ""):
        return True
    if not isinstance(raw, str):
        return False
    if _DATE_RE.match(raw):
        try:
            date.fromisoformat(raw)
            return True
        except ValueError:
            return False
    return _parse_ts(raw) is not None


def normalize(edition: dict, *, now: datetime | None = None) -> dict:
    """Fill derivable fields (date/edition/published_at) without changing content."""
    if not isinstance(edition, dict):
        raise BriefError("edition must be a JSON object")
    ed = dict(edition)
    pub = _parse_ts(ed.get("published_at"))
    if pub is None and ed.get("published_at") in (None, ""):
        pub = eastern_now(now)
        ed["published_at"] = pub.isoformat(timespec="seconds")
    if not ed.get("date") and pub is not None:
        ed["date"] = eastern_date_for(pub)
    if not ed.get("edition") and pub is not None:
        ed["edition"] = edition_for(pub)
    if isinstance(ed.get("edition"), str):
        ed["edition"] = ed["edition"].strip().lower()
    for key in ("sections", "todos"):
        if key not in ed:
            ed[key] = []
    if isinstance(ed.get("todos"), list):
        # Snapshot is "every row not Done"; drop Done rows instead of failing the publish.
        ed["todos"] = [t for t in ed["todos"] if not (isinstance(t, dict) and t.get("status") == "Done")]
    mt = ed.get("mac_tasks")
    if isinstance(mt, dict) and isinstance(mt.get("items"), list):
        # Snapshot is "every UNCHECKED box"; accept bare strings, drop checked items.
        mt = dict(mt)
        items = []
        for it in mt["items"]:
            if isinstance(it, str):
                it = {"text": it}
            if isinstance(it, dict) and it.get("checked") is True:
                continue
            items.append(it)
        mt["items"] = items
        ed["mac_tasks"] = mt
    return ed


def validate(edition: dict) -> list[str]:
    """Return a list of schema problems (empty list = valid)."""
    p: list[str] = []
    if not isinstance(edition, dict):
        return ["edition must be a JSON object"]
    d = edition.get("date")
    if not isinstance(d, str) or not _DATE_RE.match(d):
        p.append("date: required, YYYY-MM-DD (Eastern)")
    else:
        try:
            date.fromisoformat(d)
        except ValueError:
            p.append("date: not a real calendar date")
    if edition.get("edition") not in EDITIONS:
        p.append("edition: required, 'am' or 'pm'")
    for key in ("vol", "no"):
        v = edition.get(key)
        if v is not None and not (isinstance(v, (int, str)) and not isinstance(v, bool)):
            p.append(f"{key}: int or string")
    if _parse_ts(edition.get("published_at")) is None:
        p.append("published_at: required ISO-8601 timestamp")
    for key in ("theme",):
        v = edition.get(key)
        if v is not None and not isinstance(v, str):
            p.append(f"{key}: string")
    q = edition.get("quote")
    if q is not None and not isinstance(q, str):
        if not (isinstance(q, dict) and isinstance(q.get("text"), str)):
            p.append("quote: string or {text, author?}")
    w = edition.get("weather")
    if w is not None and not isinstance(w, (str, dict)):
        p.append("weather: string or object {summary, high, low, ...}")

    fp = edition.get("front_page")
    if not isinstance(fp, dict):
        p.append("front_page: required object {headline, subhead?, body?, image?}")
    else:
        if not isinstance(fp.get("headline"), str) or not fp["headline"].strip():
            p.append("front_page.headline: required non-empty string")
        for k in ("subhead", "body"):
            if fp.get(k) is not None and not isinstance(fp[k], str):
                p.append(f"front_page.{k}: string")
        _check_link(p, "front_page.image", fp.get("image"))

    secs = edition.get("sections")
    if not isinstance(secs, list):
        p.append("sections: required list")
    else:
        seen: set[str] = set()
        for i, s in enumerate(secs):
            w0 = f"sections[{i}]"
            if not isinstance(s, dict):
                p.append(f"{w0}: object")
                continue
            sid = s.get("id")
            if not isinstance(sid, str) or not _SECTION_ID_RE.match(sid):
                p.append(f"{w0}.id: lowercase slug (a-z0-9-)")
            elif sid in seen:
                p.append(f"{w0}.id: duplicate {sid!r}")
            else:
                seen.add(sid)
            if not isinstance(s.get("title"), str) or not s["title"].strip():
                p.append(f"{w0}.title: required string")
            items = s.get("items", [])
            if not isinstance(items, list):
                p.append(f"{w0}.items: list")
                continue
            for j, it in enumerate(items):
                wj = f"{w0}.items[{j}]"
                if isinstance(it, str):
                    continue
                if not isinstance(it, dict) or not isinstance(it.get("text"), str):
                    p.append(f"{wj}: string or {{text, link?}}")
                    continue
                _check_link(p, wj, it.get("link"))

    todos = edition.get("todos")
    if not isinstance(todos, list):
        p.append("todos: required list (may be empty)")
    else:
        for i, t in enumerate(todos):
            w0 = f"todos[{i}]"
            if not isinstance(t, dict):
                p.append(f"{w0}: object")
                continue
            if not isinstance(t.get("title"), str) or not t["title"].strip():
                p.append(f"{w0}.title: required string")
            st = t.get("status")
            if st not in TODO_STATUSES:
                p.append(f"{w0}.status: one of {', '.join(TODO_STATUSES)}")
            elif st == "Done":
                p.append(f"{w0}.status: Done rows must not be in the snapshot")
            if not _check_due(t.get("due")):
                p.append(f"{w0}.due: YYYY-MM-DD, ISO datetime, or null")
            for k in ("owner", "priority"):
                if t.get(k) is not None and not isinstance(t[k], str):
                    p.append(f"{w0}.{k}: string or null")
            nu = t.get("notion_url")
            if nu not in (None, "") and not (
                isinstance(nu, str) and nu.startswith("https://")
            ):
                p.append(f"{w0}.notion_url: https URL")
    p.extend(validate_mac_tasks(edition.get("mac_tasks")))
    return p


def validate_mac_tasks(mt: Any) -> list[str]:
    """Optional ``mac_tasks`` block; absent/null is valid (renders the empty state)."""
    if mt is None:
        return []
    if not isinstance(mt, dict):
        return ["mac_tasks: object {source_url, fetched_at, items:[{text}], page_found}"]
    p: list[str] = []
    su = mt.get("source_url")
    if su not in (None, "") and not (isinstance(su, str) and su.startswith("https://")):
        p.append("mac_tasks.source_url: https URL or null")
    fa = mt.get("fetched_at")
    if fa not in (None, "") and _parse_ts(fa) is None:
        p.append("mac_tasks.fetched_at: ISO-8601 timestamp or null")
    pf = mt.get("page_found", True)
    if not isinstance(pf, bool):
        p.append("mac_tasks.page_found: boolean")
    items = mt.get("items", [])
    if not isinstance(items, list):
        p.append("mac_tasks.items: list (may be empty)")
        return p
    for i, it in enumerate(items):
        w = f"mac_tasks.items[{i}]"
        if not isinstance(it, dict) or not isinstance(it.get("text"), str) or not it["text"].strip():
            p.append(f"{w}: object {{text}} with non-empty text")
        elif it.get("checked") is True:
            p.append(f"{w}: checked items must not be in the snapshot")
    return p


def mac_tasks_from_markdown(
    md: str | None,
    *,
    source_url: str | None = MAC_TASKS_URL,
    fetched_at: str | None = None,
    page_found: bool = True,
) -> dict:
    """Build the ``mac_tasks`` block from a Notion page body (enhanced Markdown).

    The Notion connector's ``notion-fetch`` returns the page body inside
    ``<content>...</content>``; to-do blocks are lines ``- [ ] text`` (unchecked)
    and ``- [x] text`` (checked). Nested to-dos are indented by tabs/spaces and are
    included as their own line (flattened, in page order). Everything else (prose,
    headings, bullets) is ignored. Pass ``page_found=False`` (or ``md=None``) when
    the page could not be fetched.
    """
    if fetched_at is None:
        fetched_at = eastern_now().isoformat(timespec="seconds")
    items: list[dict] = []
    if md is None:
        page_found = False
    elif page_found:
        body = md
        m = re.search(r"<content>(.*?)</content>", md, re.S)
        if m:
            body = m.group(1)
        in_fence = False
        for line in body.splitlines():
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            t = _TODO_LINE_RE.match(line)
            if t and t.group("mark") == " ":
                items.append({"text": t.group("text").strip()})
    return {"source_url": source_url, "fetched_at": fetched_at, "items": items, "page_found": bool(page_found)}


def load_json_arg(arg: str) -> dict:
    try:
        raw = sys.stdin.read() if arg == "-" else Path(arg).read_text(encoding="utf-8")
    except OSError as e:
        raise BriefError(f"cannot read {arg}: {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise BriefError(f"invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise BriefError("edition must be a JSON object")
    return data


def publish(edition: dict, *, root: Path | None = None, now: datetime | None = None) -> Path:
    """Validate and atomically write; same date+edition overwrites (idempotent)."""
    ed = normalize(edition, now=now)
    problems = validate(ed)
    if problems:
        raise BriefError(problems)
    root = root or store_dir()
    root.mkdir(parents=True, exist_ok=True)
    dest = root / edition_filename(ed["date"], ed["edition"])
    fd, tmp = tempfile.mkstemp(prefix=".brief-", suffix=".json", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(ed, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return dest


# ------------------------------------------------------------------ store reads


def list_editions(root: Path | None = None) -> list[tuple[str, str]]:
    """(date, edition) keys, newest first (pm after am on the same day)."""
    root = root or store_dir()
    if not root.is_dir():
        return []
    keys = [k for k in (parse_filename(p.name) for p in root.iterdir()) if k]
    return sorted(set(keys), key=_sort_key, reverse=True)


def _sort_key(k: tuple[str, str]) -> tuple[str, int]:
    return (k[0], EDITIONS.index(k[1]))


def latest_key(root: Path | None = None) -> tuple[str, str] | None:
    keys = list_editions(root)
    return keys[0] if keys else None


def load_edition(d: str, edition: str, root: Path | None = None) -> dict | None:
    root = root or store_dir()
    try:
        p = root / edition_filename(d, edition)
    except BriefError:
        return None
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def neighbors(
    key: tuple[str, str], root: Path | None = None
) -> tuple[tuple[str, str] | None, tuple[str, str] | None]:
    """(previous/older, next/newer) edition keys around ``key``."""
    asc = sorted(list_editions(root), key=_sort_key)
    if key not in asc:
        return None, None
    i = asc.index(key)
    prev_k = asc[i - 1] if i > 0 else None
    next_k = asc[i + 1] if i + 1 < len(asc) else None
    return prev_k, next_k


# ------------------------------------------------------------------- bucketing


def due_date_et(raw: Any) -> date | None:
    if not raw or not isinstance(raw, str):
        return None
    if _DATE_RE.match(raw):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    dt = _parse_ts(raw)
    return dt.astimezone(ET).date() if dt else None


def bucket_for(due: Any, today: date) -> str:
    """Overdue < today; today; week = tomorrow..today+7; later; nodate."""
    d = due_date_et(due)
    if d is None:
        return "nodate"
    if d < today:
        return "overdue"
    if d == today:
        return "today"
    if d <= today + timedelta(days=7):
        return "week"
    return "later"


def bucket_todos(todos: list[dict], today: date) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {b: [] for b in BUCKETS}
    for t in todos or []:
        if not isinstance(t, dict) or t.get("status") == "Done":
            continue
        out[bucket_for(t.get("due"), today)].append(t)
    for b in BUCKETS:
        out[b].sort(key=lambda t: (due_date_et(t.get("due")) or date.max, str(t.get("title", "")).lower()))
    return out
