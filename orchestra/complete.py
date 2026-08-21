"""Source write-back for Orchestra NOW/NEXT checkboxes.

No Orchestra-local task list. Completing a checkable item writes the Time
Allocator store or the FitDash/Google Tasks complete path already used on
Pi. Items without a real source id are a no-op.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

TA_SOURCE = "time_allocator"
FITDASH_SOURCE = "fitdash"
GT_ID_KEYS = ("gt_task_id", "google_task_id", "gt_id")
LIST_ID_KEYS = ("list_id", "gt_list_id", "google_list_id")

_ORCH = Path(__file__).resolve().parent
_ROOT = _ORCH.parent


def _first_text(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        raw = item.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return ""


def writable_source(item: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Honest write-back handle. None unless a real source id is present."""
    if not isinstance(item, dict):
        return None
    source = str(item.get("source") or "").strip()
    source_id = str(item.get("source_id") or "").strip()
    if not source_id:
        source_id = _first_text(item, GT_ID_KEYS)
        if source_id and not source:
            source = FITDASH_SOURCE
    list_id = _first_text(item, LIST_ID_KEYS)
    if source == TA_SOURCE and source_id:
        return {
            "source": TA_SOURCE,
            "source_id": source_id[:80],
            "checkable": True,
        }
    if source == FITDASH_SOURCE and source_id and list_id:
        return {
            "source": FITDASH_SOURCE,
            "source_id": source_id[:80],
            "list_id": list_id,
            "checkable": True,
        }
    return None


def is_checkable(item: Optional[dict[str, Any]]) -> bool:
    ref = writable_source(item)
    return bool(ref and ref.get("checkable"))


def _ta_data_path(workspace: Path) -> Path:
    return Path(workspace) / "holistic" / "data" / "tasks.json"


def _complete_ta(source_id: str, *, workspace: Path) -> dict[str, Any]:
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from holistic.time_allocator.domain import apply_plan, complete_block
    from holistic.time_allocator.store import load_state, save_state

    path = _ta_data_path(workspace)
    state = load_state(path)
    state, result = complete_block(state, source_id)
    if not result.get("accepted"):
        return result
    state = apply_plan(state)
    save_state(state, path)
    return result


def _complete_fitdash_quest(
    list_id: str,
    task_id: str,
    *,
    workspace: Path,
) -> dict[str, Any]:
    rd = Path(workspace) / "resistance-dashboard"
    if rd.is_dir() and str(rd) not in sys.path:
        sys.path.insert(0, str(rd))
    from rt_dashboard.daily_plan_tasks import complete_leaf

    return complete_leaf(list_id, task_id, completed=True)


def complete_item(
    item: Optional[dict[str, Any]],
    *,
    workspace: Optional[Path] = None,
) -> dict[str, Any]:
    """Complete a NOW/NEXT item in its source. No source id → no-op."""
    ws = Path(workspace) if workspace is not None else _ROOT
    if not isinstance(item, dict):
        return {"ok": False, "accepted": False, "error": "no source id"}
    # Body may be the raw POST ({source, source_id}) or a pulse row.
    ref = writable_source(item)
    if ref is None:
        return {"ok": False, "accepted": False, "error": "no source id"}
    source = ref["source"]
    source_id = ref["source_id"]
    try:
        if source == TA_SOURCE:
            result = _complete_ta(source_id, workspace=ws)
        elif source == FITDASH_SOURCE:
            result = _complete_fitdash_quest(
                str(ref.get("list_id") or ""),
                source_id,
                workspace=ws,
            )
        else:
            return {
                "ok": False,
                "accepted": False,
                "error": f"source {source} cannot write back",
            }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "accepted": False, "error": str(e), "source": source, "source_id": source_id}

    accepted = bool(result.get("ok") and result.get("accepted", result.get("ok")))
    out = dict(result)
    out["ok"] = bool(result.get("ok"))
    out["accepted"] = accepted
    out.setdefault("source", source)
    out.setdefault("source_id", source_id)
    return out
