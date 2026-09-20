#!/usr/bin/env python3
"""youtube-groom per-tick listed/add/skip/quota report. Log/read only.

Not a second playlist writer. Reads Pi
``~/.local/share/youtube-groom/groom.log`` (override with
``YOUTUBE_GROOM_DIR``). Never calls the YouTube Data API, never copies
over the live writer at ``~/.local/lib/youtube-groom/youtube_groom.py``.

Not #759. Durable surfaces Grok/GVG can read without SSH-grepping
``groom.log``:

  * ``$YOUTUBE_GROOM_DIR/tick_report.json`` (mode 600) — last tick + 24h rollup
  * ``$YOUTUBE_GROOM_DIR/ticks.jsonl`` — append-only, deduped by ``at``
  * 15m ``export-day-packets.sh`` copy → ``ops/board/youtube_groom_tick_report.json``

Diagnosis: listed high + add low + scoring skips → scoring; listed itself
low with empty skip → supply. Quota headroom is required before any
seed/tick crank.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import statistics
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

STATE_DIR = Path(
    os.environ.get("YOUTUBE_GROOM_DIR", Path.home() / ".local" / "share" / "youtube-groom")
)
LOG_PATH = STATE_DIR / "groom.log"
REPORT_PATH = STATE_DIR / "tick_report.json"
JSONL_PATH = STATE_DIR / "ticks.jsonl"
PI_WRITER_PATH = Path.home() / ".local" / "lib" / "youtube-groom" / "youtube_groom.py"

# Match live Pi writer + nest policy scorecard. Do not import youtube_groom.py
# on Pi — that file is the writer (google API), not this log reader.
HOUSE_TARGET = 100
DAILY_SOFT_CAP = 8000
YOUTUBE_DAILY_UNITS = 10000
WINDOW = timedelta(hours=24)
SCHEMA_VERSION = 1
LISTED_SUPPLY_RATIO = 0.85  # listed/remain below this fraction of house → supply candidate
SCORING_SKIP_PREFIXES = ("fit=",)
SCORING_SKIP_KEYS = frozenset({"throttled-decay"})

TS_ISO = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)"
)
INT_FIELD = re.compile(r"\b(?P<key>listed|remain|add|quota|house|del)=(?P<val>\d+)")
BOOL_FIELD = re.compile(r"\b(?P<key>hour0|dry)=(?P<val>True|False)")
APPEND_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T")


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


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _balanced_mapping(line: str, key: str) -> dict[str, Any]:
    token = key + "="
    idx = line.find(token)
    if idx < 0:
        return {}
    rest = line[idx + len(token) :]
    if not rest.startswith("{"):
        return {}
    depth = 0
    for i, ch in enumerate(rest):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = rest[: i + 1]
                try:
                    val = ast.literal_eval(blob)
                except (SyntaxError, ValueError):
                    return {}
                return val if isinstance(val, dict) else {}
    return {}


def parse_tick_line(line: str) -> Optional[dict[str, Any]]:
    """Parse a successful listed=/add=/skip=/quota= completion line."""
    if "listed=" not in line or "add=" not in line or "quota=" not in line:
        return None
    if re.search(r"\bERROR\b", line, re.I):
        return None
    m = TS_ISO.search(line)
    if not m:
        return None
    at = parse_ts(m.group("ts"))
    if at is None:
        return None
    fields: dict[str, int] = {}
    for fm in INT_FIELD.finditer(line):
        fields[fm.group("key")] = int(fm.group("val"))
    if "listed" not in fields or "add" not in fields or "quota" not in fields:
        return None
    flags: dict[str, bool] = {}
    for fm in BOOL_FIELD.finditer(line):
        flags[fm.group("key")] = fm.group("val") == "True"
    skip = _balanced_mapping(line, "skip")
    delete_reasons = {}
    if "del=" in line:
        # delete-reason dict sits immediately after del=N
        del_m = re.search(r"\bdel=\d+\s+", line)
        if del_m:
            rest = line[del_m.end() :]
            if rest.startswith("{"):
                delete_reasons = _balanced_mapping("skip=" + rest, "skip")
    return {
        "at": iso(at),
        "at_dt": at,
        "hour0": bool(flags.get("hour0", False)),
        "dry_run": bool(flags.get("dry", False)),
        "listed": fields["listed"],
        "remain": fields.get("remain", fields["listed"]),
        "add": fields["add"],
        "skip": {str(k): int(v) for k, v in skip.items()},
        "quota": fields["quota"],
        "house": fields.get("house", HOUSE_TARGET),
        "deleted": fields.get("del", 0),
        "delete_reasons": {str(k): int(v) for k, v in delete_reasons.items()},
        "source": "append" if APPEND_PREFIX.match(line.lstrip()) else "info",
    }


def parse_ticks(text: str) -> list[dict[str, Any]]:
    """Deduped successful ticks, oldest first. Prefer the ISO append line."""
    by_at: dict[str, dict[str, Any]] = {}
    for raw in text.splitlines():
        tick = parse_tick_line(raw)
        if tick is None:
            continue
        key = tick["at"]
        prev = by_at.get(key)
        if prev is None or (prev["source"] != "append" and tick["source"] == "append"):
            by_at[key] = tick
    ticks = sorted(by_at.values(), key=lambda t: t["at_dt"])
    return ticks


def _median_int(values: list[int]) -> Optional[int]:
    if not values:
        return None
    return int(round(statistics.median(values)))


def quota_deltas(ticks: list[dict[str, Any]]) -> list[int]:
    """Same-UTC-day positive quota steps (skip midnight reset)."""
    out: list[int] = []
    prev: Optional[dict[str, Any]] = None
    for tick in ticks:
        if prev is not None:
            same_day = tick["at_dt"].date() == prev["at_dt"].date()
            delta = int(tick["quota"]) - int(prev["quota"])
            if same_day and delta > 0:
                out.append(delta)
        prev = tick
    return out


def skip_is_scoring(skip: dict[str, Any]) -> bool:
    for key in skip:
        k = str(key)
        if k in SCORING_SKIP_KEYS:
            return True
        if any(k.startswith(p) for p in SCORING_SKIP_PREFIXES):
            return True
    return False


def diagnose(
    last: Optional[dict[str, Any]],
    rollup: dict[str, Any],
    *,
    house_target: int = HOUSE_TARGET,
) -> dict[str, Any]:
    if last is None:
        return {
            "verdict": "unknown",
            "rule": (
                "listed high + add low + scoring skips → scoring; "
                "listed itself low + skip empty → supply"
            ),
            "evidence": "no successful listed=/add=/quota= ticks in window",
        }
    house = int(last.get("house") or house_target)
    listed = int(last["listed"])
    remain = int(last.get("remain") or listed)
    add = int(last["add"])
    skip = dict(last.get("skip") or {})
    skip_24h = dict(rollup.get("skip_reasons") or {})
    scoring = skip_is_scoring(skip) or skip_is_scoring(skip_24h)
    supply_low = min(listed, remain) < int(house * LISTED_SUPPLY_RATIO)
    skip_empty = not skip and not skip_24h

    if not supply_low and remain >= house:
        verdict = "filled"
        evidence = (
            f"remain={remain} listed={listed} already at house={house}; "
            f"add={add} skip={skip or {}}"
        )
    elif scoring and supply_low:
        verdict = "mixed"
        evidence = (
            f"listed={listed} remain={remain} below house={house} "
            f"AND scoring skips {skip_24h or skip}"
        )
    elif scoring:
        verdict = "scoring"
        evidence = (
            f"listed={listed} remain={remain} house={house} add={add} "
            f"but skip={skip_24h or skip} (fit=/throttled-decay)"
        )
    elif supply_low and skip_empty:
        verdict = "supply"
        evidence = (
            f"listed={listed} remain={remain} vs house={house}; "
            f"add={add} skip={{}} over {rollup.get('tick_count', 0)} ticks in 24h "
            f"(add_sum={rollup.get('add_sum')}). Filter is not the limiter."
        )
    elif skip_empty and add == 0 and listed >= int(house * LISTED_SUPPLY_RATIO):
        verdict = "scoring"
        evidence = (
            f"listed={listed} high, add=0, skip empty — scoring may have "
            f"already emptied the candidate pool this tick"
        )
    else:
        verdict = "supply" if supply_low else "filled"
        evidence = (
            f"listed={listed} remain={remain} house={house} add={add} "
            f"skip={skip or {}}"
        )
    return {
        "verdict": verdict,
        "rule": (
            "listed high + add low + scoring skips → scoring; "
            "listed itself low + skip empty → supply"
        ),
        "evidence": evidence,
        "listed": listed,
        "remain": remain,
        "add": add,
        "house": house,
        "skip": skip,
        "skip_24h": skip_24h,
    }


def lever_plan(
    last: Optional[dict[str, Any]],
    ticks: list[dict[str, Any]],
    diagnosis: dict[str, Any],
    *,
    soft_cap: int = DAILY_SOFT_CAP,
    youtube_cap: int = YOUTUBE_DAILY_UNITS,
) -> dict[str, Any]:
    quota = int(last["quota"]) if last else 0
    remaining_soft = max(0, soft_cap - quota)
    remaining_yt = max(0, youtube_cap - quota)
    deltas = quota_deltas(ticks)
    median_tick = _median_int(deltas)
    half_hour_day = (median_tick or 0) * 48
    hourly_day = (median_tick or 0) * 24
    half_ok = bool(median_tick) and half_hour_day <= soft_cap
    hourly_ok = bool(median_tick) and hourly_day <= soft_cap
    verdict = diagnosis.get("verdict")
    supply = verdict in {"supply", "mixed"}

    def proposal(lever: str, apply: bool, why: str) -> dict[str, Any]:
        return {
            "lever": lever,
            "apply": apply and supply,
            "why": why,
        }

    broaden_ok = supply and remaining_soft >= 500
    more_ticks_ok = supply and half_ok
    # MAX_INSERTS_PER_TICK already removed; add budget is house-after-prune.
    add_cap_ok = False
    proposals = [
        proposal(
            "broaden_seed_channel_set",
            broaden_ok,
            (
                "Live seed set is 6 keepers + 4 throttle. Skip empty means "
                "scoring is not dropping candidates; more channels add cheap "
                f"list units (~1) and insert 50/video. Soft remaining={remaining_soft}."
                if broaden_ok
                else (
                    "Not applying: "
                    + (
                        "verdict is not supply."
                        if not supply
                        else f"soft remaining={remaining_soft} < 500."
                    )
                )
            ),
        ),
        proposal(
            "more_ticks_per_day",
            more_ticks_ok,
            (
                f"median {median_tick} units/tick. Hourly 24× ≈ {hourly_day} "
                f"(soft {soft_cap} {'ok' if hourly_ok else 'tight'}). "
                f"30-min 48× ≈ {half_hour_day} "
                f"{'fits' if half_ok else 'exceeds'} soft cap — do not 2× ticks."
            ),
        ),
        proposal(
            "raise_per_tick_add_cap",
            add_cap_ok,
            (
                "MAX_INSERTS_PER_TICK already removed. Add budget is "
                "min(HOUSE_TARGET-after_prune, CAP-after_prune) ≈ 40 when "
                "house sits ~60. Observed add is 1–4 because seed inventory "
                "is that small, not because a cap fires."
            ),
        ),
    ]
    return {
        "quota_used_today": quota,
        "daily_soft_cap": soft_cap,
        "youtube_daily_units": youtube_cap,
        "quota_remaining_soft": remaining_soft,
        "quota_remaining_youtube": remaining_yt,
        "median_units_per_tick": median_tick,
        "hourly_24x_units": hourly_day if median_tick else None,
        "half_hour_48x_units": half_hour_day if median_tick else None,
        "hourly_fits_soft": hourly_ok,
        "half_hour_fits_soft": half_ok,
        "crank_without_quota": False,
        "proposals": proposals,
    }


def rollup_24h(
    ticks: list[dict[str, Any]],
    *,
    now: datetime,
    window: timedelta = WINDOW,
) -> dict[str, Any]:
    end = now
    start = end - window
    windowed = [t for t in ticks if start <= t["at_dt"] <= end]
    skip_reasons: dict[str, int] = {}
    for t in windowed:
        for k, v in (t.get("skip") or {}).items():
            skip_reasons[str(k)] = skip_reasons.get(str(k), 0) + int(v)
    adds = [int(t["add"]) for t in windowed]
    listed = [int(t["listed"]) for t in windowed]
    remain = [int(t["remain"]) for t in windowed]
    quotas = [int(t["quota"]) for t in windowed]
    last = windowed[-1] if windowed else None
    top_skip = sorted(skip_reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return {
        "window_hours": int(window.total_seconds() // 3600),
        "from": iso(start),
        "to": iso(end),
        "tick_count": len(windowed),
        "listed_min": min(listed) if listed else None,
        "listed_max": max(listed) if listed else None,
        "listed_last": listed[-1] if listed else None,
        "remain_min": min(remain) if remain else None,
        "remain_max": max(remain) if remain else None,
        "remain_last": remain[-1] if remain else None,
        "add_sum": sum(adds) if adds else 0,
        "add_mean": round(sum(adds) / len(adds), 2) if adds else None,
        "add_min": min(adds) if adds else None,
        "add_max": max(adds) if adds else None,
        "skip_reasons": skip_reasons,
        "skip_top": [{"reason": k, "count": v} for k, v in top_skip],
        "skip_ticks_nonempty": sum(1 for t in windowed if t.get("skip")),
        "quota_last": quotas[-1] if quotas else None,
        "quota_peak": max(quotas) if quotas else None,
        "house_last": int(last["house"]) if last else HOUSE_TARGET,
        "last_at": last["at"] if last else None,
    }


def public_tick(tick: dict[str, Any]) -> dict[str, Any]:
    return {
        "at": tick["at"],
        "hour0": tick["hour0"],
        "dry_run": tick["dry_run"],
        "listed": tick["listed"],
        "remain": tick["remain"],
        "add": tick["add"],
        "skip": tick["skip"],
        "quota": tick["quota"],
        "house": tick["house"],
        "deleted": tick["deleted"],
        "delete_reasons": tick["delete_reasons"],
    }


def build_report(
    text: str,
    *,
    now: Optional[datetime] = None,
    log_path: Path = LOG_PATH,
    missing_log: bool = False,
) -> dict[str, Any]:
    now = now or utcnow()
    ticks = parse_ticks(text)
    windowed_source = ticks
    rollup = rollup_24h(windowed_source, now=now)
    last = ticks[-1] if ticks else None
    if last is not None and last["at_dt"] < now - WINDOW:
        # last tick is older than the window; still surface it, rollup may be empty
        pass
    diagnosis = diagnose(last, rollup)
    levers = lever_plan(last, ticks, diagnosis)
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": last is not None,
        "at": iso(now),
        "log_path": str(log_path),
        "missing_log": missing_log,
        "copy_over_pi": False,
        "pi_writer_path": str(PI_WRITER_PATH),
        "issue": 838,
        "not_issue": 759,
        "last_tick": public_tick(last) if last else None,
        "rollup_24h": rollup,
        "diagnosis": diagnosis,
        "quota": levers,
        "tick_count_all": len(ticks),
    }


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-tick-report-")
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


def append_jsonl(path: Path, tick: dict[str, Any], *, dry_run: bool) -> bool:
    """Append last tick if ``at`` is new. Returns True when a line would be/was written."""
    if dry_run:
        existing: set[str] = set()
        if path.is_file():
            for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("at"):
                    existing.add(str(row["at"]))
        return tick["at"] not in existing
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = False
    if path.is_file():
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("at") == tick["at"]:
                seen = True
                break
    if seen:
        return False
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(public_tick(tick), sort_keys=True) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return True


def load_log(path: Path) -> tuple[str, bool]:
    if not path.is_file():
        return "", True
    try:
        return path.read_text(encoding="utf-8", errors="replace"), False
    except OSError:
        return "", True


def run_report(
    *,
    log_path: Path = LOG_PATH,
    report_path: Path = REPORT_PATH,
    jsonl_path: Path = JSONL_PATH,
    dry_run: bool = False,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    text, missing = load_log(log_path)
    payload = build_report(text, now=now, log_path=log_path, missing_log=missing)
    appended = False
    last = payload.get("last_tick")
    if last and not missing:
        # Re-parse to get at_dt-bearing tick for jsonl via payload last_tick.
        appended = append_jsonl(jsonl_path, last, dry_run=dry_run)
    payload["jsonl_appended"] = appended
    payload["jsonl_path"] = str(jsonl_path)
    payload["report_path"] = str(report_path)
    payload["dry_run"] = dry_run
    # #852 control loop: idempotent per last_tick.at. Lives alongside this
    # reader (same dir on Pi). Never import youtube_groom — that's the writer.
    try:
        here = Path(__file__).resolve().parent
        if str(here) not in sys.path:
            sys.path.insert(0, str(here))
        from youtube_groom_control import attach_to_report as _attach_control

        payload = _attach_control(
            payload,
            dry_run=dry_run,
            state_dir=report_path.parent,
            apply_timer_changes=False,
        )
    except Exception as exc:
        payload["control_error"] = f"{type(exc).__name__}: {exc}"
        payload.setdefault("copy_over_pi", False)
    if not dry_run:
        atomic_write(report_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Per-tick listed/add/skip/quota rollup from groom.log (no YouTube I/O)."
    )
    parser.add_argument("--dry-run", action="store_true", help="evaluate; do not persist")
    parser.add_argument("--json", action="store_true", help="print full report JSON")
    parser.add_argument("--log", type=Path, default=None, help="override groom.log path")
    parser.add_argument("--report", type=Path, default=None, help="override tick_report.json")
    parser.add_argument("--jsonl", type=Path, default=None, help="override ticks.jsonl")
    parser.add_argument("--now", default=None, help="ISO timestamp for window end (tests)")
    args = parser.parse_args(argv)

    now = parse_ts(args.now) if args.now else None
    result = run_report(
        log_path=args.log or LOG_PATH,
        report_path=args.report or REPORT_PATH,
        jsonl_path=args.jsonl or JSONL_PATH,
        dry_run=args.dry_run,
        now=now,
    )
    if args.json or args.dry_run:
        print(json.dumps(result, indent=2))
    else:
        last = result.get("last_tick") or {}
        rollup = result.get("rollup_24h") or {}
        diag = result.get("diagnosis") or {}
        quota = result.get("quota") or {}
        skip = last.get("skip") or {}
        summary = {
            "ok": result.get("ok"),
            "listed": last.get("listed"),
            "add": last.get("add"),
            "skip": skip,
            "quota": last.get("quota"),
            "remain": last.get("remain"),
            "house": last.get("house"),
            "verdict": diag.get("verdict"),
            "tick_count_24h": rollup.get("tick_count"),
            "add_sum_24h": rollup.get("add_sum"),
            "quota_remaining_soft": quota.get("quota_remaining_soft"),
        }
        print(json.dumps(summary, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
