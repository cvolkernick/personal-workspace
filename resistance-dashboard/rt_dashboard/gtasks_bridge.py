"""Bridge FitDash → monorepo Google Tasks client (projects-dashboard/google_tasks.py).

Uses the same OAuth files as google-tasks-mcp:
  ~/.config/google-tasks-mcp/{token,client_secret}.json

Vercel Root Directory is resistance-dashboard/, so a byte-identical copy
ships at resistance-dashboard/projects-dashboard/google_tasks.py (includeFiles).
Nest SoT (parents[2]) still wins on Pi when that file exists.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional

_REL = Path("projects-dashboard") / "google_tasks.py"
_MOD_NAME = "fitdash_google_tasks_mod"


def _google_tasks_candidates() -> list[Path]:
    """Nest SoT first, then the Vercel-bundled copy under resistance-dashboard/."""
    here = Path(__file__).resolve()
    ordered: list[Path] = []
    # rt_dashboard/gtasks_bridge.py → parents[2] = repo root (Pi nest)
    if len(here.parents) >= 3:
        ordered.append(here.parents[2] / _REL)
    # parents[1] = resistance-dashboard (Vercel project / function root)
    if len(here.parents) >= 2:
        ordered.append(here.parents[1] / _REL)
    cwd = Path.cwd().resolve()
    ordered.append(cwd / _REL)
    for parent in cwd.parents:
        ordered.append(parent / _REL)
    seen: set[Path] = set()
    out: list[Path] = []
    for cand in ordered:
        try:
            resolved = cand.resolve()
        except OSError:
            continue
        if resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
    return out


def _exec_google_tasks(path: Path):
    if _MOD_NAME in sys.modules:
        return sys.modules[_MOD_NAME]
    spec = importlib.util.spec_from_file_location(_MOD_NAME, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MOD_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


def _import_google_tasks_from_sys_path():
    """PYTHONPATH fallback (e.g. projects-dashboard on sys.path)."""
    if "google_tasks" in sys.modules:
        return sys.modules["google_tasks"]
    try:
        return importlib.import_module("google_tasks")
    except ImportError:
        return None


def load_google_tasks():
    """Load projects-dashboard/google_tasks without requiring package install."""
    if _MOD_NAME in sys.modules:
        return sys.modules[_MOD_NAME]
    for path in _google_tasks_candidates():
        if path.is_file():
            return _exec_google_tasks(path)
    imported = _import_google_tasks_from_sys_path()
    if imported is not None:
        sys.modules[_MOD_NAME] = imported
        return imported
    raise FileNotFoundError(
        "google_tasks.py not found in nest (projects-dashboard/) "
        "or FitDash bundle (resistance-dashboard/projects-dashboard/)"
    )


def credentials_status() -> dict[str, Any]:
    try:
        gt = load_google_tasks()
        status = gt.credentials_status()
        if not isinstance(status, dict):
            return {"ok": False, "error": "Google Tasks not configured"}
        if not status.get("ok") and not status.get("error"):
            return {
                **status,
                "error": status.get("hint") or "Google Tasks not configured",
            }
        return status
    except Exception as e:
        return {"ok": False, "error": str(e)}


def resolve_list_id(title: str = "Fitness") -> Optional[str]:
    """Find a task list by title (case-insensitive)."""
    gt = load_google_tasks()
    payload = gt.list_tasklists()
    if not payload.get("ok"):
        return None
    want = (title or "").strip().lower()
    for tl in payload.get("lists") or []:
        if str(tl.get("title") or "").strip().lower() == want:
            return str(tl.get("id") or "") or None
    return None


def list_tasks(
    list_id: str, *, show_completed: bool = True, show_hidden: bool = True
) -> dict[str, Any]:
    gt = load_google_tasks()
    # projects-dashboard list_tasks signature
    return gt.list_tasks(
        list_id,
        show_completed=show_completed,
        show_hidden=show_hidden,
    )


def create_task(
    list_id: str,
    title: str,
    *,
    notes: str = "",
    due: Optional[str] = None,
    parent: Optional[str] = None,
) -> dict[str, Any]:
    gt = load_google_tasks()
    return gt.create_task(
        list_id, title, notes=notes, due=due, parent=parent
    )


def complete_task(
    list_id: str, task_id: str, *, completed: bool = True
) -> dict[str, Any]:
    gt = load_google_tasks()
    return gt.complete_task(list_id, task_id, completed=completed)


def delete_task(list_id: str, task_id: str) -> dict[str, Any]:
    gt = load_google_tasks()
    return gt.delete_task(list_id, task_id)
