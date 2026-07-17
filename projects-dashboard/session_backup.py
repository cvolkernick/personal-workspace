"""Lightweight Grok session index backup into personal-workspace git.

Why not full ~/.grok/sessions?
- ~100MB+ with multi‑MB events.jsonl / chat_history.jsonl per long session
- Often contains secrets, tokens, and personal paths
- High churn → noisy git history

What we DO commit (recommended):
- ops/session-index/latest.json — resume kit + summary metadata for all
  parent sessions (small, enough to recover IDs/titles after reboot/machine loss)
- ops/session-index/history/YYYY-MM-DDTHHMMSSZ.json — optional dated copies

Full offline archive (not for git by default):
- write_full_archive() → ~/Backups/grok-sessions/… tarball of summary.json only
  or entire dirs if you explicitly opt in.
"""

from __future__ import annotations

import json
import os
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GROK_HOME = Path(os.environ.get("GROK_HOME", Path.home() / ".grok"))
INDEX_DIR = WORKSPACE_ROOT / "ops" / "session-index"
HISTORY_DIR = INDEX_DIR / "history"
DEFAULT_ARCHIVE_ROOT = Path.home() / "Backups" / "grok-sessions"


def _iter_parent_sessions(grok_home: Path):
    sessions_root = grok_home / "sessions"
    if not sessions_root.is_dir():
        return
    for group in sessions_root.iterdir():
        if not group.is_dir() or group.name.startswith("."):
            continue
        cwd = unquote(group.name)
        for sdir in group.iterdir():
            if not sdir.is_dir():
                continue
            sumf = sdir / "summary.json"
            if not sumf.is_file():
                continue
            try:
                summary = json.loads(sumf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            kind = summary.get("session_kind") or "primary"
            if kind in ("subagent", "subagent_fork"):
                continue
            yield cwd, sdir, summary


def build_session_index(grok_home: Optional[Path] = None) -> dict[str, Any]:
    grok_home = Path(grok_home or DEFAULT_GROK_HOME)
    active_path = grok_home / "active_sessions.json"
    active_ids: set[str] = set()
    if active_path.is_file():
        try:
            raw = json.loads(active_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                active_ids = {
                    x["session_id"] for x in raw if isinstance(x, dict) and x.get("session_id")
                }
        except (OSError, json.JSONDecodeError):
            pass

    sessions: list[dict[str, Any]] = []
    for cwd, sdir, summary in _iter_parent_sessions(grok_home):
        info = summary.get("info") or {}
        sid = info.get("id") or sdir.name
        # size of dir for awareness (not full content)
        size = 0
        try:
            for root, _dirs, files in os.walk(sdir):
                for f in files:
                    try:
                        size += (Path(root) / f).stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
        sessions.append(
            {
                "id": sid,
                "title": summary.get("generated_title")
                or summary.get("session_summary")
                or sid,
                "cwd": info.get("cwd") or cwd,
                "created_at": summary.get("created_at"),
                "last_active_at": summary.get("last_active_at")
                or summary.get("updated_at"),
                "model": summary.get("current_model_id"),
                "agent_name": summary.get("agent_name"),
                "num_chat_messages": summary.get("num_chat_messages"),
                "active": sid in active_ids,
                "disk_path": str(sdir),
                "bytes_on_disk": size,
                "resume_cmd": f"grok --resume {sid}",
                # lightweight summary fields only — not full transcript
                "summary_keys": sorted(summary.keys()),
            }
        )

    sessions.sort(key=lambda s: s.get("last_active_at") or "", reverse=True)
    return {
        "version": 1,
        "kind": "grok-session-index",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "grok_home": str(grok_home),
        "note": (
            "Index only (titles, IDs, resume commands). Full transcripts stay in "
            "~/.grok/sessions and are not copied into git. Reboot-safe resume uses "
            "these IDs; machine-loss recovery of chat text needs a full archive "
            "(see session_backup.write_full_archive)."
        ),
        "count": len(sessions),
        "sessions": sessions,
    }


def write_session_index(
    repo: Path = WORKSPACE_ROOT,
    grok_home: Optional[Path] = None,
    *,
    keep_history: bool = True,
    commit: bool = False,
) -> dict[str, Any]:
    """Write ops/session-index/latest.json (+ optional history copy)."""
    repo = Path(repo).resolve()
    index = build_session_index(grok_home)
    out_dir = repo / "ops" / "session-index"
    out_dir.mkdir(parents=True, exist_ok=True)
    latest = out_dir / "latest.json"
    latest.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    written = [str(latest.relative_to(repo))]

    if keep_history:
        hist = out_dir / "history"
        hist.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        hist_path = hist / f"{stamp}.json"
        hist_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
        written.append(str(hist_path.relative_to(repo)))
        # prune history to last 30
        old = sorted(hist.glob("*.json"), reverse=True)
        for p in old[30:]:
            try:
                p.unlink()
            except OSError:
                pass

    # README for humans
    readme = out_dir / "README.md"
    if not readme.is_file():
        readme.write_text(
            "# Grok session index\n\n"
            "Lightweight backup of session **metadata** (IDs, titles, resume commands).\n"
            "Full chat logs stay in `~/.grok/sessions` and are not stored here.\n\n"
            "Refresh: `python3 projects-dashboard/session_backup.py index`\n"
            "Or via dashboard / `git_workflow.py sync`.\n",
            encoding="utf-8",
        )
        written.append(str(readme.relative_to(repo)))

    result: dict[str, Any] = {
        "ok": True,
        "written": written,
        "count": index["count"],
        "latest": str(latest),
    }
    if commit:
        from git_workflow import protect_work  # noqa: WPS433

        prot = protect_work(
            repo,
            message="chore(session-index): refresh Grok session index",
            push=True,
            paths=written,
            ensure_work_branch=True,
        )
        result["protect"] = prot
        result["ok"] = prot.get("ok", False)
    return result


def write_full_archive(
    *,
    grok_home: Optional[Path] = None,
    dest_dir: Optional[Path] = None,
    mode: str = "summaries",
) -> dict[str, Any]:
    """Write a tarball outside the git repo.

    mode:
      - summaries: only summary.json per session (small, safer)
      - full: entire session dirs (large; may include secrets — private disk only)
    """
    grok_home = Path(grok_home or DEFAULT_GROK_HOME)
    dest_dir = Path(dest_dir or DEFAULT_ARCHIVE_ROOT)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tar_path = dest_dir / f"grok-sessions-{mode}-{stamp}.tar.gz"

    sessions_root = grok_home / "sessions"
    if not sessions_root.is_dir():
        return {"ok": False, "error": "no sessions dir", "path": None}

    with tarfile.open(tar_path, "w:gz") as tar:
        if mode == "full":
            tar.add(sessions_root, arcname="sessions")
        else:
            for cwd, sdir, _summary in _iter_parent_sessions(grok_home):
                sumf = sdir / "summary.json"
                if sumf.is_file():
                    arc = f"sessions/{sdir.parent.name}/{sdir.name}/summary.json"
                    tar.add(sumf, arcname=arc)
                # also plan.md / goal if small
                for extra in ("plan.json", "goal/plan.md"):
                    p = sdir / extra
                    if p.is_file() and p.stat().st_size < 500_000:
                        tar.add(
                            p,
                            arcname=f"sessions/{sdir.parent.name}/{sdir.name}/{extra}",
                        )

    return {
        "ok": True,
        "path": str(tar_path),
        "mode": mode,
        "bytes": tar_path.stat().st_size,
        "note": "Stored outside git. Do not commit full archives to a public remote.",
    }


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "index"
    if cmd == "index":
        print(json.dumps(write_session_index(commit="--commit" in sys.argv), indent=2))
    elif cmd == "archive":
        mode = "full" if "--full" in sys.argv else "summaries"
        print(json.dumps(write_full_archive(mode=mode), indent=2))
    else:
        print("Usage: session_backup.py [index [--commit]|archive [--full]]", file=sys.stderr)
        raise SystemExit(2)
