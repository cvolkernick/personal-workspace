"""Single source of truth for blood-lab panels.

Writes go to the labs blob (Turso ``labs_panels`` or the Pi file cache).
The biomarker tables are legacy: still read so a panel that was saved only
there shows up, never written by new imports. ``scripts/migrate_lab_panels.py``
copies them into the blob, fixes draw dates, and splits collapsed rows.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from .lab_markers import canonical_id, public_marker_id
from .labs_parse import recover_collapsed_marker


def panel_draw_date(panel: dict) -> str:
    """Actual draw day. Collected wins over a reported ``date``."""
    if not isinstance(panel, dict):
        return ""
    for key in ("collected", "draw_date", "date"):
        raw = str(panel.get(key) or "")[:10]
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            return raw
    return ""


def apply_draw_date(panel: dict) -> Tuple[dict, bool]:
    """Point ``date`` at the draw day. Does not touch marker values.

    When ``collected`` disagrees with ``date``, the old ``date`` is kept on
    ``reported`` so the paperwork day is not thrown away.
    """
    out = deepcopy(panel) if isinstance(panel, dict) else {}
    collected = str(out.get("collected") or "")[:10]
    reported = str(out.get("reported") or "")[:10]
    date = str(out.get("date") or "")[:10]
    draw = panel_draw_date(out)
    corrected = bool(date and draw and date != draw)
    if corrected and not reported:
        out["reported"] = date
    if draw:
        out["draw_date"] = draw
        out["date"] = draw
        if not collected:
            out["collected"] = draw
    return out, corrected


def _marker_items(panel: dict) -> List[Tuple[str, dict]]:
    raw = panel.get("markers") if isinstance(panel, dict) else None
    items: List[Tuple[str, dict]] = []
    if isinstance(raw, dict):
        order = list(panel.get("marker_order") or raw.keys())
        seen = set()
        for key in order:
            if key in raw and isinstance(raw[key], dict):
                items.append((str(key), raw[key]))
                seen.add(key)
        for key, val in raw.items():
            if key not in seen and isinstance(val, dict):
                items.append((str(key), val))
    elif isinstance(raw, list):
        for i, val in enumerate(raw):
            if isinstance(val, dict):
                items.append((str(val.get("key") or i), val))
    return items


def normalize_panel(panel: dict) -> Tuple[dict, dict]:
    """Draw-date fix plus collapsed-row split. Unrecoverable rows stay as stored."""
    out, corrected = apply_draw_date(panel)
    stats = {"draw_date_corrected": int(corrected), "split": [], "unsplit": []}
    new: Dict[str, dict] = {}
    order: List[str] = []
    for key, marker in _marker_items(out):
        rec = recover_collapsed_marker(marker)
        if rec.get("status") == "split":
            for part in rec.get("markers") or []:
                k = str(part.get("key") or key)
                if k in new:
                    k = f"{k}_{len(new)}"
                part = dict(part)
                part["key"] = k
                new[k] = part
                order.append(k)
            stats["split"].append(public_marker_id(marker))
            continue
        new[key] = marker
        order.append(key)
        if rec.get("status") == "unrecoverable":
            stats["unsplit"].append(public_marker_id(marker))
    out["markers"] = new
    out["marker_order"] = order
    return out, stats


def identity_keys(panel: dict) -> set:
    """Keys that mean 'same draw'. Draw day + lab always participates."""
    keys = set()
    if not isinstance(panel, dict):
        return keys
    order = str(panel.get("order_id") or "").strip()
    if order:
        keys.add(("order", order))
    pid = str(panel.get("panel_id") or "").strip()
    if pid:
        keys.add(("panel", pid))
    draw = panel_draw_date(panel)
    lab = str(panel.get("lab") or "").strip().lower()
    if draw:
        keys.add(("draw", draw, lab))
    return keys


def same_panel(a: dict, b: dict) -> bool:
    """Same draw. Distinct order ids or panel ids stay distinct."""
    ao = str((a or {}).get("order_id") or "").strip()
    bo = str((b or {}).get("order_id") or "").strip()
    if ao and bo and ao != bo:
        return False
    ap = str((a or {}).get("panel_id") or "").strip()
    bp = str((b or {}).get("panel_id") or "").strip()
    if ap and bp and ap != bp and not (ao and ao == bo):
        return False
    return bool(identity_keys(a) & identity_keys(b))


def _cid(marker: dict, key: str = "") -> str:
    return canonical_id(str(marker.get("id") or marker.get("marker") or marker.get("name") or key))


def union_markers(base: dict, extra: dict) -> dict:
    """Add markers that ``base`` lacks. Never replace a stored value."""
    out = deepcopy(base)
    have = {}
    for key, marker in _marker_items(out):
        have[_cid(marker, key)] = key
    extra_items = _marker_items(extra)
    markers = dict(out.get("markers") or {}) if isinstance(out.get("markers"), dict) else {}
    if not isinstance(out.get("markers"), dict):
        markers = {k: m for k, m in _marker_items(out)}
    order = list(out.get("marker_order") or markers.keys())
    for key, marker in extra_items:
        cid = _cid(marker, key)
        if cid in have:
            continue
        k = key if key not in markers else f"{key}_{len(markers)}"
        markers[k] = deepcopy(marker)
        order.append(k)
        have[cid] = k
    out["markers"] = markers
    out["marker_order"] = order
    for field in ("panel_id", "order_id", "draw_time", "reported", "collected"):
        if not str(out.get(field) or "").strip() and str(extra.get(field) or "").strip():
            out[field] = extra[field]
    if out.get("fasting") is None and extra.get("fasting") is not None:
        out["fasting"] = extra["fasting"]
    ctx = dict(extra.get("context") or {})
    ctx.update(out.get("context") or {})
    if ctx:
        out["context"] = ctx
    if not str(out.get("draw_time") or "") and ctx.get("draw_time"):
        out["draw_time"] = ctx["draw_time"]
    if out.get("fasting") is None and "fasted" in ctx:
        out["fasting"] = bool(ctx.get("fasted"))
    return out


def union_panels(base: List[dict], extra: List[dict]) -> List[dict]:
    out = [deepcopy(p) for p in base if isinstance(p, dict)]
    for panel in extra or []:
        if not isinstance(panel, dict):
            continue
        idx = next((i for i, have in enumerate(out) if same_panel(have, panel)), None)
        if idx is None:
            out.append(deepcopy(panel))
        else:
            out[idx] = union_markers(out[idx], panel)
    return out


def rows_to_labs_panel(panel: dict, rows: List[dict]) -> dict:
    """Biomarker preview → labs panel. Copies numbers through; no recompute."""
    markers: Dict[str, dict] = {}
    order: List[str] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        cid = str(r.get("marker") or r.get("id") or "")
        key = str(r.get("key") or cid or f"m{len(order)}")
        if key in markers:
            continue
        vt = r.get("value_text")
        if vt in (None, ""):
            vt = "" if r.get("value") is None else str(r.get("value"))
        markers[key] = {
            "key": key,
            "id": cid,
            "name": r.get("marker_name") or r.get("name") or cid,
            "value": r.get("value"),
            "value_text": vt,
            "comparator": r.get("comparator") or "",
            "unit": r.get("unit") or "",
            "clinical_low": r.get("clinical_low") if "clinical_low" in r else r.get("ref_low"),
            "clinical_high": r.get("clinical_high") if "clinical_high" in r else r.get("ref_high"),
            "performance_low": r.get("performance_low"),
            "performance_high": r.get("performance_high"),
            "ref_text": r.get("ref_text"),
        }
        order.append(key)
    ctx = dict(panel.get("context") or {})
    draw = str(panel.get("draw_date") or panel.get("collected") or panel.get("date") or "")[:10]
    fasting = panel.get("fasting")
    if fasting is None and "fasted" in ctx:
        fasting = bool(ctx.get("fasted"))
    return {
        "panel_id": str(panel.get("panel_id") or ""),
        "order_id": str(panel.get("order_id") or ""),
        "lab": panel.get("lab") or "",
        "date": draw,
        "draw_date": draw,
        "collected": str(panel.get("collected") or draw or "")[:10],
        "reported": str(panel.get("reported") or "")[:10],
        "fasting": fasting,
        "draw_time": str(panel.get("draw_time") or ctx.get("draw_time") or ""),
        "context": ctx,
        "source": panel.get("source") or "",
        "specimen": panel.get("specimen") or "",
        "markers": markers,
        "marker_order": order,
        "notes": panel.get("notes") or "",
    }


def legacy_panels(user_id: str = "") -> List[dict]:
    """Biomarker-table panels shaped like labs panels. Empty when that store is absent."""
    try:
        from .biomarkers_store import load_legacy_tables
    except Exception:
        return []
    try:
        data = load_legacy_tables(user_id)
    except Exception:
        return []
    by_id: Dict[str, dict] = {}
    order: List[str] = []
    for p in data.get("panels") or []:
        pid = str(p.get("panel_id") or "")
        if not pid or pid in by_id:
            continue
        by_id[pid] = p
        order.append(pid)
    rows_by: Dict[str, List[dict]] = {}
    for row in data.get("rows") or []:
        rows_by.setdefault(str(row.get("panel_id") or ""), []).append(row)
    out = []
    for pid in order:
        out.append(rows_to_labs_panel(by_id[pid], rows_by.get(pid) or []))
    # Rows whose panel header is missing still count as a panel.
    for pid, rows in rows_by.items():
        if pid in by_id or not rows:
            continue
        draw = str(rows[0].get("draw_date") or "")[:10]
        out.append(rows_to_labs_panel({"panel_id": pid, "draw_date": draw, "lab": ""}, rows))
    return out


def attach_legacy(store: dict, user_id: str = "") -> dict:
    """In-memory union. Does not write."""
    base = deepcopy(store) if isinstance(store, dict) else {"panels": []}
    base["panels"] = union_panels(list(base.get("panels") or []), legacy_panels(user_id))
    return base


def project_biomarker(store: dict) -> Dict[str, Any]:
    """Labs panels → the shape biomarkers_view already consumes."""
    panels_out: List[dict] = []
    rows: List[dict] = []
    for raw in (store or {}).get("panels") or []:
        if not isinstance(raw, dict):
            continue
        panel, _stats = normalize_panel(raw)
        draw = panel_draw_date(panel)
        pid = str(panel.get("panel_id") or "")
        if not pid:
            lab = str(panel.get("lab") or "lab").strip().lower().replace(" ", "-")
            pid = f"{lab}-{draw}" if draw else lab
        ctx = dict(panel.get("context") or {})
        if panel.get("draw_time") and not ctx.get("draw_time"):
            ctx["draw_time"] = panel["draw_time"]
        if panel.get("fasting") is not None and "fasted" not in ctx:
            ctx["fasted"] = bool(panel.get("fasting"))
        panels_out.append(
            {
                "panel_id": pid,
                "lab": panel.get("lab") or "",
                "draw_date": draw,
                "source": panel.get("source") or "",
                "context": ctx,
                "order_id": panel.get("order_id") or "",
                "created_at": panel.get("created_at") or "",
            }
        )
        for key, marker in _marker_items(panel):
            cid = _cid(marker, key)
            rows.append(
                {
                    "panel_id": pid,
                    "marker": cid,
                    "marker_name": marker.get("name") or cid,
                    "value": marker.get("value"),
                    "value_text": marker.get("value_text") or "",
                    "comparator": marker.get("comparator") or "",
                    "unit": marker.get("unit") or "",
                    "ref_low": marker.get("clinical_low") if "clinical_low" in marker else marker.get("ref_low"),
                    "ref_high": marker.get("clinical_high") if "clinical_high" in marker else marker.get("ref_high"),
                    "ref_text": marker.get("ref_text"),
                    "draw_date": draw,
                    "source": marker.get("source") or panel.get("source") or "",
                }
            )
    panels_out.sort(key=lambda p: (p.get("draw_date") or "", p.get("panel_id") or ""))
    rows.sort(key=lambda r: (r.get("draw_date") or "", r.get("panel_id") or "", r.get("marker") or ""))
    return {"panels": panels_out, "rows": rows, "storage": (store or {}).get("storage") or ""}


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _signature(panels: List[dict]) -> tuple:
    sigs = []
    for panel in panels:
        if not isinstance(panel, dict):
            continue
        markers = []
        for key, marker in _marker_items(panel):
            markers.append(
                (
                    _cid(marker, key),
                    str(marker.get("value_text") or ""),
                    _num(marker.get("value")),
                    _num(marker.get("clinical_low") if "clinical_low" in marker else marker.get("ref_low")),
                    _num(marker.get("clinical_high") if "clinical_high" in marker else marker.get("ref_high")),
                    str(marker.get("unit") or ""),
                    str(marker.get("comparator") or ""),
                )
            )
        markers.sort()
        sigs.append(
            (
                str(panel.get("date") or "")[:10],
                panel_draw_date(panel),
                str(panel.get("panel_id") or ""),
                str(panel.get("order_id") or ""),
                str(panel.get("lab") or "").strip().lower(),
                panel.get("fasting"),
                str(panel.get("draw_time") or ""),
                tuple(markers),
            )
        )
    sigs.sort()
    return tuple(sigs)


def _replica_panels(user_id: str) -> List[dict]:
    """File-cache panels when Turso is the live store.

    The file cache is the same logical labs store on the Pi. Panels that exist
    only there are unioned in. Marker values already in the live store win.
    """
    from .labs_store import _ephemeral_host, _load_index

    try:
        from .turso_http import turso_enabled
    except Exception:
        return []
    if not turso_enabled() or _ephemeral_host():
        return []
    disk = _load_index(user_id)
    return [p for p in (disk.get("panels") or []) if isinstance(p, dict)]


def plan_unification(user_id: str = "") -> dict:
    """Compute the unified blob in memory. Does not write."""
    from .labs_store import _read_store

    live = _read_store(user_id, ensure=False)
    raw_panels = [p for p in (live.get("panels") or []) if isinstance(p, dict)]
    legacy = legacy_panels(user_id)

    def _prepared(panels: List[dict]) -> List[dict]:
        out = []
        for panel in panels:
            norm, _stats = normalize_panel(panel)
            out.append(norm)
        return out

    # Normalize before union so a legacy collapsed row becomes the split
    # markers and does not get appended again on the next run.
    combined = union_panels(_prepared(raw_panels), _prepared(_replica_panels(user_id)))
    prepared = union_panels(combined, _prepared(legacy))
    raw_sig = _signature(raw_panels)
    new_sig = _signature(prepared)
    # Report work that is not already in the live blob. A second run sees the
    # legacy collapsed row again, but the split result is already stored.
    split_ids = []
    unsplit_ids = []
    corrected = 0
    added = 0
    seen: List[dict] = []
    for panel in list(raw_panels) + list(legacy) + list(_replica_panels(user_id)):
        if any(same_panel(panel, prev) for prev in seen):
            continue
        seen.append(panel)
        norm, stats = normalize_panel(panel)
        match = next((p for p in raw_panels if same_panel(p, panel)), None)
        if match is not None and _signature([match]) == _signature([norm]):
            continue
        if match is None:
            added += 1
        corrected += int(stats["draw_date_corrected"])
        split_ids.extend(stats["split"])
        unsplit_ids.extend(stats["unsplit"])
    return {
        "changed": raw_sig != new_sig,
        "labs_panels": len(raw_panels),
        "legacy_panels": len(legacy),
        "panels_after": len(prepared),
        "added_panels": added,
        "draw_dates_corrected": corrected,
        "split_marker_ids": split_ids,
        "unsplit_marker_ids": unsplit_ids,
        "store": {
            "source_note": live.get("source_note") or "",
            "updated_at": live.get("updated_at") or "",
            "panels": prepared,
            "storage": live.get("storage") or "",
        },
    }
