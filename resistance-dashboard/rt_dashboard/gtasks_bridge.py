"""Bridge FitDash → monorepo Google Tasks client (projects-dashboard/google_tasks.py).

Uses the same OAuth files as google-tasks-mcp:
  ~/.config/google-tasks-mcp/{token,client_secret}.json
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional


def _workspace_root() -> Path:
    # resistance-dashboard/rt_dashboard/this → personal-workspace
    return Path(__file__).resolve().parents[2]


def load_google_tasks():
    """Load projects-dashboard/google_tasks without requiring package install."""
    path = _workspace_root() / "projects-dashboard" / "google_tasks.py"
    if not path.is_file():
        raise FileNotFoundError(f"google_tasks.py not found at {path}")
    name = "fitdash_google_tasks_mod"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def credentials_status() -> dict[str, Any]:
    try:
        gt = load_google_tasks()
        return gt.credentials_status()
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
