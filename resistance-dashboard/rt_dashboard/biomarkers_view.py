"""Build the biomarker dashboard view: latest vs range, deltas, flags, caveats.

Pure functions over store data + FitDash sessions/weights (read-only).
Flags say "review" — never diagnostic. Caveats are annotations, not flags.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

from . import biomarkers_config as cfg
from .biomarkers_import import display_name


def _d(s: str) -> Optional[date]:
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _fmt(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:.2f}".rstrip("0").rstrip(".") if abs(v) < 100 else f"{v:.0f}"


# --- range status -----------------------------------------------------------

def range_status(value: Optional[float], comparator: str, lo: Optional[float], hi: Optional[float]) -> str:
    """'low' | 'high' | 'in' | 'unknown' against the lab's printed range."""
    if value is None or (lo is None and hi is None):
        return "unknown"
    cmp_ = (comparator or "").strip()
    if lo is not None:
        if cmp_.startswith("<") and value <= lo:
            return "low"
        if not cmp_ and value < lo:
            return "low"
    if hi is not None:
        if cmp_.startswith(">") and value >= hi:
            return "high"
        if not cmp_ and value > hi:
            return "high"
    return "in"


# --- training context from FitDash sessions (read-only) ----------------------

def _session_fields(s: Any) -> tuple:
    if isinstance(s, dict):
        d = s.get("date")
        st = s.get("session_type") or ""
        ex = s.get("exercises") or []
        names = [str((e or {}).get("name") or "") for e in ex]
    else:
        d = getattr(s, "date", "")
        st = getattr(s, "session_type", "") or ""
        ex = getattr(s, "exercises", []) or []
        names = [str(getattr(e, "name", "") or "") for e in ex]
    return str(d or "")[:10], str(st).lower(), names


def training_context(sessions: Iterable[Any], draw_date: str) -> Dict[str, Optional[int]]:
    """Days from the last logged lift / heavy legs-or-deadlift to the draw.

    Only sessions dated strictly before the draw day count (draws are AM).
    """
    dd = _d(draw_date)
    out: Dict[str, Optional[int]] = {"days_since_hard_session": None, "days_since_heavy_legs": None}
    if not dd:
        return out
    pat = re.compile(cfg.HEAVY_LEGS_PATTERN, re.I)
    for s in sessions or []:
        sd_s, st, names = _session_fields(s)
        sd = _d(sd_s)
        if not sd or sd >= dd or not names:
            continue
        days = (dd - sd).days
        if out["days_since_hard_session"] is None or days < out["days_since_hard_session"]:
            out["days_since_hard_session"] = days
        if st == "legs" or any(pat.search(n) for n in names):
            if out["days_since_heavy_legs"] is None or days < out["days_since_heavy_legs"]:
                out["days_since_heavy_legs"] = days
    return out


def _weight_near(weights: Iterable[Any], draw_date: str, max_days: int = 7) -> Optional[float]:
    dd = _d(draw_date)
    best = None
    for w in weights or []:
        wd = _d(w.get("date") if isinstance(w, dict) else getattr(w, "date", ""))
        lb = w.get("weight_lbs") if isinstance(w, dict) else getattr(w, "weight_lbs", None)
        if not wd or not dd or lb is None or wd > dd or (dd - wd).days > max_days:
            continue
        if best is None or wd > best[0]:
            best = (wd, float(lb))
    return round(best[1], 1) if best else None


def panel_context(panel: dict, *, sessions=None, weights=None, phase: str = "") -> dict:
    """Stored per-draw context + FitDash auto-fills (auto wins where present)."""
    ctx = dict(panel.get("context") or {})
    auto = training_context(sessions or [], panel.get("draw_date") or "")
    bw = _weight_near(weights or [], panel.get("draw_date") or "")
    filled = {}
    for k, v in auto.items():
        if v is not None:
            ctx[k] = v
            filled[k] = True
    if bw is not None:
        ctx["bodyweight_lbs"] = bw
        filled["bodyweight_lbs"] = True
    if phase and not ctx.get("phase"):
        ctx["phase"] = phase
        filled["phase"] = True
    ctx["_auto"] = sorted(filled)
    return ctx


def _yes(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    s = str(v if v is not None else "").strip().lower()
    if s in ("y", "yes", "true", "1"):
        return True
    if s in ("n", "no", "false", "0"):
        return False
    return None


def _to_int(v: Any) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def active_caveats(ctx: dict) -> Dict[str, List[str]]:
    """marker id → caveat texts, from one panel's context."""
    on: List[str] = []
    if str(ctx.get("creatine") or "").lower() == "yes":
        on.append("creatine")
    if _yes(ctx.get("alcohol_48h")):
        on.append("alcohol")
    fasted = _yes(ctx.get("fasted"))
    try:
        fh = float(ctx.get("fast_hours")) if ctx.get("fast_hours") not in (None, "") else None
    except (TypeError, ValueError):
        fh = None
    if fasted is False or (fh is not None and fh < cfg.MIN_FAST_HOURS):
        on.append("not_fasted")
    dh = _to_int(ctx.get("days_since_hard_session"))
    dl = _to_int(ctx.get("days_since_heavy_legs"))
    if (dh is not None and dh * 24 <= cfg.TRAINING_RECENT_HOURS) or (
        dl is not None and dl <= cfg.HEAVY_LEGS_RECENT_DAYS
    ):
        on.append("training")
    if str(ctx.get("biotin") or "").lower() == "yes":
        on.append("biotin")
    dt = str(ctx.get("draw_time") or "")
    m = re.match(r"^(\d{1,2}):(\d{2})", dt)
    if m and int(m.group(1)) >= cfg.MORNING_CUTOFF_HOUR:
        on.append("non_morning")
    if _yes(ctx.get("recent_illness")):
        on.append("illness")
    out: Dict[str, List[str]] = {}
    for key in on:
        c = cfg.CAVEATS[key]
        for mk in c["markers"]:
            out.setdefault(mk, []).append(c["text"] + (c.get("extra") or {}).get(mk, ""))
    return out


# --- flags ------------------------------------------------------------------

def move_flags(marker: str, prev: Optional[float], cur: Optional[float], unit: str = "") -> List[str]:
    if prev is None or cur is None:
        return []
    rule = dict(cfg.MOVE_RULES.get(marker) or cfg.DEFAULT_MOVE)
    out: List[str] = []
    diff = cur - prev
    pct = (diff / abs(prev) * 100.0) if prev else None
    if "pct" in rule and pct is not None and abs(pct) >= rule["pct"] - 1e-9:
        out.append(f"review: {'up' if diff > 0 else 'down'} {abs(pct):.0f}% vs last (≥{rule['pct']:.0f}%)")
    if "abs" in rule and abs(diff) >= rule["abs"] - 1e-9 and not out:
        out.append(f"review: {'up' if diff > 0 else 'down'} {_fmt(abs(diff))} {unit} vs last (≥{_fmt(rule['abs'])})".replace("  ", " "))
    if "ratio" in rule and prev > 0 and cur / prev >= rule["ratio"]:
        out.append(f"review: {cur / prev:.1f}× last value")
    return out


def level_flags(marker: str, cur: Optional[float]) -> List[str]:
    if cur is None:
        return []
    for r in cfg.LEVEL_RULES.get(marker) or []:
        if (r["op"] == "<" and cur < r["value"]) or (r["op"] == ">" and cur > r["value"]):
            return [f"review: {r['msg']}"]
    return []


def crossing_flags(marker: str, prev: Optional[float], cur: Optional[float],
                   prev_status: str, cur_status: str) -> List[str]:
    out: List[str] = []
    if prev is None or cur is None:
        return out
    if cfg.RANGE_CROSSING_FLAGS and "unknown" not in (prev_status, cur_status) and prev_status != cur_status:
        out.append(f"review: crossed lab range ({prev_status} → {cur_status})")
    for line in cfg.CROSSING_LINES.get(marker) or []:
        if (prev < line) != (cur < line):
            out.append(f"review: crossed {_fmt(line)}")
    return out


def cut_trend_flags(marker: str, prev: Optional[float], cur: Optional[float]) -> List[str]:
    if marker not in cfg.CUT_TREND_MARKERS or prev is None or cur is None or prev <= 0:
        return []
    drop = (prev - cur) / prev * 100.0
    if drop >= cfg.CUT_TREND_MIN_DROP_PCT:
        return [f"review: falling {drop:.0f}% vs last panel (cut-trend watch, even in range)"]
    return []


# --- view -------------------------------------------------------------------

def _group_of(marker: str) -> str:
    for gid, _label, ids in cfg.PINNED_GROUPS:
        if marker in ids:
            return gid
    return cfg.OTHER_GROUP[0]


def _order_key(marker: str) -> tuple:
    for gi, (_gid, _label, ids) in enumerate(cfg.PINNED_GROUPS):
        if marker in ids:
            return (gi, ids.index(marker), "")
    return (len(cfg.PINNED_GROUPS), 0, marker)


def build_view(data: dict, *, sessions=None, weights=None, phase: str = "", today: str = "") -> dict:
    panels = sorted(data.get("panels") or [], key=lambda p: (p.get("draw_date") or "", p.get("panel_id") or ""))
    rows = data.get("rows") or []
    ctx_by_panel = {p["panel_id"]: panel_context(p, sessions=sessions, weights=weights, phase=phase)
                    for p in panels}
    by_marker: Dict[str, List[dict]] = {}
    for r in rows:
        by_marker.setdefault(r["marker"], []).append(r)
    markers_out = []
    for marker, pts in by_marker.items():
        pts = sorted(pts, key=lambda r: (r.get("draw_date") or "", r.get("panel_id") or ""))
        latest = pts[-1]
        prev = next((p for p in reversed(pts[:-1]) if p.get("draw_date") != latest.get("draw_date")), None)
        cur_v, prev_v = latest.get("value"), (prev or {}).get("value")
        unit = latest.get("unit") or ""
        cur_status = range_status(cur_v, latest.get("comparator") or "", latest.get("ref_low"), latest.get("ref_high"))
        flags: List[str] = []
        if cur_status in ("low", "high"):
            rng = f"{_fmt(latest.get('ref_low'))}–{_fmt(latest.get('ref_high'))}"
            flags.append(f"review: {'below' if cur_status == 'low' else 'above'} lab range ({rng} {unit})".strip())
        flags += level_flags(marker, cur_v)
        delta = None
        warn = None
        if prev is not None:
            if (prev.get("unit") or "") != unit:
                warn = f"unit changed ({prev.get('unit')} → {unit}); no delta"
            elif cur_v is not None and prev_v is not None:
                delta = {"abs": round(cur_v - prev_v, 4),
                         "pct": round((cur_v - prev_v) / abs(prev_v) * 100.0, 1) if prev_v else None,
                         "since": prev.get("draw_date")}
                prev_status = range_status(prev_v, prev.get("comparator") or "", prev.get("ref_low"), prev.get("ref_high"))
                if not (latest.get("comparator") or prev.get("comparator")):
                    flags += move_flags(marker, prev_v, cur_v, unit)
                flags += crossing_flags(marker, prev_v, cur_v, prev_status, cur_status)
                flags += cut_trend_flags(marker, prev_v, cur_v)
        ctx = ctx_by_panel.get(latest.get("panel_id"), {})
        caveats = active_caveats(ctx).get(marker, [])
        item = {
            "marker": marker,
            "name": display_name(marker, latest.get("marker_name") or ""),
            "group": _group_of(marker),
            "unit": unit,
            "latest": {k: latest.get(k) for k in ("value", "value_text", "comparator", "ref_low", "ref_high",
                                                   "ref_text", "draw_date", "panel_id")},
            "status": cur_status,
            "previous": ({k: prev.get(k) for k in ("value", "value_text", "draw_date")} if prev else None),
            "delta": delta,
            "series": [{"date": p.get("draw_date"), "value": p.get("value")} for p in pts],
            "flags": list(dict.fromkeys(flags)),
            "caveats": caveats,
            "warning": warn,
        }
        if marker in cfg.SHOW_DAYS_SINCE_LIFT_ON:
            item["days_since_hard_session"] = ctx.get("days_since_hard_session")
        markers_out.append(item)
    markers_out.sort(key=lambda m: _order_key(m["marker"]))
    groups = []
    for gid, label, _ids in [*cfg.PINNED_GROUPS, (*cfg.OTHER_GROUP, [])]:
        ms = [m for m in markers_out if m["group"] == gid]
        if ms:
            groups.append({"id": gid, "label": label, "markers": ms})
    latest_draw = panels[-1]["draw_date"] if panels else ""
    flagged = [m for m in markers_out if m["flags"]]
    td, ld = _d(today), _d(latest_draw)
    recent = bool(td and ld and 0 <= (td - ld).days <= cfg.RECENT_RESULT_DAYS)
    reason = []
    if recent:
        reason.append("new result")
    if flagged:
        reason.append(f"{len(flagged)} to review")
    return {
        "has_data": bool(markers_out),
        "footnote": cfg.UI_FOOTNOTE,
        "latest_draw": latest_draw,
        "panel_count": len(panels),
        "panels": [dict(p, context=ctx_by_panel.get(p["panel_id"], {})) for p in panels],
        "groups": groups,
        "flagged_count": len(flagged),
        "today_card": {
            "show": recent or bool(flagged),
            "reason": " · ".join(reason),
            "latest_draw": latest_draw,
            "flagged": [{"marker": m["marker"], "name": m["name"], "flag": m["flags"][0]} for m in flagged][:8],
        },
        "context_fields": cfg.CONTEXT_FIELDS,
        "storage": data.get("storage") or "",
    }


def load_view(*, user_id: str = "", sessions=None, weights=None, phase: str = "", today: str = "") -> dict:
    """Store read + view. Never raises into the dashboard build."""
    from .biomarkers_store import load_all

    try:
        data = load_all(user_id)
    except Exception as e:  # noqa: BLE001
        v = build_view({"panels": [], "rows": []}, today=today)
        v["error"] = f"biomarkers unavailable: {type(e).__name__}"
        return v
    return build_view(data, sessions=sessions, weights=weights, phase=phase, today=today)
