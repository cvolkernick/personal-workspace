"""Lab panels for coach + FitDash Labs UI.

Durable store: ``~/.config/resistance-dashboard/labs/<user>/`` (PHI, not git).
Override with ``FITDASH_LABS_DIR``. ``fitness/data/labs.json`` is an empty hook.

Lane is an allowlist by marker id — out-of-range does not imply diet advice.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .labs_parse import classify_band, marker_key
from .nutrition_targets import infer_phase
from .timeutil import local_today_iso

DEFAULT_REL_PATH = "fitness/data/labs.json"
STALE_AFTER_DAYS = 60

# Fallback ranges when a vendor range is missing. Not medical advice.
REFERENCE_HINTS: Dict[str, tuple] = {
    "vitamin_d_ng_ml": (30, 80),
    "vitamin_d": (30, 80),
    "ldl_mg_dl": (0, 100),
    "ldl": (0, 100),
    "hdl_mg_dl": (40, 100),
    "hdl": (40, 100),
    "triglycerides_mg_dl": (0, 150),
    "triglycerides": (0, 150),
    "total_cholesterol_mg_dl": (0, 200),
    "ferritin_ng_ml": (30, 300),
    "tsh_miu_l": (0.4, 4.0),
    "testosterone_ng_dl": (300, 1000),
    "creatinine_mg_dl": (0.7, 1.3),
    "b12_pg_ml": (200, 900),
    "apob_mg_dl": (0, 90),
    "estrogen_pg_ml": (15, 32),
}

# Allowlist by canonical id (unit-less). Not "if out of range then diet."
MARKER_POLICY: Dict[str, Dict[str, Any]] = {
    "apob": {
        "lane": "clinician",
        "coach_domain": "nutrition",
        "kitchen": True,
        "coach_action": (
            "Soluble fiber up (oats/beans/psyllium). Keep sat fat moderate. "
            "Do not deepen the cut to fix lipids."
        ),
    },
    "estrogen": {
        "lane": "clinician",
        "coach_domain": "none",
        "kitchen": False,
        "coach_action": None,
    },
    "free_t3": {
        "lane": "coach",
        "coach_domain": "recovery",
        "kitchen": False,
        "coach_action": (
            "Adaptive T3 dip is common in a deficit. Do not deepen the cut. "
            "Sleep is non-negotiable."
        ),
    },
    "triglycerides": {
        "lane": "coach",
        "coach_domain": "nutrition",
        "kitchen": False,
        "coach_action": "Mild. Alcohol/refined-carb hygiene. Not a carb crash.",
    },
    "hdl": {
        "lane": "coach",
        "coach_domain": "nutrition",
        "kitchen": False,
        "coach_action": "Keep lifting; don't crash-diet.",
    },
    "fructosamine": {
        "lane": "coach",
        "coach_domain": "nutrition",
        "kitchen": False,
        "coach_action": "Keep refined-carb load honest. Not a diabetes call.",
    },
    "total_testosterone": {
        "lane": "info",
        "coach_domain": "none",
        "kitchen": False,
        "coach_action": None,
    },
    "free_testosterone": {
        "lane": "info",
        "coach_domain": "none",
        "kitchen": False,
        "coach_action": None,
    },
    "vitamin_d": {
        "lane": "info",
        "coach_domain": "none",
        "kitchen": False,
        "coach_action": "Adequate; do not megadose.",
    },
    "creatinine": {
        "lane": "info",
        "coach_domain": "none",
        "kitchen": False,
        "coach_action": None,
    },
    "hs_crp": {
        "lane": "info",
        "coach_domain": "none",
        "kitchen": False,
        "coach_action": None,
    },
    "hscrp": {
        "lane": "info",
        "coach_domain": "none",
        "kitchen": False,
        "coach_action": None,
    },
}

_FORBIDDEN = re.compile(
    r"\b(trt|statin|anastrozole|aromatase|synthroid|levothyroxine|hypogonad)\b",
    re.I,
)


def labs_root() -> Path:
    override = os.environ.get("FITDASH_LABS_DIR")
    if override:
        return Path(override).expanduser()
    cfg = os.environ.get("RESISTANCE_DASHBOARD_CONFIG_DIR")
    if cfg:
        return Path(cfg).expanduser() / "labs"
    return Path.home() / ".config" / "resistance-dashboard" / "labs"


def _user_dir(user_id: str) -> Path:
    uid = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(user_id or "").strip()) or "local"
    return labs_root() / uid


def _index_path(user_id: str) -> Path:
    return _user_dir(user_id) / "index.json"


def default_labs() -> dict:
    return {
        "source_note": (
            "Lab panels live in ~/.config/resistance-dashboard/labs/ (not git). "
            "fitness/data/labs.json is an empty hook."
        ),
        "panels": [],
        "updated_at": "",
        "storage": "empty",
    }


def _canonical_id(key: str) -> str:
    k = str(key or "").strip().lower()
    for suffix in (
        "_pg_ml",
        "_ng_ml",
        "_ng_dl",
        "_mg_dl",
        "_mg_l",
        "_g_dl",
        "_u_l",
        "_uiu_ml",
        "_nmol_l",
        "_umol_l",
        "_pct",
        "_miu_l",
    ):
        if k.endswith(suffix):
            k = k[: -len(suffix)]
            break
    aliases = {
        "vitamin_d": "vitamin_d",
        "testosterone": "total_testosterone",
        "b12": "vitamin_b12",
        "vitamin_b12": "vitamin_b12",
        "hs_crp": "hs_crp",
        "hscrp": "hs_crp",
        "ldl": "ldl",
        "hdl": "hdl",
        "apob": "apob",
        "estrogen": "estrogen",
        "free_t3": "free_t3",
        "free_testosterone": "free_testosterone",
        "total_testosterone": "total_testosterone",
        "triglycerides": "triglycerides",
        "fructosamine": "fructosamine",
        "creatinine": "creatinine",
        "remnant_cholesterol": "remnant_cholesterol",
    }
    return aliases.get(k, k)


def _policy_for(marker_id: str) -> Dict[str, Any]:
    return MARKER_POLICY.get(
        marker_id,
        {
            "lane": "info",
            "coach_domain": "none",
            "kitchen": False,
            "coach_action": None,
        },
    )


def _hint_range(key: str) -> Optional[tuple]:
    if key in REFERENCE_HINTS:
        return REFERENCE_HINTS[key]
    cid = _canonical_id(key)
    return REFERENCE_HINTS.get(cid)


def annotate_marker(key: str, raw: Any) -> dict:
    if isinstance(raw, dict):
        marker = deepcopy(raw)
        value = marker.get("value")
        comparator = str(marker.get("comparator") or "")
        value_text = str(marker.get("value_text") or "")
        if value is None and value_text:
            m = re.match(r"^([<>])?\s*(\d+(?:\.\d+)?)$", value_text.strip())
            if m:
                comparator = m.group(1) or comparator
                value = float(m.group(2))
                value_text = f"{comparator}{m.group(2)}" if comparator else m.group(2)
        elif value is not None:
            try:
                value = float(value)
            except (TypeError, ValueError):
                value = None
        marker["value"] = value
        marker["value_text"] = value_text or (
            f"{comparator}{_trim(value)}" if comparator and value is not None else (_trim(value) if value is not None else "")
        )
        marker["comparator"] = comparator
    else:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            m = re.match(r"^([<>])?\s*(\d+(?:\.\d+)?)$", str(raw).strip())
            if not m:
                value = None
                comparator = ""
                value_text = str(raw)
            else:
                comparator = m.group(1) or ""
                value = float(m.group(2))
                value_text = f"{comparator}{m.group(2)}" if comparator else m.group(2)
        else:
            comparator = ""
            value_text = _trim(value)
        marker = {
            "key": key,
            "name": key,
            "value": value,
            "value_text": value_text,
            "comparator": comparator,
            "unit": "",
            "clinical_low": None,
            "clinical_high": None,
            "performance_low": None,
            "performance_high": None,
        }
    marker["key"] = str(marker.get("key") or key)
    cid = _canonical_id(str(marker.get("id") or marker["key"]))
    marker["id"] = cid
    cl_lo = marker.get("clinical_low")
    cl_hi = marker.get("clinical_high")
    if cl_lo is None or cl_hi is None:
        hint = _hint_range(marker["key"]) or _hint_range(cid)
        if hint:
            cl_lo, cl_hi = hint
            marker["clinical_low"] = cl_lo
            marker["clinical_high"] = cl_hi
            marker["range_source"] = "hint"
    if marker.get("value") is not None and cl_lo is not None and cl_hi is not None:
        band, cstat, pstat = classify_band(
            float(marker["value"]),
            comparator=str(marker.get("comparator") or ""),
            clinical_low=cl_lo,
            clinical_high=cl_hi,
            performance_low=marker.get("performance_low"),
            performance_high=marker.get("performance_high"),
            lower_better=cid in {"remnant_cholesterol", "hscrp", "ggt", "apob"},
        )
        marker.setdefault("band", band)
        marker.setdefault("clinical_status", cstat)
        marker.setdefault("performance_status", pstat)
        marker["status"] = cstat if band == "out_of_clinical" else (
            pstat if band == "out_of_performance" else "ok"
        )
    else:
        marker.setdefault("band", "unknown")
        marker.setdefault("clinical_status", "unknown")
        marker.setdefault("performance_status", "unknown")
        marker["status"] = "unknown"
    pol = _policy_for(cid)
    marker["lane"] = pol["lane"]
    marker["coach_domain"] = pol["coach_domain"]
    action = pol.get("coach_action")
    if action and _FORBIDDEN.search(str(action)):
        action = None
    marker["coach_action"] = action
    marker["kitchen"] = bool(pol.get("kitchen"))
    return marker


def _trim(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f == int(f):
        return str(int(f))
    return f"{f:.4f}".rstrip("0").rstrip(".")


def _as_marker_map(raw: Any) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            out[str(k)] = annotate_marker(str(k), v)
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or marker_key(str(item.get("name") or ""), str(item.get("unit") or "")))
            out[key] = annotate_marker(key, item)
    return out


def annotate_panel(panel: dict, *, targets: Optional[dict] = None, as_of: Optional[str] = None) -> dict:
    out = deepcopy(panel)
    markers = _as_marker_map(out.get("markers"))
    out["markers"] = markers
    if not out.get("marker_order"):
        out["marker_order"] = list(markers.keys())
    day = as_of or local_today_iso()
    collected = str(out.get("collected") or out.get("date") or "")[:10]
    out["stale_days"] = _days_between(collected, day) if collected else None
    out["stale"] = bool(out["stale_days"] is not None and out["stale_days"] >= STALE_AFTER_DAYS)
    out["cluster"] = energy_availability_cluster(
        markers, in_deficit=_in_deficit(targets)
    )
    return out


def _days_between(iso_a: str, iso_b: str) -> Optional[int]:
    try:
        a = datetime.strptime(str(iso_a)[:10], "%Y-%m-%d")
        b = datetime.strptime(str(iso_b)[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    return abs((b - a).days)


def _in_deficit(targets: Optional[dict]) -> bool:
    return infer_phase(targets or {}, None) == "cut"


def _lookup(markers: Dict[str, dict], cid: str) -> Optional[dict]:
    for m in markers.values():
        if m.get("id") == cid:
            return m
    return None


def energy_availability_cluster(
    markers: Dict[str, dict],
    *,
    in_deficit: bool,
) -> Optional[dict]:
    if not in_deficit:
        return None
    ft3 = _lookup(markers, "free_t3")
    tt = _lookup(markers, "total_testosterone")
    ft = _lookup(markers, "free_testosterone")
    e2 = _lookup(markers, "estrogen")
    if not ft3:
        return None
    ft3_low = ft3.get("band") == "out_of_performance" and ft3.get("performance_status") == "low"
    androgen_low = False
    for m in (tt, ft):
        if m and m.get("band") == "out_of_performance" and m.get("performance_status") == "low":
            androgen_low = True
    if not (ft3_low and androgen_low):
        return None
    ft3_txt = ft3.get("value_text") or ft3.get("value")
    tt_txt = (tt or {}).get("value_text") or (tt or {}).get("value") or "—"
    ft_txt = (ft or {}).get("value_text") or (ft or {}).get("value") or "—"
    e2_txt = (e2 or {}).get("value_text") or (e2 or {}).get("value") or "—"
    text = (
        f"Energy-availability cluster (fT3 {ft3_txt}, total T {tt_txt}, "
        f"free T {ft_txt}, E2 {e2_txt}) is consistent with a cut, not a "
        "prescription indication. Hold or ease deficit; protein stays the floor; "
        "no added volume."
    )
    if _FORBIDDEN.search(text):
        text = (
            "Energy-availability cluster while cutting. Hold or ease deficit; "
            "protein stays the floor; no added volume."
        )
    return {
        "id": "energy_availability",
        "lane": "coach",
        "coach_domain": "recovery",
        "text": text,
    }


def _atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(prefix=".labs-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_index(user_id: str) -> dict:
    p = _index_path(user_id)
    if not p.is_file():
        return default_labs()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_labs()
    if not isinstance(data, dict):
        return default_labs()
    out = default_labs()
    out["source_note"] = str(data.get("source_note") or out["source_note"])
    out["updated_at"] = str(data.get("updated_at") or "")
    out["storage"] = "config"
    panels = [raw for raw in (data.get("panels") or []) if isinstance(raw, dict)]
    panels.sort(key=lambda x: str(x.get("date") or ""))
    out["panels"] = panels
    return out


def _save_index(user_id: str, payload: dict) -> None:
    blob = json.dumps(payload, indent=2) + "\n"
    _atomic_write(_index_path(user_id), blob.encode("utf-8"))


def _panel_identity(panel: dict) -> tuple:
    order = str(panel.get("order_id") or "").strip()
    date = str(panel.get("date") or "")[:10]
    lab = str(panel.get("lab") or "").strip().lower()
    if order:
        return ("order", order)
    return ("date-lab", date, lab)


def save_panel(
    panel: dict,
    *,
    user_id: str = "",
    pdf_bytes: Optional[bytes] = None,
    workspace_dir: str = "",
) -> dict:
    del workspace_dir  # PHI never written to the git workspace
    annotated = annotate_panel(panel)
    if pdf_bytes:
        sha = hashlib.sha256(pdf_bytes).hexdigest()
        annotated["source_sha256"] = sha
        pdf_path = _user_dir(user_id) / "files" / f"{sha}.pdf"
        _atomic_write(pdf_path, pdf_bytes)
    ident = _panel_identity(annotated)
    store = _load_index(user_id)
    panels = [p for p in store.get("panels") or [] if _panel_identity(p) != ident]
    panels.append(annotated)
    panels.sort(key=lambda x: str(x.get("date") or ""))
    store["panels"] = panels
    store["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    store["storage"] = "config"
    _save_index(user_id, store)
    return load_labs(user_id=user_id)


def delete_panel(
    *,
    date: str,
    lab: str = "",
    order_id: str = "",
    user_id: str = "",
) -> dict:
    store = _load_index(user_id)
    date_s = str(date)[:10]
    lab_s = str(lab or "").strip().lower()
    order_s = str(order_id or "").strip()
    kept = []
    for p in store.get("panels") or []:
        if order_s and str(p.get("order_id") or "").strip() == order_s:
            continue
        if str(p.get("date") or "")[:10] == date_s and (
            not lab_s or str(p.get("lab") or "").strip().lower() == lab_s
        ):
            continue
        kept.append(p)
    store["panels"] = kept
    store["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    store["storage"] = "config"
    _save_index(user_id, store)
    return load_labs(user_id=user_id)


def _load_workspace_hook(workspace_dir: str, rel_path: str) -> dict:
    if not workspace_dir:
        return default_labs()
    p = Path(workspace_dir) / rel_path
    if not p.is_file():
        return default_labs()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_labs()
    if not isinstance(data, dict):
        return default_labs()
    out = default_labs()
    out["source_note"] = str(data.get("source_note") or out["source_note"])
    out["updated_at"] = str(data.get("updated_at") or "")
    out["storage"] = "workspace-hook"
    panels = []
    for raw in data.get("panels") or []:
        if isinstance(raw, dict) and raw.get("date"):
            panels.append(raw)
    panels.sort(key=lambda x: str(x.get("date") or ""))
    out["panels"] = panels
    return out


def load_labs(
    workspace_dir: str = "",
    rel_path: str = DEFAULT_REL_PATH,
    *,
    user_id: str = "",
    targets: Optional[dict] = None,
    as_of: Optional[str] = None,
) -> dict:
    cfg = _load_index(user_id)
    src = cfg if cfg.get("panels") else _load_workspace_hook(workspace_dir, rel_path)
    out = default_labs()
    out["source_note"] = src.get("source_note") or out["source_note"]
    out["updated_at"] = src.get("updated_at") or ""
    out["storage"] = src.get("storage") or ("config" if cfg.get("panels") else out["storage"])
    panels = [
        annotate_panel(raw, targets=targets, as_of=as_of)
        for raw in (src.get("panels") or [])
        if isinstance(raw, dict)
    ]
    panels.sort(key=lambda x: str(x.get("date") or ""))
    out["panels"] = panels
    return out


def latest_panel(labs: Optional[dict]) -> Optional[dict]:
    if not labs:
        return None
    panels = labs.get("panels") or []
    if not panels:
        return None
    return panels[-1]


def flag_markers(panel: Optional[dict]) -> List[dict]:
    """Range flags (clinical + performance). Lane is allowlist metadata, not this list."""
    if not panel:
        return []
    flags: List[dict] = []
    for key, raw in _as_marker_map(panel.get("markers")).items():
        band = raw.get("band")
        if band not in {"out_of_clinical", "out_of_performance"}:
            continue
        status = (
            raw.get("clinical_status")
            if band == "out_of_clinical"
            else raw.get("performance_status")
        )
        flags.append(
            {
                "marker": key,
                "name": raw.get("name") or key,
                "value": raw.get("value"),
                "value_text": raw.get("value_text"),
                "comparator": raw.get("comparator") or "",
                "status": status,
                "ref_low": raw.get("clinical_low")
                if band == "out_of_clinical"
                else raw.get("performance_low"),
                "ref_high": raw.get("clinical_high")
                if band == "out_of_clinical"
                else raw.get("performance_high"),
                "band": band,
                "lane": raw.get("lane"),
                "coach_domain": raw.get("coach_domain"),
                "unit": raw.get("unit") or "",
            }
        )
    return flags


def labs_summary_for_coach(
    labs: Optional[dict],
    *,
    targets: Optional[dict] = None,
    as_of: Optional[str] = None,
) -> dict:
    panel = None
    if labs and labs.get("panels"):
        annotated = [
            annotate_panel(p, targets=targets, as_of=as_of)
            for p in labs.get("panels") or []
            if isinstance(p, dict)
        ]
        annotated.sort(key=lambda x: str(x.get("date") or ""))
        panel = annotated[-1] if annotated else None
    if not panel:
        return {
            "has_labs": False,
            "message": (
                "No lab panels on file — upload a PDF under More → Labs. "
                "PHI stays off git."
            ),
        }
    markers = panel.get("markers") or {}
    flags = flag_markers(panel)
    clinical_flags = [f for f in flags if f.get("band") == "out_of_clinical" and f.get("lane") == "clinician"]
    cluster = panel.get("cluster")
    kitchen_lines: List[str] = []
    for m in markers.values():
        if m.get("kitchen") and m.get("coach_action") and m.get("band") == "out_of_clinical":
            kitchen_lines.append(
                f"{m.get('name')} {m.get('value_text')} {m.get('unit') or ''}".strip()
                + f" — {m.get('coach_action')}"
            )
    if cluster:
        kitchen_lines.append(cluster["text"])
    return {
        "has_labs": True,
        "date": panel.get("date"),
        "collected": panel.get("collected") or "",
        "lab": panel.get("lab") or "",
        "fasting": bool(panel.get("fasting")),
        "marker_count": len(markers),
        "flags": flags,
        "clinical_flags": clinical_flags,
        "cluster": cluster,
        "stale": bool(panel.get("stale")),
        "stale_days": panel.get("stale_days"),
        "notes": panel.get("notes") or "",
        "markers": {
            k: (m.get("value") if isinstance(m, dict) else m) for k, m in markers.items()
        },
        "kitchen_lines": kitchen_lines,
        "volume": "unchanged",
        "storage": (labs or {}).get("storage") or "",
    }
