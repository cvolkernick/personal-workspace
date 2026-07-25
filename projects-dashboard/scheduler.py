"""Auto-start scheduler for approved/queued backlog work.

Each tick (cron / systemd / dashboard) is a full autonomous cycle:
  1. Groom backlog (score + schedule slots)
  2. Auto-queue ready + now/this_week items (``auto_start=true``)
  3. Launch up to ``max_per_tick`` eligible jobs (spawn / agent / pending_terminal)

Backends:
  - ``local`` — Mac/host crontab (laptop-friendly; sleeps when machine sleeps)
  - ``raspi`` — Raspberry Pi is the 24/7 schedule authority (systemd timer)

Execution modes (Grok Build may be missing on the Pi):
  - ``auto`` — spawn Grok when available; otherwise leave job ``pending_terminal``
  - ``spawn`` — always try to open Grok (needs CLI + ideally a Terminal)
  - ``queue`` — never spawn; prepare seeds and wait for a Mac/Terminal claim
  - ``agent`` — unattended branch → work → push → PR (intended for Pi)

Status reports land under ``ops/backlog/reports/``.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backlog import (
    BACKLOG_DIR,
    WORKSPACE_ROOT,
    get_item,
    initiate_item,
    list_items,
    load_backlog,
    save_backlog,
    update_item,
)

CONFIG_PATH = BACKLOG_DIR / "scheduler.json"
JOBS_PATH = BACKLOG_DIR / "jobs.json"
REPORTS_DIR = BACKLOG_DIR / "reports"

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "enabled": False,
    # local = this machine's cron; raspi = always-on Pi owns the schedule
    "backend": "local",  # local | raspi
    # auto | spawn | queue | agent — how to run work when a tick fires
    # agent = unattended branch→work→push→PR on the server (Pi)
    "execution_mode": "auto",
    "cron_expression": "*/15 * * * *",
    "max_per_tick": 1,
    "max_concurrent": 1,
    "eligible_statuses": ["ready"],
    "eligible_slots": ["now", "this_week"],
    # When true, only items with auto_start=true are kicked off.
    # auto_queue_scheduled sets that flag for ready+scheduled items after groom.
    "require_auto_start": True,
    "auto_queue_scheduled": True,
    # On Mac dashboard load, auto-open Terminal for pending_terminal jobs
    "auto_claim_on_load": True,
    "auto_claim_max": 1,
    # Prefer server agent pipeline when headless (Pi) instead of only queueing
    "prefer_agent_on_server": True,
    # Legacy flag; prefer execution_mode. Kept for older configs.
    "spawn_grok": True,
    "install_marker": "projects-dashboard-scheduler",
    # Optional: SSH target for remote Pi timer install (user@host)
    "raspi_ssh": "",
    "raspi_dir": "",
    "last_tick_at": None,
    "last_tick_result": None,
    "last_dashboard_load_at": None,
}

JOB_STATUSES = (
    "queued",
    "pending_terminal",  # seed ready; wait for Mac/Terminal claim
    "agent_running",
    "pr_ready",  # branch pushed + PR opened — review on dashboard
    "launched",
    "running",
    "completed",
    "failed",
    "skipped",
    "cancelled",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure() -> None:
    BACKLOG_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.is_file():
        save_config(dict(DEFAULT_CONFIG))
    if not JOBS_PATH.is_file():
        save_jobs({"version": 1, "updated_at": _now(), "jobs": []})


def load_config() -> dict[str, Any]:
    _ensure()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    out = dict(DEFAULT_CONFIG)
    out.update(data if isinstance(data, dict) else {})
    return out


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    BACKLOG_DIR.mkdir(parents=True, exist_ok=True)
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    merged["updated_at"] = _now()
    CONFIG_PATH.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return merged


def load_jobs() -> dict[str, Any]:
    _ensure()
    try:
        data = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {"version": 1, "jobs": []}
    if not isinstance(data.get("jobs"), list):
        data["jobs"] = []
    return data


def save_jobs(data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    data["version"] = data.get("version") or 1
    JOBS_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def list_reports(*, limit: int = 30) -> list[dict[str, Any]]:
    _ensure()
    files = sorted(REPORTS_DIR.glob("*.json"), reverse=True)
    out = []
    for p in files[:limit]:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def write_report(report: dict[str, Any]) -> Path:
    _ensure()
    rid = report.get("id") or str(uuid.uuid4())
    report["id"] = rid
    report.setdefault("created_at", _now())
    report["updated_at"] = _now()
    path = REPORTS_DIR / f"{rid}.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["path"] = str(path.relative_to(WORKSPACE_ROOT))
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path


def set_auto_start(backlog_id: str, enabled: bool = True) -> dict[str, Any]:
    """Approve item for automatic initiate-goal on the next eligible tick.

    Manual enable is the only way to re-run an item that already kicked off.
    """
    data = load_backlog()
    found = None
    for it in data.get("items") or []:
        if it.get("id") == backlog_id:
            found = it
            break
    if not found:
        return {"ok": False, "error": "backlog item not found"}
    found["auto_start"] = bool(enabled)
    found["auto_start_set_at"] = _now()
    found["updated_at"] = _now()
    if enabled:
        found["auto_start_source"] = "manual"
        # Explicit re-queue: if item was done, reopen as ready for another pass
        if (found.get("status") or "") in ("idea", "done"):
            found["status"] = "ready"
        found.pop("auto_start_cleared_reason", None)
    else:
        found.pop("auto_start_source", None)
    save_backlog(data)
    return {"ok": True, "item": found, "auto_start": bool(enabled)}


def _jobs_for_backlog(backlog_id: str, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [j for j in jobs if j.get("backlog_id") == backlog_id]


def _kickoff_block_reason(
    item: dict[str, Any],
    jobs: list[dict[str, Any]],
    *,
    for_auto_queue: bool = False,
) -> Optional[str]:
    """Why this item must not be auto-started again (None = ok to kick off).

    Policy:
    - Never re-auto-queue after a prior kickoff unless the user manually queues.
    - Never kick off while another job is active/PR-ready for this item.
    - Never kick off if a prior job completed successfully (merged/done).
    - Manual auto_start (Queue for agent) allows a deliberate re-run after prior work.
    """
    bid = item.get("id")
    if not bid:
        return "missing_id"
    hist = _jobs_for_backlog(str(bid), jobs)
    active = {
        "queued",
        "pending_terminal",
        "agent_running",
        "launched",
        "running",
        "pr_ready",
    }
    for j in hist:
        st = j.get("status")
        if st in active:
            return f"job_{st}"
        if st == "completed":
            # Completed once = done for auto loop; manual queue can override
            if for_auto_queue:
                return "already_completed"
            if (item.get("auto_start_source") or "") != "manual":
                return "already_completed"
    # One-shot: after any kickoff, do not auto-queue again
    if item.get("last_job_id") or item.get("last_auto_started_at") or item.get("last_pr_url"):
        if for_auto_queue:
            return "already_kicked_off"
        # Eligible tick path: only allow if user explicitly re-queued
        if item.get("auto_start") and (item.get("auto_start_source") or "") == "manual":
            return None
        if item.get("auto_start") and not for_auto_queue:
            # stale auto_start from auto-queue after a prior run — block
            if (item.get("auto_start_source") or "") == "auto-queue":
                return "stale_auto_queue_after_kickoff"
            # unknown source with prior kickoff: block unless manual
            if item.get("last_job_id") or item.get("last_auto_started_at"):
                if (item.get("auto_start_source") or "") != "manual":
                    return "already_kicked_off"
    if (item.get("status") or "").lower() == "done":
        return "done"
    return None


def auto_queue_scheduled(*, force: bool = False) -> dict[str, Any]:
    """Queue *new* ready + now/this_week items for the next scheduler tick.

    Does **not** re-queue items that already kicked off or completed. Re-runs
    require an explicit dashboard “Queue for agent” (manual auto_start).
    """
    cfg = load_config()
    if not cfg.get("enabled") and not force:
        return {
            "ok": True,
            "queued": [],
            "count": 0,
            "skipped": True,
            "reason": "scheduler disabled",
        }
    if not cfg.get("auto_queue_scheduled", True) and not force:
        return {
            "ok": True,
            "queued": [],
            "count": 0,
            "skipped": True,
            "reason": "auto_queue_scheduled disabled",
        }

    slots = {str(s).lower() for s in (cfg.get("eligible_slots") or ["now", "this_week"])}
    statuses = {str(s).lower() for s in (cfg.get("eligible_statuses") or ["ready"])}
    jobs = list((load_jobs().get("jobs") or []))
    queued: list[dict[str, Any]] = []
    skipped_prior = 0
    data = load_backlog()
    dirty = False
    for it in data.get("items") or []:
        st = (it.get("status") or "idea").lower()
        if st in ("planning", "active", "done", "parked"):
            continue
        if st not in statuses:
            continue
        slot = (it.get("schedule_slot") or "later").lower()
        rank = it.get("press_rank") or 99
        try:
            rank = int(rank)
        except (TypeError, ValueError):
            rank = 99
        if slot not in slots and not (rank <= 2 and "now" in slots):
            continue
        if it.get("auto_start"):
            continue
        block = _kickoff_block_reason(it, jobs, for_auto_queue=True)
        if block:
            skipped_prior += 1
            # Clear sticky auto_start left over from older auto-queue loops
            if it.get("auto_start") and (it.get("auto_start_source") or "") == "auto-queue":
                it["auto_start"] = False
                it["updated_at"] = _now()
                dirty = True
            continue
        it["auto_start"] = True
        it["auto_start_set_at"] = _now()
        it["auto_start_source"] = "auto-queue"
        it["updated_at"] = _now()
        dirty = True
        queued.append(
            {
                "id": it.get("id"),
                "title": it.get("title"),
                "status": it.get("status"),
                "schedule_slot": it.get("schedule_slot"),
                "press_rank": it.get("press_rank"),
            }
        )
    # Also clear stale auto-queue flags on items that already ran
    for it in data.get("items") or []:
        if not it.get("auto_start"):
            continue
        if (it.get("auto_start_source") or "") != "auto-queue":
            continue
        block = _kickoff_block_reason(it, jobs, for_auto_queue=True)
        if block:
            it["auto_start"] = False
            it["auto_start_cleared_reason"] = block
            it["updated_at"] = _now()
            dirty = True
            skipped_prior += 1
    if dirty:
        save_backlog(data)
    return {
        "ok": True,
        "queued": queued,
        "count": len(queued),
        "skipped_prior_runs": skipped_prior,
        "skipped": False,
        "message": (
            f"Auto-queued {len(queued)} new item(s)"
            + (f" · skipped {skipped_prior} already-run" if skipped_prior else "")
            if queued or skipped_prior
            else "No new items to auto-queue"
        ),
    }


def _active_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        j
        for j in jobs
        if j.get("status")
        in ("queued", "pending_terminal", "agent_running", "launched", "running")
    ]


def detect_runtime() -> dict[str, Any]:
    """Detect whether this host can spawn Grok Build / Terminal."""
    grok = shutil.which("grok")
    if not grok:
        home = Path.home() / ".grok" / "bin" / "grok"
        if home.is_file() and os.access(home, os.X_OK):
            grok = str(home)
    sysname = platform.system().lower()
    # macOS only: /usr/bin/open launches Terminal.app (Linux may have an unrelated `open`)
    has_open = sysname == "darwin" and Path("/usr/bin/open").is_file()
    return {
        "ok": True,
        "platform": sysname,
        "machine": platform.machine(),
        "has_grok": bool(grok),
        "grok_bin": grok,
        "has_macos_terminal": has_open,
        "can_spawn_terminal": bool(grok and has_open),
        "can_spawn_headless": bool(grok) and not has_open,
        "recommended_execution": (
            "spawn"
            if (grok and has_open)
            else ("spawn" if grok else "queue")
        ),
        "note": (
            "Grok Build + Terminal available — can spawn interactively."
            if (grok and has_open)
            else (
                "Grok CLI present without macOS Terminal — headless spawn may work; "
                "queue to a Mac frontend is safer for interactive /goal."
                if grok
                else "Grok Build not installed — tick prepares seeds and leaves jobs "
                "pending_terminal for claim on a Mac with Terminal."
            )
        ),
    }


def resolve_execution_mode(cfg: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Decide spawn vs queue vs unattended agent for this host."""
    cfg = cfg or load_config()
    runtime = detect_runtime()
    mode = str(cfg.get("execution_mode") or "auto").lower().strip()
    if mode not in ("auto", "spawn", "queue", "agent"):
        mode = "spawn" if cfg.get("spawn_grok", True) else "queue"

    should_spawn = False
    use_agent = False
    reason = mode

    if mode == "queue":
        should_spawn = False
        use_agent = False
        reason = "execution_mode=queue → pending_terminal"
    elif mode == "spawn":
        should_spawn = True
        use_agent = False
        reason = "execution_mode=spawn"
    elif mode == "agent":
        should_spawn = False
        use_agent = True
        reason = "execution_mode=agent → branch/work/push/PR"
    else:
        # auto
        if runtime.get("can_spawn_terminal"):
            should_spawn = True
            reason = "auto: macOS Terminal + grok"
        elif cfg.get("prefer_agent_on_server", True) and (
            (cfg.get("backend") or "") == "raspi"
            or runtime.get("platform") == "linux"
            or not runtime.get("has_macos_terminal")
        ):
            use_agent = True
            reason = "auto: headless/server → agent pipeline (PR)"
        elif runtime.get("can_spawn_headless") and cfg.get("prefer_headless_spawn"):
            should_spawn = True
            reason = "auto: headless grok spawn"
        else:
            reason = "auto: queue pending_terminal"

    return {
        "mode": mode,
        "should_spawn": should_spawn,
        "use_agent": use_agent,
        "reason": reason,
        "runtime": runtime,
        "backend": cfg.get("backend") or "local",
    }


def _eligible_items(cfg: dict[str, Any], jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    statuses = set(cfg.get("eligible_statuses") or ["ready"])
    slots = set(cfg.get("eligible_slots") or ["now", "this_week"])
    require_auto = bool(cfg.get("require_auto_start", True))
    out = []
    for it in list_items(include_done=False, ranked=True):
        bid = it.get("id")
        if not bid:
            continue
        if (it.get("status") or "") in ("planning", "active", "done", "parked"):
            # planning/active already started; done/parked finished
            continue
        block = _kickoff_block_reason(it, jobs, for_auto_queue=False)
        if block:
            continue
        if require_auto and not it.get("auto_start"):
            continue
        st = it.get("status") or "idea"
        if st not in statuses:
            continue
        slot = (it.get("schedule_slot") or "later").lower()
        # allow unranked ready+auto_start as "now" if press_rank <= 2
        rank = it.get("press_rank") or 99
        try:
            rank = int(rank)
        except (TypeError, ValueError):
            rank = 99
        if slot not in slots and not (rank <= 2 and "now" in slots):
            continue
        out.append(it)
    # already press-ranked
    return out


def _cron_line(cfg: dict[str, Any]) -> str:
    expr = cfg.get("cron_expression") or "*/15 * * * *"
    py = subprocess.run(
        ["which", "python3"], capture_output=True, text=True
    )
    python = (py.stdout or "").strip() or "/usr/bin/python3"
    runner = WORKSPACE_ROOT / "projects-dashboard" / "run_scheduler.py"
    marker = cfg.get("install_marker") or "projects-dashboard-scheduler"
    # log to ops/backlog/scheduler.log
    log = BACKLOG_DIR / "scheduler.log"
    return (
        f"{expr} cd {WORKSPACE_ROOT} && {python} {runner} tick "
        f">> {log} 2>&1  # {marker}"
    )


def cron_status() -> dict[str, Any]:
    cfg = load_config()
    marker = cfg.get("install_marker") or "projects-dashboard-scheduler"
    try:
        proc = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True
        )
        text = proc.stdout or ""
        installed = marker in text
        line = next((ln for ln in text.splitlines() if marker in ln), None)
    except OSError as e:
        return {"ok": False, "error": str(e), "installed": False}
    return {
        "ok": True,
        "installed": installed,
        "line": line,
        "desired_line": _cron_line(cfg),
        "backend": cfg.get("backend") or "local",
    }


def install_cron() -> dict[str, Any]:
    """Install/replace local crontab entry for the scheduler tick.

    For 24/7 scheduling prefer backend=raspi + deploy/install_remote.sh on the Pi.
    This installs a crontab on *this* host only.
    """
    cfg = load_config()
    marker = cfg.get("install_marker") or "projects-dashboard-scheduler"
    desired = _cron_line(cfg)
    try:
        proc = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True
        )
        existing = proc.stdout or ""
        if proc.returncode not in (0, 1) and "no crontab" not in (proc.stderr or "").lower():
            # macOS returns 1 when empty
            if "no crontab" not in (proc.stderr or "").lower() and proc.returncode != 0:
                pass
        lines = [
            ln
            for ln in existing.splitlines()
            if marker not in ln and ln.strip()
        ]
        lines.append(desired)
        body = "\n".join(lines) + "\n"
        proc2 = subprocess.run(
            ["crontab", "-"],
            input=body,
            text=True,
            capture_output=True,
        )
        if proc2.returncode != 0:
            return {
                "ok": False,
                "error": proc2.stderr or "crontab install failed",
            }
    except OSError as e:
        return {"ok": False, "error": str(e)}
    cfg["enabled"] = True
    cfg["cron_installed_at"] = _now()
    save_config(cfg)
    note = None
    if (cfg.get("backend") or "local") == "raspi":
        note = (
            "backend=raspi: local cron installed as a fallback. "
            "For true 24/7 ticks, run projects-dashboard/deploy/install_remote.sh "
            "on the Pi (systemd timer)."
        )
    return {
        "ok": True,
        "installed": True,
        "line": desired,
        "config": load_config(),
        "note": note,
        "message": "Local cron installed" + (f" · {note}" if note else ""),
    }


def uninstall_cron() -> dict[str, Any]:
    cfg = load_config()
    marker = cfg.get("install_marker") or "projects-dashboard-scheduler"
    try:
        proc = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True
        )
        existing = proc.stdout or ""
        lines = [
            ln for ln in existing.splitlines() if marker not in ln and ln.strip()
        ]
        body = ("\n".join(lines) + "\n") if lines else ""
        proc2 = subprocess.run(
            ["crontab", "-"],
            input=body,
            text=True,
            capture_output=True,
        )
        if proc2.returncode != 0 and lines:
            return {"ok": False, "error": proc2.stderr or "crontab uninstall failed"}
    except OSError as e:
        return {"ok": False, "error": str(e)}
    cfg["enabled"] = False
    cfg["cron_uninstalled_at"] = _now()
    save_config(cfg)
    return {"ok": True, "installed": False, "config": load_config()}


def _reconcile_completions(jobs_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Sync job status from backlog + orphaned Terminal launches."""
    updated = []
    active_like = (
        "launched",
        "running",
        "queued",
        "pending_terminal",
        "agent_running",
        "pr_ready",
    )
    for job in jobs_data.get("jobs") or []:
        if job.get("status") not in active_like:
            continue
        bid = job.get("backlog_id")
        item = get_item(bid) if bid else None
        if not item:
            continue
        st = (item.get("status") or "").lower()
        if st == "done":
            job["status"] = "completed"
            job["completed_at"] = _now()
            job["result"] = "backlog status=done"
            report = {
                "id": f"rpt-{job.get('id', uuid.uuid4().hex[:8])}",
                "job_id": job.get("id"),
                "backlog_id": bid,
                "title": item.get("title"),
                "status": "completed",
                "summary": f"Job completed: backlog item marked done. Title: {item.get('title')}",
                "actions": [
                    f"Auto-started at {job.get('launched_at') or job.get('created_at')}",
                    f"Completed at {job.get('completed_at')}",
                    f"Backlog status now: {st}",
                ],
                "source": "scheduler-reconcile",
            }
            write_report(report)
            job["latest_report_id"] = report["id"]
            updated.append(job)
        elif st == "parked":
            job["status"] = "cancelled"
            job["completed_at"] = _now()
            job["result"] = "backlog status=parked"
            updated.append(job)
        elif st in ("planning", "active") and job.get("status") == "launched":
            job["status"] = "running"
            job["updated_at"] = _now()
            updated.append(job)
        elif (
            job.get("status") in ("launched", "running", "pending_terminal")
            and st in ("ready", "idea")
            and not job.get("pr_url")
        ):
            # Terminal session was kicked off earlier but item is no longer planning —
            # treat as orphaned so the dashboard stops showing phantom "in progress".
            prior = job.get("status")
            job["status"] = "cancelled"
            job["completed_at"] = _now()
            job["result"] = "orphaned: backlog returned to ready/idea (no live session)"
            job["updated_at"] = _now()
            report = {
                "id": f"rpt-{job.get('id')}-orphan",
                "job_id": job.get("id"),
                "backlog_id": bid,
                "title": job.get("title"),
                "status": "cancelled",
                "summary": (
                    f"Cleared stale job for “{job.get('title')}” — backlog is {st}, "
                    "not planning/active, and no PR is linked."
                ),
                "actions": [
                    "Reconcile: orphaned Terminal/agent job",
                    f"Prior job status was {prior}",
                ],
                "source": "scheduler-reconcile-orphan",
            }
            write_report(report)
            job["latest_report_id"] = report["id"]
            updated.append(job)
    return updated


def _reconcile_merged_prs(jobs_data: dict[str, Any]) -> list[dict[str, Any]]:
    """When a linked GitHub PR is merged, complete the job and mark backlog done."""
    from agent_jobs import fetch_pull_request  # noqa: WPS433

    updated: list[dict[str, Any]] = []
    for job in jobs_data.get("jobs") or []:
        if job.get("status") not in ("pr_ready", "launched", "running", "agent_running"):
            continue
        if not (job.get("pr_url") or job.get("pr_number") or job.get("branch")):
            continue
        pr = fetch_pull_request(
            pr_url=job.get("pr_url"),
            pr_number=job.get("pr_number"),
            head=job.get("branch"),
        )
        if not pr.get("ok"):
            job["pr_check_error"] = pr.get("error")
            continue
        job["pr_url"] = pr.get("url") or job.get("pr_url")
        job["pr_number"] = pr.get("number") or job.get("pr_number")
        job["pr_state"] = pr.get("state")
        job["pr_merged"] = pr.get("merged")
        if pr.get("merged"):
            job["status"] = "completed"
            job["completed_at"] = pr.get("merged_at") or _now()
            job["result"] = f"PR #{pr.get('number')} merged"
            job["updated_at"] = _now()
            bid = job.get("backlog_id")
            if bid:
                update_item(
                    str(bid),
                    {
                        "status": "done",
                        "last_pr_url": pr.get("url"),
                        "auto_start": False,
                        "completed_via": "pr_merge",
                    },
                )
            report = {
                "id": f"rpt-{job.get('id')}-merged",
                "job_id": job.get("id"),
                "backlog_id": bid,
                "title": job.get("title"),
                "status": "completed",
                "summary": (
                    f"PR merged for “{job.get('title')}”. "
                    f"{pr.get('url')}. Backlog marked done."
                ),
                "actions": [
                    f"Detected merge of PR #{pr.get('number')}",
                    f"Merged at {pr.get('merged_at') or 'unknown'}",
                    "Backlog status → done",
                ],
                "pr_url": pr.get("url"),
                "source": "scheduler-pr-merge",
            }
            write_report(report)
            job["latest_report_id"] = report["id"]
            updated.append(job)
        elif pr.get("state") == "closed" and not pr.get("merged"):
            # Closed without merge — leave pr_ready but note it
            job["pr_closed_unmerged"] = True
            job["updated_at"] = _now()
    return updated


def reconcile_jobs(*, save: bool = True) -> dict[str, Any]:
    """Run all job reconciliation (backlog + GitHub PR merge). Safe on dashboard load."""
    jobs_data = load_jobs()
    orphaned = _reconcile_completions(jobs_data)
    merged = _reconcile_merged_prs(jobs_data)
    if save and (orphaned or merged):
        save_jobs(jobs_data)
    return {
        "ok": True,
        "orphaned": len(orphaned),
        "merged": len(merged),
        "merged_jobs": [
            {
                "id": j.get("id"),
                "title": j.get("title"),
                "pr_url": j.get("pr_url"),
            }
            for j in merged
        ],
        "message": (
            f"Reconciled: {len(merged)} PR merge(s), {len(orphaned)} orphan clear(s)"
            if (merged or orphaned)
            else "Nothing to reconcile"
        ),
    }


def tick(*, force: bool = False) -> dict[str, Any]:
    """Run one full scheduler cycle (cron, systemd timer, or dashboard).

    Pre-steps (so the Pi is fully autonomous without a Mac dashboard load):
      - groom backlog ranks/schedule slots
      - auto-queue ready + eligible-slot items (``auto_start=true``)
      - reconcile completed/orphaned/merged jobs
    Then launch up to ``max_per_tick`` eligible jobs.
    """
    cfg = load_config()
    if not cfg.get("enabled") and not force:
        return {
            "ok": True,
            "skipped": True,
            "reason": "scheduler disabled",
            "config": cfg,
        }

    # --- Pre-step: groom + auto-queue (was only on dashboard load) ---
    pre_loop: dict[str, Any] = {"groom": None, "queue": None}
    try:
        from backlog_groom import groom_backlog  # noqa: WPS433

        g = groom_backlog(apply=True)
        pre_loop["groom"] = {
            "ok": g.get("ok"),
            "message": g.get("message"),
            "count": g.get("count"),
            "changes": len(g.get("changes") or []),
            "groomed_at": g.get("groomed_at"),
        }
    except Exception as e:
        pre_loop["groom"] = {"ok": False, "error": str(e)}
    try:
        pre_loop["queue"] = auto_queue_scheduled(force=force)
    except Exception as e:
        pre_loop["queue"] = {"ok": False, "error": str(e)}

    # Reload config after pre-step (queue may touch timestamps only via backlog)
    cfg = load_config()
    exec_plan = resolve_execution_mode(cfg)
    should_spawn = bool(exec_plan.get("should_spawn"))
    use_agent = bool(exec_plan.get("use_agent"))

    jobs_data = load_jobs()
    jobs = list(jobs_data.get("jobs") or [])
    recon = reconcile_jobs(save=True)
    jobs_data = load_jobs()
    jobs = list(jobs_data.get("jobs") or [])
    reconciled = (recon.get("orphaned") or 0) + (recon.get("merged") or 0)

    max_conc = int(cfg.get("max_concurrent") or 1)
    max_tick = int(cfg.get("max_per_tick") or 1)
    active = _active_jobs(jobs)
    slots = max(0, max_conc - len(active))
    launched = []
    pending = []
    pr_ready = []
    errors = []

    if slots > 0:
        eligible = _eligible_items(cfg, jobs)
        for it in eligible[: min(slots, max_tick)]:
            job_id = f"job-{uuid.uuid4().hex[:10]}"
            job = {
                "id": job_id,
                "backlog_id": it.get("id"),
                "title": it.get("title"),
                "status": "queued",
                "created_at": _now(),
                "backend": cfg.get("backend") or "local",
                "execution_mode": exec_plan.get("mode"),
                "schedule_slot": it.get("schedule_slot"),
                "press_rank": it.get("press_rank"),
                "execution_reason": exec_plan.get("reason"),
            }
            jobs.append(job)
            try:
                if use_agent:
                    from agent_jobs import execute_agent_job  # noqa: WPS433

                    job["status"] = "agent_running"
                    job["started_at"] = _now()
                    jobs_data["jobs"] = jobs
                    save_jobs(jobs_data)
                    agent_out = execute_agent_job(
                        str(it["id"]), job_id=job_id
                    )
                    job["agent"] = {
                        k: agent_out.get(k)
                        for k in (
                            "ok",
                            "error",
                            "branch",
                            "pr_url",
                            "pr_number",
                            "message",
                            "needs_terminal",
                            "steps",
                        )
                    }
                    job["branch"] = agent_out.get("branch")
                    job["seed_path"] = agent_out.get("seed_path")
                    job["launch_script"] = agent_out.get("launch_script")
                    if agent_out.get("ok") and agent_out.get("pr_url"):
                        job["status"] = "pr_ready"
                        job["pr_url"] = agent_out.get("pr_url")
                        job["pr_number"] = agent_out.get("pr_number")
                        job["pr_ready_at"] = _now()
                        summary = (
                            f"Agent finished “{it.get('title')}”. "
                            f"PR: {agent_out.get('pr_url')}"
                        )
                        actions = [
                            f"Tick: {exec_plan.get('reason')}",
                            f"Branch {agent_out.get('branch')}",
                            f"PR {agent_out.get('pr_url')}",
                        ]
                        pr_ready.append(job)
                    elif agent_out.get("needs_terminal"):
                        job["status"] = "pending_terminal"
                        job["pending_at"] = _now()
                        job["claim_hint"] = (
                            f"bash {agent_out.get('launch_script')}"
                            if agent_out.get("launch_script")
                            else "auto-claimed on next Mac dashboard load"
                        )
                        job["error"] = agent_out.get("error")
                        summary = (
                            f"Agent could not finish on server "
                            f"({agent_out.get('error')}). "
                            "Queued for Terminal auto-claim."
                        )
                        actions = [
                            f"Tick: {exec_plan.get('reason')}",
                            "Fell back to pending_terminal",
                            str(agent_out.get("error") or ""),
                        ]
                        pending.append(job)
                    else:
                        job["status"] = "failed"
                        job["error"] = agent_out.get("error") or "agent failed"
                        job["finished_at"] = _now()
                        summary = f"Agent failed: {job['error']}"
                        actions = [f"Tick: {exec_plan.get('reason')}", summary]
                        errors.append(job)
                    report = {
                        "id": f"rpt-{job_id}",
                        "job_id": job_id,
                        "backlog_id": it.get("id"),
                        "title": it.get("title"),
                        "status": job["status"],
                        "summary": summary,
                        "actions": actions,
                        "details": {"agent": job.get("agent"), "execution": exec_plan},
                        "pr_url": job.get("pr_url"),
                        "source": "scheduler-agent",
                    }
                    write_report(report)
                    job["latest_report_id"] = report["id"]
                else:
                    result = initiate_item(
                        str(it["id"]),
                        try_spawn_grok=should_spawn,
                    )
                    if not result.get("ok"):
                        job["status"] = "failed"
                        job["error"] = result.get("error") or "initiate failed"
                        job["finished_at"] = _now()
                        report = {
                            "id": f"rpt-{job_id}",
                            "job_id": job_id,
                            "backlog_id": it.get("id"),
                            "title": it.get("title"),
                            "status": "failed",
                            "summary": f"Failed to initiate: {job['error']}",
                            "actions": ["Scheduler tick attempted initiate_item"],
                            "source": "scheduler-tick",
                        }
                        write_report(report)
                        job["latest_report_id"] = report["id"]
                        errors.append(job)
                        continue

                    spawn = result.get("spawn") or {}
                    spawn_ok = bool(spawn.get("ok")) if spawn.get("attempted") else False
                    job["seed_path"] = result.get("seed_path")
                    job["prompt_path"] = result.get("prompt_path")
                    job["launch_script"] = result.get("launch_script")
                    job["spawn"] = spawn

                    if should_spawn and spawn_ok:
                        job["status"] = "launched"
                        job["launched_at"] = _now()
                        summary = (
                            f"Auto-started goal for “{it.get('title')}”. "
                            f"Spawn: {spawn.get('method') or 'ok'}."
                        )
                        actions = [
                            f"Tick execution: {exec_plan.get('reason')}",
                            "Ran initiate_item + spawned Grok",
                            f"Seed: {result.get('seed_path')}",
                        ]
                        launched.append(job)
                    else:
                        job["status"] = "pending_terminal"
                        job["pending_at"] = _now()
                        job["claim_hint"] = (
                            f"bash {result.get('launch_script')}"
                            if result.get("launch_script")
                            else "auto-claimed on Mac dashboard load"
                        )
                        if should_spawn and spawn.get("attempted") and not spawn_ok:
                            job["spawn_error"] = spawn.get("error") or "spawn failed"
                            summary = (
                                f"Prepared “{it.get('title')}” but Grok spawn failed. "
                                "Queued for Terminal auto-claim."
                            )
                        else:
                            summary = (
                                f"Prepared “{it.get('title')}” "
                                f"({exec_plan.get('reason')}). "
                                "Awaiting Terminal auto-claim."
                            )
                        actions = [
                            f"Tick execution: {exec_plan.get('reason')}",
                            "Ran initiate_item (seed + launch script)",
                            f"Seed: {result.get('seed_path')}",
                        ]
                        pending.append(job)

                    report = {
                        "id": f"rpt-{job_id}",
                        "job_id": job_id,
                        "backlog_id": it.get("id"),
                        "title": it.get("title"),
                        "status": job["status"],
                        "summary": summary,
                        "actions": actions,
                        "details": {
                            "objective_preview": (result.get("goal_objective") or "")[:400],
                            "spawn": spawn,
                            "execution": exec_plan,
                        },
                        "source": "scheduler-tick",
                    }
                    write_report(report)
                    job["latest_report_id"] = report["id"]

                # clear auto_start so we don't re-fire; stamp job link
                data = load_backlog()
                for bi in data.get("items") or []:
                    if bi.get("id") == it.get("id"):
                        bi["auto_start"] = False
                        bi["last_auto_started_at"] = _now()
                        bi["last_job_id"] = job_id
                        if job.get("pr_url"):
                            bi["last_pr_url"] = job["pr_url"]
                        bi["updated_at"] = _now()
                        break
                save_backlog(data)
            except Exception as e:
                job["status"] = "failed"
                job["error"] = str(e)
                job["finished_at"] = _now()
                errors.append(job)

    jobs_data["jobs"] = jobs
    save_jobs(jobs_data)

    queued_n = int((pre_loop.get("queue") or {}).get("count") or 0)
    result = {
        "ok": True,
        "ticked_at": _now(),
        "pre_loop": pre_loop,
        "auto_queued_count": queued_n,
        "launched": launched,
        "launched_count": len(launched),
        "pending_terminal": pending,
        "pending_terminal_count": len(pending),
        "pr_ready": pr_ready,
        "pr_ready_count": len(pr_ready),
        "errors": errors,
        "reconciled": reconciled,
        "reconcile": recon,
        "active_jobs": len(_active_jobs(jobs)),
        "eligible_remaining": len(_eligible_items(cfg, jobs)),
        "execution": exec_plan,
        "reports": list_reports(limit=10),
        "config": load_config(),
        "message": (
            f"Tick: queued {queued_n}, launched {len(launched)}, "
            f"pending Terminal {len(pending)}, PRs {len(pr_ready)}"
            + (f", errors {len(errors)}" if errors else "")
            + (f", reconciled {reconciled}" if reconciled else "")
        ),
    }
    cfg["last_tick_at"] = result["ticked_at"]
    cfg["last_tick_result"] = {
        "auto_queued_count": queued_n,
        "launched_count": result["launched_count"],
        "pending_terminal_count": result["pending_terminal_count"],
        "pr_ready_count": result["pr_ready_count"],
        "errors": len(errors),
        "reconciled": result["reconciled"],
        "execution_mode": exec_plan.get("mode"),
        "execution_reason": exec_plan.get("reason"),
    }
    save_config(cfg)
    result["config"] = load_config()
    return result


def claim_pending_jobs(
    *,
    max_jobs: int = 1,
    job_id: Optional[str] = None,
) -> dict[str, Any]:
    """Open Terminal (or headless grok) for jobs in ``pending_terminal``.

    Intended for a Mac frontend when the Pi (or any headless host) prepared
    seeds but could not spawn an interactive Grok session.
    """
    runtime = detect_runtime()
    jobs_data = load_jobs()
    jobs = list(jobs_data.get("jobs") or [])
    pending = [
        j
        for j in jobs
        if j.get("status") == "pending_terminal"
        and (not job_id or j.get("id") == job_id)
    ]
    if not pending:
        return {
            "ok": True,
            "claimed": [],
            "claimed_count": 0,
            "message": "No pending_terminal jobs",
            "runtime": runtime,
        }

    claimed = []
    errors = []
    for job in pending[: max(1, int(max_jobs))]:
        launch = job.get("launch_script")
        if not launch:
            # try backlog item
            item = get_item(str(job.get("backlog_id") or ""))
            launch = (item or {}).get("launch_script")
        if not launch:
            errors.append({"id": job.get("id"), "error": "no launch_script"})
            continue
        path = WORKSPACE_ROOT / launch
        if not path.is_file():
            errors.append({"id": job.get("id"), "error": f"missing {launch}"})
            continue
        try:
            if runtime.get("has_macos_terminal"):
                proc = subprocess.run(
                    ["open", "-a", "Terminal", str(path.resolve())],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if proc.returncode != 0:
                    raise OSError(proc.stderr or "open Terminal failed")
                method = "open -a Terminal → launch.sh"
            elif runtime.get("has_grok"):
                subprocess.Popen(
                    ["bash", str(path.resolve())],
                    cwd=str(WORKSPACE_ROOT),
                    start_new_session=True,
                )
                method = "bash launch.sh (headless)"
            else:
                errors.append(
                    {
                        "id": job.get("id"),
                        "error": "no Terminal/grok on this host — run launch script on a Mac",
                        "launch_script": launch,
                    }
                )
                continue
            job["status"] = "launched"
            job["launched_at"] = _now()
            job["claimed_at"] = _now()
            job["claim_method"] = method
            job["updated_at"] = _now()
            report = {
                "id": f"rpt-{job.get('id')}-claim",
                "job_id": job.get("id"),
                "backlog_id": job.get("backlog_id"),
                "title": job.get("title"),
                "status": "launched",
                "summary": f"Claimed pending job on frontend ({method}).",
                "actions": [
                    "claim_pending_jobs opened launch script",
                    f"Script: {launch}",
                ],
                "source": "scheduler-claim",
            }
            write_report(report)
            job["latest_report_id"] = report["id"]
            claimed.append(job)
        except Exception as e:
            errors.append({"id": job.get("id"), "error": str(e)})

    save_jobs(jobs_data)
    return {
        "ok": True,
        "claimed": claimed,
        "claimed_count": len(claimed),
        "errors": errors,
        "runtime": runtime,
        "message": (
            f"Claimed {len(claimed)} pending job(s)"
            + (f" · {len(errors)} error(s)" if errors else "")
        ),
    }


def complete_job(
    job_id: str,
    *,
    summary: str = "",
    status: str = "completed",
) -> dict[str, Any]:
    """Manually complete a job and attach a status report."""
    jobs_data = load_jobs()
    job = next((j for j in jobs_data.get("jobs") or [] if j.get("id") == job_id), None)
    if not job:
        return {"ok": False, "error": "job not found"}
    if status not in ("completed", "failed", "cancelled"):
        status = "completed"
    job["status"] = status
    job["completed_at"] = _now()
    job["result"] = summary or status
    bid = job.get("backlog_id")
    if bid and status == "completed":
        update_item(str(bid), {"status": "done"})
    report = {
        "id": f"rpt-{job_id}-done",
        "job_id": job_id,
        "backlog_id": bid,
        "title": job.get("title"),
        "status": status,
        "summary": summary or f"Job marked {status}",
        "actions": [
            f"Job {job_id} set to {status}",
            f"Manual report at {_now()}",
        ],
        "source": "scheduler-manual-complete",
    }
    write_report(report)
    job["latest_report_id"] = report["id"]
    save_jobs(jobs_data)
    return {"ok": True, "job": job, "report": report}


def run_autonomous_loop(
    *,
    groom: bool = True,
    queue: bool = True,
    min_groom_interval_sec: int = 45,
) -> dict[str, Any]:
    """Groom + auto-queue for dashboard load (cheap; does not launch jobs).

    Full kickoff (including groom/queue) lives in ``tick()`` so the Pi timer
    is autonomous without requiring a Mac dashboard open.
    """
    from datetime import datetime, timezone

    from backlog_groom import groom_backlog

    out: dict[str, Any] = {
        "ok": True,
        "groomed": False,
        "groom": None,
        "queue": None,
    }
    if groom:
        data = load_backlog()
        last = data.get("last_groomed_at")
        do_groom = True
        if last and min_groom_interval_sec > 0:
            try:
                ts = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - ts).total_seconds()
                if age < min_groom_interval_sec:
                    do_groom = False
                    out["groom_skipped"] = True
                    out["groom_skip_reason"] = f"groomed {int(age)}s ago"
            except (TypeError, ValueError):
                pass
        if do_groom:
            g = groom_backlog(apply=True)
            out["groomed"] = True
            out["groom"] = {
                "ok": g.get("ok"),
                "message": g.get("message"),
                "count": g.get("count"),
                "changes": len(g.get("changes") or []),
                "groomed_at": g.get("groomed_at"),
            }
    if queue:
        out["queue"] = auto_queue_scheduled()
    # Sync merged PRs → done + clear orphaned "running" jobs
    try:
        out["reconcile"] = reconcile_jobs(save=True)
    except Exception as e:
        out["reconcile"] = {"ok": False, "error": str(e)}
    # Auto-claim pending Terminal jobs when this host can open Grok (Mac dashboard)
    cfg = load_config()
    claim_out = None
    if cfg.get("auto_claim_on_load", True):
        runtime = detect_runtime()
        if runtime.get("can_spawn_terminal") or runtime.get("has_macos_terminal"):
            max_c = int(cfg.get("auto_claim_max") or 1)
            pending_n = sum(
                1
                for j in (load_jobs().get("jobs") or [])
                if j.get("status") == "pending_terminal"
            )
            if pending_n:
                claim_out = claim_pending_jobs(max_jobs=max_c)
                out["claim"] = {
                    "ok": claim_out.get("ok"),
                    "claimed_count": claim_out.get("claimed_count"),
                    "message": claim_out.get("message"),
                    "errors": claim_out.get("errors"),
                }
    parts = []
    if out.get("groom") and out["groom"].get("message"):
        parts.append(out["groom"]["message"])
    elif out.get("groom_skipped"):
        parts.append("Groom skipped (recent)")
    q = out.get("queue") or {}
    if q.get("count"):
        parts.append(q.get("message") or f"Queued {q['count']}")
    if claim_out and claim_out.get("claimed_count"):
        parts.append(claim_out.get("message") or f"Auto-claimed {claim_out['claimed_count']}")
    rec = out.get("reconcile") or {}
    if rec.get("merged"):
        parts.append(f"{rec['merged']} PR(s) merged → done")
    if rec.get("orphaned"):
        parts.append(f"cleared {rec['orphaned']} stale job(s)")
    out["message"] = " · ".join(parts) if parts else "Autonomous loop idle"
    return out


def mark_dashboard_load() -> dict[str, Any]:
    """Record dashboard load time (call after building PR 'new since' list)."""
    cfg = load_config()
    prev = cfg.get("last_dashboard_load_at")
    cfg["last_dashboard_load_at"] = _now()
    save_config(cfg)
    return {"ok": True, "previous": prev, "current": cfg["last_dashboard_load_at"]}


def scheduler_payload() -> dict[str, Any]:
    cfg = load_config()
    jobs_data = load_jobs()
    jobs = list(jobs_data.get("jobs") or [])
    jobs_sorted = sorted(
        jobs, key=lambda j: j.get("created_at") or "", reverse=True
    )
    cron = cron_status()
    exec_plan = resolve_execution_mode(cfg)
    # Eligible uses auto_start + schedule filters; list for UI even when disabled.
    eligible = _eligible_items(cfg, jobs)
    # also list auto_start items even if disabled for UI
    auto_items = [
        {
            "id": it.get("id"),
            "title": it.get("title"),
            "status": it.get("status"),
            "schedule_slot": it.get("schedule_slot"),
            "press_rank": it.get("press_rank"),
            "auto_start": True,
            "auto_start_source": it.get("auto_start_source"),
        }
        for it in list_items(include_done=False, ranked=True)
        if it.get("auto_start")
    ]
    pending_terminal = [j for j in jobs_sorted if j.get("status") == "pending_terminal"]
    pr_jobs = [j for j in jobs_sorted if j.get("status") == "pr_ready" or j.get("pr_url")]
    last_load = cfg.get("last_dashboard_load_at")
    new_prs = []
    for j in pr_jobs:
        when = j.get("pr_ready_at") or j.get("updated_at") or j.get("created_at") or ""
        if last_load and when and str(when) <= str(last_load):
            # still show all pr_ready; mark is_new for UI
            j = dict(j)
            j["is_new_since_load"] = False
        else:
            j = dict(j)
            j["is_new_since_load"] = True
            new_prs.append(j)
    return {
        "ok": True,
        "config": cfg,
        "cron": cron,
        "runtime": exec_plan.get("runtime"),
        "execution": exec_plan,
        "jobs": jobs_sorted[:40],
        "active_jobs": _active_jobs(jobs),
        "pending_terminal_jobs": pending_terminal[:20],
        "pr_ready_jobs": pr_jobs[:20],
        "new_prs_since_last_load": new_prs[:20],
        "eligible": [
            {
                "id": it.get("id"),
                "title": it.get("title"),
                "status": it.get("status"),
                "schedule_slot": it.get("schedule_slot"),
                "press_rank": it.get("press_rank"),
            }
            for it in eligible
        ],
        "auto_start_items": auto_items,
        "reports": list_reports(limit=20),
        "paths": {
            "config": str(CONFIG_PATH.relative_to(WORKSPACE_ROOT)),
            "jobs": str(JOBS_PATH.relative_to(WORKSPACE_ROOT)),
            "reports": str(REPORTS_DIR.relative_to(WORKSPACE_ROOT)),
        },
        "deploy_hint": (
            "Pi 24/7: bash projects-dashboard/deploy/install_remote.sh prism-agent@HOST. "
            "Needs GITHUB_TOKEN + Grok CLI on Pi for full agent→PR autonomy."
        ),
        "note": (
            "Loop: backlog → groom/schedule → tick (agent on Pi or Terminal on Mac) → "
            "PR review on dashboard. pending_terminal auto-claims when this Mac loads "
            "the dashboard. pr_ready jobs need human review/merge."
        ),
    }
