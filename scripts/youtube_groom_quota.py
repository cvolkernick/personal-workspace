#!/usr/bin/env python3
"""YouTube quota day, tick gate, and writer patch for #1074.

Sidecar for the live writer at ``~/.local/lib/youtube-groom/youtube_groom.py``.
Do not copy ``scripts/youtube_groom.py`` (the nest scorecard) over that binary.

YouTube Data API quota resets at midnight America/Los_Angeles. The writer
used to key ``state.quota.day`` on the UTC date, so the 8000-unit soft cap
was spent between 00:00Z and ~07:00Z and every later tick raised.

``groom()`` is the tick entry the writer calls. It makes no API calls when
the remaining soft budget is below ``MIN_TICK_COST``.
"""

from __future__ import annotations

import ast
import math
import os
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

QUOTA_TZ = "America/Los_Angeles"
MIN_TICK_COST = 80
DAILY_SOFT_CAP = 8000
SEARCH_UNIT_COST = 100
HARD_MAX_SEARCHES = 4
TICK_INTERVAL_MIN = 30
QUARANTINE_AFTER = 3
BACKUP_ISSUE = "1074"

COST = {
    "list": 1,
    "videos": 1,
    "rating": 1,
    "channels": 1,
    "delete": 50,
    "insert": 50,
    "update": 50,
    "search": 100,
    "subscriptions": 1,
}


class QuotaCapReached(RuntimeError):
    """Soft cap would be exceeded. Not an uncaught groom failure."""

    def __init__(self, kind: str, n: int, used: int, cap: int) -> None:
        self.kind = kind
        self.n = n
        self.used = used
        self.cap = cap
        super().__init__(
            f"quota soft-cap would exceed on {kind} x{n} (used={used})"
        )


class PatchError(RuntimeError):
    pass


def pt_zone() -> ZoneInfo:
    return ZoneInfo(QUOTA_TZ)


def as_utc(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def pt_now(now: Optional[datetime] = None) -> datetime:
    if now is None:
        now = datetime.now(timezone.utc)
    return as_utc(now).astimezone(pt_zone())


def quota_day(now: Optional[datetime] = None) -> date:
    return pt_now(now).date()


def next_reset(now: Optional[datetime] = None) -> datetime:
    """Next America/Los_Angeles midnight, as a zoned datetime."""
    local = pt_now(now)
    return (local + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def quota_window_start(now: Optional[datetime] = None) -> datetime:
    local = pt_now(now)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def _pt_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=pt_zone())
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _utc_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _overlaps(
    a0: datetime, a1: datetime, b0: datetime, b1: datetime
) -> bool:
    return a0 < b1 and b0 < a1


def reconcile_quota(
    record: Optional[dict[str, Any]],
    now: datetime,
    *,
    soft_cap: int = DAILY_SOFT_CAP,
) -> dict[str, Any]:
    """Key the counter on the Pacific quota day.

    A legacy record has no ``tz`` and stores a UTC date. Keep its units when
    that UTC day overlaps the Pacific day in progress, so a deploy does not
    grant a second budget for units YouTube has already counted. Otherwise
    reset. Always store ``tz``.
    """
    del soft_cap  # the cap is enforced by Quota, not by the stored record
    today = quota_day(now)
    src = record if isinstance(record, dict) else {}
    try:
        units = int(src.get("units") or 0)
    except (TypeError, ValueError):
        units = 0
    if units < 0:
        units = 0
    stored_tz = src.get("tz")
    raw_day = src.get("day")
    if stored_tz == QUOTA_TZ:
        if raw_day == today.isoformat():
            return {"day": today.isoformat(), "units": units, "tz": QUOTA_TZ}
        return {"day": today.isoformat(), "units": 0, "tz": QUOTA_TZ}
    try:
        utc_day = date.fromisoformat(str(raw_day))
    except (TypeError, ValueError):
        return {"day": today.isoformat(), "units": 0, "tz": QUOTA_TZ}
    u0, u1 = _utc_bounds(utc_day)
    p0, p1 = _pt_bounds(today)
    if _overlaps(u0, u1, p0, p1):
        return {"day": today.isoformat(), "units": units, "tz": QUOTA_TZ}
    return {"day": today.isoformat(), "units": 0, "tz": QUOTA_TZ}


class Quota:
    def __init__(
        self,
        state: dict[str, Any],
        soft_cap: int = DAILY_SOFT_CAP,
        now: Optional[datetime] = None,
    ) -> None:
        self.state = state
        self.soft_cap = int(soft_cap)
        self.now = as_utc(now or datetime.now(timezone.utc))
        state["quota"] = reconcile_quota(
            state.get("quota"), self.now, soft_cap=self.soft_cap
        )

    @property
    def used(self) -> int:
        return int(self.state["quota"]["units"])

    def remaining(self) -> int:
        return self.soft_cap - self.used

    def charge(self, kind: str, n: int = 1) -> None:
        self.state["quota"]["units"] = self.used + COST[kind] * n

    def can(self, kind: str, n: int = 1) -> bool:
        return self.remaining() >= COST[kind] * n

    def require(self, kind: str, n: int = 1) -> None:
        if not self.can(kind, n):
            raise QuotaCapReached(kind, n, self.used, self.soft_cap)
        self.charge(kind, n)


def ticks_left(
    now: datetime, *, interval_min: int = TICK_INTERVAL_MIN
) -> int:
    local = pt_now(now)
    reset = next_reset(now)
    seconds = max(0.0, (reset - local).total_seconds())
    return max(1, math.ceil(seconds / (interval_min * 60)))


def paced_max_searches(
    remaining: int,
    now: datetime,
    *,
    min_tick: int = MIN_TICK_COST,
    search_cost: int = SEARCH_UNIT_COST,
    hard_max: int = HARD_MAX_SEARCHES,
    interval_min: int = TICK_INTERVAL_MIN,
) -> int:
    """Search calls this tick can afford without starving later ticks.

    A fair share of ~166 units cannot hold four ``search.list`` calls.
    Reserve ``min_tick`` for every remaining tick, then place at most one
    search on a stride of the Pacific half-hours when the spare allows it.
    """
    ticks = ticks_left(now, interval_min=interval_min)
    remaining = max(0, int(remaining))
    fair = remaining // ticks
    room = fair - min_tick
    if room >= search_cost:
        return min(hard_max, room // search_cost)
    spare = remaining - min_tick * ticks
    if spare < search_cost:
        return 0
    affordable = spare // search_cost
    stride = max(1, math.ceil(ticks / affordable))
    local = pt_now(now)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    slot = int((local - midnight).total_seconds() // (interval_min * 60))
    if slot % stride == 0:
        return 1
    return 0


def resets_at_iso(now: datetime) -> str:
    return next_reset(now).isoformat()


def _append_log(path: Optional[Path], line: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")


def begin_tick(
    state: dict[str, Any],
    quota: Quota,
    now: datetime,
    *,
    dry_run: bool = False,
    save_state: Optional[Callable[[dict[str, Any]], None]] = None,
    log: Any = None,
    log_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Return a skip result when the tick cannot pay for one API round.

    ``None`` means the caller may make API calls. A skip persists ``last_result``
    and does not charge units.
    """
    if quota.remaining() >= MIN_TICK_COST:
        return None
    resets = resets_at_iso(now)
    line = f"quota_skip used={quota.used} cap={quota.soft_cap} resets_at={resets}"
    result = {
        "ok": True,
        "skipped": "quota_cap",
        "exit_code": 0,
        "at": as_utc(now).isoformat().replace("+00:00", "Z"),
        "dry_run": bool(dry_run),
        "quota_used_today": quota.used,
        "quota_remaining_soft": quota.remaining(),
        "cap": quota.soft_cap,
        "resets_at": resets,
        "log_line": line,
    }
    state["last_result"] = {
        "at": result["at"],
        "dry_run": result["dry_run"],
        "skipped": "quota_cap",
        "quota_used_today": quota.used,
        "resets_at": resets,
    }
    if log is not None:
        log.info("%s", line)
    if not dry_run:
        if save_state is not None:
            save_state(state)
        _append_log(log_path, line)
    return result


def finish_cap(
    state: dict[str, Any],
    quota: Quota,
    exc: QuotaCapReached,
    *,
    dry_run: bool = False,
    save_state: Optional[Callable[[dict[str, Any]], None]] = None,
    log: Any = None,
    log_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Stop a tick that already did some work. No traceback, no ``groom failed``."""
    partial = dict(state.get("last_result") or {})
    tick = state.pop("_tick_partial", None) or {}
    if tick:
        if "deleted" in tick:
            partial["deleted"] = tick["deleted"]
        if "added" in tick:
            partial["added"] = tick["added"]
    line = f"quota_cap {exc}"
    partial.update(
        {
            "ok": True,
            "skipped": "quota_cap",
            "exit_code": 0,
            "partial": True,
            "quota_used_today": quota.used,
            "quota_remaining_soft": quota.remaining(),
            "log_line": line,
        }
    )
    state["last_result"] = {
        k: partial[k]
        for k in partial
        if k in {
            "at",
            "dry_run",
            "skipped",
            "partial",
            "deleted",
            "added",
            "quota_used_today",
            "listed",
            "remaining",
        }
    }
    if log is not None:
        log.info("%s", line)
    if not dry_run:
        if save_state is not None:
            save_state(state)
        _append_log(log_path, line)
    return partial


def groom(
    args: Any,
    state: dict[str, Any],
    *,
    now: datetime,
    run_body: Callable[[Quota], dict[str, Any]],
    save_state: Optional[Callable[[dict[str, Any]], None]] = None,
    log: Any = None,
    log_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Run one tick. ``run_body`` is the only place API calls may happen."""
    soft = int(getattr(args, "quota_budget", DAILY_SOFT_CAP))
    dry = bool(getattr(args, "dry_run", False))
    quota = Quota(state, soft, now=now)
    skipped = begin_tick(
        state,
        quota,
        now,
        dry_run=dry,
        save_state=save_state,
        log=log,
        log_path=log_path,
    )
    if skipped is not None:
        return skipped
    try:
        return run_body(quota)
    except QuotaCapReached as exc:
        return finish_cap(
            state,
            quota,
            exc,
            dry_run=dry,
            save_state=save_state,
            log=log,
            log_path=log_path,
        )


def note_seed_404(state: dict[str, Any], channel_id: str) -> int:
    bucket = state.setdefault("seed_404", {})
    count = int(bucket.get(channel_id) or 0) + 1
    bucket[channel_id] = count
    return count


def clear_seed_404(state: dict[str, Any], channel_id: str) -> None:
    bucket = state.get("seed_404")
    if isinstance(bucket, dict):
        bucket.pop(channel_id, None)


def seed_quarantined(state: dict[str, Any], channel_id: str) -> bool:
    bucket = state.get("seed_404") or {}
    try:
        return int(bucket.get(channel_id) or 0) >= QUARANTINE_AFTER
    except (TypeError, ValueError):
        return False


def reraise_if_quota(exc: BaseException) -> None:
    if isinstance(exc, QuotaCapReached):
        raise exc


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise PatchError(f"{label}: expected 1 occurrence, found {count}")
    return text.replace(old, new, 1)


def patch_writer_source(text: str) -> str:
    """Surgical edit of the live writer. Does not replace the file with the scorecard."""
    updated = text
    if "from youtube_groom_quota import (" in updated:
        pass
    else:
        updated = _replace_once(
            updated,
            "from googleapiclient.errors import HttpError\n",
            "from googleapiclient.errors import HttpError\n"
            "from youtube_groom_quota import (\n"
            "    Quota,\n"
            "    QuotaCapReached,\n"
            "    clear_seed_404,\n"
            "    groom as quota_groom,\n"
            "    note_seed_404,\n"
            "    paced_max_searches,\n"
            "    reraise_if_quota,\n"
            "    seed_quarantined,\n"
            ")\n",
            "import",
        )
    old_quota = '''class Quota:
    def __init__(self, state: dict[str, Any], soft_cap: int) -> None:
        today = utcnow().date().isoformat()
        q = state.setdefault("quota", {"day": today, "units": 0})
        if q.get("day") != today:
            q["day"] = today
            q["units"] = 0
        self.state = state
        self.soft_cap = soft_cap

    @property
    def used(self) -> int:
        return int(self.state["quota"]["units"])

    def remaining(self) -> int:
        return self.soft_cap - self.used

    def charge(self, kind: str, n: int = 1) -> None:
        self.state["quota"]["units"] = self.used + COST[kind] * n

    def can(self, kind: str, n: int = 1) -> bool:
        return self.remaining() >= COST[kind] * n


'''
    if old_quota in updated:
        updated = updated.replace(old_quota, "", 1)
    old_exec = '''    def _exec(self, kind: str, req: Any, n: int = 1) -> Any:
        if not self.quota.can(kind, n):
            raise RuntimeError(f"quota soft-cap would exceed on {kind} x{n} (used={self.quota.used})")
        self.quota.charge(kind, n)
        return req.execute()
'''
    new_exec = '''    def _exec(self, kind: str, req: Any, n: int = 1) -> Any:
        self.quota.require(kind, n)
        return req.execute()
'''
    if old_exec in updated:
        updated = updated.replace(old_exec, new_exec, 1)
    elif "self.quota.require(kind, n)" not in updated:
        raise PatchError("missing _exec soft-cap raise")
    old_gate = '''    state = load_state()
    quota = Quota(state, args.quota_budget)
    yt = YT(dry_run=args.dry_run, quota=quota)
'''
    new_gate = '''    state = load_state()
    return quota_groom(
        args,
        state,
        now=utcnow(),
        run_body=lambda quota: _groom_after_gate(args, state, quota),
        save_state=save_state,
        log=log,
        log_path=LOG_PATH,
    )


def _groom_after_gate(
    args: argparse.Namespace, state: dict[str, Any], quota: Quota
) -> dict[str, Any]:
    yt = YT(dry_run=args.dry_run, quota=quota)
'''
    if old_gate in updated:
        updated = updated.replace(old_gate, new_gate, 1)
    elif "quota_groom(" not in updated:
        raise PatchError("missing groom quota init")
    old_uploads = '''        for cid, name in seed_map.items():
            try:
                ups = yt.uploads(cid, max_results=SEED_UPLOADS_PER_CHANNEL)
            except HttpError as exc:
                log.warning("uploads %s failed: %s", name, exc)
                continue
'''
    new_uploads = '''        for cid, name in seed_map.items():
            if seed_quarantined(state, cid):
                log.info("seed_quarantine skip %s", name)
                continue
            try:
                ups = yt.uploads(cid, max_results=SEED_UPLOADS_PER_CHANNEL)
            except QuotaCapReached:
                raise
            except HttpError as exc:
                status = getattr(getattr(exc, "resp", None), "status", None)
                if status == 404:
                    note_seed_404(state, cid)
                log.warning("uploads %s failed: %s", name, exc)
                continue
            else:
                clear_seed_404(state, cid)
'''
    if old_uploads in updated:
        updated = updated.replace(old_uploads, new_uploads, 1)
    elif "seed_quarantined(state, cid)" not in updated:
        raise PatchError("missing seed uploads loop")
    old_supply_exc = '''        except Exception:
            log.warning("supply lane failed")
'''
    new_supply_exc = '''        except Exception as exc:
            reraise_if_quota(exc)
            log.warning("supply lane failed")
'''
    if old_supply_exc in updated:
        updated = updated.replace(old_supply_exc, new_supply_exc, 1)
    old_long_exc = '''        except Exception:
            log.warning("longform filter failed")
'''
    new_long_exc = '''        except Exception as exc:
            reraise_if_quota(exc)
            log.warning("longform filter failed")
'''
    if old_long_exc in updated:
        updated = updated.replace(old_long_exc, new_long_exc, 1)
    old_prep_exc = '''        except Exception:
            log.warning("supply prepare failed")
'''
    new_prep_exc = '''        except Exception as exc:
            reraise_if_quota(exc)
            log.warning("supply prepare failed")
'''
    if old_prep_exc in updated:
        updated = updated.replace(old_prep_exc, new_prep_exc, 1)
    old_search_arg = '''                quota_can=quota.can,
                now=utcnow(),
'''
    new_search_arg = '''                quota_can=quota.can,
                max_searches=paced_max_searches(quota.remaining(), utcnow()),
                now=utcnow(),
'''
    if "paced_max_searches(quota.remaining()" not in updated:
        if old_search_arg not in updated:
            raise PatchError("missing supply_tick quota_can")
        updated = updated.replace(old_search_arg, new_search_arg, 1)
    old_deleted = '''            deleted.append({"reason": reason, "video_id": it["video_id"], "title": it["title"], "channel": it["channel"]})
'''
    new_deleted = '''            deleted.append({"reason": reason, "video_id": it["video_id"], "title": it["title"], "channel": it["channel"]})
            state.setdefault("_tick_partial", {})["deleted"] = len(deleted)
'''
    if old_deleted in updated and '["deleted"] = len(deleted)' not in updated:
        updated = updated.replace(old_deleted, new_deleted, 1)
    old_added = '''                )
                try:
                    from youtube_groom_supply import record_add
'''
    new_added = '''                )
                state.setdefault("_tick_partial", {})["added"] = len(added)
                try:
                    from youtube_groom_supply import record_add
'''
    if old_added in updated and '["added"] = len(added)' not in updated:
        updated = updated.replace(old_added, new_added, 1)
    old_save = '''    if not args.dry_run:
        save_state(state)
'''
    new_save = '''    state.pop("_tick_partial", None)
    if not args.dry_run:
        save_state(state)
'''
    if old_save in updated and 'state.pop("_tick_partial"' not in updated:
        updated = updated.replace(old_save, new_save, 1)
    old_main = '''    except Exception as exc:
        log.exception("groom failed")
'''
    new_main = '''    except QuotaCapReached as exc:
        log.info("quota_cap %s", exc)
        print(json.dumps({"ok": True, "skipped": "quota_cap", "error": str(exc)}))
        return 0
    except Exception as exc:
        log.exception("groom failed")
'''
    if old_main in updated and "except QuotaCapReached as exc:" not in updated:
        updated = updated.replace(old_main, new_main, 1)
    old_summary = '''    if args.json:
        print(json.dumps(result, indent=2))
'''
    new_summary = '''    if isinstance(result, dict) and result.get("skipped") == "quota_cap":
        print(json.dumps({
            "ok": True,
            "skipped": "quota_cap",
            "quota_used_today": result.get("quota_used_today"),
            "resets_at": result.get("resets_at"),
            "partial": bool(result.get("partial")),
        }))
        return 0

    if args.json:
        print(json.dumps(result, indent=2))
'''
    if old_summary in updated and 'result.get("skipped") == "quota_cap"' not in updated:
        updated = updated.replace(old_summary, new_summary, 1)
    ast.parse(updated)
    return updated


def patch_writer_file(path: Path, *, backup: bool = True, today: Optional[str] = None) -> dict[str, Any]:
    """Patch the live writer in place. Backup first. Do not replace the file."""
    original = path.read_text(encoding="utf-8")
    updated = patch_writer_source(original)
    if updated == original:
        return {"changed": False, "path": str(path), "backup": None}
    backup_path = None
    if backup:
        stamp = today or datetime.now(timezone.utc).strftime("%Y%m%d")
        backup_path = path.with_name(f"{path.name}.bak-{stamp}-{BACKUP_ISSUE}")
        shutil.copy2(path, backup_path)
    path.write_text(updated, encoding="utf-8")
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass
    return {
        "changed": True,
        "path": str(path),
        "backup": str(backup_path) if backup_path else None,
    }


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="YouTube groom quota-day sidecar. Does not copy the scorecard over the writer."
    )
    parser.add_argument("--patch-writer", type=Path, default=None)
    parser.add_argument("--check-writer", type=Path, default=None)
    parser.add_argument("--backup-date", default=None, help="YYYYMMDD stamp for the writer backup")
    args = parser.parse_args(argv)
    if args.check_writer:
        patch_writer_source(args.check_writer.read_text(encoding="utf-8"))
        print(f"patch ok {args.check_writer}")
        return 0
    if args.patch_writer:
        result = patch_writer_file(args.patch_writer, today=args.backup_date)
        print(json.dumps(result))
        return 0
    print(
        f"youtube_groom_quota tz={QUOTA_TZ} min_tick={MIN_TICK_COST} "
        f"soft_cap={DAILY_SOFT_CAP}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
