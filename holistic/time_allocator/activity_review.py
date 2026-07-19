"""Google Health walk candidates for Duchess confirm/deny flow."""

from __future__ import annotations

import hashlib
import sys
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .domain import log_action_progress, normalize_state

_WORKSPACE = Path(__file__).resolve().parents[2]
_RD = _WORKSPACE / "resistance-dashboard"

# Walks shorter than this are ignored (noise / room pacing)
MIN_WALK_MINUTES = 10.0
# How far back to pull sessions
DEFAULT_LOOKBACK_DAYS = 3


def _ensure_rd_path() -> None:
    p = str(_RD)
    if p not in sys.path:
        sys.path.insert(0, p)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _session_id(name: str | None, start: str, end: str) -> str:
    if name:
        # last path segment is stable-ish
        return "ex-" + name.rstrip("/").split("/")[-1][:40]
    raw = f"{start}|{end}"
    return "ex-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def fetch_walking_sessions(days: int = DEFAULT_LOOKBACK_DAYS) -> tuple[list[dict[str, Any]], str]:
    """Live Google Health exercise sessions of type WALKING."""
    days = max(1, min(int(days), 30))
    try:
        _ensure_rd_path()
        from rt_dashboard.google_health import GoogleHealthClient, GoogleHealthError  # type: ignore

        client = GoogleHealthClient()
        if not client.credentials_present():
            return [], "credentials not present"
        try:
            data = client._paginate_data_points("exercise", max_pages=3)
        except GoogleHealthError as e:
            return [], str(e)
        except Exception as e:  # noqa: BLE001
            return [], str(e)
    except Exception as e:  # noqa: BLE001
        return [], f"import/client: {e}"

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows: list[dict[str, Any]] = []
    for pt in data.get("dataPoints") or []:
        ex = pt.get("exercise") or {}
        et = str(ex.get("exerciseType") or "")
        if et != "WALKING":
            continue
        interval = ex.get("interval") or {}
        st = interval.get("startTime")
        en = interval.get("endTime")
        start_dt = _parse_dt(st)
        end_dt = _parse_dt(en)
        if not start_dt or not end_dt or end_dt <= start_dt:
            continue
        if end_dt < cutoff:
            continue
        mins = (end_dt - start_dt).total_seconds() / 60.0
        # Prefer activeDuration when present
        ad = ex.get("activeDuration")
        if isinstance(ad, str) and ad.endswith("s"):
            try:
                mins = max(mins, float(ad[:-1]) / 60.0)
            except ValueError:
                pass
        if mins < MIN_WALK_MINUTES:
            continue
        local_end = end_dt.astimezone()
        src = pt.get("dataSource") or {}
        sid = _session_id(pt.get("name"), start_dt.isoformat(), end_dt.isoformat())
        rows.append(
            {
                "id": sid,
                "start": start_dt.isoformat(timespec="seconds"),
                "end": end_dt.isoformat(timespec="seconds"),
                "minutes": int(round(mins)),
                "exercise_type": et,
                "display_name": str(ex.get("displayName") or "Walk"),
                "recording_method": str(src.get("recordingMethod") or ""),
                "source": "google_health",
                "local_date": local_end.date().isoformat(),
                "api_name": pt.get("name"),
            }
        )
    # newest first
    rows.sort(key=lambda r: r["start"], reverse=True)
    return rows, "google_health"


def sync_walk_candidates(
    state: dict[str, Any],
    *,
    days: int = DEFAULT_LOOKBACK_DAYS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge live walking sessions into state.activity_reviews (pending if new)."""
    state = normalize_state(state)
    walks, source = fetch_walking_sessions(days=days)
    meta: dict[str, Any] = {
        "ok": bool(walks) or source == "google_health",
        "source": source,
        "fetched": len(walks),
        "new_pending": 0,
        "error": None if walks or "credential" not in source else source,
    }
    if not walks and source not in ("google_health",):
        # still ok if empty list from successful fetch
        if "not present" in source or "error" in source.lower() or "import" in source:
            meta["ok"] = False
            meta["error"] = source
            return state, meta

    out = deepcopy(state)
    reviews = list(out.get("activity_reviews") or [])
    by_id = {str(r.get("id")): dict(r) for r in reviews if r.get("id")}

    for w in walks:
        wid = str(w["id"])
        if wid not in by_id:
            by_id[wid] = {
                **w,
                "status": "pending",
                "target_hint": "duchess-walk",
            }
            meta["new_pending"] += 1
        else:
            existing = by_id[wid]
            # Refresh timing/duration while still pending
            if existing.get("status") == "pending":
                for k in (
                    "start",
                    "end",
                    "minutes",
                    "display_name",
                    "recording_method",
                    "local_date",
                ):
                    if k in w:
                        existing[k] = w[k]
            by_id[wid] = existing

    out["activity_reviews"] = sorted(
        by_id.values(),
        key=lambda r: str(r.get("start") or ""),
        reverse=True,
    )
    # Cap stored reviews to keep JSON small
    out["activity_reviews"] = out["activity_reviews"][:80]
    return out, meta


def pending_walk_candidates(
    state: dict[str, Any],
    *,
    as_of: date | None = None,
    days: int = 2,
) -> list[dict[str, Any]]:
    """Pending WALKING reviews for the recent window (default last 2 local days)."""
    state = normalize_state(state)
    today = as_of or datetime.now().astimezone().date()
    since = today - timedelta(days=max(0, days - 1))
    out: list[dict[str, Any]] = []
    for r in state.get("activity_reviews") or []:
        if str(r.get("status") or "") != "pending":
            continue
        if str(r.get("exercise_type") or "WALKING") != "WALKING":
            continue
        ld = str(r.get("local_date") or "")[:10]
        if ld:
            try:
                d = date.fromisoformat(ld)
            except ValueError:
                d = today
        else:
            st = _parse_dt(r.get("start"))
            d = st.astimezone().date() if st else today
        if d < since or d > today:
            continue
        out.append(dict(r))
    out.sort(key=lambda r: str(r.get("start") or ""), reverse=True)
    return out


def review_walk(
    state: dict[str, Any],
    review_id: str,
    *,
    decision: str,
    note: str = "",
) -> dict[str, Any]:
    """Confirm (log Duchess) or deny a walk candidate.

    decision: 'confirm' | 'deny' | 'confirmed_duchess' | 'denied'
    """
    state = normalize_state(state)
    decision = (decision or "").strip().lower()
    if decision in ("confirm", "yes", "duchess", "confirmed"):
        status = "confirmed_duchess"
    elif decision in ("deny", "no", "denied", "reject"):
        status = "denied"
    else:
        raise ValueError("decision must be confirm or deny")

    rid = (review_id or "").strip()
    if not rid:
        raise ValueError("review_id is required")

    out = deepcopy(state)
    reviews = list(out.get("activity_reviews") or [])
    found = None
    for r in reviews:
        if str(r.get("id")) == rid:
            found = r
            break
    if found is None:
        raise KeyError(f"no activity review matching: {rid}")

    if found.get("status") not in (None, "pending"):
        # already decided — idempotent
        return out

    found["status"] = status
    found["reviewed_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    if note:
        found["review_note"] = note

    if status == "confirmed_duchess":
        mins = int(found.get("minutes") or 0)
        if mins <= 0:
            raise ValueError("walk has no duration to log")
        # Log against Duchess target for the walk's local date
        day = found.get("local_date")
        out["activity_reviews"] = reviews
        out = log_action_progress(
            out,
            "duchess-walk",
            minutes=float(mins),
            complete=False,
            note=note or f"confirmed Google walk {found.get('start')}",
            on=day,
        )
        # log_action_progress deep-copies; re-apply review status on new state
        revs = list(out.get("activity_reviews") or reviews)
        for r in revs:
            if str(r.get("id")) == rid:
                r["status"] = status
                r["reviewed_at"] = found["reviewed_at"]
                if note:
                    r["review_note"] = note
                break
        else:
            revs.append(found)
        out["activity_reviews"] = revs
    else:
        out["activity_reviews"] = reviews

    return out
