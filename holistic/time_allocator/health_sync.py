"""Pull health metrics (sleep) from Google Health / local fitness store into logs."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .domain import add_log, get_target, normalize_state
from .sleep_battery import merge_sleep_intervals, normalize_intervals

_WORKSPACE = Path(__file__).resolve().parents[2]
_RD = _WORKSPACE / "resistance-dashboard"


def _ensure_rd_path() -> None:
    p = str(_RD)
    if p not in sys.path:
        sys.path.insert(0, p)


def health_credentials_status() -> dict[str, Any]:
    """Report whether Google Health OAuth appears available."""
    status: dict[str, Any] = {
        "google_oauth": False,
        "local_metrics_file": False,
        "local_metrics_path": None,
        "detail": "",
    }
    metrics_path = _WORKSPACE / "fitness" / "data" / "health-metrics.json"
    if metrics_path.is_file():
        status["local_metrics_file"] = True
        status["local_metrics_path"] = str(metrics_path)

    try:
        _ensure_rd_path()
        from rt_dashboard.google_health import GoogleHealthClient  # type: ignore

        client = GoogleHealthClient()
        status["google_oauth"] = bool(client.credentials_present())
        if status["google_oauth"]:
            status["detail"] = "Google OAuth credentials present"
        elif status["local_metrics_file"]:
            status["detail"] = "No Google OAuth; can use local health-metrics.json"
        else:
            status["detail"] = "No Google OAuth and no local health-metrics.json"
    except Exception as e:  # noqa: BLE001
        if status["local_metrics_file"]:
            status["detail"] = f"Local metrics available; Google client import issue: {e}"
        else:
            status["detail"] = f"Health client unavailable: {e}"
    return status


def fetch_sleep_intervals(days: int = 14) -> tuple[list[dict[str, Any]], str]:
    """Return timed sleep intervals [{start, end, source}, ...] from Google Health.

    Uses raw dataPoints intervals (not daily aggregates) so a rolling 24h battery
    can charge/discharge correctly.
    """
    days = max(1, min(int(days), 90))
    google_err = "not attempted"
    try:
        _ensure_rd_path()
        from rt_dashboard.google_health import GoogleHealthClient, GoogleHealthError  # type: ignore

        client = GoogleHealthClient()
        if not client.credentials_present():
            return [], "credentials not present"
        try:
            # Bound pages by day range roughly (same as client internals)
            max_pages = 4 if days >= 60 else 2
            data = client._paginate_data_points("sleep", max_pages=max_pages)
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            rows: list[dict[str, Any]] = []
            for pt in data.get("dataPoints") or []:
                sleep = pt.get("sleep") or pt.get("data") or pt
                interval = (
                    (sleep.get("interval") if isinstance(sleep, dict) else None)
                    or pt.get("interval")
                    or {}
                )
                st = interval.get("startTime") or pt.get("startTime")
                en = interval.get("endTime") or pt.get("endTime")
                if not st or not en:
                    continue
                try:
                    st_s = str(st)
                    en_s = str(en)
                    if st_s.endswith("Z"):
                        st_s = st_s[:-1] + "+00:00"
                    if en_s.endswith("Z"):
                        en_s = en_s[:-1] + "+00:00"
                    start_dt = datetime.fromisoformat(st_s)
                    end_dt = datetime.fromisoformat(en_s)
                except ValueError:
                    continue
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                if end_dt < cutoff:
                    continue
                rows.append(
                    {
                        "start": start_dt.isoformat(timespec="seconds"),
                        "end": end_dt.isoformat(timespec="seconds"),
                        "source": "google_health",
                    }
                )
            rows = normalize_intervals(rows)
            if rows:
                return rows, "google_health"
            google_err = "no interval dataPoints"
        except GoogleHealthError as e:
            google_err = str(e)
        except Exception as e:  # noqa: BLE001
            google_err = str(e)
    except Exception as e:  # noqa: BLE001
        google_err = f"import/client: {e}"
    return [], f"no sleep intervals (google: {google_err})"


def fetch_sleep_samples(days: int = 14) -> tuple[list[dict[str, Any]], str]:
    """Return ([{date, sleep_hours, source}, ...], source_label).

    Preference: live Google Health → local fitness/data/health-metrics.json.
    """
    days = max(1, min(int(days), 90))

    # Prefer deriving daily totals from timed intervals (more accurate)
    intervals, isrc = fetch_sleep_intervals(days=days)
    if intervals:
        by_date: dict[str, float] = {}
        for iv in intervals:
            try:
                st = datetime.fromisoformat(str(iv["start"]).replace("Z", "+00:00"))
                en = datetime.fromisoformat(str(iv["end"]).replace("Z", "+00:00"))
            except ValueError:
                continue
            hours = max(0.0, (en - st).total_seconds() / 3600.0)
            # Attribute to local wake date (end local date)
            local_end = en.astimezone() if en.tzinfo else en
            day = local_end.date().isoformat()
            by_date[day] = by_date.get(day, 0.0) + hours
        rows = [
            {"date": d, "sleep_hours": round(h, 2), "source": isrc}
            for d, h in sorted(by_date.items())
        ]
        if rows:
            return rows, isrc

    # 1) Live Google daily aggregate fallback
    try:
        _ensure_rd_path()
        from rt_dashboard.google_health import GoogleHealthClient, GoogleHealthError  # type: ignore

        client = GoogleHealthClient()
        if client.credentials_present():
            try:
                samples = client.fetch_sleep(days=days)
                rows = [
                    {
                        "date": str(s.date),
                        "sleep_hours": float(s.sleep_hours),
                        "source": str(getattr(s, "source", None) or "google_health"),
                    }
                    for s in samples
                    if getattr(s, "date", None) is not None
                ]
                if rows:
                    return rows, "google_health"
            except GoogleHealthError as e:
                google_err = str(e)
            except Exception as e:  # noqa: BLE001
                google_err = str(e)
        else:
            google_err = "credentials not present"
    except Exception as e:  # noqa: BLE001
        google_err = f"import/client: {e}"

    # 2) Local store
    try:
        _ensure_rd_path()
        from rt_dashboard.health_metrics_store import (  # type: ignore
            load_metrics_file,
            metrics_from_payload,
        )
        import json

        path = _WORKSPACE / "fitness" / "data" / "health-metrics.json"
        if path.is_file():
            snap = load_metrics_file(str(path))
            if snap is None:
                raw = json.loads(path.read_text(encoding="utf-8"))
                snap = metrics_from_payload(raw)
            if snap and snap.sleep:
                cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()
                rows = [
                    {
                        "date": str(s.date),
                        "sleep_hours": float(s.sleep_hours),
                        "source": str(getattr(s, "source", None) or "health_metrics_store"),
                    }
                    for s in snap.sleep
                    if str(s.date) >= cutoff
                ]
                if rows:
                    return rows, "health_metrics_store"
    except Exception:  # noqa: BLE001
        pass

    return [], f"no sleep data (google: {google_err})"


def sync_sleep_logs(
    state: dict[str, Any],
    *,
    days: int = 14,
    overwrite: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge fetched sleep into timed intervals + daily KPI logs.

    Returns (new_state, meta).
    """
    state = normalize_state(state)
    meta: dict[str, Any] = {
        "ok": True,
        "imported": 0,
        "skipped": 0,
        "intervals_imported": 0,
        "source": None,
        "samples": [],
        "error": None,
    }
    if get_target(state, "sleep") is None:
        meta["ok"] = False
        meta["error"] = "no sleep target in store — seed personal targets first"
        return state, meta

    out = state

    # Timed intervals for rolling 24h battery
    intervals, isrc = fetch_sleep_intervals(days=days)
    if intervals:
        before = len(out.get("sleep_intervals") or [])
        out = merge_sleep_intervals(out, intervals)
        meta["intervals_imported"] = max(0, len(out.get("sleep_intervals") or []) - before)
        # After merge, count total stored from this source in window
        meta["intervals_imported"] = len(out.get("sleep_intervals") or [])
        meta["source"] = isrc

    samples, source = fetch_sleep_samples(days=days)
    meta["source"] = meta.get("source") or source
    meta["samples"] = samples
    if not samples and not intervals:
        meta["ok"] = False
        meta["error"] = source if source.startswith("no sleep") else "no sleep samples returned"
        return out, meta

    for s in samples:
        day = str(s.get("date") or "")[:10]
        hours = float(s.get("sleep_hours") or 0)
        if not day or hours <= 0:
            meta["skipped"] += 1
            continue
        # add_log replaces same day — always apply when overwrite
        if not overwrite:
            from .domain import logs_for_target
            from datetime import date as date_cls

            existing = logs_for_target(
                out, "sleep", since=date_cls.fromisoformat(day), until=date_cls.fromisoformat(day)
            )
            if existing:
                meta["skipped"] += 1
                continue
        note = f"synced from {s.get('source') or source}"
        out = add_log(out, "sleep", hours, on=day, note=note)
        meta["imported"] += 1
    return out, meta
