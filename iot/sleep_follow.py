"""Post-sunset Sleep Battery room dim follow.

After local sunset, poll FitDash Sleep Battery every N minutes and set configured
target group(s) brightness to the battery % (brightness-only — no color change).
When the battery hits 0%, turn those lights off and stop until the next sunset.

Follow continues **overnight past midnight until sunrise** while still active
(so a 0% empty-at ~05:00 is still acted on — not frozen at the last dim %).

FitDash field mapping (recovery Sleep battery panel):
  - pct_charged  → brightness percent (0–100)
  - empty_at     → UI "Bedtime" label (informational; off triggers on pct==0)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo

from iot.schedule import (
    DEFAULT_SCHEDULE_PATH,
    DEFAULT_STATE_PATH,
    load_schedule,
    load_state,
    location_from_schedule,
    save_state,
)
from iot.solar import sun_times_local

log = logging.getLogger("iot.sleep_follow")

IOT_DIR = Path(__file__).resolve().parent
DEFAULT_FOLLOW_STATE_KEY = "sleep_battery_follow"

# Defaults — overridable via schedule.json → sleep_battery_follow
DEFAULT_POLL_MINUTES = 15
DEFAULT_TARGET = "masterbedroom"
DEFAULT_FITDASH_URL = "http://127.0.0.1:8787"
DEFAULT_FITDASH_PATH = "/api/sleep_battery"


ControlFn = Callable[[str, str, Optional[int]], dict[str, Any]]


def pct_to_wiz_brightness(pct: float) -> int:
    """Map 0–100 sleep battery % to Wiz brightness 1–255.

    0% is handled separately as off; callers should not use this for empty.
    """
    p = max(0.0, min(100.0, float(pct)))
    if p <= 0:
        return 1
    return max(1, min(255, int(round(p / 100.0 * 255.0))))


def resolve_follow_targets(raw: Mapping[str, Any]) -> list[str]:
    """Normalize targets list; accept singular ``target`` for back-compat."""
    out: list[str] = []
    multi = raw.get("targets")
    if isinstance(multi, (list, tuple)):
        for t in multi:
            s = str(t or "").strip()
            if s and s not in out:
                out.append(s)
    if not out:
        one = str(raw.get("target") or DEFAULT_TARGET).strip() or DEFAULT_TARGET
        out = [one]
    return out


def follow_config(sched: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Read sleep_battery_follow block from schedule (with env overrides)."""
    s = dict(sched) if sched is not None else load_schedule()
    raw = dict(s.get("sleep_battery_follow") or {})
    enabled = bool(raw.get("enabled", True))
    poll = int(raw.get("poll_minutes") or DEFAULT_POLL_MINUTES)
    targets = resolve_follow_targets(raw)
    fitdash_url = (
        os.environ.get("FITDASH_URL")
        or raw.get("fitdash_url")
        or DEFAULT_FITDASH_URL
    ).rstrip("/")
    fitdash_path = str(raw.get("fitdash_path") or DEFAULT_FITDASH_PATH)
    if not fitdash_path.startswith("/"):
        fitdash_path = "/" + fitdash_path
    token = (
        os.environ.get("FITDASH_SERVICE_TOKEN")
        or raw.get("fitdash_service_token")
        or ""
    )
    return {
        "enabled": enabled,
        "poll_minutes": max(1, poll),
        # Singular target kept for older UIs / logs (first of list).
        "target": targets[0],
        "targets": targets,
        "fitdash_url": fitdash_url,
        "fitdash_path": fitdash_path,
        "fitdash_service_token": str(token).strip(),
    }


def _blank_follow(day: str) -> dict[str, Any]:
    """Fresh follow-cycle state for a local civil day."""
    return {
        "day": day,
        "active": False,
        "done": False,
        "last_poll_at": None,
        "last_pct": None,
        "last_brightness": None,
        "last_error": None,
        "activated_at": None,
        "completed_at": None,
    }


def _parse_iso_dt(raw: Any, tz: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def done_blocks_until_next_sunset(
    follow: Mapping[str, Any],
    *,
    now: datetime,
    sunset: Optional[datetime],
) -> bool:
    """Whether a completed 0% cycle should still skip ticks.

    Completing in the early morning of civil day D must not block sunset later
    that same calendar day. Skip only until the next sunset after completed_at.
    """
    if not follow.get("done"):
        return False
    if sunset is None:
        return True
    completed = _parse_iso_dt(follow.get("completed_at"), now.tzinfo)
    if completed is None:
        return True
    if now >= sunset and completed < sunset:
        return False
    return True


def _parse_follow_state(state: Mapping[str, Any]) -> dict[str, Any]:
    raw = state.get(DEFAULT_FOLLOW_STATE_KEY) or {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "day": raw.get("day"),  # local YYYY-MM-DD when follow is active / done
        "active": bool(raw.get("active")),
        "done": bool(raw.get("done")),  # true after battery 0 + lights off
        "last_poll_at": raw.get("last_poll_at"),
        "last_pct": raw.get("last_pct"),
        "last_brightness": raw.get("last_brightness"),
        "last_error": raw.get("last_error"),
        "activated_at": raw.get("activated_at"),
        "completed_at": raw.get("completed_at"),
    }


def _write_follow_state(state: dict[str, Any], follow: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(state)
    out[DEFAULT_FOLLOW_STATE_KEY] = dict(follow)
    return out


def local_now(
    sched: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
) -> tuple[datetime, ZoneInfo, Optional[dict[str, Any]]]:
    loc = location_from_schedule(sched)
    if loc:
        tz = ZoneInfo(loc["timezone"])
    else:
        tz = datetime.now().astimezone().tzinfo or ZoneInfo("UTC")
        if not isinstance(tz, ZoneInfo):
            tz = ZoneInfo("UTC")
    n = now or datetime.now(tz)
    if n.tzinfo is None:
        n = n.replace(tzinfo=tz)
    else:
        n = n.astimezone(tz)
    return n, tz, loc


def _sun_moment(
    sched: Mapping[str, Any],
    key: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    """Parse today's local sunrise/sunset for schedule location."""
    n, _tz, loc = local_now(sched, now=now)
    if not loc:
        return None
    times = sun_times_local(
        n.date(), loc["latitude"], loc["longitude"], loc["timezone"]
    )
    raw = times.get(f"{key}_local") or times.get(key)
    if raw is None:
        return None
    if isinstance(raw, datetime):
        moment = raw
    else:
        try:
            moment = datetime.fromisoformat(str(raw))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=n.tzinfo)
    else:
        moment = moment.astimezone(n.tzinfo)
    return moment


def past_sunset(
    sched: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
) -> bool:
    """True when local now is at or after today's sunset (needs location)."""
    n, _tz, _loc = local_now(sched, now=now)
    sunset = _sun_moment(sched, "sunset", now=n)
    if sunset is None:
        return False
    return n >= sunset


def before_sunrise(
    sched: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
) -> bool:
    """True when local now is strictly before today's sunrise."""
    n, _tz, _loc = local_now(sched, now=now)
    sunrise = _sun_moment(sched, "sunrise", now=n)
    if sunrise is None:
        return False
    return n < sunrise


def in_follow_window(
    sched: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
    follow: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Whether sleep-follow should poll right now.

    Window is **after sunset through overnight until sunrise** (or until done):

    - After today's sunset → yes (start/continue evening follow)
    - After midnight / before sunrise, with an **active unfinished** follow →
      yes (battery often hits 0% in the early morning — must not stop at midnight)

    Without this overnight carry, ticks after 00:00 see "before today's sunset"
    and skip, leaving lights stuck at the last dim % until the next evening.
    """
    if past_sunset(sched, now=now):
        return True
    st = follow or {}
    if st.get("active") and not st.get("done") and before_sunrise(sched, now=now):
        return True
    return False


def should_poll(
    follow: Mapping[str, Any],
    *,
    now: datetime,
    poll_minutes: int,
) -> bool:
    """Whether enough time has elapsed since last poll (or never polled)."""
    last = follow.get("last_poll_at")
    if not last:
        return True
    try:
        prev = datetime.fromisoformat(str(last))
        if prev.tzinfo is None:
            prev = prev.replace(tzinfo=now.tzinfo)
        else:
            prev = prev.astimezone(now.tzinfo)
    except (TypeError, ValueError):
        return True
    return now >= prev + timedelta(minutes=poll_minutes)


def extract_sleep_battery(payload: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Pull sleep_battery object from FitDash dashboard or dedicated endpoint."""
    if not payload:
        return None
    if "pct_charged" in payload or "pct_of_target" in payload:
        # bare battery object
        if payload.get("error") and "pct_charged" not in payload:
            return None
        return dict(payload)
    sb = payload.get("sleep_battery")
    if isinstance(sb, dict):
        return dict(sb)
    rec = payload.get("recovery")
    if isinstance(rec, dict) and isinstance(rec.get("sleep_battery"), dict):
        return dict(rec["sleep_battery"])
    return None


def fetch_sleep_battery(
    *,
    base_url: str,
    path: str = DEFAULT_FITDASH_PATH,
    token: str = "",
    timeout: float = 12.0,
) -> dict[str, Any]:
    """HTTP GET FitDash sleep battery (or full dashboard fallback).

    Returns {ok, pct_charged?, empty_at?, mode?, error?, raw?}.
    """
    urls = [
        f"{base_url.rstrip('/')}{path}",
        f"{base_url.rstrip('/')}/api/dashboard",
    ]
    # Prefer dedicated path first; if path is already dashboard, only one try.
    if path.rstrip("/") == "/api/dashboard":
        urls = [urls[0]]

    headers = {"Accept": "application/json", "User-Agent": "iot-sleep-follow/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-FitDash-Service-Token"] = token

    last_err = "no attempt"
    for url in urls:
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                data = json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8")
                err_data = json.loads(err_body) if err_body else {}
            except Exception:  # noqa: BLE001
                err_data = {}
            last_err = f"HTTP {e.code}: {err_data.get('error') or err_data.get('message') or e.reason}"
            if e.code == 401 and url.endswith("/api/sleep_battery"):
                # fall through to dashboard attempt
                continue
            if e.code == 404 and url.endswith("/api/sleep_battery"):
                continue
            return {"ok": False, "error": last_err}
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            continue

        if not isinstance(data, dict):
            last_err = "invalid JSON object"
            continue
        if data.get("error") == "auth_required" or (
            data.get("ok") is False and data.get("error") == "auth_required"
        ):
            last_err = "auth_required — set FITDASH_SERVICE_TOKEN or allow loopback service auth"
            continue

        bat = extract_sleep_battery(data)
        if not bat:
            last_err = "sleep_battery missing from response"
            continue

        pct = bat.get("pct_charged")
        if pct is None:
            pct = bat.get("pct_of_target")
        try:
            pct_f = float(pct) if pct is not None else None
        except (TypeError, ValueError):
            pct_f = None
        if pct_f is None:
            last_err = "sleep_battery has no pct_charged"
            continue

        return {
            "ok": True,
            "pct_charged": max(0.0, min(100.0, pct_f)),
            "empty_at": bat.get("empty_at"),
            "mode": bat.get("mode"),
            "level": bat.get("level"),
            "summary": bat.get("summary"),
            "source_url": url,
        }

    return {"ok": False, "error": last_err}


def decide_action(pct: float) -> dict[str, Any]:
    """Map battery % to control action (off vs brightness-only)."""
    p = max(0.0, min(100.0, float(pct)))
    if p <= 0:
        return {
            "color": "off",
            "brightness": None,
            "done": True,
            "pct": 0.0,
        }
    return {
        "color": "keep",
        "brightness": pct_to_wiz_brightness(p),
        "done": False,
        "pct": p,
    }


def tick_sleep_follow(
    *,
    control: ControlFn,
    schedule_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    now: Optional[datetime] = None,
    fetch_fn: Optional[Callable[..., dict[str, Any]]] = None,
    force: bool = False,
) -> dict[str, Any]:
    """One evaluation of the sleep-battery follow loop.

    Returns a status dict for logging / API.
    """
    sched = load_schedule(schedule_path or DEFAULT_SCHEDULE_PATH)
    cfg = follow_config(sched)
    if not cfg["enabled"] and not force:
        return {"ok": True, "skipped": True, "reason": "disabled"}

    n, _tz, loc = local_now(sched, now=now)
    day = n.date().isoformat()
    state = load_state(state_path or DEFAULT_STATE_PATH)
    follow = _parse_follow_state(state)

    # New civil day: carry unfinished overnight follow; otherwise reset.
    if follow.get("day") and follow["day"] != day:
        if follow.get("active") and not follow.get("done"):
            log.info(
                "sleep_follow carry overnight from day=%s into day=%s",
                follow.get("day"),
                day,
            )
            follow["day"] = day
        else:
            follow = _blank_follow(day)

    follow["day"] = day

    if follow.get("done") and not force:
        sunset = _sun_moment(sched, "sunset", now=n) if loc else None
        if done_blocks_until_next_sunset(follow, now=n, sunset=sunset):
            save_state(_write_follow_state(state, follow), state_path)
            return {
                "ok": True,
                "skipped": True,
                "reason": "done_for_day",
                "day": day,
                "follow": follow,
            }
        # Morning 0% completion; tonight's sunset is a new cycle.
        log.info(
            "sleep_follow new evening after morning completion day=%s completed_at=%s",
            day,
            follow.get("completed_at"),
        )
        follow = _blank_follow(day)

    if not loc:
        follow["last_error"] = "no location — cannot know sunset"
        save_state(_write_follow_state(state, follow), state_path)
        return {"ok": False, "error": follow["last_error"], "follow": follow}

    if not in_follow_window(sched, now=n, follow=follow) and not force:
        return {
            "ok": True,
            "skipped": True,
            "reason": "outside_follow_window",
            "day": day,
            "follow": follow,
        }

    # Activate on first in-window tick (post-sunset or overnight carry)
    if not follow.get("active"):
        follow["active"] = True
        follow["activated_at"] = n.isoformat(timespec="seconds")
        log.info("sleep_follow activated day=%s", day)

    if not force and not should_poll(
        follow, now=n, poll_minutes=int(cfg["poll_minutes"])
    ):
        save_state(_write_follow_state(state, follow), state_path)
        return {
            "ok": True,
            "skipped": True,
            "reason": "poll_interval",
            "day": day,
            "follow": follow,
        }

    fetcher = fetch_fn or fetch_sleep_battery
    bat = fetcher(
        base_url=cfg["fitdash_url"],
        path=cfg["fitdash_path"],
        token=cfg["fitdash_service_token"],
    )
    follow["last_poll_at"] = n.isoformat(timespec="seconds")

    if not bat.get("ok"):
        follow["last_error"] = bat.get("error") or "fetch failed"
        save_state(_write_follow_state(state, follow), state_path)
        log.warning("sleep_follow fetch failed: %s", follow["last_error"])
        return {
            "ok": False,
            "error": follow["last_error"],
            "follow": follow,
            "battery": bat,
        }

    pct = float(bat["pct_charged"])
    action = decide_action(pct)
    follow["last_pct"] = action["pct"]
    follow["last_error"] = None

    targets = list(cfg.get("targets") or [cfg.get("target") or DEFAULT_TARGET])
    controls: list[dict[str, Any]] = []
    if action["done"]:
        for target in targets:
            cr = control(target, "off", None)
            controls.append({"target": target, **(cr if isinstance(cr, dict) else {"ok": bool(cr)})})
        follow["last_brightness"] = 0
        follow["done"] = True
        follow["active"] = False
        follow["completed_at"] = n.isoformat(timespec="seconds")
        log.info(
            "sleep_follow DONE day=%s pct=0 targets=%s off ok=%s",
            day,
            targets,
            all(c.get("ok") for c in controls),
        )
    else:
        bri = int(action["brightness"])
        for target in targets:
            cr = control(target, "keep", bri)
            controls.append({"target": target, **(cr if isinstance(cr, dict) else {"ok": bool(cr)})})
        follow["last_brightness"] = bri
        follow["done"] = False
        log.info(
            "sleep_follow dim day=%s pct=%.1f bri=%s targets=%s ok=%s empty_at=%s",
            day,
            pct,
            bri,
            targets,
            all(c.get("ok") for c in controls),
            bat.get("empty_at"),
        )

    # Back-compat: single control dict is first result; multi in controls[]
    primary = controls[0] if controls else {"ok": False, "error": "no targets"}
    save_state(_write_follow_state(state, follow), state_path)
    return {
        "ok": all(c.get("ok") for c in controls) if controls else False,
        "day": day,
        "pct_charged": pct,
        "empty_at": bat.get("empty_at"),
        "action": action,
        "targets": targets,
        "control": primary,
        "controls": controls,
        "follow": follow,
        "battery": bat,
    }
