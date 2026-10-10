"""Daily Brief pages on the Horizon host, under ``/daily-brief``.

  /daily-brief                     newest edition (am or pm)
  /daily-brief/YYYY-MM-DD/am|pm    permalink
  /daily-brief/archive             editions by Eastern date
  /daily-brief/api/latest          newest edition JSON (the stored edition)
  /daily-brief/api/editions        edition index

A sibling of Horizon's own pages, not part of the Horizon model. It never
reads Horizon world-state or brief artifacts. The page is internal-only, on
Tailscale, and shows the stored edition with no money filter. The page and
``/daily-brief/api/latest`` are built from the same view. Styling reuses
Horizon's base palette and type. The newspaper's own local weather box is
shown; Horizon regime and model widgets are not.

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

from research.daily_brief.quotes import QUOTES, quote_attribution, quote_for, quote_text
from research.daily_brief.redact import scrub_edition
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
NOT_MODEL_LINE = "Daily Brief, not Horizon model output"
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


def _weather_html(w: Any) -> str:
    """Local conditions from the edition. Empty when the edition has none."""
    if not w:
        return '<div class="wx-sum muted">Not reported</div>'
    if isinstance(w, str):
        return f'<div class="wx-sum">{_e(w)}</div>'
    parts = []
    hi, lo = w.get("high"), w.get("low")
    icon = w.get("icon") or ""
    if hi is not None or lo is not None:
        parts.append(
            f'<div class="wx-temp">{_e(str(icon))} {_e(str(hi if hi is not None else DASH))}°'
            f" / {_e(str(lo if lo is not None else DASH))}°</div>"
        )
    if w.get("summary"):
        parts.append(f'<div class="wx-sum">{_e(str(w["summary"]))}</div>')
    extra = [str(w[k]) for k in ("rain", "wind", "note") if w.get(k)]
    if extra:
        parts.append(f'<div class="wx-extra muted">{_e(" · ".join(extra))}</div>')
    return "".join(parts) or '<div class="wx-sum muted">Not reported</div>'


def _quiet_line() -> str:
    return f'<p class="not-model">{_e(NOT_MODEL_LINE)}</p>'


def display_quote(ed: dict) -> Any:
    """The edition's own quote, or the quote of the day.

    Applied at render time so an edition stored without ``quote`` still shows
    one. ``_quote_html`` HTML-escapes the text and the byline.
    """
    provided = ed.get("quote")
    if quote_text(provided):
        return provided
    if not QUOTES:
        return None
    return quote_for(str(ed.get("date") or ""), str(ed.get("edition") or ""))


def _quote_html(q: Any) -> str:
    text = quote_text(q)
    if not text:
        return ""
    who = quote_attribution(q)
    cite = f"<cite>— {_e(who)}</cite>" if who else ""
    return f"<blockquote>“{_e(text)}”</blockquote>{cite}"


def _http_image(url: Any) -> str | None:
    """An http(s) image URL, or None. The page HTML-escapes the src."""
    if not _is_http(url):
        return None
    return url


def _figure(url: str | None, cls: str) -> str:
    if not url:
        return ""
    return f'<figure class="{cls}"><img src="{_e(url)}" alt="" loading="lazy"></figure>'


def _image_index(raw: Any) -> dict[str, dict]:
    """Section id to an http(s) section image and item images, keyed by text."""
    out: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return out
    for section in raw.get("sections") or []:
        if not isinstance(section, dict) or not isinstance(section.get("id"), str):
            continue
        items: dict[str, str] = {}
        for it in section.get("items") or []:
            if not isinstance(it, dict) or not isinstance(it.get("text"), str):
                continue
            url = _http_image(it.get("image"))
            if url and it["text"] not in items:
                items[it["text"]] = url
        out[section["id"]] = {"image": _http_image(section.get("image")), "items": items}
    return out


def _item_html(it: Any, image: str | None = None) -> str:
    fig = _figure(image, "item-fig")
    if isinstance(it, str):
        return f"<li>{_e(it)}{fig}</li>"
    return f"<li>{_link(it.get('text', ''), it.get('link'))}{fig}</li>"


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
# Mobile first. Boxes, pills and badges stay out so the lead and the section
# titles can carry the page.
_CSS = r"""
:root{--bg:#060a10;--panel:#0f1620;--panel2:#121c28;--border:#1c2a3a;--border2:#2a3d52;--text:#e8eef6;--muted:#7f96ad;--dim:#5a7085;
--red:#ff6b6b;--accent:#5b9fd4;--cyan:#4fd1c5;--radius:.6rem;
--sans:"Segoe UI",ui-sans-serif,system-ui,-apple-system,sans-serif;--serif:Georgia,"Times New Roman",serif}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);font-size:17px;line-height:1.6;overflow-x:hidden;min-height:100vh}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}.muted{color:var(--muted)}.red{color:var(--red)}
.paper{max-width:40rem;margin:0 auto;padding:1.35rem 1.2rem 6.75rem}
.topbar{display:flex;justify-content:space-between;align-items:baseline;gap:.75rem 1.25rem;flex-wrap:wrap;font:600 .72rem/1.2 var(--sans);letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:0 0 1.75rem}
.topbar a{color:var(--muted);background:none;border:0;padding:0}
.topbar .muted{letter-spacing:.04em;text-transform:none;font-weight:600}
.stale{border:0;border-left:2px solid var(--red);border-radius:0;color:var(--red);padding:.15rem 0 .15rem .8rem;font-size:.92rem;line-height:1.45;margin:0 0 1.4rem}
.head{display:flex;flex-direction:column;gap:1.6rem}
.masthead{order:-1;text-align:center;min-width:0}
.masthead h1{font-family:var(--serif);font-weight:900;font-size:clamp(2.65rem,11vw,4.6rem);line-height:.92;letter-spacing:-.03em;margin:0 0 .45rem;overflow-wrap:anywhere}
.tagline{font:italic 1.02rem/1.4 var(--serif);color:var(--muted)}
.not-model{margin:.2rem 0 0;font:500 .68rem var(--sans);letter-spacing:.01em;color:var(--dim)}
.wx,.qotd{font-size:.98rem;line-height:1.45;min-width:0;background:none;border:0;padding:0}
.wx h4,.qotd h4{margin:0 0 .4rem;font:600 .68rem/1 var(--sans);letter-spacing:.16em;text-transform:uppercase;color:var(--muted)}
.wx-temp{font:600 1.45rem/1.15 var(--serif);letter-spacing:-.02em}
.wx-sum{margin-top:.2rem}.wx-extra{margin-top:.15rem;font-size:.88rem}
.qotd blockquote{margin:0;font:italic 1.08rem/1.45 var(--serif)}
.qotd cite{display:block;margin-top:.4rem;font:600 .78rem/1.3 var(--sans);font-style:normal;color:var(--muted)}
.dateline{display:flex;flex-wrap:wrap;justify-content:center;gap:.4rem 1.1rem;border:0;border-top:1px solid var(--border2);border-bottom:1px solid var(--border2);padding:.7rem 0;margin:1.7rem 0 1.9rem;font:600 .68rem/1.2 var(--sans);letter-spacing:.14em;text-transform:uppercase;color:var(--muted);text-align:center}
.front{border:0;padding:0;margin:0 0 .4rem}
.theme{font:600 .72rem/1 var(--sans);letter-spacing:.16em;text-transform:uppercase;color:var(--cyan);margin:0 0 .55rem}
.front h2{font:700 clamp(1.9rem,6.4vw,2.85rem)/1.12 var(--serif);letter-spacing:-.02em;margin:0 0 .7rem;overflow-wrap:anywhere}
.front .subhead{font:italic 1.16rem/1.45 var(--serif);color:var(--muted);margin:0 0 1.15rem;max-width:40rem}
.front .body{max-width:40rem;font-size:1.05rem;line-height:1.65}
.front .body p{margin:0 0 1.05rem}
.lead-fig,.sec-fig,.item-fig{margin:1.15rem 0 1.45rem}
.lead-fig img,.sec-fig img,.item-fig img,.front img{display:block;width:100%;height:auto;border-radius:0}
.item-fig{margin:.55rem 0 .15rem}
.item-fig img{max-height:15rem;object-fit:cover}
.cards{display:grid;grid-template-columns:minmax(0,1fr);gap:1.5rem;margin:2.1rem 0 1.6rem}
.card{border:0;border-radius:0;background:none;padding:0;min-width:0;font-size:.98rem;line-height:1.45}
.card-k{font:600 .68rem/1 var(--sans);letter-spacing:.16em;text-transform:uppercase;color:var(--muted)}
.card-v{font:600 1.28rem/1.25 var(--serif);margin-top:.3rem;overflow-wrap:anywhere}
.card-sub{margin-top:.2rem;font-size:.88rem}
.card ul{list-style:none;margin:.65rem 0 0;padding:0}
.card li{margin:.4rem 0;overflow-wrap:anywhere}
.card li.overdue a,.card li.overdue{color:var(--red)}
.card .mac-count{font-size:.88rem;margin-top:.35rem}
.card .mac-count a{color:var(--text);border:0}
.pills{display:flex;flex-wrap:wrap;gap:.45rem 1rem;overflow:visible;padding:0 0 1.5rem;max-width:100%;scrollbar-width:none}
.pills a{flex:0 0 auto;background:none;border:0;border-radius:0;padding:0;color:var(--muted);font:600 .72rem/1.2 var(--sans);letter-spacing:.08em;text-transform:uppercase}
.grid{display:grid;grid-template-columns:minmax(0,1fr);gap:0}
.sec{border:0;border-top:1px solid var(--border);padding-top:1.15rem;margin-top:2.75rem;min-width:0;scroll-margin-top:1rem}
.sec-h{font:700 1.45rem/1.15 var(--serif);letter-spacing:-.015em;margin:0 0 .9rem;display:flex;justify-content:space-between;align-items:baseline;gap:.75rem}
.sec-count{font:500 .7rem var(--sans);letter-spacing:.02em;color:var(--muted)}
.sec ul{list-style:none;margin:0;padding:0}
.sec li{margin:0;padding:.6rem 0;overflow-wrap:anywhere;line-height:1.45}
.todo-group h3{font:600 .72rem/1 var(--sans);letter-spacing:.14em;text-transform:uppercase;margin:1.5rem 0 .15rem;color:var(--muted)}
.todo-overdue h3{color:var(--red)}
.todo-list{list-style:none;padding:0!important}
.todo-item{border:0;padding:.8rem 0}
.todo-item.overdue .todo-title,.todo-item.overdue .todo-due{color:var(--red);font-weight:600}
.todo-title{font-size:1.05rem}
.todo-meta{display:flex;flex-wrap:wrap;gap:.35rem .7rem;align-items:baseline;font-size:.78rem;margin-top:.15rem;color:var(--muted)}
.todo-due{color:var(--muted)}
.chip{border:0;border-radius:0;background:none;padding:0;color:var(--muted);font-size:.78rem}
.chip-hi{color:var(--red)}.chip-prog{color:var(--accent)}.chip-owner,.chip-lo{color:var(--muted)}
.empty{font-style:italic;color:var(--muted);margin:.2rem 0}
.mac-list{list-style:none;padding:0!important}
.mac-item{display:flex;gap:.7rem;align-items:flex-start;border:0;padding:.7rem 0;font-size:1.05rem;min-width:0}
.mac-box{flex:0 0 auto;width:.95rem;height:.95rem;margin-top:.28rem;border:1px solid var(--muted);border-radius:.15rem;background:transparent}
.mac-text{min-width:0;overflow-wrap:anywhere}
.mac-src{font-size:.75rem;margin-left:.4rem;letter-spacing:.04em}
.edition-nav{display:flex;justify-content:space-between;gap:1rem;margin:2.6rem 0 .4rem;font-size:.95rem}
.edition-nav a,.edition-nav span{border:0;border-radius:0;background:none;padding:.2rem 0;color:var(--text)}
.edition-nav span{opacity:.4}
.foot{font-size:.78rem;line-height:1.55;color:var(--muted);text-align:center;margin-top:2rem}
.archive-day{border:0;padding:.75rem 0;display:flex;justify-content:space-between;gap:.75rem;flex-wrap:wrap}
.archive-day a{margin-left:.75rem}
.tabbar{position:fixed;left:0;right:0;bottom:0;display:flex;justify-content:space-around;background:rgba(6,10,16,.94);border-top:1px solid var(--border);padding:.4rem 0 calc(.45rem + env(safe-area-inset-bottom));z-index:5}
.tabbar a{color:var(--muted);font-size:.68rem;letter-spacing:.06em;text-transform:uppercase;display:flex;flex-direction:column;align-items:center;gap:.1rem;min-width:3.4rem}
.tabbar a.active{color:var(--text);font-weight:600}.tabbar .i{font-size:1rem;letter-spacing:0;text-transform:none}
@media (min-width:960px){
.paper{max-width:72rem;padding:2.1rem 2.6rem 5rem}
.head{display:grid;grid-template-columns:minmax(0,1fr) minmax(16rem,1.35fr) minmax(0,1fr);gap:1.5rem 2.4rem;align-items:end}
.masthead{order:0}
.lead-wrap{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(16rem,.9fr);gap:2.5rem 3rem;align-items:start}
.lead-wrap .cards{grid-template-columns:minmax(0,1fr);gap:1.8rem;margin:0}
.lead-wrap .front .body,.lead-wrap .front .subhead{max-width:none}
.grid{grid-template-columns:minmax(0,1fr) minmax(0,1fr);column-gap:3.5rem}
#sec-todo,#sec-mac,.sec-wide{grid-column:1 / -1}
.sec-wide>ul{columns:2;column-gap:3.5rem}
.sec-wide>ul>li{break-inside:avoid}
.lead-fig{margin:1.4rem 0 1.7rem}
}
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
    ed = scrub_edition(raw)[0]
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
        f'<div class="wx"><h4>Local weather</h4>{_weather_html(ed.get("weather"))}</div>'
        f'<div class="masthead"><h1>{_e(PAPER_NAME)}</h1><div class="tagline">{_e(TAGLINE)}</div>{_quiet_line()}</div>'
        f'<div class="qotd"><h4>Quote of the day</h4>{_quote_html(display_quote(ed))}</div>'
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
    img = _figure(_http_image(fp.get("image")), "lead-fig")
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
    images = _image_index(raw)
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
        meta = images.get(sid) or {}
        item_imgs = meta.get("items") or {}
        inner = (
            "<ul>" + "".join(_item_html(i, item_imgs.get(_item_text(i))) for i in items) + "</ul>"
            if items
            else '<p class="empty">Nothing to report</p>'
        )
        fig = _figure(meta.get("image"), "sec-fig")
        blocks.append(
            (sid, s.get("title", ""), f'<section class="sec" id="sec-{_e(sid)}"><h2 class="sec-h">{_e(s.get("title", ""))}</h2>{fig}{inner}</section>')
        )
        if not placed and sid in ("day-ahead", "tomorrow"):
            blocks.append(("todo", "To-Do", todo_html))
            placed = True
    if not placed:
        blocks.insert(0, ("todo", "To-Do", todo_html))
    ti = next(i for i, b in enumerate(blocks) if b[0] == "todo")
    blocks.insert(ti + 1, ("mac", MAC_TASKS_TITLE, mac_html))
    blocks = _span_stranded(blocks)

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
    snap = (
        f'<div class="foot">To-Do: snapshot from Notion Todo List at publish '
        f'({_e(_fmt_et_time(ed.get("published_at")))}, {_e(ed["date"])}). '
        f"{mac_note}"
        "Read-only; edit items in Notion. Published "
        f'{_e(_fmt_et_time(ed.get("published_at")))} \u00b7 <a href="{edition_url(key)}">Permalink</a>'
        f'<br>{_e(NOT_MODEL_NOTE)}</div>'
    )
    top = _topbar(f'<span class="muted">{_e(label)} \u00b7 {_e(ed["date"])}</span>')
    body = (
        top + stale + head + dateline + f'<div class="lead-wrap">{front}{cards}</div>' + pills
        + f'<div class="grid">{"".join(h for _, _, h in blocks)}</div>'
        + _edition_nav(key, root) + snap
    )
    return _page(f"{PAPER_NAME} \u00b7 {label} \u00b7 {ed['date']}", body, active="latest", self_path=self_path)


# Blocks that always take the full row of the two-column desktop grid.
_FULL_ROW = ("todo", "mac")
_WIDE_CLS = "sec sec-wide"


def stranded_blocks(ids: list[str]) -> set[int]:
    """Indexes of half-width blocks that would sit alone in a desktop row.

    The desktop grid has two columns and To-Do / Mac tasks span both. A section
    right before a full-row block, or last on the page, with no partner beside
    it, leaves the other column empty. Those sections span the row instead.
    Publisher order is kept. Phones use one column, so nothing changes there.
    """
    out: set[int] = set()
    pending: int | None = None
    for i, sid in enumerate(ids):
        if sid in _FULL_ROW:
            if pending is not None:
                out.add(pending)
            pending = None
        elif pending is None:
            pending = i
        else:
            pending = None
    if pending is not None:
        out.add(pending)
    return out


def _span_stranded(blocks: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    wide = stranded_blocks([b[0] for b in blocks])
    return [
        (sid, t, h.replace('<section class="sec"', f'<section class="{_WIDE_CLS}"', 1) if i in wide else h)
        for i, (sid, t, h) in enumerate(blocks)
    ]


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
        '<div class="tagline">Archive \u00b7 editions by Eastern date</div>'
        f"{_quiet_line()}</header>"
        f'<div class="dateline"><span>{len(keys)} editions</span><span>{latest}</span></div>'
        f"{rows}"
        f'<div class="foot">{_e(NOT_MODEL_NOTE)}</div>'
    )
    return _page(f"{PAPER_NAME} \u00b7 Archive", body, active="archive", self_path=f"{BASE_PATH}/archive")


def render_empty() -> str:
    body = (
        _topbar()
        + f'<header class="masthead"><h1>{_e(PAPER_NAME)}</h1><div class="tagline">{_e(TAGLINE)}</div>{_quiet_line()}</header>'
        '<p class="empty" style="text-align:center;margin-top:2rem">No edition published yet. '
        "The morning (~8 AM ET) and evening (~8 PM ET) editions appear here once Grok publishes them.</p>"
        f'<div class="foot">{_e(NOT_MODEL_NOTE)}</div>'
    )
    return _page(PAPER_NAME, body)


def render_not_found(d: str, e: str) -> str:
    body = _topbar() + (
        f'<p class="empty" style="margin-top:2rem">No {_e(edition_label(e) if e in EDITIONS else "edition")} on file for {_e(d)}.</p>'
        f'<div class="foot">{_quiet_line()}</div>'
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
