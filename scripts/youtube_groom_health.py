#!/usr/bin/env python3
"""youtube-groom tick health — log/alert only. Not a second playlist writer.

Reads Pi ``~/.local/share/youtube-groom/groom.log`` (override with
``YOUTUBE_GROOM_DIR``). Never calls the YouTube Data API, never copies
over the live writer at ``~/.local/lib/youtube-groom/youtube_groom.py``.

Unhealthy when:
  * no successful ``listed=`` / INFO completion within STALE_AFTER of now
    (2h — two missed hourly fires), OR
  * last tick shows RefreshError / invalid_grant / uncaught exception.

Alerts Grok on #workflow only: one message on broken transition, one on
recovery, optional daily reminder if still broken >24h. Never DMs Chris
and never mentions Chris's pubkey.

Ledger: ``$YOUTUBE_GROOM_DIR/health.json`` (mode 600). The 15m
``export-day-packets.sh`` copies it to ``ops/board/youtube_groom_health.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

STATE_DIR = Path(
    os.environ.get("YOUTUBE_GROOM_DIR", Path.home() / ".local" / "share" / "youtube-groom")
)
LOG_PATH = STATE_DIR / "groom.log"
HEALTH_PATH = STATE_DIR / "health.json"
PI_WRITER_PATH = Path.home() / ".local" / "lib" / "youtube-groom" / "youtube_groom.py"
CEREMONY_DIR = Path.home() / ".local" / "lib" / "ceremony-clock"
CEREMONY_VENV = CEREMONY_DIR / "venv" / "bin" / "python"

WORKFLOW_CHANNEL = "db0e8f97-0c81-4976-b299-1c460b87134e"
# Grok (orchestrator). Do not add Chris's pubkey — Pi must not DM/notify him.
GROK_PUBKEY = "213349578fbf53a20fda8b56d0229fca699033d349aa0af00d0a860070f2f2b1"
CHRIS_PUBKEY = "c54a48a274943cf41a20d190ac87e03d8b3dd5c90b024bdc25256926952049ac"

STALE_AFTER = timedelta(hours=2)
REMINDER_AFTER = timedelta(hours=24)
SCHEMA_VERSION = 1

SUCCESS_RE = re.compile(r"\blisted\s*=\s*\d+")
ERROR_LEVEL_RE = re.compile(r"\bERROR\b", re.I)
TS_ISO = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)"
)
TS_LOG = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?P<ms>,\d+)?"
)

FAILURE_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("invalid_grant", re.compile(r"invalid_grant", re.I)),
    ("RefreshError", re.compile(r"RefreshError", re.I)),
    (
        "uncaught",
        re.compile(
            r"groom failed|Traceback \(most recent call last\)|unhandled|uncaught",
            re.I,
        ),
    ),
)

Poster = Callable[[str, list[list[str]]], dict[str, Any]]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(raw: str) -> Optional[datetime]:
    raw = raw.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def line_timestamp(line: str) -> Optional[datetime]:
    m = TS_ISO.match(line)
    if m:
        return parse_ts(m.group("ts"))
    m = TS_LOG.match(line)
    if m:
        return parse_ts(m.group("ts").replace(" ", "T") + "+00:00")
    return None


def classify_failure(line: str) -> Optional[str]:
    for kind, pat in FAILURE_KINDS:
        if pat.search(line):
            return kind
    return None


def is_success_line(line: str) -> bool:
    if ERROR_LEVEL_RE.search(line):
        return False
    return bool(SUCCESS_RE.search(line))


@dataclass
class LogScan:
    last_success_at: Optional[datetime] = None
    last_success_line: str = ""
    last_failure_at: Optional[datetime] = None
    last_failure_kind: Optional[str] = None
    last_failure_excerpt: str = ""
    # File append order, not wall-clock compare. Success is ISO UTC
    # (listed= append); failures are logging asctime (host local, no TZ).
    # Comparing those clocks on prism (America/New_York) hid last-tick auth.
    last_tick: Optional[str] = None  # "success" | "failure"
    missing_log: bool = False
    empty_log: bool = False


@dataclass
class HealthDecision:
    status: str  # healthy | broken | skipped
    reason: str
    scan: LogScan
    alert_kind: Optional[str] = None  # broken | recovery | reminder
    previous_status: Optional[str] = None
    broken_since: Optional[datetime] = None
    last_alert_at: Optional[datetime] = None
    last_alert_kind: Optional[str] = None
    persist: bool = True
    post: bool = False
    message: str = ""
    extra_tags: list[list[str]] = field(default_factory=list)


def scan_log(text: str) -> LogScan:
    scan = LogScan(empty_log=not text.strip())
    current_ts: Optional[datetime] = None
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        ts = line_timestamp(line)
        if ts is not None:
            current_ts = ts
        if not line.strip():
            continue
        if is_success_line(line):
            scan.last_success_at = current_ts or scan.last_success_at
            scan.last_success_line = line.strip()[:240]
            scan.last_tick = "success"
        kind = classify_failure(line)
        if kind:
            scan.last_failure_at = current_ts or scan.last_failure_at
            scan.last_failure_kind = kind
            scan.last_failure_excerpt = line.strip()[:240]
            scan.last_tick = "failure"
    return scan


def scan_log_path(path: Path) -> LogScan:
    if not path.is_file():
        return LogScan(missing_log=True)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return LogScan(missing_log=True)
    return scan_log(text)


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    return parse_ts(value)


def load_health(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-health-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def prod_writer_present() -> bool:
    return PI_WRITER_PATH.is_file()


def alerts_enabled() -> bool:
    raw = os.environ.get("YOUTUBE_GROOM_ALERT", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def alert_channel() -> str:
    return os.environ.get("YOUTUBE_GROOM_ALERT_CHANNEL", WORKFLOW_CHANNEL).strip() or WORKFLOW_CHANNEL


def grok_mention_tags() -> list[list[str]]:
    tags = [
        ["p", GROK_PUBKEY],
        ["youtube-groom-health", "true"],
    ]
    # Belt: never attach Chris even if env is later extended.
    return [t for t in tags if not (t[0] == "p" and t[1].lower() == CHRIS_PUBKEY.lower())]


def classify_status(scan: LogScan, *, now: datetime, writer_present: bool) -> tuple[str, str]:
    """Return (status, reason). skipped = no prod surface, do not alert."""
    if scan.missing_log or scan.empty_log:
        if writer_present:
            return "broken", "missing_log" if scan.missing_log else "empty_log"
        return "skipped", "no_prod_log"
    last_ok = scan.last_success_at
    # Last-tick is scan order (later line in the file), not last_fail >= last_ok.
    # Mixed clocks: ISO UTC success vs asctime-as-UTC would hide a later invalid_grant.
    if scan.last_tick == "failure":
        return "broken", scan.last_failure_kind or "uncaught"
    if last_ok is None:
        return "broken", "no_success"
    if now - last_ok > STALE_AFTER:
        return "broken", "stale_success"
    return "healthy", "ok"


def format_alert(kind: str, decision: HealthDecision) -> str:
    scan = decision.scan
    last_ok = iso(scan.last_success_at) or "never"
    last_fail = iso(scan.last_failure_at) or "none"
    fail_kind = scan.last_failure_kind or decision.reason
    excerpt = scan.last_failure_excerpt or ""
    ledger = str(HEALTH_PATH)
    if kind == "recovery":
        body = (
            f"youtube-groom **recovered** — successful `listed=` at {last_ok} "
            f"(prior status was `{decision.previous_status}`). "
            f"Ledger: `{ledger}`."
        )
    elif kind == "reminder":
        body = (
            f"youtube-groom still **BROKEN** (>24h). reason=`{decision.reason}` "
            f"last_failure={last_fail} (`{fail_kind}`) last_success={last_ok}. "
            f"Ledger: `{ledger}`."
        )
    else:
        body = (
            f"youtube-groom **BROKEN** — `{fail_kind}` reason=`{decision.reason}` "
            f"last_failure={last_fail} last_success={last_ok}. "
            f"Ledger: `{ledger}`."
        )
        if excerpt:
            body += f" excerpt: `{excerpt[:160]}`"
    body += "\n\n@Grok — Pi does not DM Chris. Re-auth stays a human gate."
    return body


def decide(
    scan: LogScan,
    previous: dict[str, Any],
    *,
    now: Optional[datetime] = None,
    writer_present: Optional[bool] = None,
    dry_run: bool = False,
) -> HealthDecision:
    now = now or utcnow()
    if writer_present is None:
        writer_present = prod_writer_present()
    status, reason = classify_status(scan, now=now, writer_present=writer_present)
    prev_status = previous.get("status") if isinstance(previous.get("status"), str) else None
    prev_broken_since = parse_iso(previous.get("broken_since"))
    prev_alert_at = parse_iso(previous.get("last_alert_at"))
    prev_alert_kind = previous.get("last_alert_kind") if isinstance(previous.get("last_alert_kind"), str) else None

    decision = HealthDecision(
        status=status,
        reason=reason,
        scan=scan,
        previous_status=prev_status,
        last_alert_at=prev_alert_at,
        last_alert_kind=prev_alert_kind,
        persist=not dry_run and status != "skipped",
        extra_tags=grok_mention_tags(),
    )

    if status == "skipped":
        decision.persist = False
        return decision

    if status == "broken":
        decision.broken_since = prev_broken_since or (
            scan.last_failure_at or scan.last_success_at or now
        )
        if prev_status != "broken":
            decision.alert_kind = "broken"
        elif prev_alert_at is None:
            decision.alert_kind = "broken"
        elif (now - prev_alert_at) >= REMINDER_AFTER:
            decision.alert_kind = "reminder"
        else:
            decision.alert_kind = None
    else:
        decision.broken_since = None
        if prev_status == "broken":
            decision.alert_kind = "recovery"
        else:
            decision.alert_kind = None

    if decision.alert_kind:
        decision.message = format_alert(decision.alert_kind, decision)
        # Mac checkouts may have a leftover groom.log; never page Grok unless
        # the Pi writer is on this host (or a test injects writer_present=True).
        decision.post = (not dry_run) and alerts_enabled() and bool(writer_present)
    return decision


def health_payload(
    decision: HealthDecision,
    *,
    now: datetime,
    posted: dict[str, Any] | None,
    log_path: Path = LOG_PATH,
) -> dict[str, Any]:
    scan = decision.scan
    last_alert_at = decision.last_alert_at
    last_alert_kind = decision.last_alert_kind
    if posted and posted.get("accepted") and decision.alert_kind:
        last_alert_at = now
        last_alert_kind = decision.alert_kind
    elif decision.alert_kind and not decision.post:
        # dry-run / alerts off: still record what we *would* send in the JSON
        # result, but do not advance last_alert_at on disk unless persist+post.
        pass
    event_id = None
    if posted:
        event_id = posted.get("id") or posted.get("event_id")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": decision.status,
        "reason": decision.reason,
        "checked_at": iso(now),
        "last_success_at": iso(scan.last_success_at),
        "last_failure_at": iso(scan.last_failure_at),
        "last_tick": scan.last_tick,
        "last_failure_kind": scan.last_failure_kind,
        "last_failure_excerpt": scan.last_failure_excerpt,
        "broken_since": iso(decision.broken_since),
        "last_alert_at": iso(last_alert_at),
        "last_alert_kind": last_alert_kind,
        "alert_kind": decision.alert_kind,
        "alert_channel": alert_channel() if decision.alert_kind else None,
        "alert_event_id": event_id,
        "stale_after_hours": STALE_AFTER.total_seconds() / 3600.0,
        "writer_path": str(PI_WRITER_PATH),
        "log_path": str(log_path),
        "mentions": ["Grok"],
    }


CLOCK_POST = r"""
import json, sys
from ceremony_clock import send_channel_message
req = json.load(sys.stdin)
tags = req.get("extra_tags") or []
print(json.dumps(send_channel_message(
    channel_id=req["channel_id"],
    content=req["content"],
    extra_tags=tags,
    dry_run=bool(req.get("dry_run")),
)))
"""


def post_via_clock(content: str, extra_tags: list[list[str]], *, dry_run: bool = False) -> dict[str, Any]:
    if any(t[0] == "p" and t[1].lower() == CHRIS_PUBKEY.lower() for t in extra_tags):
        raise RuntimeError("refusing to mention Chris from youtube-groom health")
    venv = Path(os.environ.get("YOUTUBE_GROOM_CLOCK_PYTHON", str(CEREMONY_VENV)))
    clock_dir = Path(os.environ.get("YOUTUBE_GROOM_CLOCK_DIR", str(CEREMONY_DIR)))
    if not venv.is_file():
        return {"ok": False, "accepted": False, "error": f"no ceremony-clock python: {venv}"}
    payload = json.dumps(
        {
            "channel_id": alert_channel(),
            "content": content,
            "extra_tags": extra_tags,
            "dry_run": dry_run,
        }
    )
    try:
        proc = subprocess.run(
            [str(venv), "-c", CLOCK_POST],
            input=payload,
            capture_output=True,
            text=True,
            cwd=str(clock_dir),
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "accepted": False, "error": str(exc)}
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:400]
        return {"ok": False, "accepted": False, "error": err or f"clock exit {proc.returncode}"}
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "accepted": False, "error": "clock returned non-JSON", "raw": proc.stdout[:200]}
    if isinstance(parsed, dict):
        parsed.setdefault("accepted", bool(parsed.get("id") or parsed.get("dry_run")))
        parsed["ok"] = bool(parsed.get("accepted"))
        return parsed
    return {"ok": False, "accepted": False, "error": "clock returned non-object"}


def run_check(
    *,
    log_path: Path = LOG_PATH,
    health_path: Path = HEALTH_PATH,
    now: Optional[datetime] = None,
    dry_run: bool = False,
    poster: Optional[Poster] = None,
    writer_present: Optional[bool] = None,
) -> dict[str, Any]:
    now = now or utcnow()
    scan = scan_log_path(log_path)
    previous = load_health(health_path)
    decision = decide(
        scan,
        previous,
        now=now,
        writer_present=writer_present,
        dry_run=dry_run,
    )
    posted: dict[str, Any] | None = None
    if decision.post and decision.message:
        send = poster or (
            lambda content, tags: post_via_clock(content, tags, dry_run=False)
        )
        posted = send(decision.message, decision.extra_tags)

    post_ok = bool(posted and posted.get("accepted"))
    persist = decision.persist
    if decision.post and not post_ok:
        # Transition still pending — do not advance last_alert_* so the next
        # scan retries the same broken/recovery/reminder post.
        persist = False

    payload = health_payload(
        decision,
        now=now,
        posted=posted if post_ok else None,
        log_path=log_path,
    )
    payload["would_alert"] = decision.alert_kind
    payload["posted"] = post_ok
    payload["post_error"] = None if not posted or post_ok else posted.get("error")
    payload["dry_run"] = dry_run
    payload["message"] = decision.message or None
    payload["missing_log"] = scan.missing_log
    payload["empty_log"] = scan.empty_log
    if persist:
        atomic_write(health_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect youtube-groom auth/tick failures from groom.log (no YouTube I/O)."
    )
    parser.add_argument("--dry-run", action="store_true", help="evaluate; do not persist or post")
    parser.add_argument("--json", action="store_true", help="print health JSON")
    parser.add_argument("--log", type=Path, default=None, help="override groom.log path")
    parser.add_argument("--health", type=Path, default=None, help="override health.json path")
    args = parser.parse_args(argv)

    result = run_check(
        log_path=args.log or LOG_PATH,
        health_path=args.health or HEALTH_PATH,
        dry_run=args.dry_run,
    )
    if args.json or args.dry_run:
        print(json.dumps(result, indent=2))
    else:
        summary = {
            "ok": result["status"] == "healthy",
            "status": result["status"],
            "reason": result["reason"],
            "would_alert": result.get("would_alert"),
            "posted": result.get("posted"),
            "last_success_at": result.get("last_success_at"),
            "last_failure_at": result.get("last_failure_at"),
        }
        print(json.dumps(summary, indent=2))
    if result["status"] == "broken":
        return 1
    if result["status"] == "skipped":
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
