#!/usr/bin/env python3
"""Daily Brief newspaper for FCC (#1091).

One JSON file per edition under the edition store
(default ``~/.local/share/fcc/brief-editions/YYYY-MM-DD-{am|pm}.json``,
Eastern date, see #1042). FCC ``server.py`` renders:

  /brief                    newest published edition (am or pm)
  /brief/YYYY-MM-DD/am|pm   permalink per edition
  /brief/archive            date-navigable list
  /api/brief/latest         newest edition JSON (read-only)

CLI (Grok's morning/evening routines call this over SSH on prism)::

  python3 financial-command/brief.py publish <file.json|->   # validate + write (idempotent)
  python3 financial-command/brief.py validate <file.json|->  # validate only
  python3 financial-command/brief.py list                    # editions, newest first
  financial-command/fcc brief publish <file.json|->          # same, via wrapper

No LLM, no network, no credentials: rendering only reads the edition store.
If the publisher (Grok) is down, /brief keeps serving the last edition with a
"stale" banner. Read-only page; todos link out to Notion for edits.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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
LOCATION = "Fort Myers, FL"
PAPER_NAME = "The Daily Brief"
TAGLINE = "All the news that fits the day. Printed for Chris, by Grok."

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(am|pm)\.json$")
_SECTION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


class BriefError(ValueError):
    """Schema or store error with a list of human-readable problems."""

    def __init__(self, problems: list[str] | str):
        if isinstance(problems, str):
            problems = [problems]
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


# --------------------------------------------------------------------------- store


def store_dir() -> Path:
    raw = os.environ.get("FCC_BRIEF_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".local" / "share" / "fcc" / "brief-editions"


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
    return p


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


# ------------------------------------------------------------------- rendering

_e = html.escape


def _fmt_long_date(d: str) -> str:
    dt = date.fromisoformat(d)
    return f"{dt.strftime('%A')}, {dt.strftime('%B')} {dt.day}, {dt.year}"


def _fmt_short_due(raw: Any) -> str:
    d = due_date_et(raw)
    if d is None:
        return "No date"
    return f"{d.strftime('%a')} {d.strftime('%b')} {d.day}"


def _fmt_et_time(raw: Any) -> str:
    dt = _parse_ts(raw)
    if dt is None:
        return ""
    t = dt.astimezone(ET)
    return t.strftime("%I:%M %p").lstrip("0") + " ET"


def edition_url(key: tuple[str, str]) -> str:
    return f"/brief/{key[0]}/{key[1]}"


def edition_label(edition: str) -> str:
    return "Morning Edition" if edition == "am" else "Evening Edition"


def _link(text: str, url: Any, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    if _is_http(url):
        return f'<a{c} href="{_e(url)}" target="_blank" rel="noopener noreferrer">{_e(text)}</a>'
    return f"<span{c}>{_e(text)}</span>" if cls else _e(text)


def _weather_html(w: Any) -> str:
    if not w:
        return '<div class="wx-sum muted">Weather not reported</div>'
    if isinstance(w, str):
        return f'<div class="wx-sum">{_e(w)}</div>'
    parts = []
    hi, lo = w.get("high"), w.get("low")
    icon = w.get("icon") or ""
    if hi is not None or lo is not None:
        parts.append(
            f'<div class="wx-temp">{_e(str(icon))} {_e(str(hi if hi is not None else "–"))}° / {_e(str(lo if lo is not None else "–"))}°</div>'
        )
    if w.get("summary"):
        parts.append(f'<div class="wx-sum">{_e(str(w["summary"]))}</div>')
    extra = [str(w[k]) for k in ("rain", "wind", "note") if w.get(k)]
    if extra:
        parts.append(f'<div class="wx-extra muted">{_e(" · ".join(extra))}</div>')
    return "".join(parts) or '<div class="wx-sum muted">Weather not reported</div>'


def _weather_short(w: Any) -> str:
    if not w:
        return "—"
    if isinstance(w, str):
        return w
    hi = w.get("high")
    s = f"{hi}°" if hi is not None else ""
    if w.get("summary"):
        s = (s + " " + str(w["summary"])).strip()
    return s or "—"


def _quote_html(q: Any) -> str:
    if not q:
        return '<p class="muted">No quote today.</p>'
    if isinstance(q, str):
        return f"<blockquote>“{_e(q)}”</blockquote>"
    who = f'<cite>— {_e(str(q.get("author")))}</cite>' if q.get("author") else ""
    return f"<blockquote>“{_e(q['text'])}”</blockquote>{who}"


def _item_html(it: Any) -> str:
    if isinstance(it, str):
        return f"<li>{_e(it)}</li>"
    return f"<li>{_link(it.get('text', ''), it.get('link'))}</li>"


def _chip(text: str, cls: str) -> str:
    return f'<span class="chip {cls}">{_e(text)}</span>'


def _prio_cls(p: str) -> str:
    s = p.lower()
    if s.startswith(("p0", "p1", "high", "urgent")):
        return "chip-hi"
    if s.startswith(("p2", "med")):
        return "chip-med"
    return "chip-lo"


def render_todos(todos: list[dict], today: date, *, edition: str = "am") -> str:
    title = "To-Do (coming due)" if edition == "pm" else "To-Do"
    groups = bucket_todos(todos, today)
    total = sum(len(v) for v in groups.values())
    head = (
        f'<section class="sec" id="sec-todo"><h2 class="sec-h">{_e(title)}'
        f'<span class="sec-count">{total} open</span></h2>'
    )
    if total == 0:
        return head + '<p class="empty">Nothing open</p></section>'
    out = [head]
    for b in BUCKETS:
        rows = groups[b]
        if not rows:
            continue
        out.append(
            f'<div class="todo-group todo-{b}" data-bucket="{b}"><h3>{_e(BUCKET_TITLES[b])} <span class="muted">({len(rows)})</span></h3><ul class="todo-list">'
        )
        for t in rows:
            chips = []
            if t.get("owner"):
                chips.append(_chip(str(t["owner"]), "chip-owner"))
            if t.get("priority"):
                chips.append(_chip(str(t["priority"]), _prio_cls(str(t["priority"]))))
            if t.get("status") == "In progress":
                chips.append(_chip("In progress", "chip-prog"))
            ttl = _link(str(t.get("title", "")), t.get("notion_url"), "todo-title")
            out.append(
                f'<li class="todo-item{" overdue" if b == "overdue" else ""}">'
                f'{ttl}<div class="todo-meta"><span class="todo-due">{_e(_fmt_short_due(t.get("due")))}</span>{"".join(chips)}</div></li>'
            )
        out.append("</ul></div>")
    out.append("</section>")
    return "".join(out)


def _reminders_card(todos: list[dict], today: date) -> str:
    g = bucket_todos(todos, today)
    urgent = g["overdue"] + g["today"]
    n = len(urgent)
    top = "".join(
        f'<li class="{"overdue" if t in g["overdue"] else ""}">{_link(str(t.get("title", "")), t.get("notion_url"))}</li>'
        for t in urgent[:3]
    )
    body = f"<ul>{top}</ul>" if top else '<p class="muted">Nothing overdue or due today</p>'
    return (
        f'<div class="card card-rem"><div class="card-k">Reminders</div>'
        f'<div class="card-v{" red" if g["overdue"] else ""}">{n} due</div>'
        f'<div class="card-sub muted">{len(g["overdue"])} overdue · {len(g["today"])} today</div>{body}</div>'
    )


def _upcoming_items(ed: dict) -> list[Any]:
    order = ("day-ahead", "tomorrow", "schedule", "upcoming")
    by_id = {s.get("id"): s for s in ed.get("sections") or [] if isinstance(s, dict)}
    for sid in order:
        s = by_id.get(sid)
        if s and s.get("items"):
            return list(s["items"])[:3]
    return []


def _item_text(it: Any) -> str:
    return it if isinstance(it, str) else str(it.get("text", ""))


_CSS = r"""
:root{--bg:#fbfaf7;--fg:#151515;--muted:#6b6b6b;--card:#ffffff;--border:#e4e1da;--accent:#151515;--red:#c0262d;--chip:#f1efe9;--link:#0b4f9c;
--serif:"Old Standard TT","Playfair Display",Georgia,"Times New Roman",serif;--sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--radius:.6rem}
@media (prefers-color-scheme:dark){:root{--bg:#0d0d0f;--fg:#ececec;--muted:#9a9a9a;--card:#16161a;--border:#2a2a30;--accent:#ececec;--red:#ff5a60;--chip:#23232a;--link:#7cb4ff}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font-family:var(--sans);line-height:1.5;overflow-x:hidden}
a{color:var(--link)}.muted{color:var(--muted)}.red{color:var(--red)}
.paper{max-width:1040px;margin:0 auto;padding:.75rem .9rem 5.5rem}
.topbar{display:flex;justify-content:space-between;align-items:center;gap:.5rem;font-size:.8rem;margin-bottom:.4rem;flex-wrap:wrap}
.topbar a{text-decoration:none;border:1px solid var(--border);border-radius:999px;padding:.2rem .6rem;color:var(--fg);background:var(--card)}
.stale{border:1px solid var(--red);color:var(--red);border-radius:var(--radius);padding:.4rem .6rem;font-size:.8rem;margin:.4rem 0}
.head{display:grid;grid-template-columns:1fr 2fr 1fr;gap:.75rem;align-items:center;border-bottom:1px solid var(--border);padding-bottom:.5rem}
.wx,.qotd{font-size:.8rem;border:1px solid var(--border);border-radius:var(--radius);padding:.45rem .6rem;background:var(--card);min-width:0}
.wx h4,.qotd h4{margin:0 0 .2rem;font:600 .65rem var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.wx-temp{font:700 1.1rem var(--serif)}.qotd blockquote{margin:0;font:italic .85rem var(--serif)}.qotd cite{font-size:.72rem;color:var(--muted)}
.masthead{text-align:center;min-width:0}
.masthead h1{font-family:var(--serif);font-weight:900;font-size:clamp(2rem,7vw,3.6rem);line-height:1;margin:.1rem 0;letter-spacing:-.01em;overflow-wrap:anywhere}
.tagline{font:italic .8rem var(--serif);color:var(--muted)}
.dateline{display:flex;flex-wrap:wrap;justify-content:space-between;gap:.25rem .8rem;border-top:3px double var(--accent);border-bottom:1px solid var(--accent);padding:.3rem 0;margin:.5rem 0;font:600 .7rem var(--sans);letter-spacing:.06em;text-transform:uppercase}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.6rem;margin:.6rem 0}
.card{border:1px solid var(--border);border-radius:var(--radius);background:var(--card);padding:.55rem .65rem;min-width:0;font-size:.8rem}
.card-k{font:600 .62rem var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.card-v{font:700 1.05rem var(--serif);overflow-wrap:anywhere}.card ul{margin:.25rem 0 0;padding-left:1rem}.card li{overflow-wrap:anywhere}
.card li.overdue a,.card li.overdue{color:var(--red)}
.pills{display:flex;gap:.35rem;overflow-x:auto;padding:.2rem 0 .5rem;scrollbar-width:none;max-width:100%}
.pills a{flex:0 0 auto;font-size:.75rem;text-decoration:none;color:var(--fg);background:var(--chip);border:1px solid var(--border);border-radius:999px;padding:.2rem .65rem}
.front{border-bottom:1px solid var(--border);padding-bottom:.6rem;margin-bottom:.4rem}
.front h2{font:900 clamp(1.5rem,5vw,2.3rem)/1.1 var(--serif);margin:.3rem 0;overflow-wrap:anywhere}
.front .subhead{font:italic 1rem var(--serif);color:var(--muted);margin:.2rem 0 .5rem}
.front .body{columns:2 18rem;column-gap:1.4rem;font-family:var(--serif);font-size:1rem}
.front .body p{margin:0 0 .6rem}.front .body p:first-child::first-letter{float:left;font:900 3.3rem/.8 var(--serif);padding:.3rem .4rem 0 0}
.front img{max-width:100%;height:auto;border-radius:var(--radius);margin:.4rem 0}
.theme{font:600 .7rem var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 1.4rem}
.sec{border-top:2px solid var(--accent);padding-top:.35rem;margin-top:.8rem;min-width:0;scroll-margin-top:.5rem}
.sec-h{font:800 1.15rem var(--serif);margin:0 0 .3rem;display:flex;justify-content:space-between;align-items:baseline;gap:.5rem}
.sec-count{font:500 .7rem var(--sans);color:var(--muted)}
.sec ul{margin:0;padding-left:1.1rem}.sec li{margin:.2rem 0;overflow-wrap:anywhere}
.todo-group h3{font:700 .72rem var(--sans);letter-spacing:.06em;text-transform:uppercase;margin:.55rem 0 .2rem}
.todo-overdue h3{color:var(--red)}
.todo-list{list-style:none;padding:0!important}
.todo-item{border-bottom:1px dotted var(--border);padding:.3rem 0}
.todo-item.overdue .todo-title,.todo-item.overdue .todo-due{color:var(--red);font-weight:600}
.todo-title{font-family:var(--serif);font-size:.98rem}
.todo-meta{display:flex;flex-wrap:wrap;gap:.3rem;align-items:center;font-size:.72rem;margin-top:.1rem}
.todo-due{color:var(--muted)}
.chip{border-radius:999px;padding:.05rem .45rem;background:var(--chip);border:1px solid var(--border);font-size:.68rem}
.chip-hi{border-color:var(--red);color:var(--red)}.chip-prog{border-color:var(--link);color:var(--link)}
.empty{font-style:italic;color:var(--muted)}
.edition-nav{display:flex;justify-content:space-between;gap:.5rem;margin:1.2rem 0 .5rem;font-size:.85rem}
.edition-nav a,.edition-nav span{border:1px solid var(--border);border-radius:999px;padding:.3rem .8rem;text-decoration:none;color:var(--fg);background:var(--card)}
.edition-nav span{opacity:.4}
.foot{font-size:.72rem;color:var(--muted);text-align:center;margin-top:.8rem}
.archive-day{border-bottom:1px solid var(--border);padding:.45rem 0;display:flex;justify-content:space-between;gap:.5rem;flex-wrap:wrap}
.archive-day a{margin-left:.5rem}
.tabbar{position:fixed;left:0;right:0;bottom:0;display:flex;justify-content:space-around;background:var(--card);border-top:1px solid var(--border);padding:.35rem 0 calc(.35rem + env(safe-area-inset-bottom));z-index:5}
.tabbar a{color:var(--muted);text-decoration:none;font-size:.7rem;display:flex;flex-direction:column;align-items:center;min-width:3.4rem}
.tabbar a.active{color:var(--fg);font-weight:700}.tabbar .i{font-size:1rem}
@media (max-width:760px){.head{grid-template-columns:1fr 1fr}.masthead{grid-column:1/-1;order:-1}.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.grid{grid-template-columns:minmax(0,1fr)}.front .body{columns:1}}
"""


def _page(title: str, body: str, *, active: str = "brief") -> str:
    tabs = [
        ("/", "◎", "FCC", "fcc"),
        ("/brief", "▤", "Brief", "brief"),
        ("/brief/archive", "☰", "Archive", "archive"),
    ]
    tabbar = "".join(
        f'<a href="{h}" class="{"active" if k == active else ""}"><span class="i" aria-hidden="true">{i}</span>{l}</a>'
        for h, i, l, k in tabs
    )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        '<meta name="color-scheme" content="light dark">'
        '<meta name="robots" content="noindex,nofollow">'
        f"<title>{_e(title)}</title>"
        '<link rel="icon" href="/favicon.svg"><link rel="manifest" href="/manifest.webmanifest">'
        f"<style>{_CSS}</style></head><body>"
        f'<div class="paper">{body}</div>'
        f'<nav class="tabbar" aria-label="Brief navigation">{tabbar}</nav>'
        "</body></html>"
    )


def _edition_nav(key: tuple[str, str], root: Path | None) -> str:
    prev_k, next_k = neighbors(key, root)

    def a(k: tuple[str, str] | None, label: str, rel: str) -> str:
        if not k:
            return f"<span>{label}</span>"
        return f'<a rel="{rel}" href="{edition_url(k)}">{label}</a>'

    return (
        '<nav class="edition-nav" aria-label="Editions">'
        f'{a(prev_k, "← Older", "prev")}<a href="/brief/archive">Archive</a>{a(next_k, "Newer →", "next")}</nav>'
    )


def render_edition(
    ed: dict,
    *,
    root: Path | None = None,
    is_latest_view: bool = False,
    now: datetime | None = None,
) -> str:
    key = (ed["date"], ed["edition"])
    today = date.fromisoformat(ed["date"])
    todos = ed.get("todos") or []
    long_date = _fmt_long_date(ed["date"])
    label = edition_label(ed["edition"])

    stale = ""
    if is_latest_view:
        pub = _parse_ts(ed.get("published_at"))
        if pub is not None:
            age_h = (eastern_now(now) - pub.astimezone(ET)).total_seconds() / 3600
            if age_h > STALE_HOURS:
                stale = (
                    f'<div class="stale" role="status">Latest edition is from {_e(long_date)} '
                    f"({_e(label)}). A newer edition has not been published yet; "
                    "showing the last one on file.</div>"
                )

    vol = ed.get("vol")
    no = ed.get("no")
    dateline = (
        '<div class="dateline">'
        f"<span>Vol. {_e(str(vol)) if vol is not None else '—'}</span>"
        f"<span>No. {_e(str(no)) if no is not None else '—'}</span>"
        f"<span>{_e(long_date)}</span><span>{_e(label)}</span><span>{_e(LOCATION)}</span></div>"
    )
    head = (
        '<header class="head">'
        f'<div class="wx"><h4>Weather</h4>{_weather_html(ed.get("weather"))}</div>'
        f'<div class="masthead"><h1>{_e(PAPER_NAME)}</h1><div class="tagline">{_e(TAGLINE)}</div></div>'
        f'<div class="qotd"><h4>Quote of the day</h4>{_quote_html(ed.get("quote"))}</div>'
        "</header>"
    )

    fp = ed.get("front_page") or {}
    highlight = ed.get("theme") or fp.get("headline") or ""
    upcoming = _upcoming_items(ed)
    up_html = (
        "<ul>" + "".join(f"<li>{_e(_item_text(i))}</li>" for i in upcoming) + "</ul>"
        if upcoming
        else '<p class="muted">Nothing scheduled</p>'
    )
    cards = (
        '<div class="cards">'
        f'<div class="card"><div class="card-k">Weather</div><div class="card-v">{_e(_weather_short(ed.get("weather")))}</div></div>'
        f'<div class="card"><div class="card-k">Today</div><div class="card-v">{_e(highlight) or "—"}</div></div>'
        f"{_reminders_card(todos, today)}"
        f'<div class="card"><div class="card-k">{"Tomorrow" if ed["edition"] == "pm" else "Upcoming"}</div>{up_html}</div>'
        "</div>"
    )

    body_paras = [p.strip() for p in re.split(r"\n\s*\n", fp.get("body") or "") if p.strip()]
    img = (
        f'<img src="{_e(fp["image"])}" alt="" loading="lazy">' if _is_http(fp.get("image")) else ""
    )
    theme_html = '<div class="theme">' + _e(ed.get("theme") or "") + "</div>" if ed.get("theme") else ""
    sub_html = '<p class="subhead">' + _e(fp["subhead"]) + "</p>" if fp.get("subhead") else ""
    front = (
        '<article class="front" id="sec-front">'
        f"{theme_html}"
        f"<h2>{_e(fp.get('headline', ''))}</h2>"
        f"{sub_html}"
        f"{img}"
        f'<div class="body">{"".join(f"<p>{_e(p)}</p>" for p in body_paras)}</div>'
        "</article>"
    )

    # Sections in publisher order; To-Do inserted after the schedule section
    # (day-ahead / tomorrow) or replaces an explicit "todo" placeholder.
    secs = [s for s in ed.get("sections") or [] if isinstance(s, dict)]
    todo_html = render_todos(todos, today, edition=ed["edition"])
    blocks: list[tuple[str, str, str]] = []  # (id, title, html)
    placed = False
    for s in secs:
        sid = s.get("id", "")
        if sid in ("front", "front-page"):
            continue
        if sid in ("todo", "to-do", "todos"):
            if not placed:
                blocks.append(("todo", "To-Do", todo_html))
                placed = True
            continue
        items = s.get("items") or []
        inner = (
            "<ul>" + "".join(_item_html(i) for i in items) + "</ul>"
            if items
            else '<p class="empty">Nothing to report</p>'
        )
        blocks.append(
            (sid, s.get("title", ""), f'<section class="sec" id="sec-{_e(sid)}"><h2 class="sec-h">{_e(s.get("title", ""))}</h2>{inner}</section>')
        )
        if not placed and sid in ("day-ahead", "tomorrow"):
            blocks.append(("todo", "To-Do", todo_html))
            placed = True
    if not placed:
        blocks.insert(0, ("todo", "To-Do", todo_html))

    pills = (
        '<nav class="pills" aria-label="Sections"><a href="#sec-front">Front Page</a>'
        + "".join(f'<a href="#sec-{_e(i)}">{_e(t)}</a>' for i, t, _ in blocks)
        + "</nav>"
    )
    snap = (
        f'<div class="foot">To-Do: snapshot from Notion Todo List at publish '
        f'({_e(_fmt_et_time(ed.get("published_at")))}, {_e(ed["date"])}). '
        "Read-only; edit items in Notion. Published "
        f'{_e(_fmt_et_time(ed.get("published_at")))} · <a href="{edition_url(key)}">Permalink</a></div>'
    )
    top = (
        '<div class="topbar"><a href="/">← FCC</a>'
        f'<span class="muted">{_e(label)} · {_e(ed["date"])}</span>'
        '<a href="/brief/archive">Archive</a></div>'
    )
    body = (
        top
        + stale
        + head
        + dateline
        + cards
        + pills
        + front
        + f'<div class="grid">{"".join(h for _, _, h in blocks)}</div>'
        + _edition_nav(key, root)
        + snap
    )
    return _page(f"{PAPER_NAME} · {label} · {ed['date']}", body, active="brief")


def render_archive(root: Path | None = None) -> str:
    keys = list_editions(root)
    by_day: dict[str, list[str]] = {}
    for d, e in keys:
        by_day.setdefault(d, []).append(e)
    if not by_day:
        rows = '<p class="empty">No editions published yet.</p>'
    else:
        rows = ""
        month = ""
        for d in sorted(by_day, reverse=True):
            m = date.fromisoformat(d).strftime("%B %Y")
            if m != month:
                rows += f'<h2 class="sec-h" style="margin-top:1rem">{_e(m)}</h2>'
                month = m
            links = "".join(
                f'<a href="{edition_url((d, e))}">{"Morning" if e == "am" else "Evening"}</a>'
                for e in sorted(by_day[d], key=EDITIONS.index)
            )
            rows += f'<div class="archive-day"><span>{_e(_fmt_long_date(d))}</span><span>{links}</span></div>'
    latest = (
        f'<a href="{edition_url(keys[0])}">Newest: {_e(keys[0][0])} {_e(keys[0][1])}</a>' if keys else ""
    )
    body = (
        '<div class="topbar"><a href="/">← FCC</a><a href="/brief">Latest edition</a></div>'
        f'<header class="masthead" style="margin:.4rem 0"><h1>{_e(PAPER_NAME)}</h1>'
        '<div class="tagline">Archive · editions by Eastern date</div></header>'
        f'<div class="dateline"><span>{len(keys)} editions</span><span>{latest}</span></div>'
        f"{rows}"
    )
    return _page(f"{PAPER_NAME} · Archive", body, active="archive")


def render_empty() -> str:
    body = (
        '<div class="topbar"><a href="/">← FCC</a><a href="/brief/archive">Archive</a></div>'
        f'<header class="masthead"><h1>{_e(PAPER_NAME)}</h1><div class="tagline">{_e(TAGLINE)}</div></header>'
        '<p class="empty" style="text-align:center;margin-top:2rem">No edition published yet. '
        "The morning (~8 AM ET) and evening (~8 PM ET) editions appear here once Grok publishes them.</p>"
    )
    return _page(PAPER_NAME, body)


def render_not_found(d: str, e: str) -> str:
    body = (
        '<div class="topbar"><a href="/brief">Latest edition</a><a href="/brief/archive">Archive</a></div>'
        f'<p class="empty" style="margin-top:2rem">No {_e(edition_label(e) if e in EDITIONS else "edition")} on file for {_e(d)}.</p>'
    )
    return _page(f"{PAPER_NAME} · not found", body)


_PERMA_RE = re.compile(r"^/brief/(\d{4}-\d{2}-\d{2})/(am|pm)/?$")


def route(path: str, *, root: Path | None = None, now: datetime | None = None) -> tuple[int, str, str] | None:
    """Map a request path to (status, content_type, body), or None if not a brief route."""
    if path in ("/brief", "/brief/"):
        k = latest_key(root)
        if not k:
            return 200, "text/html; charset=utf-8", render_empty()
        ed = load_edition(*k, root=root)
        if ed is None:
            return 500, "text/html; charset=utf-8", render_not_found(*k)
        return 200, "text/html; charset=utf-8", render_edition(ed, root=root, is_latest_view=True, now=now)
    if path in ("/brief/archive", "/brief/archive/"):
        return 200, "text/html; charset=utf-8", render_archive(root)
    m = _PERMA_RE.match(path)
    if m:
        ed = load_edition(m.group(1), m.group(2), root=root)
        if ed is None:
            return 404, "text/html; charset=utf-8", render_not_found(m.group(1), m.group(2))
        return 200, "text/html; charset=utf-8", render_edition(ed, root=root, now=now)
    if path == "/api/brief/latest":
        k = latest_key(root)
        ed = load_edition(*k, root=root) if k else None
        payload = {"ok": ed is not None, "edition": ed, "url": edition_url(k) if k else None}
        return (200 if ed else 404), "application/json", json.dumps(payload, ensure_ascii=False)
    if path == "/api/brief/editions":
        keys = list_editions(root)
        payload = {"ok": True, "editions": [{"date": d, "edition": e, "url": edition_url((d, e))} for d, e in keys]}
        return 200, "application/json", json.dumps(payload)
    if path.startswith("/brief/"):
        return 404, "text/html; charset=utf-8", render_not_found(path[7:17], "")
    return None


# -------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fcc brief", description="FCC Daily Brief edition store (#1091)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_pub = sub.add_parser("publish", help="validate + write an edition (re-publish overwrites)")
    p_pub.add_argument("file", help="edition JSON path, or - for stdin")
    p_val = sub.add_parser("validate", help="validate only, write nothing")
    p_val.add_argument("file")
    sub.add_parser("list", help="list editions, newest first")
    sub.add_parser("path", help="print the edition store directory")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "publish":
            dest = publish(load_json_arg(args.file))
            d, e = parse_filename(dest.name) or ("", "")
            print(json.dumps({"ok": True, "path": str(dest), "url": edition_url((d, e)), "date": d, "edition": e}))
            return 0
        if args.cmd == "validate":
            ed = normalize(load_json_arg(args.file))
            problems = validate(ed)
            print(json.dumps({"ok": not problems, "problems": problems, "date": ed.get("date"), "edition": ed.get("edition")}))
            return 0 if not problems else 2
        if args.cmd == "list":
            for d, e in list_editions():
                print(f"{d} {e} {edition_url((d, e))}")
            return 0
        if args.cmd == "path":
            print(store_dir())
            return 0
    except BriefError as e:
        print(json.dumps({"ok": False, "problems": e.problems}), file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
