#!/usr/bin/env python3
"""Time a FitDash payload build, a warm read, /api/agent/today, and JSON size.

Uses a local fixture health cache (no network, no credentials). Set
DASHBOARD_CACHE_DIR before this process imports the dashboard modules if you
want a different cache directory; this script points it at a temp dir itself
when it is the entry point.

    python3 resistance-dashboard/scripts/bench_fitdash_payload.py
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path


def _seed_health(cache_dir: Path) -> None:
    os.environ["DASHBOARD_CACHE_DIR"] = str(cache_dir)
    os.environ.setdefault("FITDASH_BUILD_PARALLEL", "1")
    os.environ.setdefault("GITHUB_PREFER_LOCAL", "1")
    os.environ.setdefault("FITDASH_QUEST_GT_SYNC", "0")
    os.environ.setdefault("HEALTH_PULL_TIMEOUT_SEC", "2")
    os.environ.setdefault("GITHUB_PULL_TIMEOUT_SEC", "2")
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from rt_dashboard.dashboard_cache import save_health_cache
    from rt_dashboard.models import (
        CaloriesBurnedDay,
        FoodLogEntry,
        HealthSnapshot,
        HydrationDay,
        SleepSample,
        StepSample,
        WeightSample,
    )

    today = datetime(2026, 8, 10)
    logs = []
    weight = []
    sleep = []
    burned = []
    hydration = []
    steps = []
    for offset in range(60):
        day = (today - timedelta(days=59 - offset)).strftime("%Y-%m-%d")
        weight.append(WeightSample(date=day, weight_lbs=180.0 - offset * 0.05))
        sleep.append(SleepSample(date=day, sleep_hours=7.1))
        burned.append(CaloriesBurnedDay(date=day, calories=2400))
        hydration.append(HydrationDay(date=day, water_ml=1800))
        steps.append(StepSample(date=day, steps=8000))
        for meal in range(6):
            logs.append(
                FoodLogEntry(
                    date=day,
                    name=f"food-{meal}",
                    calories=180 + meal * 20,
                    protein_g=12 + meal,
                    carbs_g=20,
                    fat_g=6,
                    time=f"{7 + meal * 2:02d}:10",
                    nutrients={"fiber_g": 3.0, "sodium_mg": 400.0},
                )
            )
    save_health_cache(
        HealthSnapshot(
            weight=weight,
            sleep=sleep,
            food_logs=logs,
            hydration=hydration,
            calories_burned=burned,
            steps=steps,
        )
    )


def _ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def main() -> None:
    cache = Path(tempfile.mkdtemp(prefix="fitdash-bench-"))
    _seed_health(cache)
    import server
    from rt_dashboard.agent_today import export_agent_today

    started = time.perf_counter()
    payload = server.build_dashboard_data(force_refresh=False)
    cold_ms = _ms(started)

    server._PAYLOAD_MEMO_ENABLED = True
    server.invalidate_dashboard_memo()
    os.environ["FITDASH_PAYLOAD_TTL_SEC"] = "300"
    server.load_dashboard_data(user_id=None)
    started = time.perf_counter()
    server.load_dashboard_data(user_id=None)
    warm_ms = _ms(started)

    if hasattr(server, "load_agent_today_response"):
        server._PAYLOAD_MEMO_ENABLED = True
        server.invalidate_agent_today_cache()
        started = time.perf_counter()
        server.load_agent_today_response(None, query="", read_only=True)
        today_cold_ms = _ms(started)
        started = time.perf_counter()
        server.load_agent_today_response(None, query="", read_only=True)
        today_warm_ms = _ms(started)
    else:
        started = time.perf_counter()
        export_agent_today(payload)
        today_cold_ms = _ms(started)
        started = time.perf_counter()
        export_agent_today(payload)
        today_warm_ms = _ms(started)

    pretty = json.dumps(payload, indent=2).encode("utf-8")
    compact = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    gz = gzip.compress(compact, compresslevel=5)

    rows = {
        "cold_build_ms": round(cold_ms, 1),
        "warm_read_ms": round(warm_ms, 1),
        "today_cold_ms": round(today_cold_ms, 1),
        "today_warm_ms": round(today_warm_ms, 1),
        "bytes_indent2": len(pretty),
        "bytes_compact": len(compact),
        "bytes_gzip": len(gz),
        "payload_keys": sorted(payload.keys()),
    }
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
