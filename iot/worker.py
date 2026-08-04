#!/usr/bin/env python3
"""Headless IoT schedule worker (no browser / no UI required).

Runs the same sunrise/sunset routines as the dashboard, suitable for a
Raspberry Pi or always-on host that does not sleep.

Usage (from monorepo root):
  PYTHONPATH=. python3 iot/worker.py
  PYTHONPATH=. python3 iot/worker.py --once          # single tick (cron-friendly)
  PYTHONPATH=. python3 iot/worker.py --interval 30

On a Pi with the tree at ~/iot-workspace:
  cd ~/iot-workspace && PYTHONPATH=. python3 iot/worker.py
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Optional

IOT_DIR = Path(__file__).resolve().parent
ROOT = IOT_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from iot.control import DEFAULT_BRIGHTNESS, load_bulbs, load_groups  # noqa: E402
from iot.schedule import (  # noqa: E402
    DEFAULT_SCHEDULE_PATH,
    DEFAULT_STATE_PATH,
    load_schedule,
    location_from_schedule,
    run_due,
    schedule_status,
)
from iot.sleep_follow import tick_sleep_follow  # noqa: E402
from iot.wiz_adapter import execute_control, run_async  # noqa: E402

log = logging.getLogger("iot.worker")


def _control(target: str, color: str, brightness: Optional[int]):
    bri = DEFAULT_BRIGHTNESS if brightness is None else int(brightness)
    return run_async(
        execute_control(
            target,
            color,
            bri,
            registry=load_bulbs(),
            groups=load_groups(),
        )
    )


def tick(
    *,
    schedule_path: Path,
    state_path: Path,
) -> int:
    """Run one schedule evaluation. Returns number of routines fired."""
    sched = load_schedule(schedule_path)
    loc = location_from_schedule(sched)
    if not loc:
        log.warning(
            "No location configured in %s — set latitude/longitude "
            "(or use the dashboard once) so sunrise/sunset can be computed.",
            schedule_path,
        )
        return 0
    status = schedule_status(sched)
    next_ev = status.get("next_event")
    if next_ev:
        log.info(
            "next=%s at %s (in %ss) color=%s",
            next_ev.get("id"),
            next_ev.get("fire_hhmm"),
            next_ev.get("seconds_until"),
            next_ev.get("color"),
        )
    results = run_due(
        control=_control,
        schedule_path=schedule_path,
        state_path=state_path,
    )
    for r in results:
        rid = (r.get("routine") or {}).get("id")
        cr = r.get("control") or {}
        log.info(
            "FIRED %s ok=%s target=%s color=%s",
            rid,
            cr.get("ok"),
            (r.get("routine") or {}).get("target"),
            (r.get("routine") or {}).get("color"),
        )
        if not cr.get("ok"):
            log.error("control result: %s", cr)

    # After sunset routines: dim master bedroom to FitDash Sleep Battery %
    try:
        sf = tick_sleep_follow(
            control=_control,
            schedule_path=schedule_path,
            state_path=state_path,
        )
        if not sf.get("skipped"):
            log.info(
                "sleep_follow ok=%s pct=%s bri=%s reason=%s",
                sf.get("ok"),
                sf.get("pct_charged"),
                (sf.get("action") or {}).get("brightness"),
                sf.get("error") or sf.get("reason"),
            )
    except Exception:
        log.exception("sleep_follow tick failed")

    return len(results)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Headless IoT schedule worker")
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Seconds between ticks (default 30)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single tick and exit (for cron/systemd timer)",
    )
    parser.add_argument(
        "--schedule",
        type=Path,
        default=None,
        help="Path to schedule.json",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=None,
        help="Path to schedule state JSON",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Debug logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [iot-worker] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    schedule_path = (args.schedule or DEFAULT_SCHEDULE_PATH).resolve()
    state_path = (args.state or DEFAULT_STATE_PATH).resolve()
    state_path.parent.mkdir(parents=True, exist_ok=True)

    log.info("schedule=%s state=%s root=%s", schedule_path, state_path, ROOT)
    if not schedule_path.is_file():
        log.error("schedule file missing: %s", schedule_path)
        return 2

    stop = False

    def _stop(*_a):
        nonlocal stop
        stop = True
        log.info("shutdown signal received")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    if args.once:
        n = tick(schedule_path=schedule_path, state_path=state_path)
        log.info("once tick complete fired=%s", n)
        return 0

    log.info("headless worker running (interval=%ss); Ctrl+C to stop", args.interval)
    while not stop:
        try:
            tick(schedule_path=schedule_path, state_path=state_path)
        except Exception:
            log.exception("tick failed")
        # interruptible sleep
        for _ in range(max(1, int(args.interval))):
            if stop:
                break
            time.sleep(1)
    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
