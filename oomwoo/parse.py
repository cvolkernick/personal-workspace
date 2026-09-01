"""Parse makerspet/oomwoo README into modules, deliverables, and v0 targets."""

from __future__ import annotations

import re
from typing import Any

STATUS_BUCKETS: dict[str, str] = {
    "mostly complete": "done",
    "complete": "done",
    "done": "done",
    "2gb achieved": "done",
    "in progress": "in_progress",
    "ready to start work": "ready",
    "ready": "ready",
}

STATUS_RANK = {"done": 3, "in_progress": 2, "ready": 1, "unknown": 0}

_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_CHECKBOX = re.compile(r"^[-*]\s+\[([ xX])\]\s+(.+)$")
_BULLET = re.compile(r"^[-*]\s+(.+)$")


def _cells(line: str) -> list[str]:
    raw = line.strip()
    if not raw.startswith("|"):
        return []
    if raw.endswith("|"):
        raw = raw[1:-1]
    else:
        raw = raw[1:]
    return [c.strip() for c in raw.split("|")]


def _first_link(cell: str) -> tuple[str, str]:
    match = _MD_LINK.search(cell or "")
    if not match:
        return (cell or "").strip(), ""
    return match.group(1).strip(), match.group(2).strip()


def _plain(text: str) -> str:
    return _MD_LINK.sub(r"\1", text or "").strip()


def bucket_status(label: str) -> str:
    key = re.sub(r"\s+", " ", (label or "").strip().lower())
    if key in STATUS_BUCKETS:
        return STATUS_BUCKETS[key]
    if "complete" in key or "achieved" in key:
        return "done"
    if "progress" in key:
        return "in_progress"
    if "ready" in key:
        return "ready"
    return "unknown"


def parse_modules(readme: str) -> list[dict[str, Any]]:
    """Parse the Requests for Contributions markdown table.

    Duplicate module IDs keep the more-complete status and merged notes.
    Rows with an empty ID stay distinct (keyed by module title).
    """
    lines = (readme or "").splitlines()
    header_idx = -1
    cols: dict[str, int] = {}
    for i, line in enumerate(lines):
        cells = _cells(line)
        if not cells:
            continue
        lowered = [c.lower() for c in cells]
        if "module" in lowered and "status" in lowered:
            header_idx = i
            cols = {name: idx for idx, name in enumerate(lowered)}
            break
    if header_idx < 0:
        return []

    found: list[dict[str, Any]] = []
    for line in lines[header_idx + 1 :]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if found:
                break
            continue
        cells = _cells(line)
        if not cells or all(re.fullmatch(r":?-{3,}:?", c or "") for c in cells):
            continue
        module_cell = cells[cols.get("module", 0)] if cells else ""
        id_cell = cells[cols["id"]] if "id" in cols and cols["id"] < len(cells) else ""
        status_cell = (
            cells[cols["status"]] if "status" in cols and cols["status"] < len(cells) else ""
        )
        notes_cell = cells[cols["notes"]] if "notes" in cols and cols["notes"] < len(cells) else ""
        title, title_url = _first_link(module_cell)
        slug, slug_url = _first_link(id_cell)
        if not title or title.lower() == "module":
            continue
        status_label = _plain(status_cell)
        item = {
            "title": title,
            "id": slug,
            "url": slug_url or title_url,
            "status_label": status_label,
            "status": bucket_status(status_label),
            "notes": _plain(notes_cell),
        }
        found.append(item)
    return _merge_modules(found)


def _merge_modules(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in items:
        key = (item.get("id") or "").strip() or f"title:{(item.get('title') or '').strip()}"
        prev = merged.get(key)
        if prev is None:
            merged[key] = dict(item)
            order.append(key)
            continue
        if STATUS_RANK.get(item["status"], 0) > STATUS_RANK.get(prev["status"], 0):
            notes = " · ".join(n for n in (prev.get("notes"), item.get("notes")) if n)
            prev.update(item)
            prev["notes"] = notes
        elif item.get("notes") and item.get("notes") not in (prev.get("notes") or ""):
            prev["notes"] = " · ".join(n for n in (prev.get("notes"), item.get("notes")) if n)
    return [merged[k] for k in order]


def parse_deliverables(readme: str) -> list[dict[str, Any]]:
    """Parse `- [x]` / `- [ ]` items under Open Source Deliverables."""
    lines = (readme or "").splitlines()
    start = -1
    for i, line in enumerate(lines):
        if re.search(r"open source deliverables", line, re.I):
            start = i + 1
            break
    if start < 0:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        match = _CHECKBOX.match(stripped)
        if match:
            title, url = _first_link(match.group(2))
            out.append(
                {
                    "title": _plain(match.group(2)),
                    "done": match.group(1).lower() == "x",
                    "url": url,
                }
            )
            continue
        if out and stripped == "":
            # allow a single blank; stop on the next non-list block
            continue
        if out and stripped and not stripped.startswith(("-", "*")):
            break
    return out


def parse_v0_targets(readme: str) -> list[str]:
    lines = (readme or "").splitlines()
    start = -1
    for i, line in enumerate(lines):
        if re.search(r"v0 target", line, re.I):
            start = i + 1
            break
    if start < 0:
        return []
    out: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("## ") or re.search(r"open source deliverables", stripped, re.I):
            break
        match = _BULLET.match(stripped)
        if match:
            out.append(_plain(match.group(1)))
    return out


def summarize(modules: list[dict[str, Any]], deliverables: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"done": 0, "in_progress": 0, "ready": 0, "unknown": 0}
    for mod in modules:
        counts[mod.get("status") or "unknown"] = counts.get(mod.get("status") or "unknown", 0) + 1
    total = len(modules)
    score = 0.0
    if total:
        score = (
            counts["done"] * 1.0 + counts["in_progress"] * 0.5 + counts.get("unknown", 0) * 0.25
        ) / total
    deliv_done = sum(1 for d in deliverables if d.get("done"))
    return {
        "modules_total": total,
        "modules_done": counts["done"],
        "modules_in_progress": counts["in_progress"],
        "modules_ready": counts["ready"],
        "modules_unknown": counts.get("unknown", 0),
        "module_score": round(score, 3),
        "deliverables_total": len(deliverables),
        "deliverables_done": deliv_done,
    }
