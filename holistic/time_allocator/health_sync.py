"""Pull health metrics (sleep) from Google Health / local fitness store into logs."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .domain import add_log, get_target, normalize_state

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


def fetch_sleep_samples(days: int = 14) -> tuple[list[dict[str, Any]], str]:
    """Return ([{date, sleep_hours, source}, ...], source_label).

    Preference: live Google Health → local fitness/data/health-metrics.json.
    """
    days = max(1, min(int(days), 90))

    # 1) Live Google
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
    """Merge fetched sleep samples into state logs for the sleep target.

    Returns (new_state, meta).
    """
    state = normalize_state(state)
    meta: dict[str, Any] = {
        "ok": True,
        "imported": 0,
        "skipped": 0,
        "source": None,
        "samples": [],
        "error": None,
    }
    if get_target(state, "sleep") is None:
        meta["ok"] = False
        meta["error"] = "no sleep target in store — seed personal targets first"
        return state, meta

    samples, source = fetch_sleep_samples(days=days)
    meta["source"] = source
    meta["samples"] = samples
    if not samples:
        meta["ok"] = False
        meta["error"] = source if source.startswith("no sleep") else "no sleep samples returned"
        return state, meta

    out = state
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

            existing = logs_for_target(out, "sleep", since=date_cls.fromisoformat(day), until=date_cls.fromisoformat(day))
            if existing:
                meta["skipped"] += 1
                continue
        note = f"synced from {s.get('source') or source}"
        out = add_log(out, "sleep", hours, on=day, note=note)
        meta["imported"] += 1
    return out, meta
