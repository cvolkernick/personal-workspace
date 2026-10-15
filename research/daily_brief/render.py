"""Daily Brief pages on the Horizon host, under ``/daily-brief``.

  /daily-brief                     newest edition (am or pm)
  /daily-brief/YYYY-MM-DD/am|pm    permalink
  /daily-brief/archive             editions by Eastern date
  /daily-brief/api/latest          newest edition JSON (stored edition; quote fallback)
  /daily-brief/api/editions        edition index

A sibling of Horizon's own pages, not part of the Horizon model. It never
reads Horizon world-state or brief artifacts. The page is internal-only, on
Tailscale, and shows the stored edition with no money filter. The page and
``/daily-brief/api/latest`` are built from the same view. A missing quote is
filled in that view with the quote of the day (date + edition); the file on
disk is not rewritten, and the "No quote today." placeholder is never emitted.
Styling reuses
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

from research.daily_brief.fund import CONTEXT_NOTE, FUND_TITLE, MACRO_LABEL
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


_QUOTE_PLACEHOLDER = "No quote today."


def display_quote(ed: dict) -> Any:
    """The edition's own quote, or the quote of the day.

    Applied at render time so an edition stored without ``quote`` still shows
    one. ``_quote_html`` HTML-escapes the text and the byline. The same
    function fills the API JSON, so the page and the API cannot pick different
    lines. Never returns the placeholder.
    """
    provided = ed.get("quote")
    if quote_text(provided) and quote_text(provided) != _QUOTE_PLACEHOLDER:
        return provided
    if not QUOTES:
        return None
    picked = quote_for(str(ed.get("date") or ""), str(ed.get("edition") or ""))
    if quote_text(picked) == _QUOTE_PLACEHOLDER:
        return None
    return picked


def edition_for_api(ed: dict) -> dict:
    """Stored edition for JSON, with the quote the HTML page shows.

    A quote already on the edition is left as published (string or object,
    ``by`` or ``author``). A missing, blank, or placeholder quote becomes
    ``display_quote`` for this date and edition. Does not rewrite the file.
    """
    clean = scrub_edition(ed)[0]
    shown = display_quote(ed)
    if shown is None:
        clean.pop("quote", None)
        return clean
    if quote_text(clean.get("quote")) and quote_text(clean.get("quote")) != _QUOTE_PLACEHOLDER:
        return clean
    clean["quote"] = shown
    return clean


def _quote_html(q: Any) -> str:
    text = quote_text(q)
    if not text:
        return ""
    who = quote_attribution(q)
    cite = f"<cite>— {_e(who)}</cite>" if who else ""
    return f"<blockquote>“{_e(text)}”</blockquote>{cite}"


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


def _fmt_et_stamp(raw: Any) -> str:
    dt = _parse_ts(raw)
    if dt is None:
        return ""
    t = dt.astimezone(ET)
    return f"{t.strftime('%b')} {t.day}, " + t.strftime("%I:%M %p").lstrip("0") + " ET"


_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def _safe_text_html(text: str) -> str:
    """Untrusted agent text: escaped, bullets and paragraphs kept, bare http(s) URLs linked. Never raw HTML."""
    out: list[str] = []
    in_list = False
    para: list[str] = []

    def inline(line: str) -> str:
        res, pos = [], 0
        for m in _URL_RE.finditer(line):
            res.append(_e(line[pos:m.start()]))
            res.append(_link(m.group(0), m.group(0)))
            pos = m.end()
        res.append(_e(line[pos:]))
        return "".join(res)

    def flush() -> None:
        if para:
            out.append("<p>" + "<br>".join(inline(x) for x in para) + "</p>")
            para.clear()

    for line in (text or "").split("\n"):
        st = line.strip()
        if st[:2] in ("- ", "* "):
            flush()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(st[2:].strip())}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if not st:
            flush()
        else:
            para.append(st)
    flush()
    if in_list:
        out.append("</ul>")
    return "".join(out)


def _fund_dev_html(d: dict) -> str:
    url = d.get("url")
    src = _link("source", url, "fund-src") if _is_http(url) else '<span class="fund-meta">no source link</span>'
    return f'<li class="fund-dev">{_e(d.get("text", ""))} {src}</li>'


def _fund_row_html(row: dict, filed: bool = True) -> str:
    sym = str(row.get("symbol", ""))
    pinned = row.get("relevance") == "pinned"
    chip = f'<span class="chip {"chip-prog" if pinned else ""} fund-rel">{"pinned" if pinned else "watch only"}</span>'
    devs = [d for d in row.get("developments") or [] if isinstance(d, dict)]
    dev_html = "<ul>" + "".join(_fund_dev_html(d) for d in devs) + "</ul>" if devs else '<p class="empty fund-gap">' + ("Not covered in Naka\u2019s file for this edition." if filed else "Waiting on Naka\u2019s file.") + "</p>"
    dis = row.get("file_relevance_disagrees")
    dis_html = f'<p class="fund-meta fund-flag">Naka\u2019s file says \u201c{_e(str(dis))}\u201d; consider_share.json decides.</p>' if dis else ""
    return (
        f'<div class="fund-row" id="fund-{_e(sym)}" data-symbol="{_e(sym)}" data-relevance="{"pinned" if pinned else "watch"}">'
        f'<h3><span class="fund-sym">{_e(sym)}</span> {chip}</h3>'
        f'<p class="fund-relevance">{_e(row.get("relevance_line") or "")}</p>{dev_html}{dis_html}</div>'
    )


def _fund_other_html(o: dict) -> str:
    sym = str(o.get("symbol", ""))
    w = o.get("file_relevance")
    chip = f'<span class="chip fund-rel">{_e(str(w))}</span>' if w else ""
    devs = "<ul>" + "".join(_fund_dev_html(d) for d in o.get("developments") or [] if isinstance(d, dict)) + "</ul>"
    return (f'<div class="fund-row" data-symbol="{_e(sym)}" data-relevance="other"><h3><span class="fund-sym">{_e(sym)}</span> {chip}</h3>'
            f'<p class="fund-relevance">Not in consider_share.json; from Naka\u2019s file.</p>{devs}</div>')


def _fund_notes_banner(n: Any) -> str:
    n = n if isinstance(n, dict) else {}
    state = n.get("state") if n.get("state") in ("filed", "missing", "mismatch", "invalid") else "missing"
    if state == "filed":
        bits = [f"File date {_e(str(n.get('date') or n.get('edition_date') or ''))}"]
        if n.get("filed_at"):
            bits.append(f"filed {_e(_fmt_et_stamp(n['filed_at']))}")
        if n.get("commit_time"):
            bits.append(f"committed {_e(_fmt_et_stamp(n['commit_time']))}")
        if n.get("mtime"):
            bits.append(f"on prism {_e(_fmt_et_stamp(n['mtime']))}")
        miss = [x for x in n.get("missing_sections") or [] if isinstance(x, str)]
        flag = ""
        if miss:
            flag += f'<p class="fund-meta fund-flag">Missing required section(s): {_e(", ".join(miss))}.</p>'
        other = [x for x in n.get("problems") or [] if isinstance(x, str)]
        if other:
            flag += f'<p class="fund-meta fund-flag">Format notes: {_e("; ".join(other))}.</p>'
        return (f'<div class="fund-banner" data-notes-state="filed"><p class="fund-meta">Naka\u2019s fund notes \u00b7 {" \u00b7 ".join(bits)}. '
                f'Written by Naka, shown as filed.</p>{flag}</div>')
    note = n.get("note") or f"Naka\u2019s fund notes not yet filed for {n.get('edition_date') or 'this edition'}."
    extra = f' <span class="fund-meta">Most recent file on disk: {_e(str(n["latest_on_disk"]))} (not shown).</span>' if n.get("latest_on_disk") else ""
    mt = f' <span class="fund-meta">File on prism modified {_e(_fmt_et_stamp(n["mtime"]))}.</span>' if state in ("mismatch", "invalid") and n.get("mtime") else ""
    return f'<div class="fund-banner fund-notfiled" data-notes-state="{state}"><p class="empty">{_e(note)}{extra}{mt}</p></div>'


def _fund_text_block(title: str, key: str, n: Any, cls: str) -> str:
    n = n if isinstance(n, dict) else {}
    if n.get("state") != "filed":
        return ""
    txt = n.get(key)
    head = f'<h3 class="fund-sub">{_e(title)}</h3>'
    if not txt:
        return f'<div class="{cls}" data-present="false">{head}<p class="empty">Section not in Naka\u2019s file.</p></div>'
    return f'<div class="{cls}" data-present="true">{head}<div class="fund-text">{_safe_text_html(txt)}</div></div>'


def _fund_unknown_html(n: Any) -> str:
    n = n if isinstance(n, dict) else {}
    parts = []
    for u in n.get("unknown_sections") or []:
        if isinstance(u, dict):
            parts.append(f'<details class="fund-unknown"><summary>{_e(str(u.get("heading", "")))} <span class="fund-meta">(extra section)</span></summary>{_safe_text_html(str(u.get("text", "")))}</details>')
    unp = [x for x in n.get("unparsed") or [] if isinstance(x, str)]
    if unp:
        parts.append('<details class="fund-unknown"><summary>Lines that did not match the format</summary>' + _safe_text_html("\n".join("- " + x for x in unp)) + "</details>")
    return "".join(parts)


def _fund_macro_html(macro: Any) -> str:
    m = macro if isinstance(macro, dict) else {}
    lines = [ln for ln in m.get("lines") or [] if isinstance(ln, dict)]
    held = '<span class="chip fund-held">held fixture</span>' if m.get("held", True) else ""
    state = "items" if any(ln.get("text") for ln in lines) else "placeholder"
    head = f'<h3 class="fund-sub">Macro \u00b7 {_e(m.get("label") or MACRO_LABEL)} {held}</h3>'
    if state == "placeholder":
        body = f'<p class="empty fund-macro-empty">{_e(m.get("placeholder") or "")}</p>'
        topics = "".join(f'<li class="fund-macro-topic"><b>{_e(ln.get("label", ""))}</b> <span class="muted">placeholder, no line supplied</span></li>' for ln in lines)
        body += f"<ul>{topics}</ul>"
    else:
        rows = []
        for ln in lines:
            if ln.get("text"):
                when = f' <span class="fund-meta">{_e(_fmt_et_stamp(ln.get("as_of")) or str(ln.get("as_of") or ""))}</span>' if ln.get("as_of") else ""
                rows.append(f'<li class="fund-macro-topic"><b>{_e(ln.get("label", ""))}</b> {_link(ln["text"], ln.get("link"))}{when}</li>')
            else:
                rows.append(f'<li class="fund-macro-topic"><b>{_e(ln.get("label", ""))}</b> <span class="muted">placeholder, no line supplied</span></li>')
        body = "<ul>" + "".join(rows) + "</ul>"
    return f'<div class="fund-macro" data-macro-state="{state}">{head}{body}</div>'


def _fmt_pct(x: Any) -> str:
    return DASH if x is None else f"{x:.1f}%"


def _fmt_pp(x: Any) -> str:
    return DASH if x is None else f"{x:+.1f} pp"


def _fund_holdings_html(h: Any) -> str:
    h = h if isinstance(h, dict) else {}
    head = '<h3 class="fund-sub">Holdings and drift vs the 50/50 pins</h3>'
    if h.get("state") != "ok":
        asof = f' <span class="fund-meta">Snapshot as_of {_e(_fmt_et_stamp(h.get("as_of")))}.</span>' if h.get("as_of") else ""
        return (f'<div class="fund-holdings" data-holdings-state="no_data">{head}'
                f'<p class="empty">{_e(h.get("note") or "No data: holdings not supplied.")}{asof}</p></div>')
    rows = []
    for r in h.get("rows") or []:
        drift = r.get("drift_pp")
        cls = ' class="num fund-drift-neg"' if isinstance(drift, (int, float)) and abs(drift) >= 5 else ' class="num"'
        rows.append(
            f'<tr data-symbol="{_e(str(r.get("symbol", "")))}"><td><b>{_e(str(r.get("symbol", "")))}</b></td>'
            f'<td>{"pinned" if r.get("pinned") else "watch only"}</td><td>{_e(str(r.get("sleeve") or DASH))}</td>'
            f'<td class="num">{_fmt_pct(r.get("weight_nav_pct"))}</td><td class="num">{_fmt_pct(r.get("stocks_sleeve_weight_pct"))}</td>'
            f'<td class="num">{_fmt_pct(r.get("target_pct"))}</td><td{cls}>{_fmt_pp(drift)}</td></tr>'
        )
    if h.get("cash_weight_pct") is not None:
        rows.append(f'<tr><td><b>cash</b></td><td>{DASH}</td><td>{DASH}</td><td class="num">{_fmt_pct(h["cash_weight_pct"])}</td><td></td><td></td><td></td></tr>')
    sl = "".join(
        f'<li>{_e(s.get("label", ""))}: {_fmt_pct(s.get("weight_deployed_pct"))} of deployed vs {_fmt_pct(s.get("target_pct"))} target '
        f'({_fmt_pp(s.get("drift_pp"))}, {"in band" if s.get("in_band") else "outside band" if s.get("in_band") is False else "band unknown"})</li>'
        for s in h.get("sleeves") or []
    )
    note = f'<p class="fund-meta">{_e(h["note"])}</p>' if h.get("note") else ""
    return (
        f'<div class="fund-holdings" data-holdings-state="ok">{head}'
        f'<p class="fund-meta">Robinhood agentic snapshot as_of {_e(_fmt_et_stamp(h.get("as_of")))} ({_e(str(h.get("age_hours")))}h old, stale after '
        f'{_e(str(h.get("stale_after_hours")))}h). Basis: {_e(str(h.get("basis")))}. Drift is in percentage points of the stocks sleeve; weights only, no balances.</p>'
        f'{note}<table class="fund-table"><thead><tr><th>Symbol</th><th>Relevance</th><th>Sleeve</th><th>NAV</th><th>Stocks sleeve</th><th>Target</th><th>Drift</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table><ul>{sl}</ul></div>'
    )


def _fund_status_html(st: Any) -> str:
    head = '<h3 class="fund-sub">Status (context only, not permission)</h3>'
    if not isinstance(st, dict):
        return f'<div class="fund-status" data-status-state="no_data">{head}<p class="empty">No data: status not supplied.</p></div>'
    fm = st.get("fund_manager") or {}
    live = {True: "true", False: "false"}.get(fm.get("live"), "unknown")
    iss = st.get("issue") or {}
    state = iss.get("state")
    iss_txt = state if state in ("open", "closed") else "no data (GitHub not reachable)"
    title = f" \u2014 {iss.get('title')}" if iss.get("title") else ""
    link = _link(f"#{iss.get('number')}", iss.get("url"))
    return (
        f'<div class="fund-status" data-status-state="ok">{head}<ul>'
        f'<li>fund_manager.live: <b data-live="{live}">{_e(live)}</b> <span class="fund-meta">(status {_e(str(fm.get("status") or "unknown"))}, {_e(str(fm.get("source") or ""))})</span></li>'
        f'<li>{link}: <b data-issue-state="{_e(str(state))}">{_e(iss_txt)}</b>{_e(title)}</li></ul>'
        f'<p class="fund-meta">{_e(st.get("note") or "")}</p></div>'
    )


def _fund_naka_html(nk: Any) -> str:
    nk = nk if isinstance(nk, dict) else {}
    head = '<h3 class="fund-sub">Naka: latest actions taken</h3>'
    if nk.get("state") == "entry":
        return (f'<div class="fund-naka-block" data-naka-state="entry">{head}<p class="fund-meta">Entry dated {_e(str(nk.get("date")))} '
                f'(investment/naka-actions.md, append-only)</p><div class="fund-naka fund-text">{_safe_text_html(nk.get("text") or "")}</div></div>')
    return (f'<div class="fund-naka-block" data-naka-state="none">{head}'
            f'<p class="empty">{_e(nk.get("note") or "No Naka entry on file yet.")}</p></div>')


def render_fund(fund: Any) -> str:
    """Fund watch section. Absent block renders nothing (editions before this field)."""
    if not isinstance(fund, dict):
        return ""
    rows = [r for r in fund.get("monitored") or [] if isinstance(r, dict)]
    others = [o for o in fund.get("other_tickers") or [] if isinstance(o, dict)]
    notes = fund.get("notes") if isinstance(fund.get("notes"), dict) else {}
    stamp = _fmt_et_stamp(fund.get("generated_at"))
    asof = f" Monitored set from consider_share.json (as of {_e(str(fund.get('consider_share_as_of')))})." if fund.get("consider_share_as_of") else " Monitored set from consider_share.json."
    intro = (
        f'<p class="fund-note"><b>{_e(fund.get("note") or CONTEXT_NOTE)}</b>{asof}'
        f'{" Built " + _e(stamp) + "." if stamp else ""}</p>'
    )
    body = "".join(_fund_row_html(r, notes.get("state") == "filed") for r in rows) if rows else '<p class="empty">No monitored symbols (consider_share.json not readable).</p>'
    other_html = (
        '<h3 class="fund-sub">Other tickers in Naka\u2019s file</h3><div class="fund-rows">' + "".join(_fund_other_html(o) for o in others) + "</div>"
        if others else ""
    )
    return (
        f'<section class="sec sec-fund" id="sec-fund"><h2 class="sec-h">{_e(FUND_TITLE)}'
        f'<span class="sec-count">{len(rows)} monitored</span></h2>{intro}{_fund_notes_banner(notes)}'
        f'{_fund_status_html(fund.get("status"))}{_fund_holdings_html(fund.get("holdings"))}'
        f'<h3 class="fund-sub">Monitored set and developments (last ~48h, from Naka\u2019s file)</h3><div class="fund-rows">{body}</div>{other_html}'
        f'{_fund_text_block("Naka: direction (advisory)", "direction", notes, "fund-direction")}'
        f'{_fund_text_block("Naka: actions taken (this file)", "actions", notes, "fund-actions")}'
        f'{_fund_naka_html(fund.get("naka"))}{_fund_unknown_html(notes)}'
        f'{_fund_macro_html(fund.get("macro"))}</section>'
    )


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
.head{display:grid;grid-template-columns:1fr 2fr 1fr;gap:.75rem;align-items:center;border-bottom:1px solid var(--border);padding-bottom:.5rem}
.wx,.qotd{font-size:.8rem;border:1px solid var(--border);border-radius:var(--radius);padding:.45rem .6rem;background:var(--panel);min-width:0}
.wx h4,.qotd h4{margin:0 0 .2rem;font:600 .65rem var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.wx-temp{font:700 1.1rem var(--serif)}
.qotd blockquote{margin:0;font:italic .85rem var(--serif)}.qotd cite{font-size:.72rem;color:var(--muted)}
.masthead{min-width:0}
.masthead h1{font-family:var(--serif);font-weight:900;font-size:clamp(2rem,7vw,3.4rem);line-height:1;margin:.1rem 0;letter-spacing:-.01em;overflow-wrap:anywhere}
.tagline{font:italic .8rem var(--serif);color:var(--muted)}
.not-model{margin:.2rem 0 0;font:500 .68rem var(--sans);letter-spacing:.01em;color:var(--dim)}
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
.sec-fund{grid-column:1/-1}
.fund-note{font-size:.8rem;color:var(--muted);margin:.1rem 0 .4rem}.fund-note b{color:var(--text)}
.fund-rows{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.6rem}
.fund-row{border:1px solid var(--border);border-radius:var(--radius);background:var(--panel);padding:.5rem .65rem;min-width:0}
.fund-row h3,.fund-sub{font:700 .95rem var(--sans);margin:0 0 .15rem;display:flex;flex-wrap:wrap;gap:.35rem;align-items:center}
.fund-sub{font-size:.78rem;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-top:.8rem}
.fund-relevance{margin:0 0 .25rem;font-size:.78rem;color:var(--muted)}
.fund-row ul{padding-left:.2rem!important;list-style:none}.fund-dev{margin:.25rem 0}
.fund-meta{font-size:.7rem;color:var(--dim)}.fund-kind{text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
.fund-gap{margin:.2rem 0;font-size:.78rem}
.fund-held{border-color:var(--cyan);color:var(--cyan)}
.fund-drift-neg{color:var(--red)}.fund-table{width:100%;border-collapse:collapse;font-size:.8rem}.fund-table th{font:600 .62rem var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--muted);text-align:left;border-bottom:1px solid var(--border2);padding:.2rem .3rem}.fund-table td{padding:.2rem .3rem;border-bottom:1px dotted var(--border)}.fund-table td.num{text-align:right;font-variant-numeric:tabular-nums}.fund-status li{margin:.2rem 0}.fund-naka{font-size:.85rem;margin:.2rem 0}
.fund-macro li{margin:.2rem 0}.fund-banner{margin:.3rem 0}.fund-notfiled{border:1px dashed var(--border2);border-radius:var(--radius);padding:.45rem .65rem;background:var(--panel)}.fund-flag{color:var(--red)}.fund-text p{margin:.2rem 0}.fund-text ul{padding-left:1.1rem}.fund-unknown{margin:.3rem 0;font-size:.82rem}.fund-src{font-size:.72rem;margin-left:.2rem}
.edition-nav{display:flex;justify-content:space-between;gap:.5rem;margin:1.2rem 0 .5rem;font-size:.85rem}
.edition-nav a,.edition-nav span{border:1px solid var(--border);border-radius:999px;padding:.3rem .8rem;color:var(--text);background:var(--panel)}
.edition-nav span{opacity:.4}
.foot{font-size:.72rem;color:var(--muted);text-align:center;margin-top:.8rem}
.archive-day{border-bottom:1px solid var(--border);padding:.45rem 0;display:flex;justify-content:space-between;gap:.5rem;flex-wrap:wrap}
.archive-day a{margin-left:.5rem}
.tabbar{position:fixed;left:0;right:0;bottom:0;display:flex;justify-content:space-around;background:rgba(6,10,16,.92);border-top:1px solid var(--border);padding:.35rem 0 calc(.35rem + env(safe-area-inset-bottom));z-index:5}
.tabbar a{color:var(--muted);font-size:.7rem;display:flex;flex-direction:column;align-items:center;min-width:3.4rem}
.tabbar a.active{color:var(--text);font-weight:700}.tabbar .i{font-size:1rem}
@media (max-width:760px){.fund-rows{grid-template-columns:minmax(0,1fr)}.head{grid-template-columns:1fr 1fr}.masthead{grid-column:1/-1;order:-1}.cards{grid-template-columns:minmax(0,1fr)}.grid{grid-template-columns:minmax(0,1fr)}.front .body{columns:1}}
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
    fund_html = render_fund(ed.get("fund"))
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
    if fund_html:
        # Fund watch sits after Business; with no Business section, right after Mac tasks.
        bi = next((i for i, b in enumerate(blocks) if b[0] == "business"), ti + 1)
        blocks.insert(bi + 1, ("fund", FUND_TITLE, fund_html))

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
        clean = edition_for_api(ed) if ed else None
        payload = {"ok": clean is not None, "edition": clean, "url": edition_url(k) if k else None}
        return (200 if clean else 404), "application/json", json.dumps(payload, ensure_ascii=False)
    if path == BASE_PATH + "/api/editions":
        keys = list_editions(root)
        payload = {"ok": True, "editions": [{"date": d, "edition": e, "url": edition_url((d, e))} for d, e in keys]}
        return 200, "application/json", json.dumps(payload)
    rest = path[len(BASE_PATH) + 1 :]
    return 404, HTML, render_not_found(rest[:10], "")
