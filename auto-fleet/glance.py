"""Glance-first presentation helpers for the Auto Fleet dashboard.

Formats existing /api/fleet fields. Does not invent bookings, payoffs, or
units. DIMO distances are kilometres; the operator is US, so we render miles.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

try:
    from . import car_cards
except ImportError:  # script / unittest path
    import car_cards  # type: ignore

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
    locked = (finance or {}).get("locked") if isinstance(finance, Mapping) else {}
    if isinstance(locked, dict) and locked.get("show_balances") is False:
        return {"due": False, "ptp": None, "amount_due": None, "past_due": None}
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
    now: Any = None,
) -> str:
    mins = int((poll_interval_s or DEFAULT_POLL_S) / 60)
    raw = turo or {}
    schedule = raw.get("schedule")
    if schedule is None:
        schedule = car_cards.schedule_for_bookings(raw.get("bookings") or [], now)
    live = car_cards.live_trips(schedule)
    if live:
        first = live[0] if isinstance(live[0], dict) else {}
        status = first.get("status") or "booked"
        guest = first.get("guest") or "guest"
        start = first.get("start") or "?"
        end = first.get("end") or "?"
        return f"{status} · {guest} · {start} → {end}"
    canceled = [s for s in schedule if (s or {}).get("phase") == "canceled"]
    if canceled:
        first = canceled[0]
        guest = first.get("guest") or "guest"
        start = first.get("start") or "?"
        end = first.get("end") or "?"
        return f"canceled · {guest} · {start} → {end}"
    return f"0 trips · watching {mins}m"


_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _ymd(value: Any):
    if value is None or value == "":
        return None
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def human_when(start: Any, end: Any) -> str:
    """Human date span for Schedule rows, e.g. Aug 28–30. No invented times."""
    a = _ymd(start)
    b = _ymd(end)
    if a is None and b is None:
        return ""
    if a is not None and b is None:
        return f"{_MONTHS[a.month - 1]} {a.day}"
    if a is None and b is not None:
        return f"{_MONTHS[b.month - 1]} {b.day}"
    if a == b:
        return f"{_MONTHS[a.month - 1]} {a.day}"
    if a.month == b.month and a.year == b.year:
        return f"{_MONTHS[a.month - 1]} {a.day}–{b.day}"
    return f"{_MONTHS[a.month - 1]} {a.day}–{_MONTHS[b.month - 1]} {b.day}"


def trip_phase(booking: Mapping[str, Any]) -> str:
    status = str(booking.get("status") or "").lower()
    if status in {"canceled", "cancelled"} or booking.get("phase") == "canceled":
        return "canceled"
    phase = booking.get("phase")
    if phase in {"active", "upcoming"}:
        return str(phase)
    return "upcoming"


def queue_bookings(
    trips: Sequence[Mapping[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    live: list[dict[str, Any]] = []
    canceled: list[dict[str, Any]] = []
    for raw in trips or []:
        if not isinstance(raw, dict):
            continue
        if trip_phase(raw) == "canceled":
            canceled.append(dict(raw))
        else:
            live.append(dict(raw))
    live.sort(
        key=lambda b: (0 if trip_phase(b) == "active" else 1, str(b.get("start") or ""))
    )
    canceled.sort(key=lambda b: str(b.get("start") or ""))
    return live, canceled


def booking_row_html(booking: Mapping[str, Any], *, next_trip: bool = False) -> str:
    """Structured Schedule row — not a joined prose line."""
    phase = trip_phase(booking)
    when = human_when(booking.get("start"), booking.get("end"))
    chip_kind = "ok" if phase == "active" else ("mute" if phase == "canceled" else "")
    next_badge = _chip("NEXT", "next") if next_trip else ""
    who = (
        f'<div class="booking-who">{_esc(booking.get("guest"))}</div>'
        if booking.get("guest")
        else ""
    )
    res = ""
    if booking.get("trip_id"):
        tid = _esc(booking["trip_id"])
        res = (
            f'<button type="button" class="booking-res" data-copy="{tid}" '
            f'title="Copy reservation">#{tid}</button>'
        )
    pickup = (
        f'<span class="booking-pickup">{_esc(booking.get("pickup"))}</span>'
        if booking.get("pickup")
        else ""
    )
    when_html = (
        f'<div class="booking-when">{_esc(when)} <span class="tz">ET</span></div>'
        if when
        else ""
    )
    cls = f"booking {phase}" + (" next" if next_trip else "")
    return (
        f'<article class="{cls}" data-phase="{_esc(phase)}">'
        f'<span class="booking-dot" aria-hidden="true"></span>'
        f'<div class="booking-main">'
        f'<div class="booking-top">{when_html}'
        f'<div class="booking-flags">{next_badge}{_chip(phase, chip_kind)}</div></div>'
        f'{who}<div class="booking-meta">{res}{pickup}</div>'
        f"</div></article>"
    )


def schedule_queue_html(schedule: Sequence[Mapping[str, Any]] | None) -> str:
    live, canceled = queue_bookings(schedule)
    rows = [booking_row_html(b, next_trip=(i == 0)) for i, b in enumerate(live)]
    rows.extend(booking_row_html(b) for b in canceled)
    if not rows:
        return '<div class="empty">No upcoming trips</div>'
    return f'<div class="queue">{"".join(rows)}</div>'


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
    schedule = turo.get("schedule")
    if schedule is None:
        schedule = car_cards.schedule_for_bookings(turo.get("bookings") or [], now)
    live = car_cards.live_trips(schedule)
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
        "available": len(live) == 0,
        "turo_line": turo_line(turo, poll_interval_s, now),
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
    if ident.get("host_label"):
        chips.append(_chip(str(ident["host_label"])))
    if ident.get("plate"):
        chips.append(_chip(str(ident["plate"])))
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

    locked = finance.get("locked") if isinstance(finance.get("locked"), dict) else {}
    locked_bits = []
    if locked.get("lender"):
        locked_bits.append(str(locked["lender"]))
    if locked.get("apr_pct") is not None:
        locked_bits.append(f'{locked["apr_pct"]}% APR')
    if locked.get("monthly") is not None:
        locked_bits.append(f'{money(locked["monthly"])}/mo')
    locked_html = (
        f'<div class="row muted">{_esc(" · ".join(locked_bits))}</div>'
        if locked_bits
        else ""
    )

    turo = unit.get("turo") if isinstance(unit.get("turo"), dict) else {}
    schedule = turo.get("schedule")
    if schedule is None:
        schedule = car_cards.schedule_for_bookings(turo.get("bookings") or [], now)
    schedule = [b for b in schedule if isinstance(b, dict)]
    schedule_html = schedule_queue_html(schedule)

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
    show_balances = locked.get("show_balances", True)
    if show_balances and portal.get("contractual_monthly") is not None:
        extra.append(f'{money(portal["contractual_monthly"])}/mo')
    if locked.get("apr_pct") is not None:
        extra.append(f'{locked["apr_pct"]}% APR')
    elif portal.get("apr_pct") is not None:
        extra.append(f'{portal["apr_pct"]}% APR')
    if show_balances and portal.get("principal_balance") is not None:
        extra.append(f'principal {money(portal["principal_balance"])}')
    if extra:
        stale = "stale" if portal.get("stale", True) else "sheet"
        cost_bits.append(
            f'<div class="row muted">{_esc(" · ".join(extra))} {_chip(stale, "warn")}</div>'
        )
    invoice_items = turo.get("invoice_ready") or finance.get("invoice_ready") or []
    for item in invoice_items:
        if not isinstance(item, dict):
            continue
        cost_bits.append(
            f'<div class="row invoice-ready">{_esc(item.get("title") or "")}</div>'
        )
    lines = finance.get("sheet_lines") or []
    for line in lines:
        if not isinstance(line, dict):
            continue
        cost_bits.append(
            f'<div class="row">{_esc(line.get("item"))} · {money(line.get("monthly")) or "—"}</div>'
        )
    if not cost_bits and not locked_bits:
        cost_bits.append(
            f'<div class="empty">{_esc(finance.get("note") or "No Fleet-tab lines for this unit.")}</div>'
        )

    trip_bits = []
    for b in schedule:
        flags = []
        for drv in b.get("extra_drivers") or []:
            if isinstance(drv, dict) and drv.get("name"):
                ver = " · Turo-verified" if drv.get("turo_verified") else ""
                flags.append(f'extra driver {drv["name"]}{ver}')
        if b.get("drop_off"):
            flags.append(f'drop-off {b["drop_off"]}')
        if b.get("phone"):
            flags.append(f'phone {b["phone"]}')
        for ask in b.get("guest_asks") or []:
            flags.append(f'phone tap {ask}')
        if not flags and not b.get("guest"):
            continue
        summary = " · ".join(
            str(p)
            for p in (b.get("status"), b.get("guest"), b.get("trip_id") and f"#{b['trip_id']}")
            if p
        )
        extra_html = "".join(f'<div class="row">{_esc(f)}</div>' for f in flags)
        trip_bits.append(
            f'<details class="trip"><summary>{_esc(summary)}</summary>{extra_html}</details>'
        )

    photo = g.get("photo")
    img = (
        f'<img src="{_esc(photo)}" alt="{_esc(g.get("title"))}" />' if photo else ""
    )
    trip_html = ""
    if trip_bits:
        trip_html = (
            '<div class="strip"><h3>Trip detail</h3>'
            + "".join(trip_bits)
            + "</div>"
        )
    return (
        f'<article class="card {fresh}" data-unit="{_esc(unit.get("id"))}" '
        f'data-freshness="{_esc(fresh)}">'
        f'<div class="hero">{img}<div>'
        f'<h2>{_esc(g.get("title"))}</h2>'
        f'<div class="chips">{"".join(chips)}</div>'
        f"</div></div>"
        f'<div class="strip"><h3>Vehicle</h3>{locked_html}'
        f'<h3>DIMO {_chip(str(dimo_st), "ok" if dimo_st == "ok" else "warn")}</h3>'
        f"{dimo_body}</div>"
        f'<div class="strip"><h3>Schedule</h3>{schedule_html}</div>'
        f'<div class="strip"><h3>Money</h3>{"".join(cost_bits)}</div>'
        f"{trip_html}"
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
