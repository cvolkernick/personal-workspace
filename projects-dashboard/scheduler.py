"""Local auto-start scheduler for approved/queued backlog work.

Cron (or manual tick) picks items with ``auto_start=true`` that are eligible
(status ready / scheduled now|this_week, not already planning/running) and
runs ``initiate_item`` (kick off Grok /goal). Status reports land under
``ops/backlog/reports/`` and are shown on the dashboard.

Local-first. Later: same job store can be consumed by a Raspberry Pi agent.
"""

from __future__ import annotations

import json
import os
import subprocess
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
    "backend": "local",  # local | raspi (future)
    "cron_expression": "*/15 * * * *",
    "max_per_tick": 1,
    "max_concurrent": 1,
    "eligible_statuses": ["ready"],
    "eligible_slots": ["now", "this_week"],
    # When true, only items with auto_start=true are kicked off.
    # auto_queue_scheduled sets that flag for ready+scheduled items after groom.
    "require_auto_start": True,
    "auto_queue_scheduled": True,
    "spawn_grok": True,
    "install_marker": "projects-dashboard-scheduler",
    "last_tick_at": None,
    "last_tick_result": None,
}

JOB_STATUSES = (
    "queued",
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
    """Approve item for automatic initiate-goal on the next eligible tick."""
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
        if (found.get("status") or "") == "idea":
            # Auto-start implies approved for kickoff; keep idea→ready signal for ranking
            found["status"] = "ready"
    else:
        found.pop("auto_start_source", None)
    save_backlog(data)
    return {"ok": True, "item": found, "auto_start": bool(enabled)}


def auto_queue_scheduled(*, force: bool = False) -> dict[str, Any]:
    """Queue ready + now/this_week items for the next scheduler tick.

    Part of the autonomous loop: after groom schedules work, mark it
    auto_start so cron (or Run tick) can initiate without a manual click.
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
    queued: list[dict[str, Any]] = []
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
    if dirty:
        save_backlog(data)
    return {
        "ok": True,
        "queued": queued,
        "count": len(queued),
        "skipped": False,
        "message": (
            f"Auto-queued {len(queued)} scheduled item(s) for kickoff"
            if queued
            else "No new items to auto-queue"
        ),
    }


def _active_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [j for j in jobs if j.get("status") in ("queued", "launched", "running")]


def _eligible_items(cfg: dict[str, Any], jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    statuses = set(cfg.get("eligible_statuses") or ["ready"])
    slots = set(cfg.get("eligible_slots") or ["now", "this_week"])
    require_auto = bool(cfg.get("require_auto_start", True))
    in_flight_ids = {
        j.get("backlog_id")
        for j in jobs
        if j.get("status") in ("queued", "launched", "running")
    }
    out = []
    for it in list_items(include_done=False, ranked=True):
        bid = it.get("id")
        if not bid or bid in in_flight_ids:
            continue
        if (it.get("status") or "") in ("planning", "active", "done", "parked"):
            # planning/active already started manually or previously
            if (it.get("status") or "") in ("planning", "active"):
                continue
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
    """Install/replace local crontab entry for the scheduler tick."""
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
    return {"ok": True, "installed": True, "line": desired, "config": load_config()}


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
    """Mark launched/running jobs completed when backlog status is done/parked."""
    updated = []
    for job in jobs_data.get("jobs") or []:
        if job.get("status") not in ("launched", "running", "queued"):
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
    return updated


def tick(*, force: bool = False) -> dict[str, Any]:
    """Run one scheduler cycle (called by cron or dashboard)."""
    cfg = load_config()
    if not cfg.get("enabled") and not force:
        return {
            "ok": True,
            "skipped": True,
            "reason": "scheduler disabled",
            "config": cfg,
        }

    jobs_data = load_jobs()
    jobs = list(jobs_data.get("jobs") or [])
    reconciled = _reconcile_completions(jobs_data)

    max_conc = int(cfg.get("max_concurrent") or 1)
    max_tick = int(cfg.get("max_per_tick") or 1)
    active = _active_jobs(jobs)
    slots = max(0, max_conc - len(active))
    launched = []
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
                "schedule_slot": it.get("schedule_slot"),
                "press_rank": it.get("press_rank"),
            }
            jobs.append(job)
            try:
                result = initiate_item(
                    str(it["id"]),
                    try_spawn_grok=bool(cfg.get("spawn_grok", True)),
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

                job["status"] = "launched"
                job["launched_at"] = _now()
                job["spawn"] = result.get("spawn")
                job["seed_path"] = result.get("seed_path")
                job["prompt_path"] = result.get("prompt_path")
                job["launch_script"] = result.get("launch_script")
                report = {
                    "id": f"rpt-{job_id}",
                    "job_id": job_id,
                    "backlog_id": it.get("id"),
                    "title": it.get("title"),
                    "status": "launched",
                    "summary": (
                        f"Auto-started goal for “{it.get('title')}”. "
                        f"Backlog status → planning. Spawn: "
                        f"{(result.get('spawn') or {}).get('method') or 'n/a'}."
                    ),
                    "actions": [
                        "Selected by scheduler tick (auto_start + schedule/status filters)",
                        "Ran initiate_item (seed + /goal prompt + optional Terminal launch)",
                        f"Seed: {result.get('seed_path')}",
                        f"Prompt: {result.get('prompt_path')}",
                    ],
                    "details": {
                        "objective_preview": (result.get("goal_objective") or "")[:400],
                        "spawn": result.get("spawn"),
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
                        bi["updated_at"] = _now()
                        break
                save_backlog(data)
                launched.append(job)
            except Exception as e:
                job["status"] = "failed"
                job["error"] = str(e)
                job["finished_at"] = _now()
                errors.append(job)

    jobs_data["jobs"] = jobs
    save_jobs(jobs_data)

    result = {
        "ok": True,
        "ticked_at": _now(),
        "launched": launched,
        "launched_count": len(launched),
        "errors": errors,
        "reconciled": len(reconciled),
        "active_jobs": len(_active_jobs(jobs)),
        "eligible_remaining": len(_eligible_items(cfg, jobs)),
        "reports": list_reports(limit=10),
        "config": load_config(),
    }
    cfg["last_tick_at"] = result["ticked_at"]
    cfg["last_tick_result"] = {
        "launched_count": result["launched_count"],
        "errors": len(errors),
        "reconciled": result["reconciled"],
    }
    save_config(cfg)
    result["config"] = load_config()
    return result


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
    """Groom backlog and auto-queue scheduled ready work (dashboard load / cron pre-step).

    Does not kick off jobs — that remains ``tick()`` / cron so load stays cheap.
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
    parts = []
    if out.get("groom") and out["groom"].get("message"):
        parts.append(out["groom"]["message"])
    elif out.get("groom_skipped"):
        parts.append("Groom skipped (recent)")
    q = out.get("queue") or {}
    if q.get("count"):
        parts.append(q.get("message") or f"Queued {q['count']}")
    out["message"] = " · ".join(parts) if parts else "Autonomous loop idle"
    return out


def scheduler_payload() -> dict[str, Any]:
    cfg = load_config()
    jobs_data = load_jobs()
    jobs = list(jobs_data.get("jobs") or [])
    jobs_sorted = sorted(
        jobs, key=lambda j: j.get("created_at") or "", reverse=True
    )
    cron = cron_status()
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
    return {
        "ok": True,
        "config": cfg,
        "cron": cron,
        "jobs": jobs_sorted[:40],
        "active_jobs": _active_jobs(jobs),
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
        "note": (
            "Autonomous loop: dashboard load grooms + auto-queues ready items in "
            "now/this_week; cron (or Run tick) initiates goals and writes status reports. "
            "Later: same store on Raspberry Pi."
        ),
    }
