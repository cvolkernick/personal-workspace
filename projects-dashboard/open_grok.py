"""Open a Grok Build session in macOS Terminal for Workflow Management.

Default: continue the most recent personal-workspace session (--continue).
Optional: start a fresh session with a workflow-management prompt.
"""

from __future__ import annotations

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


def write_launch_script(*, mode: str = "continue") -> dict[str, Any]:
    """Write (or refresh) the Terminal launch script + optional prompt file."""
    LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
    mode = (mode or "continue").lower().strip()
    if mode not in ("continue", "new"):
        mode = "continue"

    PROMPT_FILE.write_text(DEFAULT_NEW_PROMPT + "\n", encoding="utf-8")

    script = textwrap.dedent(
        f"""\
        #!/bin/bash
        # Open Grok Build for Workflow Management (personal-workspace)
        set -euo pipefail
        ROOT={_bash_quote(str(WORKSPACE_ROOT))}
        cd "$ROOT"
        PROMPT_FILE={_bash_quote(str(PROMPT_FILE))}
        MODE={_bash_quote(mode)}

        if command -v grok >/dev/null 2>&1; then
          GROK_BIN="$(command -v grok)"
        elif [ -x "$HOME/.grok/bin/grok" ]; then
          GROK_BIN="$HOME/.grok/bin/grok"
        else
          echo "grok CLI not found. Install Grok Build or add it to PATH." >&2
          exit 1
        fi

        echo "=== Workflow Management → Grok Build ==="
        echo "Workspace: $ROOT"
        echo "Mode: $MODE (continue = resume last session for this cwd)"
        echo ""

        if [ "$MODE" = "new" ]; then
          echo "Starting a new Workflow Management Grok session…"
          exec "$GROK_BIN" --cwd "$ROOT" --fullscreen "$(cat "$PROMPT_FILE")"
        fi

        echo "Continuing most recent Grok session for personal-workspace…"
        exec "$GROK_BIN" --cwd "$ROOT" --fullscreen --continue
        """
    )
    LAUNCH_SCRIPT.write_text(script, encoding="utf-8")
    LAUNCH_SCRIPT.chmod(LAUNCH_SCRIPT.stat().st_mode | 0o111)
    return {
        "launch_script": str(LAUNCH_SCRIPT.relative_to(WORKSPACE_ROOT)),
        "prompt_file": str(PROMPT_FILE.relative_to(WORKSPACE_ROOT)),
        "mode": mode,
    }


def open_workflow_grok(*, mode: str = "continue") -> dict[str, Any]:
    """Open macOS Terminal running Grok Build for the workflow workspace."""
    mode = (mode or "continue").lower().strip()
    if mode not in ("continue", "new"):
        mode = "continue"

    paths = write_launch_script(mode=mode)
    grok = _which_grok()
    result: dict[str, Any] = {
        "ok": False,
        "mode": mode,
        "workspace": str(WORKSPACE_ROOT),
        "grok_bin": grok,
        **paths,
    }

    if not grok:
        result["error"] = "grok CLI not found (~/.grok/bin/grok or PATH)"
        return result

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
        result["method"] = "open -a Terminal → grok --cwd personal-workspace"
        result["message"] = (
            "Opened Terminal with Grok Build"
            + (
                " (continue last workflow session)"
                if mode == "continue"
                else " (new Workflow Management session)"
            )
        )
        result["hint"] = (
            "If Terminal opens but Grok errors, run: "
            f"grok --cwd {WORKSPACE_ROOT} --continue"
        )
        return result
    except OSError as e:
        result["error"] = str(e)
        return result
    except subprocess.TimeoutExpired:
        result["error"] = "Timed out opening Terminal"
        return result
