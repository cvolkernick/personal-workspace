"""Turn manual JSON / CSV / Rythm PDFs into biomarker store rows.

Flow: ``preview_*`` → dry-run dict (rows, duplicates, warnings), nothing
written. ``commit_preview`` writes it through biomarkers_store.insert_panel
(which skips anything already stored for the same marker+draw_date+panel).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from . import biomarkers_config as cfg

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ImportError_(ValueError):
    """Bad import input (user-facing message)."""


def _norm_name(s: str) -> str:
    s = str(s or "").lower().replace("µ", "u").replace("μ", "u")
    s = re.sub(r"\(.*?\)", " ", s)  # "hs-CRP (High-Sensitivity …)" → "hs-crp"
    s = re.sub(r"_(pg_ml|ng_ml|ng_dl|mg_dl|mg_l|g_dl|u_l|uiu_ml|miu_l|nmol_l|umol_l|pct)$", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


_ALIAS: Dict[str, str] = {}
for _cid, (_disp, _aliases) in cfg.MARKERS.items():
    for _a in (_cid, _disp, *_aliases):
        _ALIAS[_norm_name(_a)] = _cid


def canonical_marker(name: str) -> str:
    """Canonical id for a display name/key; unknown names become snake_case."""
    n = _norm_name(name)
    if n in _ALIAS:
        return _ALIAS[n]
    return re.sub(r"\s+", "_", n) or "unknown"


def display_name(marker: str, fallback: str = "") -> str:
    if marker in cfg.MARKERS:
        return cfg.MARKERS[marker][0]
    return fallback or marker.replace("_", " ").title()


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _parse_value(raw: Any) -> Tuple[Optional[float], str, str]:
    """→ (numeric value, value_text, comparator). Keeps '<12' as cmp '<'."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw), (str(raw)), ""
    s = str(raw or "").strip()
    m = re.match(r"^([<>]=?)?\s*(-?\d+(?:\.\d+)?)", s.replace(",", ""))
    if not m:
        return None, s, ""
    return float(m.group(2)), s, (m.group(1) or "")


def _parse_range(text: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    s = str(text or "").strip()
    if not s:
        return None, None, None
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)$", s)
    if m:
        return float(m.group(1)), float(m.group(2)), None
    m = re.match(r"^<=?\s*(-?\d+(?:\.\d+)?)$", s)
    if m:
        return None, float(m.group(1)), s
    m = re.match(r"^>=?\s*(-?\d+(?:\.\d+)?)$", s)
    if m:
        return float(m.group(1)), None, s
    return None, None, s


def _unit(name: str, unit: Any) -> str:
    u = str(unit or "").strip()
    # Rythm prints ratio markers with no unit; the legacy parser tags them "pct".
    if u.lower() == "pct" and "ratio" in str(name).lower():
        return "ratio"
    return u


def normalize_row(raw: dict, *, draw_date: str, source: str) -> dict:
    name = raw.get("marker") or raw.get("name") or raw.get("marker_name") or raw.get("key") or ""
    if not str(name).strip():
        raise ImportError_("row missing marker name")
    marker = canonical_marker(raw.get("marker_id") or name) if raw.get("marker_id") else canonical_marker(name)
    # Prefer the printed text ("747", "<12") over a float re-render ("747.0").
    vraw = raw.get("value_text") if raw.get("value_text") not in (None, "") else raw.get("value")
    value, vtext, cmp_ = _parse_value(vraw)
    if raw.get("comparator"):
        cmp_ = str(raw["comparator"])
    lo = _num(raw.get("ref_low", raw.get("clinical_low")))
    hi = _num(raw.get("ref_high", raw.get("clinical_high")))
    rtext = raw.get("ref_text")
    if lo is None and hi is None and raw.get("range"):
        lo, hi, rtext = _parse_range(raw["range"])
    d = str(raw.get("draw_date") or draw_date or "").strip()
    if not _DATE.match(d):
        raise ImportError_(f"{name}: draw_date must be YYYY-MM-DD (got {d!r})")
    if value is None and not vtext:
        raise ImportError_(f"{name}: missing value")
    return {
        "marker": marker,
        "marker_name": str(name).strip(),
        "value": value,
        "value_text": vtext,
        "comparator": cmp_,
        "unit": _unit(name, raw.get("unit")),
        "ref_low": lo,
        "ref_high": hi,
        "ref_text": (str(rtext).strip() or None) if rtext else None,
        "draw_date": d,
        "source": str(raw.get("source") or source or "").strip(),
    }


def _panel_id(lab: str, draw_date: str, hint: str = "") -> str:
    if hint:
        return re.sub(r"[^A-Za-z0-9._-]+", "-", str(hint)).strip("-")
    slug = re.sub(r"[^a-z0-9]+", "-", (lab or "manual").lower()).strip("-") or "manual"
    return f"{slug}-{draw_date}"


def _finish(panel: dict, rows: List[dict], *, existing: Optional[set], warnings: List[str]) -> dict:
    if not rows:
        raise ImportError_("no marker rows found")
    if len(rows) > cfg.MAX_IMPORT_ROWS:
        raise ImportError_(f"too many rows ({len(rows)} > {cfg.MAX_IMPORT_ROWS})")
    pid = panel["panel_id"]
    seen, out, dupes = set(), [], []
    for r in rows:
        key = (r["marker"], r["draw_date"], pid)
        if key in seen:
            warnings.append(f"{r['marker_name']}: repeated in this import — kept first")
            continue
        seen.add(key)
        r = dict(r, duplicate=bool(existing and key in existing))
        if r["duplicate"]:
            dupes.append(r["marker"])
        out.append(r)
    ctx = {k: v for k, v in (panel.get("context") or {}).items() if k in cfg.CONTEXT_KEYS}
    return {
        "dry_run": True,
        "panel": dict(panel, context=ctx),
        "rows": out,
        "new_count": len(out) - len(dupes),
        "duplicate_count": len(dupes),
        "duplicates": dupes,
        "warnings": warnings,
    }


def preview_json(payload: Any, *, existing: Optional[set] = None, source: str = "manual") -> dict:
    """Accepts {panel..., markers:[...]} / {markers:{key:{...}}} / [rows]."""
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    if isinstance(payload, list):
        payload = {"markers": payload}
    if not isinstance(payload, dict):
        raise ImportError_("JSON must be an object or a list of rows")
    draw = str(payload.get("draw_date") or payload.get("collected") or "").strip()
    lab = str(payload.get("lab") or "manual")
    markers = payload.get("markers") or payload.get("rows") or []
    if isinstance(markers, dict):
        order = payload.get("marker_order") or list(markers)
        markers = [dict(markers[k], key=k) for k in order if k in markers]
    src = payload.get("source")
    if isinstance(src, dict):
        src = src.get("original_upload_filename") or src.get("kind") or source
    src = str(src or source)
    rows = [normalize_row(m, draw_date=draw, source=src) for m in markers]
    if not draw and rows:
        draw = rows[0]["draw_date"]
    panel = {
        "panel_id": _panel_id(lab, draw, payload.get("panel_id") or ""),
        "lab": lab, "draw_date": draw, "source": src,
        "context": dict(payload.get("context") or {}),
    }
    if payload.get("draw_time") and "draw_time" not in panel["context"]:
        panel["context"]["draw_time"] = payload["draw_time"]
    if payload.get("fasting") is not None and "fasted" not in panel["context"]:
        panel["context"]["fasted"] = bool(payload["fasting"])
    return _finish(panel, rows, existing=existing, warnings=[])


def preview_csv(text: str, *, draw_date: str = "", lab: str = "manual", panel_id: str = "",
                existing: Optional[set] = None, context: Optional[dict] = None) -> dict:
    """CSV header: marker,value,unit,ref_low,ref_high[,ref_text,range,draw_date]."""
    reader = csv.DictReader(io.StringIO(text.strip()))
    if not reader.fieldnames or "marker" not in [f.strip().lower() for f in reader.fieldnames]:
        raise ImportError_("CSV needs a header row with at least marker,value")
    rows = []
    for raw in reader:
        raw = {str(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        if not raw.get("marker"):
            continue
        rows.append(normalize_row(raw, draw_date=draw_date, source="csv"))
    d = draw_date or (rows[0]["draw_date"] if rows else "")
    panel = {"panel_id": _panel_id(lab, d, panel_id), "lab": lab, "draw_date": d,
             "source": "csv", "context": dict(context or {})}
    return _finish(panel, rows, existing=existing, warnings=[])


def pdf_to_text(data: bytes) -> str:
    """pdftotext (poppler) first; fall back to the in-repo stream extractor."""
    exe = shutil.which("pdftotext")
    if exe:
        with tempfile.TemporaryDirectory() as td:
            p = f"{td}/in.pdf"
            with open(p, "wb") as fh:
                fh.write(data)
            try:
                out = subprocess.run([exe, "-raw", p, "-"], capture_output=True, timeout=60, check=True)
                txt = out.stdout.decode("utf-8", "replace")
                if txt.strip():
                    return txt
            except (subprocess.SubprocessError, OSError):
                pass
    from .labs_parse import extract_pdf_text

    return extract_pdf_text(data)


_COLLECTED_TIME = re.compile(
    r"COLLECTED\s*\n?\s*\d{1,2}/\d{1,2}/\d{4}\s+(\d{1,2}):(\d{2})\s*(AM|PM)", re.I
)


def preview_pdf_text(text: str, *, filename: str = "", existing: Optional[set] = None,
                     source_sha256: str = "") -> dict:
    from .labs_parse import parse_lab_text

    lab_panel = parse_lab_text(text, filename=filename)
    draw = lab_panel.get("collected") or lab_panel.get("date") or ""
    ctx: Dict[str, Any] = {"fasted": bool(lab_panel.get("fasting"))}
    m = _COLLECTED_TIME.search(text or "")
    if m:
        h = int(m.group(1)) % 12 + (12 if m.group(3).upper() == "PM" else 0)
        ctx["draw_time"] = f"{h:02d}:{m.group(2)}"
    src = filename or "rythm.pdf"
    rows = []
    for k in lab_panel.get("marker_order") or []:
        mk = lab_panel["markers"][k]
        rows.append(normalize_row({
            "name": mk["name"], "value": mk["value"], "value_text": mk["value_text"],
            "comparator": mk.get("comparator"),
            "unit": mk.get("unit"), "ref_low": mk.get("clinical_low"), "ref_high": mk.get("clinical_high"),
        }, draw_date=draw, source=src))
    order_id = lab_panel.get("order_id") or ""
    lab = lab_panel.get("lab") or "Rythm Health"
    hint = f"rythm-{order_id}" if order_id and "rythm" in lab.lower() else (f"{lab}-{order_id}" if order_id else "")
    panel = {"panel_id": _panel_id(lab, draw, hint), "lab": lab, "draw_date": draw,
             "source": src + (f" sha256:{source_sha256[:16]}" if source_sha256 else ""), "context": ctx}
    return _finish(panel, rows, existing=existing, warnings=[])


def preview_pdf(data: bytes, *, filename: str = "", existing: Optional[set] = None) -> dict:
    from .labs_parse import MAX_PDF_BYTES

    if not data:
        raise ImportError_("empty PDF")
    if len(data) > MAX_PDF_BYTES:
        raise ImportError_("PDF too large (8 MB max)")
    sha = hashlib.sha256(data).hexdigest()
    return preview_pdf_text(pdf_to_text(data), filename=filename, existing=existing, source_sha256=sha)


def commit_preview(preview: dict, *, user_id: str = "") -> dict:
    from .biomarkers_store import insert_panel

    rows = [{k: v for k, v in r.items() if k != "duplicate"} for r in preview.get("rows") or []]
    res = insert_panel(preview["panel"], rows, user_id=user_id)
    return dict(res, dry_run=False)
