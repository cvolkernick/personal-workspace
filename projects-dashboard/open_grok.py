"""Open a specific named Grok Build session in macOS Terminal.

Resolves the Workflow Management session by pinned ID and/or title match
(not ``--continue``, which only opens the most recent cwd session).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any, Optional

from workspace import WORKSPACE_ROOT

LAUNCH_DIR = WORKSPACE_ROOT / "ops" / "backlog"
LAUNCH_SCRIPT = LAUNCH_DIR / "open-workflow-grok.launch.sh"
PROMPT_FILE = LAUNCH_DIR / "open-workflow-grok.prompt.txt"
CONFIG_PATH = LAUNCH_DIR / "workflow-session.json"

# Default pin: title-matched session used for this dashboard.
DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "session_name": "Workflow Management",
    "session_id": "019f6e82-4398-72d0-b180-58f68680ae23",
    "match_titles": ["Workflow Management"],
}

DEFAULT_NEW_PROMPT = textwrap.dedent(
    """\
    /goal Continue Workflow Management for personal-workspace.

    You are the operator for the Workflow Management dashboard at
    projects-dashboard/ (http://127.0.0.1:8765/). Scope:

    - Backlog groom / schedule / autonomous kickoff (scheduler + cron)
    - Status reports under ops/backlog/reports/
    - Protect & push, readiness, recommendations
    - Self-improving autonomous loop: enter work → groom → queue → tick → report

    Use the repo at personal-workspace as cwd. Prefer small durable changes,
    commit/push when work should persist, and keep the dashboard usable.

    Please /rename Workflow Management if this session title is not already set.
    """
).strip()


def _bash_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _which_grok() -> Optional[str]:
    found = shutil.which("grok")
    if found:
        return found
    home = Path.home() / ".grok" / "bin" / "grok"
    if home.is_file() and os.access(home, os.X_OK):
        return str(home)
    return None


def _grok_home() -> Path:
    env = os.environ.get("GROK_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".grok"


def _sessions_root() -> Path:
    return _grok_home() / "sessions"


def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.is_file():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg.update(raw)
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
    out = dict(DEFAULT_CONFIG)
    out.update(cfg or {})
    CONFIG_PATH.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out


def _read_summary(summary_path: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _find_summary_for_id(session_id: str) -> Optional[Path]:
    root = _sessions_root()
    if not root.is_dir() or not session_id:
        return None
    # Fast path: any cwd group
    for p in root.glob(f"*/{session_id}/summary.json"):
        if p.is_file():
            return p
    # Nested (unlikely for parent sessions)
    for p in root.glob(f"*/*/{session_id}/summary.json"):
        if p.is_file():
            return p
    return None


def _session_titles(summary: dict[str, Any]) -> list[str]:
    titles = []
    for key in ("generated_title", "session_summary", "title", "name"):
        val = summary.get(key)
        if isinstance(val, str) and val.strip():
            titles.append(val.strip())
    return titles


def find_session_by_title(
    names: list[str],
    *,
    prefer_cwd: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Find a non-subagent session whose title matches one of ``names`` (case-insensitive)."""
    root = _sessions_root()
    if not root.is_dir():
        return None
    wanted = [n.strip().lower() for n in names if n and str(n).strip()]
    if not wanted:
        return None

    candidates: list[tuple[str, dict[str, Any], Path]] = []
    for summary_path in root.glob("*/*/summary.json"):
        summary = _read_summary(summary_path)
        if not summary:
            continue
        if (summary.get("session_kind") or "parent") == "subagent":
            continue
        titles = _session_titles(summary)
        titles_l = [t.lower() for t in titles]
        # Exact match first priority via sort key
        exact = any(t in wanted for t in titles_l)
        partial = any(any(w in t or t in w for w in wanted) for t in titles_l)
        if not exact and not partial:
            continue
        info = summary.get("info") if isinstance(summary.get("info"), dict) else {}
        sid = info.get("id") or summary_path.parent.name
        updated = str(
            summary.get("updated_at")
            or summary.get("last_active_at")
            or summary.get("created_at")
            or ""
        )
        cwd = info.get("cwd") or ""
        entry = {
            "session_id": sid,
            "cwd": cwd,
            "title": titles[0] if titles else sid,
            "titles": titles,
            "updated_at": updated,
            "exact": exact,
            "summary_path": str(summary_path),
        }
        candidates.append((updated, entry, summary_path))

    if not candidates:
        return None

    def sort_key(item: tuple[str, dict[str, Any], Path]) -> tuple:
        updated, entry, _ = item
        cwd_match = 0
        if prefer_cwd and entry.get("cwd"):
            cwd_match = 1 if Path(entry["cwd"]).resolve() == Path(prefer_cwd).resolve() else 0
            # also prefer parent of workspace (e.g. home when workspace is under home)
            if not cwd_match and str(prefer_cwd).startswith(str(entry.get("cwd") or "") + os.sep):
                cwd_match = 1
        return (
            1 if entry.get("exact") else 0,
            cwd_match,
            updated,
        )

    candidates.sort(key=sort_key, reverse=True)
    return candidates[0][1]


def resolve_workflow_session(
    *,
    session_id: Optional[str] = None,
    session_name: Optional[str] = None,
) -> dict[str, Any]:
    """Resolve which session Open Grok should resume."""
    cfg = load_config()
    name = (session_name or cfg.get("session_name") or "Workflow Management").strip()
    match_titles = list(cfg.get("match_titles") or [])
    if name and name not in match_titles:
        match_titles = [name] + match_titles

    sid = (session_id or cfg.get("session_id") or "").strip()
    resolved: Optional[dict[str, Any]] = None

    if sid:
        sp = _find_summary_for_id(sid)
        if sp:
            summary = _read_summary(sp) or {}
            info = summary.get("info") if isinstance(summary.get("info"), dict) else {}
            titles = _session_titles(summary)
            resolved = {
                "session_id": info.get("id") or sid,
                "cwd": info.get("cwd") or str(WORKSPACE_ROOT),
                "title": titles[0] if titles else name,
                "titles": titles,
                "updated_at": summary.get("updated_at"),
                "source": "pinned_id",
                "summary_path": str(sp),
            }

    if not resolved:
        found = find_session_by_title(
            match_titles,
            prefer_cwd=str(WORKSPACE_ROOT),
        )
        if found:
            found["source"] = "title_match"
            resolved = found

    if not resolved:
        return {
            "ok": False,
            "error": (
                f"No Grok session found for “{name}”. "
                f"Open Grok, run /rename {name}, then set session_id in "
                f"{CONFIG_PATH.relative_to(WORKSPACE_ROOT)} "
                f"or retry after renaming."
            ),
            "session_name": name,
            "config": cfg,
        }

    # Persist pin so later opens are stable even if title drifts
    if resolved.get("session_id") and resolved["session_id"] != cfg.get("session_id"):
        cfg["session_id"] = resolved["session_id"]
        cfg["session_name"] = name
        save_config(cfg)

    return {
        "ok": True,
        "session_id": resolved["session_id"],
        "session_name": name,
        "title": resolved.get("title"),
        "cwd": resolved.get("cwd") or str(WORKSPACE_ROOT),
        "source": resolved.get("source"),
        "updated_at": resolved.get("updated_at"),
        "config": load_config(),
    }


def write_launch_script(
    *,
    session_id: str,
    cwd: str,
    session_name: str = "Workflow Management",
) -> dict[str, Any]:
    """Write Terminal launch script that resumes a specific session ID."""
    LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
    PROMPT_FILE.write_text(DEFAULT_NEW_PROMPT + "\n", encoding="utf-8")

    # Prefer session's recorded cwd; fall back to workspace for file ops context
    run_cwd = cwd or str(WORKSPACE_ROOT)
    script = textwrap.dedent(
        f"""\
        #!/bin/bash
        # Open named Grok Build session: {session_name}
        set -euo pipefail
        ROOT={_bash_quote(str(WORKSPACE_ROOT))}
        RUN_CWD={_bash_quote(run_cwd)}
        SESSION_ID={_bash_quote(session_id)}
        SESSION_NAME={_bash_quote(session_name)}
        cd "$RUN_CWD"

        if command -v grok >/dev/null 2>&1; then
          GROK_BIN="$(command -v grok)"
        elif [ -x "$HOME/.grok/bin/grok" ]; then
          GROK_BIN="$HOME/.grok/bin/grok"
        else
          echo "grok CLI not found. Install Grok Build or add it to PATH." >&2
          exit 1
        fi

        echo "=== Workflow Management → Grok Build ==="
        echo "Session: $SESSION_NAME"
        echo "ID:      $SESSION_ID"
        echo "Cwd:     $RUN_CWD"
        echo "Workspace files: $ROOT"
        echo ""
        echo "Resuming named session (not most-recent)…"
        exec "$GROK_BIN" --cwd "$RUN_CWD" --fullscreen --resume "$SESSION_ID"
        """
    )
    LAUNCH_SCRIPT.write_text(script, encoding="utf-8")
    LAUNCH_SCRIPT.chmod(LAUNCH_SCRIPT.stat().st_mode | 0o111)
    return {
        "launch_script": str(LAUNCH_SCRIPT.relative_to(WORKSPACE_ROOT)),
        "prompt_file": str(PROMPT_FILE.relative_to(WORKSPACE_ROOT)),
        "session_id": session_id,
        "cwd": run_cwd,
        "session_name": session_name,
    }


def open_workflow_grok(
    *,
    mode: str = "named",
    session_id: Optional[str] = None,
    session_name: Optional[str] = None,
) -> dict[str, Any]:
    """Open macOS Terminal resuming the pinned/named Workflow Management session.

    ``mode`` is accepted for API compatibility; ``continue``/``named`` both
    resolve the named session. ``new`` starts a fresh session with the workflow
    prompt (rare; not exposed in the UI).
    """
    mode = (mode or "named").lower().strip()
    grok = _which_grok()
    result: dict[str, Any] = {
        "ok": False,
        "mode": mode,
        "workspace": str(WORKSPACE_ROOT),
        "grok_bin": grok,
    }

    if not grok:
        result["error"] = "grok CLI not found (~/.grok/bin/grok or PATH)"
        return result

    if mode == "new":
        paths = write_launch_script(
            session_id="new",
            cwd=str(WORKSPACE_ROOT),
            session_name=session_name or "Workflow Management",
        )
        # Override script for new session (no resume)
        LAUNCH_SCRIPT.write_text(
            textwrap.dedent(
                f"""\
                #!/bin/bash
                set -euo pipefail
                ROOT={_bash_quote(str(WORKSPACE_ROOT))}
                PROMPT_FILE={_bash_quote(str(PROMPT_FILE))}
                cd "$ROOT"
                if command -v grok >/dev/null 2>&1; then GROK_BIN="$(command -v grok)"
                elif [ -x "$HOME/.grok/bin/grok" ]; then GROK_BIN="$HOME/.grok/bin/grok"
                else echo "grok CLI not found" >&2; exit 1; fi
                exec "$GROK_BIN" --cwd "$ROOT" --fullscreen "$(cat "$PROMPT_FILE")"
                """
            ),
            encoding="utf-8",
        )
        LAUNCH_SCRIPT.chmod(LAUNCH_SCRIPT.stat().st_mode | 0o111)
        result.update(paths)
    else:
        resolved = resolve_workflow_session(
            session_id=session_id,
            session_name=session_name,
        )
        if not resolved.get("ok"):
            result.update(resolved)
            return result
        paths = write_launch_script(
            session_id=str(resolved["session_id"]),
            cwd=str(resolved.get("cwd") or WORKSPACE_ROOT),
            session_name=str(resolved.get("session_name") or "Workflow Management"),
        )
        result.update(resolved)
        result.update(paths)

    try:
        proc = subprocess.run(
            ["open", "-a", "Terminal", str(LAUNCH_SCRIPT.resolve())],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            result["error"] = (
                proc.stderr or proc.stdout or "open Terminal failed"
            ).strip()
            result["returncode"] = proc.returncode
            return result
        result["ok"] = True
        result["method"] = "open -a Terminal → grok --resume <session_id>"
        sid = result.get("session_id") or ""
        title = result.get("title") or result.get("session_name") or "Workflow Management"
        result["message"] = (
            f"Opened Grok: {title}"
            + (f" ({sid[:13]}…)" if len(str(sid)) > 13 else f" ({sid})" if sid else "")
        )
        result["hint"] = f'grok --resume {sid}' if sid and sid != "new" else None
        return result
    except OSError as e:
        result["error"] = str(e)
        return result
    except subprocess.TimeoutExpired:
        result["error"] = "Timed out opening Terminal"
        return result
