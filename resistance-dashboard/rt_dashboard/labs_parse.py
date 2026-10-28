"""Parse lab PDFs into structured markers. P0 = Rythm Health text layout.

Never float()-drop comparators (``Estrogen <12`` stays ``comparator='<'``).
"""

from __future__ import annotations

import io
import re
import zlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

MAX_PDF_BYTES = 8 * 1024 * 1024

_US_DATE = re.compile(
    r"\b(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2})\s*(AM|PM))?\b",
    re.I,
)
_HEADER_ROW = re.compile(
    r"Test\s+Value\s+Unit\s+Range(?:\s+Performance\s+Range)?",
    re.I,
)
# Unit allows eGFR-style mL/min/1.73m2 (multiple slashes, a digit).
# Clinical range is two-sided (lo-hi) or one-sided (>60 / <3).
_NUM = r"\d+(?:\.\d+)?"
_UNIT = r"[A-Za-zµμ%](?:[A-Za-zµμ%0-9]*[A-Za-zµμ%])?(?:/[A-Za-z0-9.]+)*"
_ROW_CORE = (
    rf"(?P<name>.+?)\s+"
    rf"(?P<cmp>[<>]=?)?(?P<val>{_NUM})\s+"
    rf"(?P<unit>{_UNIT})\s+"
    rf"(?:(?P<cl_lo>{_NUM})\s*[-–]\s*(?P<cl_hi>{_NUM})"
    rf"|(?P<cl_op>[<>]=?)\s*(?P<cl_bound>{_NUM}))"
    rf"(?:\s+(?:(?P<pf_lo>{_NUM})\s*[-–]\s*(?P<pf_hi>{_NUM})"
    rf"|(?P<pf_op>[<>]=?)\s*(?P<pf_bound>{_NUM})))?"
)
_ROW = re.compile(rf"^{_ROW_CORE}\s*$")
# Lead match, not end-anchored, so a second marker on the same line is a remainder.
_ROW_LEAD = re.compile(rf"^{_ROW_CORE}(?=\s|$)")
_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
_Tj = re.compile(r"\((?:\\.|[^\\)])*\)\s*Tj")
_TJ = re.compile(r"\[(.*?)\]\s*TJ", re.DOTALL)
_Tj_STR = re.compile(r"\((?:\\.|[^\\)])*\)")

# Display-name stem → key stem (unit suffix added separately).
_NAME_STEM: Dict[str, str] = {
    "vitamin b12": "vitamin_b12",
    "uric acid": "uric_acid",
    "fructosamine": "fructosamine",
    "ggt": "ggt",
    "alkaline phosphatase (alp)": "alp",
    "alkaline phosphatase": "alp",
    "alp": "alp",
    "apob": "apob",
    "creatinine": "creatinine",
    "thyroid stimulating hormone": "tsh",
    "tsh": "tsh",
    "free t3": "free_t3",
    "triglycerides": "triglycerides",
    "shbg": "shbg",
    "hdl cholesterol": "hdl",
    "hdl": "hdl",
    "total cholesterol": "total_cholesterol",
    "hs-crp": "hs_crp",
    "hscrp": "hs_crp",
    "hs_crp": "hs_crp",
    "albumin": "albumin",
    "vitamin d": "vitamin_d",
    "total testosterone": "total_testosterone",
    "ferritin": "ferritin",
    "estrogen": "estrogen",
    "free testosterone": "free_testosterone",
    "ldl cholesterol": "ldl",
    "ldl": "ldl",
    "ldl/apob ratio": "ldl_apob",
    "total cholesterol/hdl ratio": "tc_hdl",
    "triglycerides/hdl ratio": "tg_hdl",
    "remnant cholesterol": "remnant_cholesterol",
}

# Performance-range "lower is better" — below clinical_low can still be in_performance.
_LOWER_BETTER = {"remnant_cholesterol", "hs_crp", "hscrp", "ggt", "apob"}


class LabParseError(ValueError):
    """Unrecognized or unreadable lab PDF/text."""


def _clock_time(text: str) -> str:
    """Clock time printed next to a US date, as HH:MM. No timezone conversion."""
    m = _US_DATE.search(text or "")
    if not m or not m.group(4):
        return ""
    hour = int(m.group(4))
    minute = int(m.group(5))
    ampm = (m.group(6) or "").upper()
    if ampm == "PM" and hour != 12:
        hour += 12
    elif ampm == "AM" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}"


def _range_pair(lo, hi, op, bound) -> Tuple[Optional[float], Optional[float]]:
    if lo not in (None, "") and hi not in (None, ""):
        return float(lo), float(hi)
    if op and bound not in (None, ""):
        v = float(bound)
        if str(op).startswith("<"):
            return None, v
        if str(op).startswith(">"):
            return v, None
    return None, None


def parse_us_date(text: str) -> Optional[str]:
    m = _US_DATE.search(text or "")
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _unescape_pdf_literal(blob: str) -> str:
    inner = blob[1:-1] if blob.startswith("(") and blob.endswith(")") else blob
    inner = inner.replace("\\(", "(").replace("\\)", ")")
    inner = inner.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")
    inner = inner.replace("\\\\", "\\")
    return inner


def _extract_pdf_text_stdlib(data: bytes) -> str:
    chunks: List[str] = []
    for match in _STREAM.finditer(data):
        raw = match.group(1)
        try:
            payload = zlib.decompress(raw)
        except zlib.error:
            payload = raw
        try:
            text = payload.decode("latin-1")
        except UnicodeDecodeError:
            continue
        for op in _Tj.findall(text):
            chunks.append(_unescape_pdf_literal(op.rsplit("Tj", 1)[0].strip()))
        for arr in _TJ.findall(text):
            parts = [_unescape_pdf_literal(s) for s in _Tj_STR.findall(arr)]
            chunks.append("".join(parts))
    return "\n".join(c for c in chunks if c.strip())


def extract_pdf_text(data: bytes) -> str:
    if not data or not data.lstrip().startswith(b"%PDF"):
        raise LabParseError("Not a PDF")
    text = ""
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(io.BytesIO(data))
        parts = [(page.extract_text() or "") for page in reader.pages]
        text = "\n".join(parts).strip()
    except Exception:
        text = ""
    if not text:
        text = _extract_pdf_text_stdlib(data).strip()
    if not text:
        raise LabParseError("Could not extract text from this PDF (scan/OCR is not P0)")
    return text


def _field_after(text: str, label: str) -> str:
    pat = re.compile(rf"^{re.escape(label)}\s*$[\r\n]+^(.+)$", re.I | re.M)
    m = pat.search(text)
    if not m:
        return ""
    return m.group(1).strip()


def _name_embeds_two_markers(name: str) -> bool:
    from .lab_markers import canonical_ids_in_text

    return len(canonical_ids_in_text(name)) >= 2


def _split_row_line(line: str) -> List[str]:
    """Split one physical line into complete marker rows.

    A line that only matches by swallowing a second marker into the name
    returns [] so the caller cannot store a collapsed row.
    """
    rest = " ".join((line or "").split())
    if not rest:
        return []
    parts: List[str] = []
    while rest:
        m = _ROW_LEAD.match(rest)
        if not m:
            return []
        name = re.sub(r"\s+", " ", m.group("name")).strip()
        if _name_embeds_two_markers(name):
            return []
        parts.append(rest[: m.end()].strip())
        rest = rest[m.end() :].strip()
    return parts


def _join_wrapped_rows(block: str) -> List[str]:
    """Join hyphen-wrapped names. Never glue a finished marker onto the next one."""
    raw_lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    out: List[str] = []
    buf = ""

    def _emit(line: str) -> None:
        parts = _split_row_line(line)
        if parts:
            out.extend(parts)

    for ln in raw_lines:
        low = ln.lower()
        if low.startswith("all tests listed") or low.startswith("generated at"):
            break
        own = _split_row_line(ln)
        if own and buf and buf.endswith("-") and not re.match(r"^(?:[<>]=?\s*)?\d", ln):
            # Hyphenated name wrap ("Pro-" / "tein) 0.40 ...") must win over
            # treating the continuation as a brand-new marker.
            parts = _split_row_line(buf[:-1] + ln)
            if parts:
                out.extend(parts)
                buf = ""
                continue
        if own:
            # A finished row is never glued onto the previous fragment.
            if buf:
                _emit(buf)
                buf = ""
            out.extend(own)
            continue
        if buf:
            candidates: List[str] = []
            # Word-wrap hyphen ("Pro-" / "tein"), not a range dash before a number.
            if buf.endswith("-") and not re.match(r"^(?:[<>]=?\s*)?\d", ln):
                candidates.append(buf[:-1] + ln)
            candidates.append(buf + " " + ln)
            emitted = False
            for cand in candidates:
                parts = _split_row_line(cand)
                if parts:
                    out.extend(parts)
                    buf = ""
                    emitted = True
                    break
            if emitted:
                continue
            buf = candidates[-1]
            continue
        buf = ln
    if buf:
        _emit(buf)
    return out


def _norm_name(name: str) -> str:
    s = re.sub(r"\s+", " ", (name or "").strip().lower())
    s = re.sub(r"\s*\([^)]*\)\s*", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if s.startswith("hs-crp") or s.startswith("hs crp"):
        return "hs-crp"
    return s


def _stem(name: str) -> str:
    key = _norm_name(name)
    if key in _NAME_STEM:
        return _NAME_STEM[key]
    # Parenthetical already stripped; try first token aliases
    if key.startswith("hs"):
        return "hs_crp"
    return re.sub(r"[^a-z0-9]+", "_", key).strip("_") or "unknown"


def _unit_slug(unit: str) -> str:
    u = (unit or "").replace("µ", "u").replace("μ", "u").strip()
    return re.sub(r"[^a-z0-9]+", "_", u.lower()).strip("_")


def marker_key(name: str, unit: str) -> str:
    stem = _stem(name)
    us = _unit_slug(unit)
    return f"{stem}_{us}" if us else stem


def _trim_num(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f == int(f):
        return str(int(f))
    return f"{f:.4f}".rstrip("0").rstrip(".")


def classify_band(
    value: float,
    *,
    comparator: str,
    clinical_low: Optional[float],
    clinical_high: Optional[float],
    performance_low: Optional[float],
    performance_high: Optional[float],
    lower_better: bool = False,
) -> Tuple[str, str, str]:
    """Return (band, clinical_status, performance_status).

    Band priority: in performance range wins (so remnant-low but in 0–20
    is ``in_performance``). Else out of clinical. Else out of performance.
    """
    clinical_status = "ok"
    if clinical_low is not None or clinical_high is not None:
        lo = float(clinical_low) if clinical_low is not None else None
        hi = float(clinical_high) if clinical_high is not None else None
        if comparator == "<":
            if lo is not None and value <= lo:
                clinical_status = "low"
            elif hi is not None and value > hi:
                clinical_status = "high"
        elif comparator == ">":
            if hi is not None and value >= hi:
                clinical_status = "high"
            elif lo is not None and value < lo:
                clinical_status = "low"
        else:
            if lo is not None and value < lo:
                clinical_status = "low"
            elif hi is not None and value > hi:
                clinical_status = "high"

    performance_status = "ok"
    in_perf = False
    has_perf = performance_low is not None or performance_high is not None
    if has_perf:
        plo = float(performance_low) if performance_low is not None else None
        phi = float(performance_high) if performance_high is not None else None
        if plo is not None and value < plo:
            performance_status = "low"
        elif phi is not None and value > phi:
            performance_status = "high"
        else:
            in_perf = True
            performance_status = "ok"

    if in_perf:
        return "in_performance", clinical_status, performance_status
    if clinical_status != "ok":
        if lower_better and clinical_status == "low" and has_perf and in_perf:
            return "in_performance", clinical_status, performance_status
        return "out_of_clinical", clinical_status, performance_status
    if has_perf and performance_status != "ok":
        return "out_of_performance", clinical_status, performance_status
    return "in_performance", clinical_status, performance_status


def parse_lab_text(text: str, filename: str = "") -> dict:
    blob = str(text or "").replace("\xa0", " ")
    blob = blob.replace("µ", "u").replace("μ", "u")
    if not _HEADER_ROW.search(blob):
        raise LabParseError(
            "This PDF is not a Rythm panel I can parse yet (Quest/LabCorp not P0)"
        )
    if not re.search(r"Rythm", blob, re.I):
        raise LabParseError(
            "This PDF is not a Rythm panel I can parse yet (Quest/LabCorp not P0)"
        )
    header_m = _HEADER_ROW.search(blob)
    assert header_m is not None
    rows = _join_wrapped_rows(blob[header_m.end() :])
    markers: Dict[str, dict] = {}
    marker_order: List[str] = []
    for ln in rows:
        m = _ROW.match(ln)
        if not m:
            continue
        name = re.sub(r"\s+", " ", m.group("name")).strip()
        if _name_embeds_two_markers(name):
            # Refuse a collapsed row. Adjacent markers stay unparsed rather
            # than landing as one measurement.
            continue
        cmp_ch = m.group("cmp") or ""
        val_s = m.group("val")
        value = float(val_s)
        unit = m.group("unit")
        key = marker_key(name, unit)
        if key in markers:
            continue
        cl_lo, cl_hi = _range_pair(m.group("cl_lo"), m.group("cl_hi"), m.group("cl_op"), m.group("cl_bound"))
        pf_lo, pf_hi = _range_pair(m.group("pf_lo"), m.group("pf_hi"), m.group("pf_op"), m.group("pf_bound"))
        stem = _stem(name)
        band, cstat, pstat = classify_band(
            value,
            comparator=cmp_ch,
            clinical_low=cl_lo,
            clinical_high=cl_hi,
            performance_low=pf_lo,
            performance_high=pf_hi,
            lower_better=stem in _LOWER_BETTER,
        )
        markers[key] = {
            "key": key,
            "id": stem,
            "name": name,
            "value": value,
            "value_text": f"{cmp_ch}{val_s}" if cmp_ch else val_s,
            "comparator": cmp_ch or "",
            "unit": unit.replace("uIU", "µIU").replace("umol", "µmol")
            if "IU" in unit or unit.lower().startswith("umol")
            else unit,
            "clinical_low": cl_lo,
            "clinical_high": cl_hi,
            "performance_low": pf_lo,
            "performance_high": pf_hi,
            "band": band,
            "clinical_status": cstat,
            "performance_status": pstat,
        }
        marker_order.append(key)
    if not markers:
        raise LabParseError("Rythm table header found but no marker rows parsed")
    fasting_raw = _field_after(blob, "FASTING")
    collected_raw = _field_after(blob, "COLLECTED")
    reported_raw = _field_after(blob, "REPORTED")
    # Draw date is COLLECTED. REPORTED is a later paperwork date and must not
    # become the panel date (that is what dated a May draw as the report day).
    collected = parse_us_date(collected_raw) or ""
    reported = parse_us_date(reported_raw) or ""
    draw = collected or reported
    lab = _field_after(blob, "LAB") or "Rythm Health"
    return {
        "date": draw,
        "draw_date": draw,
        "collected": collected,
        "reported": reported,
        "draw_time": _clock_time(collected_raw),
        "lab": lab,
        "fasting": fasting_raw.lower() in {"yes", "y", "true"},
        "specimen": _field_after(blob, "SPECIMEN"),
        "order_id": _field_after(blob, "ORDER ID"),
        "source": "upload",
        "filename": filename or "",
        "markers": markers,
        "marker_order": marker_order,
        "notes": "",
    }


# Alias used by tests / older call sites.
parse_rythm_panel = parse_lab_text


def parse_lab_pdf(data: bytes, filename: str = "") -> dict:
    if not data:
        raise LabParseError("Empty upload")
    if len(data) > MAX_PDF_BYTES:
        raise LabParseError(f"PDF too large (max {MAX_PDF_BYTES // (1024 * 1024)} MB)")
    text = extract_pdf_text(data)
    return parse_lab_text(text, filename=filename)


def canonical_marker_id(name: str) -> str:
    raw = str(name or "").strip()
    if raw in _NAME_STEM:
        return _NAME_STEM[raw]
    snake = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    # legacy keys vitamin_d_ng_ml → vitamin_d
    if snake.endswith("_ng_ml") or snake.endswith("_mg_dl") or snake.endswith("_pg_ml"):
        stem = re.sub(r"_(ng_ml|mg_dl|pg_ml|u_l|nmol_l|pct)$", "", snake)
        if stem in _NAME_STEM.values() or stem in {
            "vitamin_d",
            "ldl",
            "hdl",
            "triglycerides",
            "ferritin",
            "creatinine",
            "tsh",
        }:
            return stem
        aliases = {
            "vitamin_d_ng_ml": "vitamin_d",
            "ldl_mg_dl": "ldl",
            "hdl_mg_dl": "hdl",
            "triglycerides_mg_dl": "triglycerides",
            "total_cholesterol_mg_dl": "total_cholesterol",
            "ferritin_ng_ml": "ferritin",
            "tsh_miu_l": "tsh",
            "testosterone_ng_dl": "total_testosterone",
            "creatinine_mg_dl": "creatinine",
            "b12_pg_ml": "vitamin_b12",
            "vitamin_b12_pg_ml": "vitamin_b12",
        }
        if snake in aliases:
            return aliases[snake]
    return _stem(raw)


def display_name(marker_id: str, fallback: str = "") -> str:
    pretty = {
        "vitamin_b12": "Vitamin B12",
        "vitamin_d": "Vitamin D",
        "apob": "ApoB",
        "hdl": "HDL",
        "ldl": "LDL",
        "hs_crp": "hs-CRP",
        "hscrp": "hs-CRP",
        "total_testosterone": "Total testosterone",
        "free_testosterone": "Free testosterone",
        "free_t3": "Free T3",
        "remnant_cholesterol": "Remnant cholesterol",
        "estrogen": "Estrogen",
        "triglycerides": "Triglycerides",
        "creatinine": "Creatinine",
    }
    return pretty.get(marker_id) or fallback or marker_id.replace("_", " ")


def parse_rythm_panel(text: str) -> dict:
    """List-of-markers shape used by labs_store.ingest_pdf."""
    panel = parse_lab_text(text)
    order = panel.get("marker_order") or list(panel.get("markers") or {})
    markers = panel.get("markers") or {}
    if isinstance(markers, dict):
        panel["markers"] = [markers[k] for k in order if k in markers]
    return panel


def marker_from_legacy(key: str, raw: Any) -> Optional[dict]:
    mid = canonical_marker_id(key)
    if isinstance(raw, dict):
        try:
            value = float(raw.get("value"))
        except (TypeError, ValueError):
            return None
        return {
            "id": str(raw.get("id") or mid),
            "name": str(raw.get("name") or display_name(mid, key)),
            "value": value,
            "value_text": str(raw.get("value_text") or raw.get("value") or ""),
            "comparator": (
                "lt"
                if str(raw.get("comparator") or "") in {"<", "lt"}
                else "gt"
                if str(raw.get("comparator") or "") in {">", "gt"}
                else "eq"
            ),
            "unit": str(raw.get("unit") or ""),
            "clinical_low": raw.get("clinical_low"),
            "clinical_high": raw.get("clinical_high"),
            "performance_low": raw.get("performance_low"),
            "performance_high": raw.get("performance_high"),
        }
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return {
        "id": mid,
        "name": display_name(mid, key),
        "value": value,
        "value_text": str(raw),
        "comparator": "eq",
        "unit": "",
        "clinical_low": None,
        "clinical_high": None,
        "performance_low": None,
        "performance_high": None,
    }


def _has_token(blob: str, token: str) -> bool:
    """True when token is present on its own, not as part of a longer number."""
    if not token:
        return False
    return re.search(rf"(?<![\d.]){re.escape(token)}(?![\d.])", blob) is not None


def _stored_source_text(marker: dict) -> str:
    """Text already stored on the row. Nothing is added that was not stored."""
    bits: List[str] = []
    name = str(marker.get("name") or marker.get("marker_name") or "")
    if name:
        bits.append(name)
    blob = name
    vt = str(marker.get("value_text") or "")
    if vt and not _has_token(blob, vt):
        bits.append(vt)
        blob = " ".join(bits)
    unit = str(marker.get("unit") or "")
    if unit and not _has_token(blob, unit):
        bits.append(unit)
        blob = " ".join(bits)
    lo = marker.get("clinical_low")
    if lo is None:
        lo = marker.get("ref_low")
    hi = marker.get("clinical_high")
    if hi is None:
        hi = marker.get("ref_high")
    if lo is not None and hi is not None:
        pair = f"{_trim_num(lo)} - {_trim_num(hi)}"
        if not _has_token(blob, pair):
            bits.append(pair)
            blob = " ".join(bits)
    elif lo is not None and not _has_token(blob, f">{_trim_num(lo)}") and not _has_token(blob, f">={_trim_num(lo)}"):
        bits.append(f">{_trim_num(lo)}")
    elif hi is not None and not _has_token(blob, f"<{_trim_num(hi)}") and not _has_token(blob, f"<={_trim_num(hi)}"):
        bits.append(f"<{_trim_num(hi)}")
    return " ".join(bits)


def recover_collapsed_marker(marker: dict) -> dict:
    """Split one stored row that glued two markers together.

    ``single`` — not collapsed. ``split`` — each recovered value token was
    already in the stored text. ``unrecoverable`` — the name mentions two
    markers but both values are not in the stored fields. The caller must
    leave an unrecoverable row unchanged rather than guess.
    """
    if not isinstance(marker, dict):
        return {"status": "single", "markers": [], "ids": []}
    from .lab_markers import canonical_ids_in_text

    source = _stored_source_text(marker)
    name = str(marker.get("name") or marker.get("marker_name") or "")
    ids = canonical_ids_in_text(name)
    if len(ids) < 2:
        ids = canonical_ids_in_text(source)
    parts = _split_row_line(source)
    if len(ids) < 2 and len(parts) < 2:
        return {"status": "single", "markers": [], "ids": ids}
    if len(parts) < 2:
        return {"status": "unrecoverable", "markers": [], "ids": ids}
    built: List[dict] = []
    found: List[str] = []
    for line in parts:
        m = _ROW.match(line)
        if not m:
            return {"status": "unrecoverable", "markers": [], "ids": ids}
        val_s = m.group("val") or ""
        if not _has_token(source, val_s):
            return {"status": "unrecoverable", "markers": [], "ids": ids}
        row_name = re.sub(r"\s+", " ", m.group("name")).strip()
        if _name_embeds_two_markers(row_name):
            return {"status": "unrecoverable", "markers": [], "ids": ids}
        cmp_ch = m.group("cmp") or ""
        value = float(val_s)
        unit = m.group("unit") or ""
        cl_lo, cl_hi = _range_pair(m.group("cl_lo"), m.group("cl_hi"), m.group("cl_op"), m.group("cl_bound"))
        pf_lo, pf_hi = _range_pair(m.group("pf_lo"), m.group("pf_hi"), m.group("pf_op"), m.group("pf_bound"))
        stem = _stem(row_name)
        key = marker_key(row_name, unit)
        built.append(
            {
                "key": key,
                "id": stem,
                "name": row_name,
                "value": value,
                "value_text": f"{cmp_ch}{val_s}" if cmp_ch else val_s,
                "comparator": cmp_ch,
                "unit": unit,
                "clinical_low": cl_lo,
                "clinical_high": cl_hi,
                "performance_low": pf_lo,
                "performance_high": pf_hi,
            }
        )
        for cid in canonical_ids_in_text(row_name):
            if cid not in found:
                found.append(cid)
    if ids and not set(ids).issubset(set(found)):
        return {"status": "unrecoverable", "markers": [], "ids": ids}
    return {"status": "split", "markers": built, "ids": found or ids}
