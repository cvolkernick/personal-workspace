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
from datetime import datetime, timedelta, timezone
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
_BOTTLE_CACHE: Dict[str, Any] = {}  # {fetched_at, charge}
_BOTTLE_TTL_SEC = 300.0
_SIP_CACHE: Dict[str, Any] = {}  # {hours, fetched_at, samples}
_SIP_TTL_SEC = 300.0

# Parse Bottle charge keys verified from community clients / unofficial server schema:
# - batteryLevel: hidrateapp-server Bottle model + hidratespark-mcp
# - battery: alias some community clients report
_BOTTLE_BATTERY_KEYS = ("batteryLevel", "battery")


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
    """Minimal Parse REST client for Day totals, Sip times, Bottle charge."""

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

    def fetch_bottle_rows(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        """Return raw Parse ``Bottle`` objects for the signed-in user."""
        self.ensure_session()
        params = {
            "limit": str(max(1, min(int(limit), 100))),
            "order": "-updatedAt",
        }
        data = self._request("GET", "/classes/Bottle", params=params, session=True)
        results = data.get("results") if isinstance(data, dict) else None
        return list(results or [])

    def fetch_bottle_charge(self, *, use_cache: bool = True) -> Dict[str, Any]:
        """Read bottle charge from Parse ``Bottle`` — no invented percent."""
        global _BOTTLE_CACHE
        if use_cache:
            cached = _BOTTLE_CACHE
            if (
                cached.get("charge") is not None
                and (time.time() - float(cached.get("fetched_at") or 0))
                < _BOTTLE_TTL_SEC
            ):
                return dict(cached["charge"])

        try:
            rows = self.fetch_bottle_rows()
        except HidrateError as e:
            if e.status in (401, 403) or "invalid" in str(e).lower():
                global _SESSION_TOKEN
                self._session_token = None
                _SESSION_TOKEN = None
                rows = self.fetch_bottle_rows()
            else:
                raise

        charge = summarize_bottle_charge(rows)
        _BOTTLE_CACHE = {"fetched_at": time.time(), "charge": dict(charge)}
        return charge

    def fetch_sip_rows(
        self,
        *,
        start: datetime,
        end: Optional[datetime] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Return raw Parse ``Sip`` objects filtered on ``time`` (not createdAt)."""
        self.ensure_session()

        def _parse_iso(dt: datetime) -> str:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        where: Dict[str, Any] = {
            "time": {"$gte": {"__type": "Date", "iso": _parse_iso(start)}}
        }
        if end is not None:
            where["time"]["$lte"] = {"__type": "Date", "iso": _parse_iso(end)}
        params = {
            "where": json.dumps(where, separators=(",", ":")),
            "limit": str(max(1, min(int(limit), 500))),
            "order": "time",
        }
        data = self._request("GET", "/classes/Sip", params=params, session=True)
        results = data.get("results") if isinstance(data, dict) else None
        return list(results or [])

    def fetch_hydration_samples(
        self, *, hours: int = 48, use_cache: bool = True
    ) -> List[Dict[str, Any]]:
        """Timestamped Hidrate sips for the recent window. Empty if none."""
        hours = max(1, min(int(hours), 72))
        global _SIP_CACHE
        if use_cache:
            cached = _SIP_CACHE
            if (
                cached.get("hours") == hours
                and cached.get("samples") is not None
                and (time.time() - float(cached.get("fetched_at") or 0))
                < _SIP_TTL_SEC
            ):
                return list(cached["samples"])

        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=hours)
        try:
            rows = self.fetch_sip_rows(start=start, end=now, limit=500)
        except HidrateError as e:
            if e.status in (401, 403) or "invalid" in str(e).lower():
                global _SESSION_TOKEN
                self._session_token = None
                _SESSION_TOKEN = None
                rows = self.fetch_sip_rows(start=start, end=now, limit=500)
            else:
                raise
        samples = parse_sip_samples(rows)
        _SIP_CACHE = {
            "hours": hours,
            "fetched_at": time.time(),
            "samples": list(samples),
        }
        return samples


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


def _empty_bottle_charge(
    status: str, *, error: Optional[str] = None
) -> Dict[str, Any]:
    """Honest empty — never invent a battery percent."""
    return {
        "available": False,
        "percent": None,
        "field": None,
        "name": None,
        "serial": None,
        "status": status,
        "error": error,
    }


def _bottle_sync_iso(row: Dict[str, Any]) -> str:
    for key in ("lastSynced", "updatedAt"):
        v = row.get(key)
        if isinstance(v, dict):
            iso = str(v.get("iso") or "").strip()
            if iso:
                return iso
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _bottle_battery_percent(row: Dict[str, Any]) -> Optional[Tuple[float, str]]:
    """Return (percent, field) from a Parse Bottle row, or None if absent.

    Field names are community-verified (``batteryLevel`` on the unofficial
    Bottle schema / hidratespark-mcp; ``battery`` as a reported alias).
    Values are surfaced as reported when they parse as 0–100. Out-of-range
    or unparseable values are treated as missing — no invented scale.
    """
    if not isinstance(row, dict):
        return None
    for key in _BOTTLE_BATTERY_KEYS:
        if key not in row:
            continue
        v = row.get(key)
        if v is None or v == "":
            continue
        try:
            n = float(v)
        except (TypeError, ValueError):
            continue
        if n != n or n < 0 or n > 100:
            continue
        return n, key
    return None


def summarize_bottle_charge(rows: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Pick charge from Bottle rows. Honest empty when field/list is missing."""
    bottles = [r for r in (rows or []) if isinstance(r, dict)]
    if not bottles:
        return _empty_bottle_charge("empty")

    scored: List[Tuple[str, Dict[str, Any], float, str]] = []
    for row in bottles:
        parsed = _bottle_battery_percent(row)
        if parsed is None:
            continue
        percent, field = parsed
        scored.append((_bottle_sync_iso(row), row, percent, field))
    if not scored:
        return _empty_bottle_charge("missing_field")

    scored.sort(key=lambda item: item[0], reverse=True)
    _iso, row, percent, field = scored[0]
    name = str(row.get("name") or "").strip() or None
    serial = str(row.get("serialNumber") or "").strip() or None
    return {
        "available": True,
        "percent": percent,
        "field": field,
        "name": name,
        "serial": serial,
        "status": "ok",
        "error": None,
    }


def _sip_event_iso(row: Dict[str, Any]) -> str:
    """Authoritative sip time. ``createdAt`` is sync time — do not use."""
    raw = row.get("time")
    if isinstance(raw, dict):
        iso = str(raw.get("iso") or "").strip()
        if iso:
            return iso
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return ""


def _sip_amount_ml(row: Dict[str, Any]) -> Optional[float]:
    v = row.get("amount")
    if v is None or v == "":
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n or n < 0:
        return None
    return n


def parse_sip_samples(rows: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Map Parse Sip rows to timestamped samples. Skip rows missing time/amount."""
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        iso = _sip_event_iso(row)
        ml = _sip_amount_ml(row)
        if not iso or ml is None:
            continue
        out.append(
            {
                "logged_at": iso,
                "water_ml": round(float(ml), 1),
                "source": "hidrate",
            }
        )
    return out


def hidrate_hydration_samples(
    *,
    client: Optional[HidrateClient] = None,
    hours: int = 48,
    use_cache: bool = True,
) -> List[Dict[str, Any]]:
    """Timestamped Hidrate sips, or []. Never invents water."""
    if not hidrate_credentials_present():
        return []
    hc = client or HidrateClient()
    if not hc.credentials_present():
        return []
    try:
        return hc.fetch_hydration_samples(hours=hours, use_cache=use_cache)
    except HidrateError as e:
        log.warning("Hidrate sip pull failed: %s", e)
        return []
    except Exception as e:  # noqa: BLE001
        log.warning("Hidrate sip pull failed: %s", e)
        return []


def hidrate_bottle_charge(
    *, client: Optional[HidrateClient] = None, use_cache: bool = True
) -> Dict[str, Any]:
    """Dashboard helper: Bottle charge or honest empty / unavailable."""
    if not hidrate_credentials_present():
        return _empty_bottle_charge("not_configured")
    hc = client or HidrateClient()
    if not hc.credentials_present():
        return _empty_bottle_charge("not_configured")
    try:
        return hc.fetch_bottle_charge(use_cache=use_cache)
    except HidrateError as e:
        log.warning("Hidrate Bottle charge pull failed: %s", e)
        return _empty_bottle_charge("unavailable", error=str(e))
    except Exception as e:  # noqa: BLE001
        log.warning("Hidrate Bottle charge pull failed: %s", e)
        return _empty_bottle_charge("unavailable", error=str(e))


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
