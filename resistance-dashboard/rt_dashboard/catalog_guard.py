"""Planner catalog guard (Frankenfit spec 2026-10-27).

Three jobs, all driven by ``fitness/exercises/catalog_guard.json`` (data,
not per-lift branches):

1. ``apply_catalog_guard``: merge guard data into whatever catalog loaded
   (GitHub, the local fallback, or the bundle). Legacy ids fold into their
   canonical id, missing canonical rows are injected, declared aliases and
   load fields are attached.
2. ``guard_plan``: every planned exercise resolves to a catalog id. Unknown
   names get the closest same-pattern catalog substitute (a warning is
   logged) or are dropped with a visible note. Never a blank row.
3. ``starting_weight``: a lift with no history starts from a related lift's
   most recent first working set x factor, rounded down to the load step,
   else a "find a working weight" cue. Never a blank weight.

Pure functions. No I/O except reading the bundled JSON. Never writes
sessions, plans, or Turso. Rows whose lift already has logged sets today are
left byte-for-byte as they came in.
"""

from __future__ import annotations

import json
import logging
import math
import re
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

_LOG = logging.getLogger("fitdash.catalog_guard")

GUARD_PATH = "fitness/exercises/catalog_guard.json"
GUARD_MARK = "_catalog_guard"

DEFAULT_NO_HISTORY_TEXT = "Find a working weight: RPE 7 (about 3 reps left)"
DEFAULT_RPE_CUE = "about 2-3 reps in reserve (RPE 7-8)"
DEFAULT_LABEL = "Starting estimate from {lift}"
DEFAULT_REMOVED = "removed: {name} (not in equipment library)"

_CACHE: Dict[str, Any] = {"mtime": None, "data": None}


def _norm(name: Any) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def load_guard_config() -> dict:
    """Bundled guard data. Missing or broken file -> {} (guard is a no-op)."""
    from .workout_planner import _bundled_path

    path = _bundled_path(GUARD_PATH)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _CACHE["mtime"] == mtime and isinstance(_CACHE["data"], dict):
        return _CACHE["data"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _LOG.warning("catalog_guard: unreadable %s", path)
        return {}
    if not isinstance(data, dict):
        return {}
    _CACHE.update(mtime=mtime, data=data)
    return data


def _cfg(config: Optional[dict]) -> dict:
    return config if isinstance(config, dict) else load_guard_config()


# ---------------------------------------------------------------- catalog


def apply_catalog_guard(catalog: Optional[dict], config: Optional[dict] = None) -> dict:
    """Catalog with guard data merged. Idempotent; the input is not mutated."""
    cfg = _cfg(config)
    cat = deepcopy(catalog) if isinstance(catalog, dict) else {"exercises": []}
    rows = [r for r in (cat.get("exercises") or []) if isinstance(r, dict)]
    if not cfg or cat.get(GUARD_MARK):
        cat["exercises"] = rows
        return cat
    entries = {
        str(e.get("id")): e
        for e in (cfg.get("exercises") or [])
        if isinstance(e, dict) and e.get("id")
    }
    ids = {str(r.get("id") or "") for r in rows}

    # 1) Legacy ids fold into the canonical id (logs carry the canonical name).
    legacy = cfg.get("legacy_ids") or {}
    out: List[dict] = []
    for row in rows:
        rid = str(row.get("id") or "")
        canon = legacy.get(rid)
        if not canon:
            out.append(row)
            continue
        if canon in ids:
            continue  # canonical row already present: drop the legacy duplicate
        fixed = dict(row)
        fixed["id"] = canon
        if canon in entries and entries[canon].get("name"):
            fixed["name"] = entries[canon]["name"]
        fixed["legacy_id"] = rid
        aliases = list(fixed.get("aliases") or [])
        if row.get("name") and row.get("name") != fixed.get("name"):
            aliases.append(str(row["name"]))
        fixed["aliases"] = aliases
        out.append(fixed)
        ids.add(canon)
    rows = out

    # 2) Inject canonical rows that are still missing.
    for eid, entry in entries.items():
        if eid in ids or not entry.get("inject_if_missing"):
            continue
        row = {k: deepcopy(v) for k, v in entry.items() if k != "inject_if_missing"}
        row["injected_by_guard"] = True
        rows.append(row)
        ids.add(eid)

    by_id = {str(r.get("id") or ""): r for r in rows}

    # 3) Field overlay (+ copy fields from a sibling, e.g. Smith incline).
    for eid, fields in (cfg.get("fields") or {}).items():
        row = by_id.get(eid)
        if row is None or not isinstance(fields, dict):
            continue
        copy = fields.get("copy_from") if isinstance(fields.get("copy_from"), dict) else None
        if copy:
            src = by_id.get(str(copy.get("id") or ""))
            if src is not None:
                for key in copy.get("fields") or []:
                    if key in src:
                        row[key] = deepcopy(src[key])
                row["copied_from"] = str(copy.get("id"))
        for key, val in fields.items():
            if key == "copy_from":
                continue
            if key == "aliases":
                merged = list(row.get("aliases") or [])
                for a in val or []:
                    if _norm(a) not in {_norm(x) for x in merged}:
                        merged.append(a)
                row["aliases"] = merged
            else:
                row[key] = deepcopy(val)

    # 4) Related-lift relations as data on the row.
    for eid, rels in (cfg.get("related_lifts") or {}).items():
        row = by_id.get(eid)
        if row is None:
            continue
        row["related_lifts"] = [
            {"related_lift": str(r.get("related_lift")), "factor": float(r.get("factor"))}
            for r in rels or []
            if isinstance(r, dict) and r.get("related_lift") and r.get("factor") is not None
        ]

    cat["exercises"] = rows
    cat[GUARD_MARK] = True
    return cat


def _index(catalog: Optional[dict]) -> Dict[str, dict]:
    return {
        str(r["id"]): r
        for r in ((catalog or {}).get("exercises") or [])
        if isinstance(r, dict) and r.get("id")
    }


def resolve_catalog_id(name: Any, catalog: Optional[dict], row_id: Any = None) -> Optional[str]:
    """Catalog id for a planned name: id, exact name, declared alias, or NAME_ALIASES."""
    from .workout_planner import NAME_ALIASES, _slug

    by_id = _index(catalog)
    if not by_id:
        return None
    rid = str(row_id or "").strip()
    key = _norm(name)
    if rid and rid in by_id and (not key or _norm(by_id[rid].get("name")) == key):
        return rid
    if not key:
        return rid if rid in by_id else None
    for eid, ex in by_id.items():
        if _norm(ex.get("name")) == key:
            return eid
    for eid, ex in by_id.items():
        if key in {_norm(a) for a in (ex.get("aliases") or [])}:
            return eid
    alias = NAME_ALIASES.get(key)
    if alias and alias in by_id:
        return alias
    slug = _slug(str(name))
    if slug in by_id:
        return slug
    legacy = (load_guard_config().get("legacy_ids") or {})
    canon = legacy.get(slug) or legacy.get(rid)
    if canon and canon in by_id:
        return canon
    if rid and rid in by_id:
        return rid
    return None


# ------------------------------------------------------------ substitutes


def _family(ex: dict) -> Optional[str]:
    from .workout_planner import pattern_family

    explicit = str(ex.get("movement_pattern") or "").strip().lower()
    if explicit:
        return explicit
    return pattern_family(ex)


# Hip isolations carry an explicit ``movement_pattern`` in the catalog.
# An unknown planned row maps to the same pattern by primary muscle (or by
# name when it has no muscles), so a swap never crosses muscles: adduction
# only to adductor rows, abduction only to abductor rows, flexion only to
# hip-flexor rows.
HIP_PATTERN_BY_MUSCLE = {
    "adductors": "hip_adduction",
    "abductors": "hip_abduction",
    "hip_flexors": "hip_flexion",
}
_HIP_NAME_TOKENS = (
    ("abduct", "hip_abduction"),
    ("adduct", "hip_adduction"),
    ("copenhagen", "hip_adduction"),
    ("hip flexion", "hip_flexion"),
    ("hip flexor", "hip_flexion"),
    ("knee raise", "hip_flexion"),
    ("march", "hip_flexion"),
)


def _hip_family(name: str, prim: Sequence[Any]) -> Optional[str]:
    from .workout_planner import normalize_muscle

    fams = {
        HIP_PATTERN_BY_MUSCLE[m]
        for m in (normalize_muscle(str(p)) for p in prim or [])
        if m in HIP_PATTERN_BY_MUSCLE
    }
    if len(fams) == 1:
        return fams.pop()
    if prim:
        return None
    for token, fam in _HIP_NAME_TOKENS:
        if token in name:
            return fam
    return None


def _guess_family(row: dict) -> Optional[str]:
    """Pattern for a name that is not in the catalog (no id to look up)."""
    from .workout_planner import pattern_family

    name = str(row.get("name") or "")
    prim = list(row.get("primary_muscles") or [])
    low = _norm(name)
    hip = _hip_family(low, prim)
    if hip:
        return hip
    if not prim:
        if any(t in low for t in ("bench", "chest press", "floor press", "fly")):
            prim = ["chest"]
        elif any(t in low for t in ("shoulder press", "overhead", "ohp")):
            prim = ["delts"]
        elif any(t in low for t in ("row", "pulldown", "pull-up", "pullup", "chin")):
            prim = ["lats"]
        elif any(t in low for t in ("squat", "leg press", "lunge")):
            prim = ["quads"]
        elif "curl" in low and "leg" in low:
            prim = ["hamstrings"]
    return pattern_family(
        {"name": name, "movement": row.get("movement") or "compound", "primary_muscles": prim}
    )


def _tagset(ex: dict) -> Set[str]:
    from .workout_planner import movement_required_tags

    return set(movement_required_tags(ex))


def _has_history(sessions: Sequence[Any], ex: dict, by_id: Dict[str, dict]) -> Optional[dict]:
    from .workout_planner import last_performance

    try:
        return last_performance(list(sessions or []), str(ex.get("name") or ""), by_id or None)
    except Exception:  # noqa: BLE001
        return None


def find_substitute(
    row: dict,
    catalog: dict,
    sessions: Sequence[Any],
    *,
    equipment: Optional[dict] = None,
    exclude_ids: Iterable[str] = (),
) -> Optional[dict]:
    """Closest available catalog lift with the same movement pattern.

    Same pattern is required. Then prefer the same equipment, a lift with
    history, the most recent log, and catalog priority.
    """
    from .workout_planner import available_exercises, movement_feasible

    fam = _guess_family(row)
    if not fam:
        return None
    by_id = _index(catalog)
    excl = set(exclude_ids)
    want_tags: Set[str] = set()
    low = _norm(row.get("name"))
    if "smith" in low:
        want_tags = {"smith_machine"}
    elif "barbell" in low:
        want_tags = {"barbell"}
    elif low.startswith("db ") or "dumbbell" in low:
        want_tags = {"dumbbells"}
    scored = []
    for ex in available_exercises(catalog):
        eid = str(ex.get("id"))
        if eid in excl:
            continue
        full = by_id.get(eid, ex)
        if _family(full) != fam:
            continue
        if isinstance(equipment, dict) and not movement_feasible(ex, equipment):
            continue
        last = _has_history(sessions, full, by_id)
        same_eq = 1 if (want_tags and want_tags & _tagset(ex)) else 0
        scored.append(
            (same_eq, 1 if last else 0, str((last or {}).get("date") or ""), int(ex.get("priority") or 0), full)
        )
    if not scored:
        return None
    scored.sort(key=lambda t: t[:4], reverse=True)
    return scored[0][4]


# --------------------------------------------------------- starting weight


def _round_down(value: float, step: float) -> float:
    step = float(step) if step and step > 0 else 5.0
    return round(math.floor(float(value) / step + 1e-9) * step, 1)


def _step_for(ex: dict, equipment: Optional[dict]) -> float:
    from .workout_planner import load_step_lbs

    try:
        explicit = float(ex.get("load_step_lbs"))
        if explicit > 0:
            return explicit
    except (TypeError, ValueError):
        pass
    return float(load_step_lbs(ex, None, equipment))


def starting_weight(
    catalog_ex: dict,
    catalog: dict,
    sessions: Sequence[Any],
    *,
    equipment: Optional[dict] = None,
    config: Optional[dict] = None,
) -> dict:
    """Starting load for a lift with no history of its own.

    Returns ``{"weight_lbs": float|None, "source_lift": str|None, "factor",
    "basis", "target_reps", "label", "cue", "text"}``. ``weight_lbs`` is None
    only when no related lift has history; ``text`` then carries the cue.
    """
    from .workout_planner import _judged_set, progression_band

    cfg = _cfg(config)
    sw = cfg.get("starting_weight") or {}
    lo, _hi = progression_band(catalog_ex)
    target = int(lo) + int(sw.get("reps_above_band_bottom", 2))
    cue = str(sw.get("rpe_cue") or DEFAULT_RPE_CUE)
    label_tmpl = str(sw.get("label") or DEFAULT_LABEL)
    by_id = _index(catalog)
    step = _step_for(catalog_ex, equipment)
    self_id = str(catalog_ex.get("id") or "")

    rels = catalog_ex.get("related_lifts")
    if rels is None:
        rels = (cfg.get("related_lifts") or {}).get(self_id) or []
    candidates: List[tuple] = []
    for rel in rels:
        rid = str(rel.get("related_lift") or "")
        rex = by_id.get(rid) or {"id": rid, "name": rid.replace("-", " ").title()}
        last = _has_history(sessions, rex, by_id)
        if last:
            candidates.append((rex, float(rel.get("factor")), last, "related"))
            break  # data order is the priority order
    if not candidates:
        gen = cfg.get("generic_fallback") or {}
        factor = float(gen.get("factor", 0.90))
        fam = _family(catalog_ex)
        tags = _tagset(catalog_ex)
        best = None
        for eid, ex in by_id.items():
            if eid == self_id or not fam or _family(ex) != fam or _tagset(ex) != tags:
                continue
            last = _has_history(sessions, ex, by_id)
            if last and (best is None or str(last.get("date")) > str(best[2].get("date"))):
                best = (ex, factor, last, "generic")
        if best:
            candidates.append(best)

    if candidates:
        rex, factor, last, kind = candidates[0]
        base, base_reps = _judged_set(last)
        weight = _round_down(base * factor, step)
        if weight > 0:
            lift = str(rex.get("name") or rex.get("id"))
            return {
                "weight_lbs": weight,
                "source_lift": lift,
                "source_id": str(rex.get("id") or ""),
                "factor": factor,
                "basis": {"date": last.get("date"), "weight_lbs": base, "reps": base_reps},
                "kind": kind,
                "step_lbs": step,
                "target_reps": target,
                "label": label_tmpl.format(lift=lift),
                "cue": cue,
                "text": None,
            }
    return {
        "weight_lbs": None,
        "source_lift": None,
        "source_id": None,
        "factor": None,
        "basis": None,
        "kind": "find_weight",
        "step_lbs": step,
        "target_reps": target,
        "label": None,
        "cue": cue,
        "text": str(sw.get("no_history_text") or DEFAULT_NO_HISTORY_TEXT),
    }


# -------------------------------------------------------------- the guard


def logged_names_for_day(sessions: Sequence[Any], day: Optional[str]) -> Set[str]:
    """Normalized names of lifts with real logged sets on ``day``."""
    key = str(day or "")[:10]
    out: Set[str] = set()
    if not key:
        return out
    for s in sessions or []:
        d = s.get("date") if isinstance(s, dict) else getattr(s, "date", None)
        if str(d or "")[:10] != key:
            continue
        exs = s.get("exercises") if isinstance(s, dict) else getattr(s, "exercises", None)
        for ex in exs or []:
            name = ex.get("name") if isinstance(ex, dict) else getattr(ex, "name", "")
            sets = ex.get("sets") if isinstance(ex, dict) else getattr(ex, "sets", None)
            if name and sets:
                out.add(_norm(name))
    return out


def _weight_of(row: dict) -> Any:
    rx = row.get("prescription") if isinstance(row.get("prescription"), dict) else {}
    w = rx.get("weight_lbs")
    return row.get("weight_lbs") if w is None else w


def _is_assisted(ex: dict) -> bool:
    from .workout_planner import catalog_is_assisted

    try:
        return bool(catalog_is_assisted(ex))
    except Exception:  # noqa: BLE001
        return False


def _apply_start(row: dict, cat_ex: dict, catalog: dict, sessions, equipment, config) -> None:
    from .workout_planner import progression_band, rep_range_label

    start = starting_weight(cat_ex, catalog, sessions, equipment=equipment, config=config)
    rx = dict(row.get("prescription") or {})
    reps = int(start["target_reps"])
    lo, hi = progression_band(cat_ex)
    for target in (row, rx):
        target["weight_lbs"] = start["weight_lbs"]
        target["load"] = start["weight_lbs"]
        target["reps"] = reps
        target["target_reps"] = reps
        target["rpe_cue"] = start["cue"]
        target["rep_range"] = rep_range_label((lo, hi)) if target is row else [lo, hi]
    rx["rep_range_label"] = rep_range_label((lo, hi))
    if start["weight_lbs"] is not None:
        reason = (
            f"{start['label']}: {start['basis']['weight_lbs']:g}×{start['basis']['reps']} "
            f"on {start['basis']['date']} ×{start['factor']:.2f} → {start['weight_lbs']:g} lb "
            f"(rounded down to {start['step_lbs']:g} lb), aim {reps}, {start['cue']}."
        )
        source = "related_estimate"
        row.pop("weight_text", None)
        rx.pop("weight_text", None)
    else:
        reason = f"{start['text']}, aim {reps}."
        source = "find_weight"
        row["weight_text"] = start["text"]
        rx["weight_text"] = start["text"]
    for target in (row, rx):
        target["load_source"] = source
        target["load_reason"] = reason
        # Still a seed for double progression; load_source says how it started.
        target["progression_reason"] = "seed"
        target["starting_estimate"] = {
            k: start[k] for k in ("source_lift", "source_id", "factor", "basis", "kind", "step_lbs")
        }
    row["rationale"] = reason
    row["prescription"] = rx


def guard_plan(
    plan: Optional[dict],
    catalog: Optional[dict],
    sessions: Sequence[Any],
    *,
    equipment: Optional[dict] = None,
    as_of: Optional[str] = None,
    config: Optional[dict] = None,
) -> Optional[dict]:
    """Every row resolves to a catalog id; no blank weight. In-memory only.

    Rows whose lift already has logged sets on ``as_of`` are returned as-is.
    """
    if not isinstance(plan, dict):
        return plan
    exercises = plan.get("exercises")
    if not isinstance(exercises, list) or not exercises:
        return plan
    cfg = _cfg(config)
    cat = apply_catalog_guard(catalog, cfg)
    by_id = _index(cat)
    if not by_id:
        return plan
    logged = logged_names_for_day(sessions, as_of)
    removed_tmpl = str(cfg.get("removed_note") or DEFAULT_REMOVED)
    out_rows: List[dict] = []
    removed: List[dict] = []
    substituted: List[dict] = []
    used: Set[str] = set()
    for raw in exercises:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("exercise") or "").strip()
        if _norm(name) in logged:
            out_rows.append(raw)  # already logged today: never touched
            continue
        row = dict(raw)
        cid = resolve_catalog_id(name, cat, row.get("id"))
        if cid is None:
            sub = find_substitute(row, cat, sessions, equipment=equipment, exclude_ids=used)
            if sub is None:
                note = removed_tmpl.format(name=name or "?")
                _LOG.warning("catalog_guard: %s", note)
                removed.append({"name": name, "note": note})
                continue
            _LOG.warning(
                "catalog_guard: substituted %r -> %r (same movement pattern)", name, sub.get("name")
            )
            from .workout_planner import prescribe

            last = _has_history(sessions, sub, by_id)
            rx = prescribe(sub, last, equipment=equipment, as_of=as_of)
            keep = {k: row[k] for k in ("sets",) if row.get(k) is not None}
            row = {
                "id": sub["id"],
                "name": sub["name"],
                "primary_muscles": list(sub.get("primary_muscles") or []),
                "secondary_muscles": list(sub.get("secondary_muscles") or []),
                "movement": sub.get("movement"),
                "equipment": sub.get("equipment"),
                "weight_lbs": rx.get("weight_lbs"),
                "load": rx.get("weight_lbs"),
                "sets": keep.get("sets") or rx.get("sets"),
                "reps": rx.get("reps"),
                "target_reps": rx.get("target_reps"),
                "rep_range": rx.get("rep_range_label"),
                "progression_reason": rx.get("progression_reason"),
                "rationale": f"Substituted for {name} (not in the catalog). {rx.get('rationale') or ''}".strip(),
                "last": rx.get("last"),
                "prescription": {
                    k: rx.get(k)
                    for k in ("weight_lbs", "load", "sets", "reps", "target_reps", "rep_range", "rep_range_label", "progression_reason")
                },
                "substituted": True,
                "original_name": name,
            }
            if keep.get("sets"):
                row["prescription"]["sets"] = keep["sets"]
            substituted.append({"from": name, "to": sub["name"]})
            cid = sub["id"]
        cat_ex = by_id[cid]
        row["catalog_id"] = cid
        if not row.get("id"):
            row["id"] = cid
        canon = str(cat_ex.get("name") or name)
        if _norm(canon) != _norm(name) and not row.get("substituted"):
            row["planned_as"] = name
            row["name"] = canon
            row["id"] = cid
        used.add(cid)
        if _weight_of(row) is None and not _is_assisted(cat_ex):
            last = _has_history(sessions, cat_ex, by_id)
            if last is None:
                _apply_start(row, cat_ex, cat, sessions, equipment, cfg)
            else:
                # History exists but no load came through: show the cue, never blank.
                text = str((cfg.get("starting_weight") or {}).get("no_history_text") or DEFAULT_NO_HISTORY_TEXT)
                row["weight_text"] = text
                rx = dict(row.get("prescription") or {})
                rx["weight_text"] = text
                row["prescription"] = rx
        out_rows.append(row)
    plan = dict(plan)
    plan["exercises"] = out_rows
    if removed or substituted:
        guard = {"removed": removed, "substituted": substituted}
        plan["catalog_guard"] = guard
        notes = [r["note"] for r in removed] + [
            f"substituted: {s['from']} → {s['to']}" for s in substituted
        ]
        msg = str(plan.get("message") or "")
        plan["message"] = (msg + " · " if msg else "") + " · ".join(notes)
        ctx = plan.get("context")
        if isinstance(ctx, dict):
            ctx = dict(ctx)
            ctx["catalog_guard"] = guard
            plan["context"] = ctx
    return plan
