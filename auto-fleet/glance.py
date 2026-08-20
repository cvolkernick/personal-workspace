"""Glance-first presentation helpers for the Auto Fleet dashboard.

Formats existing /api/fleet fields. Does not invent bookings, payoffs, or
units. DIMO distances are kilometres; the operator is US, so we render miles.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

KM_PER_MILE = 1.609344
STALE_AFTER_S = 24 * 3600
DEAD_AFTER_S = 7 * 24 * 3600
DEFAULT_POLL_S = 900

PHOTOS = {
    "m3-2020": "/static/fleet/tesla-model-3-2020.jpg",
    "r1s-2023": "/static/fleet/rivian-r1s-2023.jpg",
    "m3-2022": "/static/fleet/tesla-model-3-2022.jpg",
    "corolla-2022": "/static/fleet/toyota-corolla-2022.jpg",
    "corolla-2024": "/static/fleet/toyota-corolla-2024.jpg",
}


def _esc(value: Any) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _normalize_iso(raw: str) -> str:
    """Pad/trim fractional seconds so 3.9 fromisoformat accepts DIMO stamps."""
    text = raw.strip()
    tz = ""
    if text.endswith("Z"):
        text, tz = text[:-1], "+00:00"
    elif len(text) >= 6 and text[-6] in "+-" and text[-3] == ":":
        text, tz = text[:-6], text[-6:]
    elif len(text) >= 5 and text[-5] in "+-":
        text, tz = text[:-5], text[-5:]
    if "." in text:
        head, frac = text.split(".", 1)
        frac = "".join(ch for ch in frac if ch.isdigit())
        frac = (frac + "000000")[:6]
        text = f"{head}.{frac}"
    return text + tz


def parse_ts(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(_normalize_iso(str(value)))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def miles_from_km(km: Any, ndigits: int = 0) -> Optional[float]:
    if km is None or km == "":
        return None
    try:
        miles = float(km) / KM_PER_MILE
    except (TypeError, ValueError):
        return None
    if ndigits <= 0:
        return float(int(round(miles)))
    return round(miles, ndigits)


def odo_miles(km: Any) -> Optional[int]:
    mi = miles_from_km(km, 0)
    return None if mi is None else int(mi)


def range_miles(km: Any) -> Optional[float]:
    return miles_from_km(km, 1)


def soc_pct(soc: Any) -> Optional[int]:
    if soc is None or soc == "":
        return None
    try:
        return int(round(float(soc)))
    except (TypeError, ValueError):
        return None


def relative_age(ts: Any, now: Any) -> Optional[str]:
    dt = parse_ts(ts)
    now_dt = parse_ts(now) or datetime.now(timezone.utc)
    if dt is None:
        return None
    seconds = max(0.0, (now_dt - dt).total_seconds())
    if seconds < 45:
        return "just now"
    if seconds < 3600:
        mins = max(1, int(seconds // 60))
        return f"{mins}m ago"
    if seconds < 86400:
        hours = max(1, int(seconds // 3600))
        return f"{hours}h ago"
    days = max(1, int(seconds // 86400))
    return f"{days}d ago"


def freshness(ts: Any, now: Any) -> str:
    dt = parse_ts(ts)
    now_dt = parse_ts(now) or datetime.now(timezone.utc)
    if dt is None:
        return "unknown"
    seconds = (now_dt - dt).total_seconds()
    if seconds > DEAD_AFTER_S:
        return "dead"
    if seconds > STALE_AFTER_S:
        return "stale"
    return "live"


def money(n: Any) -> Optional[str]:
    if n is None or n == "":
        return None
    try:
        return f"${float(n):,.2f}"
    except (TypeError, ValueError):
        return None


def _portal(finance: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if not finance:
        return {}
    portal = finance.get("portal") or finance.get("portal_override")
    return portal if isinstance(portal, dict) else {}


def due_from_finance(finance: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    portal = _portal(finance)
    ptp = portal.get("ptp") or portal.get("promise_to_pay")
    if not isinstance(ptp, dict):
        ptp = None
    amount_due = portal.get("amount_due")
    past_due = portal.get("past_due")
    has = bool(ptp) or amount_due not in (None, "") or past_due not in (None, "")
    return {
        "due": has,
        "ptp": ptp,
        "amount_due": amount_due,
        "past_due": past_due,
    }


def turo_line(
    turo: Optional[Mapping[str, Any]],
    poll_interval_s: int | None = DEFAULT_POLL_S,
) -> str:
    bookings = list((turo or {}).get("bookings") or [])
    mins = int((poll_interval_s or DEFAULT_POLL_S) / 60)
    if not bookings:
        return f"0 trips · watching {mins}m"
    bookings = sorted(bookings, key=lambda b: str((b or {}).get("start") or ""))
    first = bookings[0] if isinstance(bookings[0], dict) else {}
    status = first.get("status") or "booked"
    guest = first.get("guest") or "guest"
    start = first.get("start") or "?"
    end = first.get("end") or "?"
    return f"{status} · {guest} · {start} → {end}"


def photo_for(unit: Mapping[str, Any]) -> Optional[str]:
    uid = str(unit.get("id") or "")
    if uid in PHOTOS:
        return PHOTOS[uid]
    ident = unit.get("identity") if isinstance(unit.get("identity"), dict) else {}
    make = str(ident.get("make") or "").lower()
    model = str(ident.get("model") or "").lower()
    year = ident.get("year")
    if "tesla" in make and "3" in model:
        return PHOTOS["m3-2022"]
    if "rivian" in make or "r1s" in model:
        return PHOTOS["r1s-2023"]
    if "corolla" in model and year == 2024:
        return PHOTOS["corolla-2024"]
    if "corolla" in model:
        return PHOTOS["corolla-2022"]
    return None


def maps_url(location: Any) -> Optional[str]:
    if not isinstance(location, dict):
        return None
    lat = location.get("lat", location.get("latitude"))
    lon = location.get("lon", location.get("longitude"))
    if lat is None or lon is None:
        return None
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        return None
    return f"https://maps.google.com/?q={lat_f},{lon_f}"


def glance_for_unit(
    unit: Mapping[str, Any],
    *,
    now: Any,
    poll_interval_s: int | None = DEFAULT_POLL_S,
) -> dict[str, Any]:
    ident = unit.get("identity") if isinstance(unit.get("identity"), dict) else {}
    dimo = unit.get("dimo") if isinstance(unit.get("dimo"), dict) else {}
    turo = unit.get("turo") if isinstance(unit.get("turo"), dict) else {}
    finance = unit.get("finance") if isinstance(unit.get("finance"), dict) else {}
    year = ident.get("year")
    model = ident.get("model") or ""
    year_model = " ".join(str(p) for p in (year, model) if p not in (None, ""))
    soc = soc_pct(dimo.get("soc"))
    odo = odo_miles(dimo.get("odometer"))
    rng = range_miles(dimo.get("range"))
    last_seen = dimo.get("last_seen")
    fresh = freshness(last_seen, now)
    due = due_from_finance(finance)
    bookings = list(turo.get("bookings") or [])
    hero = f"{soc}%" if soc is not None else (f"{odo:,} mi" if odo is not None else "—")
    return {
        "id": unit.get("id"),
        "year_model": year_model or str(unit.get("id") or "unit"),
        "title": " ".join(
            str(p)
            for p in (ident.get("year"), ident.get("make"), ident.get("model"))
            if p not in (None, "")
        )
        or str(unit.get("id") or "unit"),
        "role": ident.get("role") or "unknown",
        "vin_short": ("…" + str(ident.get("vin"))[-6:]) if ident.get("vin") else None,
        "last_seen": last_seen,
        "last_seen_relative": relative_age(last_seen, now),
        "freshness": fresh,
        "soc": soc,
        "range_mi": rng,
        "odo_mi": odo,
        "hero": hero,
        "available": len(bookings) == 0,
        "turo_line": turo_line(turo, poll_interval_s),
        "due": due["due"],
        "ptp": due["ptp"],
        "amount_due": due["amount_due"],
        "past_due": due["past_due"],
        "photo": photo_for(unit),
        "maps_url": maps_url(dimo.get("location")),
        "show_soc": soc is not None,
    }


def _chip(text: str, kind: str = "") -> str:
    cls = f"chip {kind}".strip()
    return f'<span class="{cls}">{_esc(text)}</span>'


def render_unit_card_html(
    unit: Mapping[str, Any],
    *,
    now: Any,
    poll_interval_s: int | None = DEFAULT_POLL_S,
    glance: Optional[Mapping[str, Any]] = None,
) -> str:
    """Card HTML used by tests. Must not paste inbox_status into the card."""
    g = dict(glance or glance_for_unit(unit, now=now, poll_interval_s=poll_interval_s))
    finance = unit.get("finance") if isinstance(unit.get("finance"), dict) else {}
    dimo = unit.get("dimo") if isinstance(unit.get("dimo"), dict) else {}
    ident = unit.get("identity") if isinstance(unit.get("identity"), dict) else {}
    fresh = g.get("freshness") or "unknown"
    chips = [_chip(str(g.get("role") or "unknown"))]
    if g.get("vin_short"):
        chips.append(_chip(str(g["vin_short"])))
    if ident.get("lender"):
        chips.append(_chip(str(ident["lender"])))
    if fresh in ("stale", "dead"):
        chips.append(_chip(str(fresh), "err" if fresh == "dead" else "warn"))
    if g.get("due"):
        chips.append(_chip("due", "err"))

    dimo_st = dimo.get("status") or "unconfigured"
    dimo_body = ""
    if dimo_st == "unconfigured":
        dimo_body = '<div class="empty">DIMO unconfigured</div>'
    elif dimo_st == "error":
        dimo_body = f'<div class="err">{_esc(dimo.get("error") or "DIMO error")}</div>'
    else:
        rows = []
        if g.get("show_soc"):
            soc = int(g["soc"])
            bar_kind = "err" if soc < 25 or fresh == "dead" else ("warn" if soc < 50 else "ok")
            rows.append(
                f'<div class="row">SoC {soc}%</div>'
                f'<div class="soc {bar_kind}" data-soc="{soc}">'
                f'<span style="width:{soc}%"></span></div>'
            )
            if g.get("range_mi") is not None:
                rows.append(f'<div class="row">Range {g["range_mi"]} mi</div>')
        if g.get("odo_mi") is not None:
            rows.append(f'<div class="row">Odo {g["odo_mi"]:,} mi</div>')
        if g.get("last_seen_relative"):
            rows.append(
                f'<div class="row muted">Last seen {_esc(g["last_seen_relative"])}</div>'
            )
        if g.get("maps_url"):
            rows.append(f'<div class="row"><a class="maps" href="{_esc(g["maps_url"])}">Map</a></div>')
        dimo_body = "".join(rows) or '<div class="empty">No vehicle signals</div>'

    turo_html = f'<div class="row">{_esc(g.get("turo_line") or "0 trips")}</div>'

    portal = _portal(finance)
    due = due_from_finance(finance)
    cost_bits: list[str] = []
    if due["ptp"]:
        ptp = due["ptp"]
        cost_bits.append(
            f'<div class="row due-lead">PTP {money(ptp.get("amount")) or "—"} '
            f'due {_esc(ptp.get("due") or "—")}</div>'
        )
    if due["amount_due"] not in (None, ""):
        cost_bits.append(f'<div class="row">Due {money(due["amount_due"])}</div>')
    if due["past_due"] not in (None, ""):
        cost_bits.append(f'<div class="row">Past due {money(due["past_due"])}</div>')
    extra = []
    if portal.get("contractual_monthly") is not None:
        extra.append(f'{money(portal["contractual_monthly"])}/mo')
    if portal.get("apr_pct") is not None:
        extra.append(f'{portal["apr_pct"]}% APR')
    if portal.get("principal_balance") is not None:
        extra.append(f'principal {money(portal["principal_balance"])}')
    if extra:
        stale = "stale" if portal.get("stale", True) else "sheet"
        cost_bits.append(
            f'<div class="row muted">{_esc(" · ".join(extra))} {_chip(stale, "warn")}</div>'
        )
    lines = finance.get("sheet_lines") or []
    for line in lines:
        if not isinstance(line, dict):
            continue
        cost_bits.append(
            f'<div class="row">{_esc(line.get("item"))} · {money(line.get("monthly")) or "—"}</div>'
        )
    if not lines and not portal:
        cost_bits.append(
            f'<div class="empty">{_esc(finance.get("note") or "No Fleet-tab lines for this unit.")}</div>'
        )

    photo = g.get("photo")
    img = (
        f'<img src="{_esc(photo)}" alt="{_esc(g.get("title"))}" />' if photo else ""
    )
    return (
        f'<article class="card {fresh}" data-unit="{_esc(unit.get("id"))}" '
        f'data-freshness="{_esc(fresh)}">'
        f'<div class="hero">{img}<div>'
        f'<h2>{_esc(g.get("title"))}</h2>'
        f'<div class="chips">{"".join(chips)}</div>'
        f"</div></div>"
        f'<div class="strip"><h3>DIMO {_chip(str(dimo_st), "ok" if dimo_st == "ok" else "warn")}</h3>'
        f"{dimo_body}</div>"
        f'<div class="strip"><h3>Turo</h3>{turo_html}</div>'
        f'<div class="strip"><h3>Notes &amp; costs</h3>{"".join(cost_bits)}</div>'
        f"</article>"
    )


def render_cards_html(
    units: Sequence[Mapping[str, Any]],
    *,
    now: Any,
    inbox_status: str | None,
    poll_interval_s: int | None = DEFAULT_POLL_S,
) -> str:
    """Glance + cards + one Turo inbox footer. inbox_status appears once."""
    cells = []
    cards = []
    for unit in units:
        g = glance_for_unit(unit, now=now, poll_interval_s=poll_interval_s)
        badges = [_esc(g["year_model"]), _esc(g["role"])]
        if g.get("last_seen_relative"):
            badges.append(_esc(g["last_seen_relative"]))
        if g["freshness"] in ("stale", "dead"):
            badges.append(_esc(g["freshness"]))
        badges.append(_esc(g["hero"]))
        if g.get("available"):
            badges.append("available")
        if g.get("due"):
            badges.append("due")
        cells.append(
            f'<a class="glance-cell {g["freshness"]}" href="#unit-{_esc(unit.get("id"))}">'
            f"{' · '.join(badges)}</a>"
        )
        cards.append(
            render_unit_card_html(
                unit, now=now, poll_interval_s=poll_interval_s, glance=g
            )
        )
    footer = ""
    if inbox_status:
        footer = (
            '<details class="turo-inbox"><summary>Turo inbox</summary>'
            f"<p>{_esc(inbox_status)}</p></details>"
        )
    return (
        f'<div class="glance">{"".join(cells)}</div>'
        f'<div class="grid">{"".join(cards)}</div>'
        f"{footer}"
    )
