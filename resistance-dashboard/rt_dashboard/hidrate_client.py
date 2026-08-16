"""HidrateSpark Parse cloud client (unofficial).

Email/password login against ``https://www.hidrateapp.com/parse/`` using the
shared Android client Application-Id / Client-Key. Stdlib only (urllib).

When credentials are present, FitDash treats Hidrate ``Day`` totals as the
hydration source of truth and overlays them onto Google Health snapshots so
partial Health Connect / Fitbit water rows cannot double-count.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .models import HealthSnapshot, HydrationDay
from .timeutil import local_today_iso, local_tz

log = logging.getLogger("resistance-dashboard.hidrate")

DEFAULT_SERVER_URL = "https://www.hidrateapp.com/parse"
# Shared keys embedded in the public Android app (not per-user secrets).
DEFAULT_APP_ID = "a5Il6d0n6WWkLQwBzlxvpF5P7PEkUYkX045CRgwM"
DEFAULT_CLIENT_KEY = "mWasknCNtr9dSQGPwUBWb5u4Ilf8Qkeqkwz9Q4eL"

# Android package that writes Hydration to Health Connect / Google Health.
HIDRATE_ANDROID_PACKAGE = "hidratenow.com.hidrate.hidrateandroid"

# Process-local session + Day series cache (avoid login/query on every page load).
_SESSION_TOKEN: Optional[str] = None
_SERIES_CACHE: Dict[str, Any] = {}  # {days, fetched_at, series}
_SERIES_TTL_SEC = 300.0


class HidrateError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def hidrate_credentials_present() -> bool:
    email = (os.environ.get("HIDRATE_EMAIL") or "").strip()
    password = (os.environ.get("HIDRATE_PASSWORD") or "").strip()
    if not email or not password:
        return False
    if email.startswith("PASTE_YOUR_") or password.startswith("PASTE_YOUR_"):
        return False
    return True


class HidrateClient:
    """Minimal Parse REST client for Day totals (+ optional bottles)."""

    def __init__(
        self,
        *,
        email: Optional[str] = None,
        password: Optional[str] = None,
        app_id: Optional[str] = None,
        client_key: Optional[str] = None,
        server_url: Optional[str] = None,
        timeout_sec: float = 25.0,
    ) -> None:
        self.email = (email or os.environ.get("HIDRATE_EMAIL") or "").strip()
        self.password = (password or os.environ.get("HIDRATE_PASSWORD") or "").strip()
        self.app_id = (
            app_id
            or (os.environ.get("HIDRATE_APP_ID") or "").strip()
            or DEFAULT_APP_ID
        )
        self.client_key = (
            client_key
            or (os.environ.get("HIDRATE_CLIENT_KEY") or "").strip()
            or DEFAULT_CLIENT_KEY
        )
        base = (
            server_url
            or (os.environ.get("HIDRATE_SERVER_URL") or "").strip()
            or DEFAULT_SERVER_URL
        )
        self.server_url = base.rstrip("/")
        self.timeout_sec = float(timeout_sec)
        global _SESSION_TOKEN
        self._session_token: Optional[str] = _SESSION_TOKEN

    def credentials_present(self) -> bool:
        if not self.email or not self.password:
            return False
        if self.email.startswith("PASTE_YOUR_") or self.password.startswith(
            "PASTE_YOUR_"
        ):
            return False
        return bool(self.app_id and self.client_key)

    def _headers(self, *, session: bool = False) -> Dict[str, str]:
        h = {
            "X-Parse-Application-Id": self.app_id,
            "X-Parse-Client-Key": self.client_key,
            "X-Parse-REST-API-Key": self.client_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if session and self._session_token:
            h["X-Parse-Session-Token"] = self._session_token
        return h

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, str]] = None,
        session: bool = False,
    ) -> Any:
        url = f"{self.server_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url, headers=self._headers(session=session), method=method.upper()
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return {}
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise HidrateError(
                f"Hidrate HTTP {e.code}: {body[:300] or e.reason}",
                status=e.code,
                body=body,
            ) from e
        except urllib.error.URLError as e:
            raise HidrateError(f"Hidrate network error: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise HidrateError(f"Hidrate invalid JSON: {e}") from e

    def login(self) -> Dict[str, Any]:
        if not self.credentials_present():
            raise HidrateError(
                "Missing HIDRATE_EMAIL / HIDRATE_PASSWORD (or app keys)."
            )
        data = self._request(
            "GET",
            "/login",
            params={"username": self.email, "password": self.password},
            session=False,
        )
        token = data.get("sessionToken") if isinstance(data, dict) else None
        if not token:
            raise HidrateError("Hidrate login succeeded but no sessionToken returned")
        global _SESSION_TOKEN
        self._session_token = str(token)
        _SESSION_TOKEN = self._session_token
        return data if isinstance(data, dict) else {}

    def ensure_session(self) -> None:
        if not self._session_token:
            self.login()

    def fetch_day_rows(
        self, *, start_date: str, end_date: Optional[str] = None, limit: int = 120
    ) -> List[Dict[str, Any]]:
        """Return raw Parse ``Day`` objects for date range (inclusive)."""
        self.ensure_session()
        where: Dict[str, Any] = {"date": {"$gte": start_date}}
        if end_date:
            where["date"]["$lte"] = end_date
        params = {
            "where": json.dumps(where, separators=(",", ":")),
            "limit": str(max(1, min(int(limit), 500))),
            "order": "date",
        }
        data = self._request("GET", "/classes/Day", params=params, session=True)
        results = data.get("results") if isinstance(data, dict) else None
        return list(results or [])

    def fetch_hydration_days(
        self, days: int = 90, *, use_cache: bool = True
    ) -> List[HydrationDay]:
        """Daily water totals from Parse ``Day.totalAmount`` (ml)."""
        days = max(1, min(int(days), 120))
        global _SERIES_CACHE
        if use_cache:
            cached = _SERIES_CACHE
            if (
                cached.get("days") == days
                and cached.get("series") is not None
                and (time.time() - float(cached.get("fetched_at") or 0))
                < _SERIES_TTL_SEC
            ):
                return list(cached["series"])

        today = local_today_iso()
        start = (
            datetime.now(local_tz()) - timedelta(days=days - 1)
        ).strftime("%Y-%m-%d")
        try:
            rows = self.fetch_day_rows(start_date=start, end_date=today, limit=days + 5)
        except HidrateError as e:
            # Session may have expired — clear and retry once.
            if e.status in (401, 403) or "invalid" in str(e).lower():
                global _SESSION_TOKEN
                self._session_token = None
                _SESSION_TOKEN = None
                rows = self.fetch_day_rows(
                    start_date=start, end_date=today, limit=days + 5
                )
            else:
                raise
        out: List[HydrationDay] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            date = str(row.get("date") or "")[:10]
            if not date or date > today:
                continue
            ml = _day_total_ml(row)
            if ml is None:
                continue
            out.append(
                HydrationDay(date=date, water_ml=round(float(ml), 1), source="hidrate")
            )
        # De-dupe by date (API sometimes returns stale duplicates)
        by: Dict[str, HydrationDay] = {}
        for h in out:
            by[h.date] = h
        series = [by[k] for k in sorted(by.keys())]
        _SERIES_CACHE = {
            "days": days,
            "fetched_at": time.time(),
            "series": list(series),
        }
        return series


def _day_total_ml(row: Dict[str, Any]) -> Optional[float]:
    for key in ("totalAmount", "totalBottleAmount", "totalVolumeAmount"):
        v = row.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def overlay_hidrate_hydration(
    snapshot: HealthSnapshot,
    *,
    days: int = 90,
    client: Optional[HidrateClient] = None,
) -> Tuple[HealthSnapshot, Dict[str, Any]]:
    """Prefer Hidrate Day.totalAmount over Google Health on overlapping dates.

    Hidrate already ingests Health Connect / Fitbit glasses into Day.totalAmount
    (non-bottle sips, often on app-open). Adding GH points on top double-counts.
    Google Health rows stay only for dates **without** a Hidrate Day (older
    Fitbit history).

    Returns ``(snapshot, meta)`` where meta describes source / errors for cache notes.
    """
    meta: Dict[str, Any] = {
        "configured": hidrate_credentials_present(),
        "applied": False,
        "days": 0,
        "error": None,
    }
    if not hidrate_credentials_present():
        return snapshot, meta

    hc = client or HidrateClient()
    if not hc.credentials_present():
        return snapshot, meta

    try:
        series = hc.fetch_hydration_days(days=days)
    except HidrateError as e:
        log.warning("Hidrate hydration pull failed: %s", e)
        meta["error"] = str(e)
        return snapshot, meta
    except Exception as e:  # noqa: BLE001
        log.warning("Hidrate hydration pull failed: %s", e)
        meta["error"] = str(e)
        return snapshot, meta

    if not series:
        meta["error"] = "empty_series"
        return snapshot, meta

    hidrate_by = {h.date: h for h in series}
    # Keep non-overlapping GH / other history; Hidrate wins on collision.
    kept = [h for h in (snapshot.hydration or []) if h.date not in hidrate_by]
    merged = kept + list(hidrate_by.values())
    merged.sort(key=lambda h: h.date)
    snapshot.hydration = merged
    meta["applied"] = True
    meta["days"] = len(series)
    meta["source"] = "hidrate"
    return snapshot, meta
