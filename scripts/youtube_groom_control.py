#!/usr/bin/env python3
"""youtube-groom continuous-improvement loop (#852). Not a second writer.

Reads the last tick (from tick_report.json / last_tick dict), measures
distance from HOUSE_TARGET ± TOLERANCE (100 ± 10), and advances **one**
criteria notch per tick. Writes:

  * ``$YOUTUBE_GROOM_DIR/control_state.json`` (mode 600) — knobs + last action
  * ``$YOUTUBE_GROOM_DIR/knobs.json`` — next-tick overlay the live writer loads
  * merges a ``control`` block into ``tick_report.json``

Never calls the YouTube Data API. Never copies over the live writer at
``~/.local/lib/youtube-groom/youtube_groom.py``. Do not import
``youtube_groom`` on Pi — that filename is the writer (google API).

Live-writer hook (surgical in-place, not a nest-copy)::

    try:
        from youtube_groom_control import apply_live_knobs
        apply_live_knobs(globals())
    except Exception:
        pass
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

STATE_DIR = Path(
    os.environ.get("YOUTUBE_GROOM_DIR", Path.home() / ".local" / "share" / "youtube-groom")
)
REPORT_PATH = STATE_DIR / "tick_report.json"
STATE_PATH = STATE_DIR / "control_state.json"
KNOBS_PATH = STATE_DIR / "knobs.json"
JSONL_PATH = STATE_DIR / "control.jsonl"
PI_WRITER_PATH = Path.home() / ".local" / "lib" / "youtube-groom" / "youtube_groom.py"
TIMER_DROPIN = Path.home() / ".config" / "systemd" / "user" / "youtube-groom.timer.d" / "control.conf"

# Match nest policy scorecard. Do not import youtube_groom.py on Pi.
HOUSE_TARGET = 100
HOUSE_TARGET_TOLERANCE = 10
BAND_LOW = HOUSE_TARGET - HOUSE_TARGET_TOLERANCE  # 90
BAND_HIGH = HOUSE_TARGET + HOUSE_TARGET_TOLERANCE  # 110
CAP = 200
DAILY_SOFT_CAP = 8000
YOUTUBE_DAILY_UNITS = 10000
SCHEMA_VERSION = 1
COOLDOWN_TICKS = 2
SEED_BROADEN_SOFT_MIN = 500
INSERT_UNIT_COST = 50

MIN_FIT_NOTCHES = (0, 1, 2)
THROTTLE_NOTCHES = (0.00, 0.05, 0.10, 0.25, 0.40)
TICKS_PER_DAY_NOTCHES = (24, 48)

# Extra seed ladder from the 2026-08-14 inspect (channels that were already
# on AI Curated, not in the live 6 keepers + 4 throttle). One channel per
# broaden notch. IDs resolved from the public @handle pages 2026-09-20.
EXTRA_SEED_LADDER = (
    ("UCtvg5cXLY_tHDJeBoRySBtg", "What Bitcoin Did"),
    ("UCCpNQKYvrnWQNjZprabMJlw", "Peter H. Diamandis"),
    ("UCYXLs8tkNQrENrT1s60rxCw", "Anthony Pompliano"),
    ("UCk6EGp5yqsB-YtBE3AF8dWw", "Bitcoin Magazine"),
    ("UCfs-Vb0DOIZNN0xKyfz-svg", "Natalie Brunell"),
    ("UCPcO_WZXKQa1lFwCGltWc8A", "Brent Johnson Milkshakes Pod"),
)

DEFAULT_KNOBS: dict[str, Any] = {
    "MIN_FIT": 0,
    "SEED_THROTTLE_WEIGHT_FLOOR": 0.10,
    "SEED_EXTRA": {},
    "MAX_INSERTS_PER_TICK": None,
    "ticks_per_day": 24,
    "PRUNE_TO_BAND": False,
    "CAP": CAP,
    "HOUSE_TARGET": HOUSE_TARGET,
    "HOUSE_TARGET_TOLERANCE": HOUSE_TARGET_TOLERANCE,
}

WRITER_HOOK = (
    "try:\n"
    "    from youtube_groom_control import apply_live_knobs\n"
    "    apply_live_knobs(globals())\n"
    "except Exception:\n"
    "    pass\n"
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime] = None) -> str:
    dt = dt or utcnow()
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-yg-control-")
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


def band_metrics(
    last_tick: Optional[dict[str, Any]],
    *,
    house: int = HOUSE_TARGET,
    tolerance: int = HOUSE_TARGET_TOLERANCE,
) -> dict[str, Any]:
    """Playlist count, net new, signed distance from the 100 ± 10 band."""
    low = house - tolerance
    high = house + tolerance
    if not last_tick:
        return {
            "playlist_count": None,
            "added": 0,
            "removed": 0,
            "net_new": 0,
            "band_low": low,
            "band_high": high,
            "house": house,
            "tolerance": tolerance,
            "distance": None,
            "side": "unknown",
            "tick_at": None,
        }
    listed = int(last_tick.get("listed") or 0)
    count = int(last_tick.get("remain") if last_tick.get("remain") is not None else listed)
    added = int(last_tick.get("add") or 0)
    removed = int(last_tick.get("deleted") or 0)
    net = added - removed
    if count < low:
        side = "below"
        distance = low - count
    elif count > high:
        side = "above"
        distance = count - high
    else:
        side = "inside"
        distance = 0
    return {
        "playlist_count": count,
        "added": added,
        "removed": removed,
        "net_new": net,
        "band_low": low,
        "band_high": high,
        "house": house,
        "tolerance": tolerance,
        "distance": distance,
        "side": side,
        "tick_at": last_tick.get("at"),
    }


def next_lower(value: float, notches: tuple[float, ...]) -> Optional[float]:
    below = [n for n in notches if n < float(value) - 1e-12]
    return max(below) if below else None


def next_higher(value: float, notches: tuple[float, ...]) -> Optional[float]:
    above = [n for n in notches if n > float(value) + 1e-12]
    return min(above) if above else None


def _seed_extra_dict(knobs: dict[str, Any]) -> dict[str, str]:
    raw = knobs.get("SEED_EXTRA") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def next_extra_seed(knobs: dict[str, Any]) -> Optional[tuple[str, str]]:
    have = set(_seed_extra_dict(knobs))
    for cid, name in EXTRA_SEED_LADDER:
        if cid not in have:
            return cid, name
    return None


def drop_last_extra_seed(knobs: dict[str, Any]) -> Optional[tuple[str, str]]:
    extra = _seed_extra_dict(knobs)
    if not extra:
        return None
    applied = [pair for pair in EXTRA_SEED_LADDER if pair[0] in extra]
    if not applied:
        cid = next(iter(extra))
        return cid, extra[cid]
    return applied[-1]


def quota_remaining_soft(quota: Optional[dict[str, Any]]) -> int:
    if not quota:
        return 0
    if quota.get("quota_remaining_soft") is not None:
        return max(0, int(quota["quota_remaining_soft"]))
    used = int(quota.get("quota_used_today") or quota.get("quota") or 0)
    cap = int(quota.get("daily_soft_cap") or DAILY_SOFT_CAP)
    return max(0, cap - used)


def quota_allows_seeds(quota: Optional[dict[str, Any]]) -> tuple[bool, str]:
    remaining = quota_remaining_soft(quota)
    if remaining < INSERT_UNIT_COST:
        return False, f"quota_exhausted remaining_soft={remaining}"
    if remaining < SEED_BROADEN_SOFT_MIN:
        return False, f"soft remaining={remaining} < {SEED_BROADEN_SOFT_MIN}"
    return True, f"soft remaining={remaining}"


def quota_allows_ticks(quota: Optional[dict[str, Any]], *, target_per_day: int = 48) -> tuple[bool, str]:
    remaining = quota_remaining_soft(quota)
    if remaining < INSERT_UNIT_COST:
        return False, f"quota_exhausted remaining_soft={remaining}"
    median = quota.get("median_units_per_tick") if quota else None
    if not median:
        return False, "no median_units_per_tick — cannot prove 48× fits soft cap"
    projected = int(median) * int(target_per_day)
    cap = int((quota or {}).get("daily_soft_cap") or DAILY_SOFT_CAP)
    if projected > cap:
        return False, f"{target_per_day}× median {median} ≈ {projected} exceeds soft {cap}"
    return True, f"{target_per_day}× median {median} ≈ {projected} fits soft {cap}"


def _adjustment(
    action: str,
    knob: Optional[str],
    *,
    frm: Any = None,
    to: Any = None,
    why: str,
    blocker: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "knob": knob,
        "from": frm,
        "to": to,
        "why": why,
        "blocker": blocker,
    }


def loosen_one(
    knobs: dict[str, Any],
    metrics: dict[str, Any],
    quota: Optional[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """One incremental loosen notch. Bias: more skippable fresh content."""
    next_knobs = deepcopy(knobs)
    limits: list[str] = []
    net = int(metrics.get("net_new") or 0)
    dist = metrics.get("distance")
    starve = (
        f"below band by {dist}; net_new={net} insufficient to close the gap"
        if net <= 0
        else f"below band by {dist}; net_new={net} still not inside 90–110"
    )

    min_fit = int(next_knobs.get("MIN_FIT") or 0)
    if min_fit > 0:
        nxt = min_fit - 1
        next_knobs["MIN_FIT"] = nxt
        return (
            next_knobs,
            _adjustment(
                "loosen",
                "MIN_FIT",
                frm=min_fit,
                to=nxt,
                why=f"{starve}. Ease fit gate {min_fit}→{nxt}.",
            ),
            limits,
        )
    limits.append("MIN_FIT=0 (already at floor)")

    floor = float(next_knobs.get("SEED_THROTTLE_WEIGHT_FLOOR") or 0.0)
    nxt_floor = next_lower(floor, THROTTLE_NOTCHES)
    if nxt_floor is not None:
        next_knobs["SEED_THROTTLE_WEIGHT_FLOOR"] = nxt_floor
        return (
            next_knobs,
            _adjustment(
                "loosen",
                "SEED_THROTTLE_WEIGHT_FLOOR",
                frm=floor,
                to=nxt_floor,
                why=f"{starve}. Lower throttled-channel weight floor {floor}→{nxt_floor}.",
            ),
            limits,
        )
    limits.append("SEED_THROTTLE_WEIGHT_FLOOR=0.00 (already at floor)")

    ok, qwhy = quota_allows_seeds(quota)
    extra = next_extra_seed(next_knobs)
    if extra is None:
        limits.append("EXTRA_SEED_LADDER exhausted")
    elif not ok:
        if "quota_exhausted" in qwhy:
            return (
                knobs,
                _adjustment(
                    "rest",
                    None,
                    why=f"{starve}. Broaden seed blocked: {qwhy}.",
                    blocker="quota_exhausted",
                ),
                limits + [f"broaden_seed:{qwhy}"],
            )
        limits.append(f"broaden_seed:{qwhy}")
    else:
        cid, name = extra
        merged = dict(_seed_extra_dict(next_knobs))
        merged[cid] = name
        next_knobs["SEED_EXTRA"] = merged
        return (
            next_knobs,
            _adjustment(
                "loosen",
                "SEED_EXTRA",
                frm=len(merged) - 1,
                to=len(merged),
                why=(
                    f"{starve}. Broaden seed set +{name} ({cid}). "
                    f"Quota ok ({qwhy})."
                ),
            ),
            limits,
        )

    if next_knobs.get("MAX_INSERTS_PER_TICK") is not None:
        frm = next_knobs.get("MAX_INSERTS_PER_TICK")
        next_knobs["MAX_INSERTS_PER_TICK"] = None
        return (
            next_knobs,
            _adjustment(
                "loosen",
                "MAX_INSERTS_PER_TICK",
                frm=frm,
                to=None,
                why=f"{starve}. Raise per-tick add cap: {frm}→removed.",
            ),
            limits,
        )
    limits.append("MAX_INSERTS_PER_TICK already removed")

    ticks = int(next_knobs.get("ticks_per_day") or 24)
    if ticks < 48:
        ok, qwhy = quota_allows_ticks(quota, target_per_day=48)
        if not ok:
            blocker = "quota_exhausted" if "quota_exhausted" in qwhy else "ticks_quota"
            return (
                knobs,
                _adjustment(
                    "rest",
                    None,
                    why=f"{starve}. more_ticks_per_day blocked: {qwhy}.",
                    blocker=blocker,
                ),
                limits + [f"more_ticks_per_day:{qwhy}"],
            )
        next_knobs["ticks_per_day"] = 48
        return (
            next_knobs,
            _adjustment(
                "loosen",
                "ticks_per_day",
                frm=ticks,
                to=48,
                why=f"{starve}. More ticks/day {ticks}→48. Quota ok ({qwhy}).",
            ),
            limits,
        )
    limits.append("ticks_per_day=48 (already at documented max)")

    return (
        knobs,
        _adjustment(
            "rest",
            None,
            why=f"{starve}. Every knob at documented limit: {limits}.",
            blocker="knobs_at_limit",
        ),
        limits,
    )


def tighten_one(
    knobs: dict[str, Any],
    metrics: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """One incremental tighten notch. Prune-to-band first (CAP → 110)."""
    next_knobs = deepcopy(knobs)
    limits: list[str] = []
    dist = metrics.get("distance")
    why_base = f"above band by {dist}"
    band_cap = int(metrics.get("band_high") or BAND_HIGH)

    cap_now = int(next_knobs.get("CAP") or CAP)
    if not next_knobs.get("PRUNE_TO_BAND") or cap_now > band_cap:
        next_knobs["PRUNE_TO_BAND"] = True
        next_knobs["CAP"] = band_cap
        return (
            next_knobs,
            _adjustment(
                "tighten",
                "CAP",
                frm=cap_now,
                to=band_cap,
                why=f"{why_base}. Prune oldest/lowest-fit down into band (CAP {cap_now}→{band_cap}).",
            ),
            limits,
        )
    limits.append(f"PRUNE_TO_BAND already (CAP={cap_now})")

    min_fit = int(next_knobs.get("MIN_FIT") or 0)
    nxt_fit = next_higher(min_fit, tuple(float(x) for x in MIN_FIT_NOTCHES))
    if nxt_fit is not None:
        nxt_i = int(nxt_fit)
        next_knobs["MIN_FIT"] = nxt_i
        return (
            next_knobs,
            _adjustment(
                "tighten",
                "MIN_FIT",
                frm=min_fit,
                to=nxt_i,
                why=f"{why_base}. Raise fit gate {min_fit}→{nxt_i}.",
            ),
            limits,
        )
    limits.append("MIN_FIT=2 (already at ceiling)")

    floor = float(next_knobs.get("SEED_THROTTLE_WEIGHT_FLOOR") or 0.0)
    nxt_floor = next_higher(floor, THROTTLE_NOTCHES)
    if nxt_floor is not None:
        next_knobs["SEED_THROTTLE_WEIGHT_FLOOR"] = nxt_floor
        return (
            next_knobs,
            _adjustment(
                "tighten",
                "SEED_THROTTLE_WEIGHT_FLOOR",
                frm=floor,
                to=nxt_floor,
                why=f"{why_base}. Raise throttled-channel weight floor {floor}→{nxt_floor}.",
            ),
            limits,
        )
    limits.append("SEED_THROTTLE_WEIGHT_FLOOR at ceiling")

    dropped = drop_last_extra_seed(next_knobs)
    if dropped is not None:
        cid, name = dropped
        merged = dict(_seed_extra_dict(next_knobs))
        merged.pop(cid, None)
        next_knobs["SEED_EXTRA"] = merged
        return (
            next_knobs,
            _adjustment(
                "tighten",
                "SEED_EXTRA",
                frm=cid,
                to=None,
                why=f"{why_base}. Drop extra seed {name} ({cid}).",
            ),
            limits,
        )
    limits.append("SEED_EXTRA empty")

    ticks = int(next_knobs.get("ticks_per_day") or 24)
    if ticks > 24:
        next_knobs["ticks_per_day"] = 24
        return (
            next_knobs,
            _adjustment(
                "tighten",
                "ticks_per_day",
                frm=ticks,
                to=24,
                why=f"{why_base}. Fewer ticks/day {ticks}→24.",
            ),
            limits,
        )
    limits.append("ticks_per_day=24")

    return (
        knobs,
        _adjustment(
            "rest",
            None,
            why=f"{why_base}. Every tighten knob at documented limit: {limits}.",
            blocker="knobs_at_limit",
        ),
        limits,
    )


def decide(
    metrics: dict[str, Any],
    knobs: dict[str, Any],
    state: dict[str, Any],
    quota: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Pick rest / one loosen / one tighten. Idempotent per last_tick.at."""
    knobs = {**DEFAULT_KNOBS, **(knobs or {})}
    last_action = state.get("last_action")
    cooldown = int(state.get("cooldown_remaining") or 0)
    tick_at = metrics.get("tick_at")
    processed_at = state.get("last_tick_at")

    if tick_at and processed_at and tick_at == processed_at and state.get("last_adjustment"):
        replay = deepcopy(state["last_adjustment"])
        replay["replay"] = True
        return {
            "knobs": deepcopy(state.get("knobs") or knobs),
            "adjustment": replay,
            "limits_hit": list(state.get("limits_hit") or []),
            "cooldown_remaining": cooldown,
            "idempotent": True,
        }

    side = metrics.get("side") or "unknown"
    if side == "unknown":
        adj = _adjustment("rest", None, why="no successful tick to measure")
        return {
            "knobs": knobs,
            "adjustment": adj,
            "limits_hit": [],
            "cooldown_remaining": cooldown,
            "idempotent": False,
        }

    if side == "inside":
        adj = _adjustment(
            "rest",
            None,
            why=(
                f"playlist_count={metrics.get('playlist_count')} inside "
                f"{metrics.get('band_low')}–{metrics.get('band_high')}"
            ),
        )
        return {
            "knobs": knobs,
            "adjustment": adj,
            "limits_hit": [],
            "cooldown_remaining": max(0, cooldown - 1),
            "idempotent": False,
        }

    want = "loosen" if side == "below" else "tighten"
    if last_action in {"loosen", "tighten"} and want != last_action and cooldown > 0:
        adj = _adjustment(
            "rest",
            None,
            why=(
                f"anti-oscillation: cooldown {cooldown} blocks {want} on the tick "
                f"after {last_action}"
            ),
            blocker="anti_oscillation",
        )
        return {
            "knobs": knobs,
            "adjustment": adj,
            "limits_hit": [],
            "cooldown_remaining": cooldown - 1,
            "idempotent": False,
        }

    if want == "loosen":
        next_knobs, adj, limits = loosen_one(knobs, metrics, quota)
    else:
        next_knobs, adj, limits = tighten_one(knobs, metrics)

    new_cooldown = COOLDOWN_TICKS if adj["action"] in {"loosen", "tighten"} else max(0, cooldown - 1)
    return {
        "knobs": next_knobs,
        "adjustment": adj,
        "limits_hit": limits,
        "cooldown_remaining": new_cooldown,
        "idempotent": False,
    }


def public_control(metrics: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    adj = decision["adjustment"]
    knobs = decision["knobs"]
    extra = _seed_extra_dict(knobs)
    return {
        "schema_version": SCHEMA_VERSION,
        "issue": 852,
        "copy_over_pi": False,
        "crank_without_quota": False,
        "playlist_count": metrics.get("playlist_count"),
        "net_new": metrics.get("net_new"),
        "added": metrics.get("added"),
        "removed": metrics.get("removed"),
        "band_low": metrics.get("band_low"),
        "band_high": metrics.get("band_high"),
        "house": metrics.get("house"),
        "tolerance": metrics.get("tolerance"),
        "distance_from_band": metrics.get("distance"),
        "side": metrics.get("side"),
        "adjustment": adj,
        "limits_hit": decision.get("limits_hit") or [],
        "cooldown_remaining": decision.get("cooldown_remaining"),
        "idempotent": bool(decision.get("idempotent")),
        "knobs": {
            "MIN_FIT": knobs.get("MIN_FIT"),
            "SEED_THROTTLE_WEIGHT_FLOOR": knobs.get("SEED_THROTTLE_WEIGHT_FLOOR"),
            "SEED_EXTRA": extra,
            "seed_extra_count": len(extra),
            "MAX_INSERTS_PER_TICK": knobs.get("MAX_INSERTS_PER_TICK"),
            "ticks_per_day": knobs.get("ticks_per_day"),
            "PRUNE_TO_BAND": knobs.get("PRUNE_TO_BAND"),
            "CAP": knobs.get("CAP"),
            "HOUSE_TARGET": knobs.get("HOUSE_TARGET"),
            "HOUSE_TARGET_TOLERANCE": knobs.get("HOUSE_TARGET_TOLERANCE"),
        },
    }


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    if not path.is_file():
        return {
            "schema_version": SCHEMA_VERSION,
            "knobs": deepcopy(DEFAULT_KNOBS),
            "last_action": None,
            "last_tick_at": None,
            "last_adjustment": None,
            "cooldown_remaining": 0,
            "limits_hit": [],
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "schema_version": SCHEMA_VERSION,
            "knobs": deepcopy(DEFAULT_KNOBS),
            "last_action": None,
            "last_tick_at": None,
            "last_adjustment": None,
            "cooldown_remaining": 0,
            "limits_hit": [],
        }
    if not isinstance(data, dict):
        data = {}
    knobs = {**DEFAULT_KNOBS, **(data.get("knobs") or {})}
    data["knobs"] = knobs
    return data


def persist_state(
    path: Path,
    knobs_path: Path,
    metrics: dict[str, Any],
    decision: dict[str, Any],
    public: dict[str, Any],
    *,
    dry_run: bool,
    jsonl_path: Optional[Path] = None,
) -> None:
    if dry_run:
        return
    state = {
        "schema_version": SCHEMA_VERSION,
        "at": iso(),
        "knobs": decision["knobs"],
        "last_action": decision["adjustment"]["action"],
        "last_tick_at": metrics.get("tick_at"),
        "last_adjustment": decision["adjustment"],
        "cooldown_remaining": decision.get("cooldown_remaining"),
        "limits_hit": decision.get("limits_hit") or [],
        "copy_over_pi": False,
    }
    atomic_write(path, json.dumps(state, indent=2, sort_keys=True) + "\n")
    knobs_out = {
        "schema_version": SCHEMA_VERSION,
        "MIN_FIT": decision["knobs"]["MIN_FIT"],
        "SEED_THROTTLE_WEIGHT_FLOOR": decision["knobs"]["SEED_THROTTLE_WEIGHT_FLOOR"],
        "SEED_EXTRA": _seed_extra_dict(decision["knobs"]),
        "MAX_INSERTS_PER_TICK": decision["knobs"].get("MAX_INSERTS_PER_TICK"),
        "ticks_per_day": decision["knobs"].get("ticks_per_day"),
        "PRUNE_TO_BAND": bool(decision["knobs"].get("PRUNE_TO_BAND")),
        "CAP": decision["knobs"].get("CAP"),
        "HOUSE_TARGET": decision["knobs"].get("HOUSE_TARGET"),
        "HOUSE_TARGET_TOLERANCE": decision["knobs"].get("HOUSE_TARGET_TOLERANCE"),
        "copy_over_pi": False,
    }
    atomic_write(knobs_path, json.dumps(knobs_out, indent=2, sort_keys=True) + "\n")
    jsonl = jsonl_path or (path.parent / "control.jsonl")
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    with jsonl.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(public, sort_keys=True) + "\n")
    try:
        os.chmod(jsonl, 0o600)
    except OSError:
        pass


def timer_dropin_text(ticks_per_day: int) -> str:
    if int(ticks_per_day) >= 48:
        return (
            "[Unit]\n"
            "Description=Half-hour YouTube AI Curated groom (#852 control loop)\n\n"
            "[Timer]\n"
            "OnCalendar=*:0/30\n"
            "Persistent=true\n"
            "RandomizedDelaySec=90\n"
            "AccuracySec=2min\n"
        )
    return (
        "[Unit]\n"
        "Description=Hourly YouTube AI Curated groom\n\n"
        "[Timer]\n"
        "OnCalendar=hourly\n"
        "Persistent=true\n"
        "RandomizedDelaySec=90\n"
        "AccuracySec=2min\n"
    )


def apply_timer(
    ticks_per_day: int,
    *,
    dropin: Path = TIMER_DROPIN,
    run: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    """Write a user-timer drop-in. Does not touch ExecStart / the writer."""
    text = timer_dropin_text(ticks_per_day)
    dropin.parent.mkdir(parents=True, exist_ok=True)
    if dropin.is_file() and dropin.read_text(encoding="utf-8") == text:
        return {"changed": False, "path": str(dropin), "ticks_per_day": ticks_per_day}
    dropin.write_text(text, encoding="utf-8")
    runner = run or subprocess.run
    try:
        runner(
            ["systemctl", "--user", "daemon-reload"],
            check=False,
            capture_output=True,
            text=True,
        )
        runner(
            ["systemctl", "--user", "restart", "youtube-groom.timer"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return {"changed": True, "path": str(dropin), "ticks_per_day": ticks_per_day, "systemctl": False}
    return {"changed": True, "path": str(dropin), "ticks_per_day": ticks_per_day, "systemctl": True}


def apply_live_knobs(g: dict[str, Any], *, knobs_path: Optional[Path] = None) -> dict[str, Any]:
    """Mutate the live writer module globals from knobs.json. No YouTube I/O."""
    path = knobs_path or (Path(g["STATE_DIR"]) / "knobs.json" if "STATE_DIR" in g else KNOBS_PATH)
    if not path.is_file():
        return {}
    try:
        knobs = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(knobs, dict):
        return {}
    applied: dict[str, Any] = {}
    for key in (
        "MIN_FIT",
        "SEED_THROTTLE_WEIGHT_FLOOR",
        "HOUSE_TARGET",
        "CAP",
        "HOUSE_TARGET_TOLERANCE",
    ):
        if key in knobs and key in g:
            g[key] = knobs[key]
            applied[key] = knobs[key]
    extra = knobs.get("SEED_EXTRA") or {}
    if extra and isinstance(extra, dict) and "SEED_KEEPERS" in g and isinstance(g["SEED_KEEPERS"], dict):
        g["SEED_KEEPERS"] = {**g["SEED_KEEPERS"], **{str(k): str(v) for k, v in extra.items()}}
        applied["SEED_EXTRA"] = extra
    return applied


def run_loop(
    last_tick: Optional[dict[str, Any]],
    quota: Optional[dict[str, Any]] = None,
    *,
    state_path: Path = STATE_PATH,
    knobs_path: Path = KNOBS_PATH,
    dry_run: bool = False,
    apply_timer_changes: bool = False,
) -> dict[str, Any]:
    state = load_state(state_path)
    metrics = band_metrics(last_tick)
    decision = decide(metrics, state.get("knobs") or DEFAULT_KNOBS, state, quota)
    public = public_control(metrics, decision)
    persist_state(
        state_path,
        knobs_path,
        metrics,
        decision,
        public,
        dry_run=dry_run,
        jsonl_path=state_path.parent / "control.jsonl",
    )
    timer_info = None
    prev_ticks = int((state.get("knobs") or DEFAULT_KNOBS).get("ticks_per_day") or 24)
    new_ticks = int(decision["knobs"].get("ticks_per_day") or 24)
    if (
        apply_timer_changes
        and not dry_run
        and not decision.get("idempotent")
        and new_ticks != prev_ticks
        and decision["adjustment"]["knob"] == "ticks_per_day"
    ):
        timer_info = apply_timer(new_ticks)
    public["timer"] = timer_info
    public["dry_run"] = dry_run
    return public


def merge_into_report(report: dict[str, Any], control: dict[str, Any]) -> dict[str, Any]:
    out = dict(report)
    out["control"] = control
    out["playlist_count"] = control.get("playlist_count")
    out["net_new"] = control.get("net_new")
    out["distance_from_band"] = control.get("distance_from_band")
    return out


def attach_to_report(
    payload: dict[str, Any],
    *,
    dry_run: bool,
    state_dir: Optional[Path] = None,
    apply_timer_changes: bool = False,
) -> dict[str, Any]:
    """Called from tick_report.run_report so 15m export stays consistent."""
    base = Path(state_dir) if state_dir is not None else STATE_DIR
    control = run_loop(
        payload.get("last_tick"),
        payload.get("quota"),
        state_path=base / "control_state.json",
        knobs_path=base / "knobs.json",
        dry_run=dry_run,
        apply_timer_changes=apply_timer_changes,
    )
    return merge_into_report(payload, control)


def load_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="U2 curator control loop: hold playlist at HOUSE_TARGET ± 10 (no YouTube I/O)."
    )
    parser.add_argument("--dry-run", action="store_true", help="evaluate; do not persist")
    parser.add_argument("--json", action="store_true", help="print full control JSON")
    parser.add_argument("--report", type=Path, default=None, help="override tick_report.json")
    parser.add_argument("--state", type=Path, default=None, help="override control_state.json")
    parser.add_argument("--knobs", type=Path, default=None, help="override knobs.json")
    parser.add_argument(
        "--apply-timer",
        action="store_true",
        help="write youtube-groom.timer.d drop-in when ticks_per_day changes",
    )
    args = parser.parse_args(argv)

    report_path = args.report or REPORT_PATH
    report = load_report(report_path)
    last = report.get("last_tick")
    quota = report.get("quota")
    control = run_loop(
        last,
        quota,
        state_path=args.state or STATE_PATH,
        knobs_path=args.knobs or KNOBS_PATH,
        dry_run=args.dry_run,
        apply_timer_changes=args.apply_timer and not args.dry_run,
    )
    if report and not args.dry_run:
        merged = merge_into_report(report, control)
        atomic_write(report_path, json.dumps(merged, indent=2, sort_keys=True) + "\n")

    if args.json or args.dry_run:
        print(json.dumps(control, indent=2))
    else:
        adj = control.get("adjustment") or {}
        print(
            json.dumps(
                {
                    "playlist_count": control.get("playlist_count"),
                    "net_new": control.get("net_new"),
                    "distance_from_band": control.get("distance_from_band"),
                    "side": control.get("side"),
                    "action": adj.get("action"),
                    "knob": adj.get("knob"),
                    "why": adj.get("why"),
                    "blocker": adj.get("blocker"),
                },
                indent=2,
            )
        )
    return 0 if control.get("side") != "unknown" else 2


if __name__ == "__main__":
    sys.exit(main())
