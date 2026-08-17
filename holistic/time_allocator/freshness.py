"""House-cadence upkeep batteries (sleep-battery math, chore cadence).

Write SoT: holistic/data/freshness.json — not KPI tasks.json.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_HOLISTIC_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FRESHNESS_PATH = _HOLISTIC_ROOT / "data" / "freshness.json"
ENV_FRESHNESS_PATH = "TIME_ALLOCATOR_FRESHNESS"

SEED_ITEMS: list[dict[str, Any]] = [
    {"id": "dishes", "title": "Dishes", "interval_days": 1, "curve": "linear", "last_done": None},
    {"id": "clothes", "title": "Wash clothes", "interval_days": 5, "curve": "linear", "last_done": None},
    {"id": "towels", "title": "Wash towels", "interval_days": 7, "curve": "linear", "last_done": None},
    {"id": "sheets", "title": "Wash sheets", "interval_days": 14, "curve": "linear", "last_done": None},
    {"id": "lawn", "title": "Mow the lawn", "interval_days": 7, "curve": "linear", "last_done": None},
    {"id": "plants", "title": "Water plants", "interval_days": 4, "curve": "cliff", "last_done": None},
    {"id": "groceries", "title": "Grocery shop", "interval_days": 7, "curve": "linear", "last_done": None},
    {"id": "trash", "title": "Trash / recycling", "interval_days": 3, "curve": "linear", "last_done": None},
    {"id": "vacuum", "title": "Vacuum / floors", "interval_days": 7, "curve": "linear", "last_done": None},
    {"id": "bathroom", "title": "Clean bathroom", "interval_days": 14, "curve": "linear", "last_done": None},
    {
        "id": "money-trees",
        "title": "Water Money Trees",
        "interval_days": 7,
        "curve": "cliff",
        "last_done": "2026-08-14T17:35:56Z",
    },
    {
        "id": "air-filter",
        "title": "Replace air filter",
        "interval_days": 90,
        "curve": "linear",
        "last_done": "2026-08-12T11:35:24Z",
    },
    {
        "id": "duchess-bath",
        "title": "Bathe Duchess",
        "interval_days": 45,
        "curve": "linear",
        "last_done": None,
    },
    {
        "id": "water-bowl",
        "title": "Water-bowl filter",
        "interval_days": 30,
        "curve": "linear",
        "last_done": "2026-07-14T03:34:18Z",
    },
]

SEED_BY_ID = {str(item["id"]): item for item in SEED_ITEMS}

OK_MIN = 0.40
MID_MIN = 0.15
CLIFF_HOLD = 0.80


def resolve_freshness_path(
    path: str | Path | None = None, *, data_path: str | Path | None = None
) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()
    env = os.environ.get(ENV_FRESHNESS_PATH)
    if env:
        return Path(env).expanduser().resolve()
    if data_path is not None:
        return Path(data_path).expanduser().resolve().parent / "freshness.json"
    return DEFAULT_FRESHNESS_PATH.resolve()


def parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def charge_linear(u: float) -> float:
    return clamp01(1.0 - u)


def charge_cliff(u: float) -> float:
    if u < CLIFF_HOLD:
        return 1.0
    return clamp01(1.0 - (u - CLIFF_HOLD) / (1.0 - CLIFF_HOLD))


def level_for(charge: float, *, unknown: bool = False, overdue: bool = False) -> str:
    if unknown or overdue or charge < MID_MIN:
        return "red"
    if charge < OK_MIN:
        return "mid"
    return "ok"


def compute_item(item: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    item_id = str(item.get("id") or "").strip()
    title = str(item.get("title") or item_id)
    curve = str(item.get("curve") or "linear").strip().lower()
    if curve not in ("linear", "cliff"):
        curve = "linear"
    interval_days = float(item.get("interval_days") or 0)
    if interval_days <= 0:
        raise ValueError(f"interval_days must be > 0 for {item_id or title}")
    interval = timedelta(days=interval_days)
    last_raw = item.get("last_done")
    last_done = parse_dt(last_raw)

    if last_done is None:
        return {
            "id": item_id,
            "title": title,
            "charge": 0.0,
            "level": "red",
            "empty_at": iso_utc(now),
            "overdue_hours": 0.0,
            "curve": curve,
            "last_done": None,
            "interval_days": interval_days,
        }

    age = now - last_done
    u = age.total_seconds() / interval.total_seconds()
    overdue = u > 1.0
    if overdue:
        charge = 0.0
        overdue_hours = (age - interval).total_seconds() / 3600.0
    else:
        charge = charge_linear(u) if curve == "linear" else charge_cliff(u)
        overdue_hours = 0.0

    return {
        "id": item_id,
        "title": title,
        "charge": round(charge, 6),
        "level": level_for(charge, overdue=overdue),
        "empty_at": iso_utc(last_done + interval),
        "overdue_hours": round(overdue_hours, 4),
        "curve": curve,
        "last_done": iso_utc(last_done),
        "interval_days": interval_days,
    }


def sort_computed(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Emptiest first: charge ASC, then most overdue, then title."""
    return sorted(
        items,
        key=lambda it: (
            float(it.get("charge") or 0.0),
            -float(it.get("overdue_hours") or 0.0),
            str(it.get("title") or "").lower(),
            str(it.get("id") or ""),
        ),
    )


def compute_freshness(
    store: dict[str, Any] | None, *, now: datetime | None = None
) -> dict[str, Any]:
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    raw_items = list((store or {}).get("items") or [])
    computed = [compute_item(item, now=clock) for item in raw_items]
    computed = sort_computed(computed)
    red_count = sum(1 for it in computed if it.get("level") == "red")
    return {
        "items": computed,
        "red_count": red_count,
        "win": red_count == 0 and bool(computed),
        "generated_at": iso_utc(clock),
    }


def _normalize_store_item(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    item_id = str(raw.get("id") or "").strip()
    if not item_id:
        return None
    seed = SEED_BY_ID.get(item_id, {})
    interval = raw.get("interval_days", seed.get("interval_days"))
    try:
        interval_days = float(interval)
    except (TypeError, ValueError):
        interval_days = float(seed.get("interval_days") or 1)
    curve = str(raw.get("curve") or seed.get("curve") or "linear").strip().lower()
    if curve not in ("linear", "cliff"):
        curve = "linear"
    last_done = raw.get("last_done")
    if last_done == "":
        last_done = None
    return {
        "id": item_id,
        "title": str(raw.get("title") or seed.get("title") or item_id),
        "interval_days": interval_days,
        "curve": curve,
        "last_done": last_done,
    }


def merge_seed(store: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    changed = False
    for raw in list((store or {}).get("items") or []):
        item = _normalize_store_item(raw)
        if item is None or item["id"] in seen:
            changed = True
            continue
        seen.add(item["id"])
        items.append(item)
    for seed in SEED_ITEMS:
        if seed["id"] not in seen:
            items.append(dict(seed))
            seen.add(str(seed["id"]))
            changed = True
    version = int((store or {}).get("version") or 1)
    if store is None:
        changed = True
    return {"version": version, "items": items}, changed


def empty_store() -> dict[str, Any]:
    return {"version": 1, "items": [dict(item) for item in SEED_ITEMS]}


def load_freshness(
    path: str | Path | None = None, *, data_path: str | Path | None = None
) -> dict[str, Any]:
    p = resolve_freshness_path(path, data_path=data_path)
    raw: dict[str, Any] | None = None
    if p.is_file():
        loaded = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"invalid freshness store (expected object): {p}")
        raw = loaded
    store, changed = merge_seed(raw)
    if changed or not p.is_file():
        save_freshness(store, path=p)
    return store


def save_freshness(
    store: dict[str, Any], path: str | Path | None = None, *, data_path: str | Path | None = None
) -> Path:
    p = resolve_freshness_path(path, data_path=data_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    merged, _ = merge_seed(store)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(merged, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p


def mark_done(
    store: dict[str, Any],
    item_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    key = (item_id or "").strip()
    if not key:
        raise ValueError("id is required")
    clock = now or datetime.now(timezone.utc)
    merged, _ = merge_seed(store)
    found = False
    items = []
    for item in merged["items"]:
        row = dict(item)
        if str(row.get("id")) == key:
            row["last_done"] = iso_utc(clock)
            found = True
        items.append(row)
    if not found:
        raise KeyError(f"unknown upkeep id: {key}")
    merged["items"] = items
    return merged


def freshness_payload(
    path: str | Path | None = None,
    *,
    data_path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    store = load_freshness(path, data_path=data_path)
    payload = compute_freshness(store, now=now)
    payload["ok"] = True
    payload["path"] = str(resolve_freshness_path(path, data_path=data_path))
    return payload


def mark_done_and_compute(
    item_id: str,
    *,
    path: str | Path | None = None,
    data_path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    p = resolve_freshness_path(path, data_path=data_path)
    store = load_freshness(p)
    updated = mark_done(store, item_id, now=now)
    save_freshness(updated, path=p)
    payload = compute_freshness(updated, now=now)
    payload["ok"] = True
    payload["path"] = str(p)
    return payload
