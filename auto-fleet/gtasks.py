"""Load the nest Google Tasks client (same prism path FitDash-on-Pi uses).

Fleet is Pi/intranet. Creds stay in ~/.config/google-tasks-mcp/ via
projects-dashboard/google_tasks.py. Do not put GOOGLE_TASKS_* on Vercel.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

_REL = Path("projects-dashboard") / "google_tasks.py"
_MOD_NAME = "auto_fleet_google_tasks_mod"


def _google_tasks_candidates() -> list[Path]:
    """Nest SoT first (Pi), then the FitDash in-repo bundle."""
    here = Path(__file__).resolve()
    ordered: list[Path] = []
    # auto-fleet/gtasks.py → parents[1] = repo root
    if len(here.parents) >= 2:
        root = here.parents[1]
        ordered.append(root / _REL)
        ordered.append(root / "resistance-dashboard" / _REL)
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
        if not status.get("ok"):
            err = status.get("error") or status.get("hint") or "Google Tasks not configured"
            if "Google Tasks" not in err:
                err = f"Google Tasks not configured — {err}"
            return {**status, "error": err}
        return status
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
