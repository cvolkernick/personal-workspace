"""Daily Brief pages on the Horizon host, under ``/daily-brief``.

  /daily-brief                     newest edition (am or pm)
  /daily-brief/YYYY-MM-DD/am|pm    permalink
  /daily-brief/archive             editions by Eastern date
  /daily-brief/api/latest          newest edition JSON (money-scrubbed)
  /daily-brief/api/editions        edition index

A sibling of Horizon's own pages, not part of the Horizon model. It never
reads Horizon world-state or brief artifacts and never reads FCC books.
Every edition passes through ``redact.scrub_edition`` before rendering, so FCC
marks, balances and capital so-whats do not reach this host. Styling reuses
Horizon's base palette and type; there are no regime, weather or model widgets.

Links are root-absolute (``/daily-brief/...``) so they also work through
FCC's ``/horizon/*`` lens, which prefixes root-absolute URLs. In-page anchors
carry the page path for the same reason (the lens injects a ``<base>``).
"""

from __future__ import annotations

import html
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from research.daily_brief.redact import HELD_NOTE, scrub_edition
from research.daily_brief.store import (
    BASE_PATH,
    BUCKET_TITLES,
    BUCKETS,
    EDITIONS,
    ET,
    MAC_TASKS_EMPTY,
    MAC_TASKS_TITLE,
    STALE_HOURS,
    _is_http,
    _parse_ts,
    bucket_todos,
    due_date_et,
    eastern_now,
    edition_url,
    latest_key,
    list_editions,
    load_edition,
    neighbors,
)

PAPER_NAME = "The Daily Brief"
TAGLINE = "All the news that fits the day. Printed by Grok."
LOCATION = "Fort Myers, FL"
DASH, OLDER, NEWER = "\u2014", "\u2190 Older", "Newer \u2192"
NOT_MODEL_NOTE = "A personal newspaper published by Grok. Not Horizon model output."

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


def edition_label(edition: str) -> str:
    return "Morning Edition" if edition == "am" else "Evening Edition"


def _link(text: str, url: Any, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    if _is_http(url):
        return f'<a{c} href="{_e(url)}" target="_blank" rel="noopener noreferrer">{_e(text)}</a>'
    return f"<span{c}>{_e(text)}</span>" if cls else _e(text)


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


def mac_task_items(mt: Any) -> list[str]:
    """Unchecked Mac task texts from an edition's ``mac_tasks`` block.

    Backward compatible: a missing/null/malformed block or ``page_found: false``
    yields [] (rendered as the "Nothing waiting at the Mac" empty state).
    """
    if not isinstance(mt, dict) or mt.get("page_found", True) is False:
        return []
    out = []
    for it in mt.get("items") or []:
        if isinstance(it, str):
            it = {"text": it}
        if not isinstance(it, dict) or it.get("checked") is True:
            continue
        txt = it.get("text")
        if isinstance(txt, str) and txt.strip():
            out.append(txt.strip())
    return out


def render_mac_tasks(mt: Any) -> str:
    items = mac_task_items(mt)
    n = len(items)
    src = mt.get("source_url") if isinstance(mt, dict) else None
    open_link = (
        f' <a class="mac-src" href="{_e(src)}" target="_blank" rel="noopener noreferrer">Notion</a>'
        if isinstance(src, str) and src.startswith("https://")
        else ""
    )
    state = "items" if n else ("absent" if not isinstance(mt, dict) else ("missing" if mt.get("page_found", True) is False else "clear"))
    head = (
        f'<section class="sec" id="sec-mac" data-mac-state="{state}"><h2 class="sec-h">{_e(MAC_TASKS_TITLE)}'
        f'<span class="sec-count">{n} waiting{open_link}</span></h2>'
    )
    if not n:
        return head + f'<p class="empty">{_e(MAC_TASKS_EMPTY)}</p></section>'
    rows = "".join(
        f'<li class="mac-item"><span class="mac-box" aria-hidden="true"></span><span class="mac-text">{_e(t)}</span></li>'
        for t in items
    )
    return head + f'<ul class="mac-list" aria-label="Unchecked Mac tasks">{rows}</ul></section>'


def _reminders_card(todos: list[dict], today: date, mac_count: int = 0) -> str:
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
        f'<div class="card-sub muted">{len(g["overdue"])} overdue · {len(g["today"])} today</div>'
        f'<div class="card-sub mac-count"><a href="#sec-mac">{mac_count} Mac task{"" if mac_count == 1 else "s"}</a></div>'
        f"{body}</div>"
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


# Horizon base palette and type (research/horizon/index.html :root). Dark only, like Horizon.
_CSS = r"""
:root{--bg:#060a10;--panel:#0f1620;--panel2:#121c28;--border:#1c2a3a;--border2:#2a3d52;--text:#e8eef6;--muted:#7f96ad;--dim:#5a7085;
--red:#ff6b6b;--accent:#5b9fd4;--cyan:#4fd1c5;--radius:.6rem;
--sans:"Segoe UI",ui-sans-serif,system-ui,-apple-system,sans-serif;--serif:Georgia,"Times New Roman",serif}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);font-size:14.5px;line-height:1.45;overflow-x:hidden;min-height:100vh}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}.muted{color:var(--muted)}.red{color:var(--red)}
.paper{max-width:1040px;margin:0 auto;padding:.75rem .9rem 5.5rem}
.topbar{display:flex;justify-content:space-between;align-items:center;gap:.5rem;font-size:.8rem;margin-bottom:.4rem;flex-wrap:wrap}
.topbar a{border:1px solid var(--border);border-radius:999px;padding:.2rem .6rem;color:var(--text);background:var(--panel)}
.stale{border:1px solid var(--red);color:var(--red);border-radius:var(--radius);padding:.4rem .6rem;font-size:.8rem;margin:.4rem 0}
.head{display:grid;grid-template-columns:2fr 1fr;gap:.75rem;align-items:center;border-bottom:1px solid var(--border);padding-bottom:.5rem}
.qotd{font-size:.8rem;border:1px solid var(--border);border-radius:var(--radius);padding:.45rem .6rem;background:var(--panel);min-width:0}
.qotd h4{margin:0 0 .2rem;font:600 .65rem var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.qotd blockquote{margin:0;font:italic .85rem var(--serif)}.qotd cite{font-size:.72rem;color:var(--muted)}
.masthead{min-width:0}
.masthead h1{font-family:var(--serif);font-weight:900;font-size:clamp(2rem,7vw,3.4rem);line-height:1;margin:.1rem 0;letter-spacing:-.01em;overflow-wrap:anywhere}
.tagline{font:italic .8rem var(--serif);color:var(--muted)}
.dateline{display:flex;flex-wrap:wrap;justify-content:space-between;gap:.25rem .8rem;border-top:3px double var(--border2);border-bottom:1px solid var(--border2);padding:.3rem 0;margin:.5rem 0;font:600 .7rem var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.6rem;margin:.6rem 0}
.card{border:1px solid var(--border);border-radius:var(--radius);background:var(--panel);padding:.55rem .65rem;min-width:0;font-size:.8rem}
.card-k{font:600 .62rem var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.card-v{font:700 1.05rem var(--sans);overflow-wrap:anywhere}.card ul{margin:.25rem 0 0;padding-left:1rem}.card li{overflow-wrap:anywhere}
.card li.overdue a,.card li.overdue{color:var(--red)}
.pills{display:flex;gap:.35rem;overflow-x:auto;padding:.2rem 0 .5rem;scrollbar-width:none;max-width:100%}
.pills a{flex:0 0 auto;font-size:.75rem;color:var(--text);background:var(--panel2);border:1px solid var(--border);border-radius:999px;padding:.2rem .65rem}
.front{border-bottom:1px solid var(--border);padding-bottom:.6rem;margin-bottom:.4rem}
.front h2{font:800 clamp(1.4rem,5vw,2.1rem)/1.15 var(--serif);margin:.3rem 0;overflow-wrap:anywhere}
.front .subhead{font:italic 1rem var(--serif);color:var(--muted);margin:.2rem 0 .5rem}
.front .body{columns:2 18rem;column-gap:1.4rem;font-size:1rem}
.front .body p{margin:0 0 .6rem}
.front img{max-width:100%;height:auto;border-radius:var(--radius);margin:.4rem 0}
.theme{font:600 .7rem var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--cyan)}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 1.4rem}
.sec{border-top:2px solid var(--border2);padding-top:.35rem;margin-top:.8rem;min-width:0;scroll-margin-top:.5rem}
.sec-h{font:700 1.1rem var(--sans);margin:0 0 .3rem;display:flex;justify-content:space-between;align-items:baseline;gap:.5rem}
.sec-count{font:500 .7rem var(--sans);color:var(--muted)}
.sec ul{margin:0;padding-left:1.1rem}.sec li{margin:.2rem 0;overflow-wrap:anywhere}
.todo-group h3{font:700 .72rem var(--sans);letter-spacing:.06em;text-transform:uppercase;margin:.55rem 0 .2rem;color:var(--muted)}
.todo-overdue h3{color:var(--red)}
.todo-list{list-style:none;padding:0!important}
.todo-item{border-bottom:1px dotted var(--border);padding:.3rem 0}
.todo-item.overdue .todo-title,.todo-item.overdue .todo-due{color:var(--red);font-weight:600}
.todo-title{font-size:.98rem}
.todo-meta{display:flex;flex-wrap:wrap;gap:.3rem;align-items:center;font-size:.72rem;margin-top:.1rem}
.todo-due{color:var(--muted)}
.chip{border-radius:999px;padding:.05rem .45rem;background:var(--panel2);border:1px solid var(--border);font-size:.68rem}
.chip-hi{border-color:var(--red);color:var(--red)}.chip-prog{border-color:var(--accent);color:var(--accent)}
.empty{font-style:italic;color:var(--muted)}
.mac-list{list-style:none;padding:0!important}
.mac-item{display:flex;gap:.5rem;align-items:flex-start;border-bottom:1px dotted var(--border);padding:.3rem 0;font-size:.98rem;min-width:0}
.mac-box{flex:0 0 auto;width:.9rem;height:.9rem;margin-top:.28rem;border:1.5px solid var(--text);border-radius:.18rem;background:var(--panel)}
.mac-text{min-width:0;overflow-wrap:anywhere}
.mac-src{font-size:.7rem;margin-left:.35rem}
.card .mac-count{font-size:.72rem;margin-top:.1rem}.card .mac-count a{color:var(--text);border-bottom:1px dotted var(--muted)}
.edition-nav{display:flex;justify-content:space-between;gap:.5rem;margin:1.2rem 0 .5rem;font-size:.85rem}
.edition-nav a,.edition-nav span{border:1px solid var(--border);border-radius:999px;padding:.3rem .8rem;color:var(--text);background:var(--panel)}
.edition-nav span{opacity:.4}
.foot{font-size:.72rem;color:var(--muted);text-align:center;margin-top:.8rem}
.archive-day{border-bottom:1px solid var(--border);padding:.45rem 0;display:flex;justify-content:space-between;gap:.5rem;flex-wrap:wrap}
.archive-day a{margin-left:.5rem}
.tabbar{position:fixed;left:0;right:0;bottom:0;display:flex;justify-content:space-around;background:rgba(6,10,16,.92);border-top:1px solid var(--border);padding:.35rem 0 calc(.35rem + env(safe-area-inset-bottom));z-index:5}
.tabbar a{color:var(--muted);font-size:.7rem;display:flex;flex-direction:column;align-items:center;min-width:3.4rem}
.tabbar a.active{color:var(--text);font-weight:700}.tabbar .i{font-size:1rem}
@media (max-width:760px){.head{grid-template-columns:minmax(0,1fr)}.cards{grid-template-columns:minmax(0,1fr)}.grid{grid-template-columns:minmax(0,1fr)}.front .body{columns:1}}
"""

HTML = "text/html; charset=utf-8"
_PERMA_RE = re.compile(r"^" + re.escape(BASE_PATH) + r"/(\d{4}-\d{2}-\d{2})/(am|pm)/?$")


def _page(title: str, body: str, *, active: str = "latest", self_path: str = BASE_PATH) -> str:
    # In-page anchors carry the page path: the FCC lens injects <base href="/horizon/">.
    body = body.replace('href="#', f'href="{self_path}#')
    tabs = [(BASE_PATH, "\u25a4", "Latest", "latest"), (f"{BASE_PATH}/archive", "\u2630", "Archive", "archive")]
    tabbar = "".join(
        f'<a href="{h}" class="{"active" if k == active else ""}"><span class="i" aria-hidden="true">{i}</span>{l}</a>'
        for h, i, l, k in tabs
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        '<meta name="color-scheme" content="dark">'
        '<meta name="robots" content="noindex,nofollow">'
        f"<title>{_e(title)}</title>"
        f"<style>{_CSS}</style></head><body data-page=\"daily-brief\">"
        f'<div class="paper">{body}</div>'
        f'<nav class="tabbar" aria-label="Daily Brief navigation">{tabbar}</nav>'
        "</body></html>"
    )


def _topbar(middle: str = "") -> str:
    return (
        f'<div class="topbar"><a href="{BASE_PATH}">Daily Brief</a>'
        f'{middle}<a href="{BASE_PATH}/archive">Archive</a></div>'
    )


def _edition_nav(key: tuple[str, str], root: Path | None) -> str:
    prev_k, next_k = neighbors(key, root)

    def a(k: tuple[str, str] | None, label: str, rel: str) -> str:
        if not k:
            return f"<span>{label}</span>"
        return f'<a rel="{rel}" href="{edition_url(k)}">{label}</a>'

    return (
        '<nav class="edition-nav" aria-label="Editions">'
        f'{a(prev_k, OLDER, "prev")}<a href="{BASE_PATH}/archive">Archive</a>{a(next_k, NEWER, "next")}</nav>'
    )


def render_edition(
    raw: dict,
    *,
    root: Path | None = None,
    is_latest_view: bool = False,
    now: datetime | None = None,
) -> str:
    ed, held = scrub_edition(raw)
    key = (ed["date"], ed["edition"])
    self_path = BASE_PATH if is_latest_view else edition_url(key)
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

    vol, no = ed.get("vol"), ed.get("no")
    dateline = (
        '<div class="dateline">'
        f"<span>Vol. {_e(str(vol)) if vol is not None else DASH}</span>"
        f"<span>No. {_e(str(no)) if no is not None else DASH}</span>"
        f"<span>{_e(long_date)}</span><span>{_e(label)}</span><span>{_e(LOCATION)}</span></div>"
    )
    head = (
        '<header class="head">'
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
        f'<div class="card"><div class="card-k">Today</div><div class="card-v">{_e(highlight) or DASH}</div></div>'
        f"{_reminders_card(todos, today, len(mac_task_items(ed.get('mac_tasks'))))}"
        f'<div class="card"><div class="card-k">{"Tomorrow" if ed["edition"] == "pm" else "Upcoming"}</div>{up_html}</div>'
        "</div>"
    )

    body_paras = [p.strip() for p in re.split(r"\n\s*\n", fp.get("body") or "") if p.strip()]
    img = f'<img src="{_e(fp["image"])}" alt="" loading="lazy">' if _is_http(fp.get("image")) else ""
    theme_html = '<div class="theme">' + _e(ed.get("theme") or "") + "</div>" if ed.get("theme") else ""
    sub_html = '<p class="subhead">' + _e(fp["subhead"]) + "</p>" if fp.get("subhead") else ""
    front = (
        '<article class="front" id="sec-front">'
        f"{theme_html}<h2>{_e(fp.get('headline', ''))}</h2>{sub_html}{img}"
        f'<div class="body">{"".join(f"<p>{_e(p)}</p>" for p in body_paras)}</div>'
        "</article>"
    )

    # Publisher order; To-Do after the schedule section (day-ahead / tomorrow)
    # or in place of an explicit "todo" placeholder; Mac tasks right after To-Do.
    secs = [s for s in ed.get("sections") or [] if isinstance(s, dict)]
    todo_html = render_todos(todos, today, edition=ed["edition"])
    mac_html = render_mac_tasks(ed.get("mac_tasks"))
    blocks: list[tuple[str, str, str]] = []
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
    ti = next(i for i, b in enumerate(blocks) if b[0] == "todo")
    blocks.insert(ti + 1, ("mac", MAC_TASKS_TITLE, mac_html))

    pills = (
        '<nav class="pills" aria-label="Sections"><a href="#sec-front">Front Page</a>'
        + "".join(f'<a href="#sec-{_e(i)}">{_e(t)}</a>' for i, t, _ in blocks)
        + "</nav>"
    )
    mt = ed.get("mac_tasks")
    mac_at = _fmt_et_time(mt.get("fetched_at")) if isinstance(mt, dict) else ""
    mac_note = (
        f"Mac tasks: snapshot of the Notion checklist page ({_e(mac_at)}). "
        if mac_at
        else ("" if isinstance(mt, dict) else "Mac tasks: no snapshot in this edition. ")
    )
    noun = "line" if held == 1 else "lines"
    held_note = f'<span data-held="{held}">{held} {noun} {HELD_NOTE}. </span>' if held else ""
    snap = (
        f'<div class="foot">To-Do: snapshot from Notion Todo List at publish '
        f'({_e(_fmt_et_time(ed.get("published_at")))}, {_e(ed["date"])}). '
        f"{mac_note}{held_note}"
        "Read-only; edit items in Notion. Published "
        f'{_e(_fmt_et_time(ed.get("published_at")))} \u00b7 <a href="{edition_url(key)}">Permalink</a>'
        f'<br>{_e(NOT_MODEL_NOTE)}</div>'
    )
    top = _topbar(f'<span class="muted">{_e(label)} \u00b7 {_e(ed["date"])}</span>')
    body = (
        top + stale + head + dateline + cards + pills + front
        + f'<div class="grid">{"".join(h for _, _, h in blocks)}</div>'
        + _edition_nav(key, root) + snap
    )
    return _page(f"{PAPER_NAME} \u00b7 {label} \u00b7 {ed['date']}", body, active="latest", self_path=self_path)


def render_archive(root: Path | None = None) -> str:
    keys = list_editions(root)
    by_day: dict[str, list[str]] = {}
    for d, e in keys:
        by_day.setdefault(d, []).append(e)
    if not by_day:
        rows = '<p class="empty">No editions published yet.</p>'
    else:
        rows, month = "", ""
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
    latest = f'<a href="{edition_url(keys[0])}">Newest: {_e(keys[0][0])} {_e(keys[0][1])}</a>' if keys else ""
    body = (
        _topbar()
        + f'<header class="masthead" style="margin:.4rem 0"><h1>{_e(PAPER_NAME)}</h1>'
        '<div class="tagline">Archive \u00b7 editions by Eastern date</div></header>'
        f'<div class="dateline"><span>{len(keys)} editions</span><span>{latest}</span></div>'
        f"{rows}"
        f'<div class="foot">{_e(NOT_MODEL_NOTE)}</div>'
    )
    return _page(f"{PAPER_NAME} \u00b7 Archive", body, active="archive", self_path=f"{BASE_PATH}/archive")


def render_empty() -> str:
    body = (
        _topbar()
        + f'<header class="masthead"><h1>{_e(PAPER_NAME)}</h1><div class="tagline">{_e(TAGLINE)}</div></header>'
        '<p class="empty" style="text-align:center;margin-top:2rem">No edition published yet. '
        "The morning (~8 AM ET) and evening (~8 PM ET) editions appear here once Grok publishes them.</p>"
        f'<div class="foot">{_e(NOT_MODEL_NOTE)}</div>'
    )
    return _page(PAPER_NAME, body)


def render_not_found(d: str, e: str) -> str:
    body = _topbar() + (
        f'<p class="empty" style="margin-top:2rem">No {_e(edition_label(e) if e in EDITIONS else "edition")} on file for {_e(d)}.</p>'
    )
    return _page(f"{PAPER_NAME} \u00b7 not found", body)


def is_daily_brief_path(path: str) -> bool:
    return path == BASE_PATH or path.startswith(BASE_PATH + "/")


def route(path: str, *, root: Path | None = None, now: datetime | None = None) -> tuple[int, str, str] | None:
    """Map a request path to (status, content_type, body), or None if not a Daily Brief path."""
    if not is_daily_brief_path(path):
        return None
    if path in (BASE_PATH, BASE_PATH + "/"):
        k = latest_key(root)
        if not k:
            return 200, HTML, render_empty()
        ed = load_edition(*k, root=root)
        if ed is None:
            return 500, HTML, render_not_found(*k)
        return 200, HTML, render_edition(ed, root=root, is_latest_view=True, now=now)
    if path in (BASE_PATH + "/archive", BASE_PATH + "/archive/"):
        return 200, HTML, render_archive(root)
    m = _PERMA_RE.match(path)
    if m:
        ed = load_edition(m.group(1), m.group(2), root=root)
        if ed is None:
            return 404, HTML, render_not_found(m.group(1), m.group(2))
        return 200, HTML, render_edition(ed, root=root, now=now)
    if path == BASE_PATH + "/api/latest":
        k = latest_key(root)
        ed = load_edition(*k, root=root) if k else None
        clean = scrub_edition(ed)[0] if ed else None
        payload = {"ok": clean is not None, "edition": clean, "url": edition_url(k) if k else None}
        return (200 if clean else 404), "application/json", json.dumps(payload, ensure_ascii=False)
    if path == BASE_PATH + "/api/editions":
        keys = list_editions(root)
        payload = {"ok": True, "editions": [{"date": d, "edition": e, "url": edition_url((d, e))} for d, e in keys]}
        return 200, "application/json", json.dumps(payload)
    rest = path[len(BASE_PATH) + 1 :]
    return 404, HTML, render_not_found(rest[:10], "")
