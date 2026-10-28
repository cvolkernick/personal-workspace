"""Canonical lab-marker ids, display names, and unit spelling.

Alias lookup only. Measured values are never rewritten here.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from . import biomarkers_config as cfg
from .biomarkers_import import _norm_name, canonical_marker, display_name as _bm_display

# Ids whose canonical form contains a digit. A collapsed row's snake id
# also contains digits; those are NOT in this set, so logs can redact them.
KNOWN_DIGIT_IDS = frozenset({"vitamin_b12", "free_t3", "hba1c", "free_t4"})


def canonical_id(name: str) -> str:
    """Canonical id for a stored id or a display name. Unknown names stay snake_case."""
    return canonical_marker(name)


def display_name(marker: str, fallback: str = "") -> str:
    cid = canonical_id(marker)
    if cid in cfg.MARKERS:
        return cfg.MARKERS[cid][0]
    return _bm_display(cid, fallback)


def unit_key(unit: str) -> str:
    """Spelling key for 'same unit' checks. Does not convert between unit systems."""
    s = str(unit or "").replace("µ", "u").replace("μ", "u").lower()
    return re.sub(r"\s+", "", s)


def _alias_pairs() -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    seen = set()
    for cid, (disp, aliases) in cfg.MARKERS.items():
        for raw in (cid, disp, *aliases):
            phrase = _norm_name(raw)
            if not phrase or (phrase, cid) in seen:
                continue
            seen.add((phrase, cid))
            pairs.append((phrase, cid))
    pairs.sort(key=lambda item: len(item[0]), reverse=True)
    return pairs


_PAIRS = _alias_pairs()


def canonical_ids_in_text(text: str) -> List[str]:
    """Marker ids mentioned in free text, longest alias first, each id once.

    Used to notice a collapsed row ('eGFR … LDL/ApoB') without reading values.
    """
    norm = _norm_name(text)
    if not norm:
        return []
    occupied = [False] * len(norm)
    found: List[Tuple[int, str]] = []
    for phrase, cid in _PAIRS:
        start = 0
        while True:
            i = norm.find(phrase, start)
            if i < 0:
                break
            end = i + len(phrase)
            bounded = (i == 0 or norm[i - 1] == " ") and (end == len(norm) or norm[end] == " ")
            if bounded and not any(occupied[i:end]):
                for j in range(i, end):
                    occupied[j] = True
                found.append((i, cid))
            start = i + 1
    found.sort()
    out: List[str] = []
    for _pos, cid in found:
        if cid not in out:
            out.append(cid)
    return out


def public_marker_id(marker: Dict) -> str:
    """Id safe to print. A collapsed row is the canonical ids in its name, not the raw snake id."""
    name = " ".join(
        str((marker or {}).get(k) or "")
        for k in ("name", "marker_name")
    )
    embedded = canonical_ids_in_text(name)
    if len(embedded) >= 2:
        return "+".join(embedded)
    raw = str((marker or {}).get("id") or (marker or {}).get("marker") or name)
    cid = canonical_id(raw)
    if cid in cfg.MARKERS or cid in KNOWN_DIGIT_IDS or not re.search(r"\d", cid):
        return cid or "unknown"
    if embedded:
        return "+".join(embedded)
    return "collapsed"
