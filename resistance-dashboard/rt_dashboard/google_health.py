"""Google Health API client for body weight and sleep (legacy Fit fallback).

Uses OAuth2 refresh-token flow:
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN

Preferred scopes (Google Health API):
  https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly
  https://www.googleapis.com/auth/googlehealth.sleep.readonly
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    CaloriesBurnedDay,
    FoodLogEntry,
    HealthSnapshot,
    HydrationDay,
    NutritionDay,
    SleepSample,
    WeightSample,
)

TOKEN_URL = "https://oauth2.googleapis.com/token"
HEALTH_BASE = "https://health.googleapis.com/v4/users/me"
FIT_BASE = "https://www.googleapis.com/fitness/v1/users/me"

# Google Health API rejects access tokens that also carry Calendar (and some other)
# scopes — error DISALLOWED_OAUTH_SCOPES / cl_readonly. Refresh with this subset so
# a multi-scope refresh token (Health + Calendar granted together) still works.
HEALTH_OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly "
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly "
    "https://www.googleapis.com/auth/googlehealth.nutrition.readonly "
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly"
)

# Legacy Fit data types (fallback only)
WEIGHT_DATA_TYPE = "com.google.weight"
SLEEP_DATA_TYPE = "com.google.sleep.segment"


class GoogleHealthError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def _ns(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def _parse_iso_date_from_ns(nanos: int) -> str:
    sec = nanos / 1_000_000_000
    return datetime.fromtimestamp(sec, tz=timezone.utc).strftime("%Y-%m-%d")


def _load_installed_client_from_disk() -> Tuple[str, str]:
    """Pick up client_id/secret from common local OAuth client files."""
    candidates = [
        os.environ.get("GOOGLE_CREDENTIALS_FILE", ""),
        str(Path.home() / ".config" / "resistance-dashboard" / "google-oauth-client.json"),
        str(Path.home() / "Downloads" / "credentials.json"),
        str(Path.home() / "grok_excel_test" / "credentials.json"),
        "/Users/cvolkernick/grok_excel_test/credentials.json",
    ]
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            # Prefer web (Google Health setup) then installed (legacy desktop)
            block = data.get("web") or data.get("installed") or {}
            cid = block.get("client_id") or ""
            sec = block.get("client_secret") or ""
            if cid and sec:
                return cid, sec
        except Exception:
            continue
    return "", ""


class GoogleHealthClient:
    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        refresh_token: Optional[str] = None,
        access_token: Optional[str] = None,
    ):
        disk_id, disk_secret = _load_installed_client_from_disk()
        self.client_id = client_id or os.environ.get("GOOGLE_CLIENT_ID", "") or disk_id
        self.client_secret = (
            client_secret or os.environ.get("GOOGLE_CLIENT_SECRET", "") or disk_secret
        )
        self.refresh_token = refresh_token or os.environ.get("GOOGLE_REFRESH_TOKEN", "")
        self._access_token = access_token or os.environ.get("GOOGLE_ACCESS_TOKEN", "")
        self._token_expiry = 0.0

    def credentials_present(self) -> bool:
        if self._access_token:
            return True
        return bool(self.client_id and self.client_secret and self.refresh_token)

    def ensure_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expiry - 60:
            return self._access_token
        if self._access_token and not self.refresh_token:
            return self._access_token
        if not (self.client_id and self.client_secret and self.refresh_token):
            raise GoogleHealthError(
                "Missing Google OAuth credentials. Set GOOGLE_CLIENT_ID, "
                "GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN (or GOOGLE_ACCESS_TOKEN)."
            )
        # Always mint a Health-only access token. Unrestricted refresh can return
        # Calendar scopes that health.googleapis.com rejects with HTTP 403.
        body = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
                "scope": HEALTH_OAUTH_SCOPES,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            raise GoogleHealthError(
                f"Google token refresh failed: HTTP {e.code}", status=e.code, body=err
            ) from e
        self._access_token = data["access_token"]
        self._token_expiry = time.time() + int(data.get("expires_in", 3600))
        return self._access_token

    def _request(self, method: str, url: str, body: Optional[dict] = None) -> dict:
        token = self.ensure_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "resistance-dashboard/1.0",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            # Per-call timeout stays modest; streams run in parallel in fetch_health.
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            raise GoogleHealthError(
                f"Google Health/Fit API error HTTP {e.code}", status=e.code, body=err
            ) from e

    def _paginate_data_points(self, data_type: str, max_pages: int = 10) -> dict:
        """Collect dataPoints pages for a Google Health data type."""
        all_pts: List[dict] = []
        page_token: Optional[str] = None
        for _ in range(max_pages):
            q: Dict[str, str] = {"pageSize": "100"}
            if page_token:
                q["pageToken"] = page_token
            url = f"{HEALTH_BASE}/dataTypes/{data_type}/dataPoints?{urllib.parse.urlencode(q)}"
            data = self._request("GET", url)
            all_pts.extend(data.get("dataPoints") or [])
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return {"dataPoints": all_pts}

    def fetch_weight_health_api(self, days: int = 30) -> List[WeightSample]:
        """Google Health API: GET .../dataTypes/weight/dataPoints"""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        # Cap pages: ~100 pts/page is plenty for daily weigh-ins over 90d.
        max_pages = 3 if days >= 60 else 2
        data = self._paginate_data_points("weight", max_pages=max_pages)
        return parse_health_api_weight(data, start=start)

    def fetch_sleep_health_api(self, days: int = 14) -> List[SleepSample]:
        """Google Health API: GET .../dataTypes/sleep/dataPoints"""
        samples, _intervals = self.fetch_sleep_health_bundle(days=days)
        return samples

    def fetch_sleep_health_bundle(
        self, days: int = 14
    ) -> Tuple[List[SleepSample], List[Dict[str, Any]]]:
        """Daily sleep totals + timed intervals (for sleep battery).

        Same dataPoints as Time Allocator: real start/end times, not a fixed 7am wake.
        """
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        max_pages = 4 if days >= 60 else 2
        data = self._paginate_data_points("sleep", max_pages=max_pages)
        intervals = parse_sleep_intervals(data, start=start)
        # Prefer daily totals derived from timed intervals (local wake date)
        samples = sleep_samples_from_intervals(intervals)
        if not samples:
            samples = parse_health_api_sleep(data, start=start)
        return samples, intervals

    def fetch_weight_fit(self, days: int = 30) -> List[WeightSample]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        body = {
            "aggregateBy": [{"dataTypeName": WEIGHT_DATA_TYPE}],
            "bucketByTime": {"durationMillis": 86400000},
            "startTimeMillis": int(start.timestamp() * 1000),
            "endTimeMillis": int(end.timestamp() * 1000),
        }
        data = self._request("POST", f"{FIT_BASE}/dataset:aggregate", body)
        return parse_weight_aggregate(data)

    def fetch_sleep_fit(self, days: int = 14) -> List[SleepSample]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        params = urllib.parse.urlencode(
            {
                "startTime": start.isoformat().replace("+00:00", "Z"),
                "endTime": end.isoformat().replace("+00:00", "Z"),
                "activityType": 72,
            }
        )
        try:
            data = self._request("GET", f"{FIT_BASE}/sessions?{params}")
            samples = parse_sleep_sessions(data)
            if samples:
                return samples
        except GoogleHealthError:
            pass
        body = {
            "aggregateBy": [{"dataTypeName": "com.google.activity.segment"}],
            "bucketByTime": {"durationMillis": 86400000},
            "startTimeMillis": int(start.timestamp() * 1000),
            "endTimeMillis": int(end.timestamp() * 1000),
        }
        data = self._request("POST", f"{FIT_BASE}/dataset:aggregate", body)
        return parse_sleep_from_activity_buckets(data)

    def fetch_weight(self, days: int = 30) -> List[WeightSample]:
        try:
            samples = self.fetch_weight_health_api(days=days)
            if samples:
                return samples
        except GoogleHealthError:
            pass
        return self.fetch_weight_fit(days=days)

    def fetch_sleep(self, days: int = 14) -> List[SleepSample]:
        samples, _ = self.fetch_sleep_bundle(days=days)
        return samples

    def fetch_sleep_bundle(
        self, days: int = 14
    ) -> Tuple[List[SleepSample], List[Dict[str, Any]]]:
        """Return (daily samples, timed intervals) for charts + battery."""
        try:
            samples, intervals = self.fetch_sleep_health_bundle(days=days)
            if samples or intervals:
                return samples, intervals
        except GoogleHealthError:
            pass
        return self.fetch_sleep_fit(days=days), []

    def _civil_range_body(
        self,
        days: int,
        page_size: int = 60,
        *,
        end_date: Optional[datetime] = None,
    ) -> dict:
        """Build civil date range for dataPoints:dailyRollUp.

        Google Health treats ``range.end`` as **exclusive** (live check: end=today
        omits today's partial total-calories; end=tomorrow includes today's kcalSum).
        Request end = last_inclusive_day + 1.

        Default last inclusive day is **local** civil today (not UTC).
        """
        if end_date is None:
            end_inclusive = datetime.now().astimezone().date()
        elif isinstance(end_date, datetime):
            end_inclusive = (
                end_date.astimezone().date()
                if end_date.tzinfo is not None
                else end_date.date()
            )
        else:
            end_inclusive = end_date
        start = end_inclusive - timedelta(days=max(1, days - 1))
        end_exclusive = end_inclusive + timedelta(days=1)
        # Google enforces windowSizeDays * pageSize <= maxDuration for some types
        page_size = min(page_size, max(1, days))
        return {
            "range": {
                "start": {
                    "date": {
                        "year": start.year,
                        "month": start.month,
                        "day": start.day,
                    }
                },
                "end": {
                    "date": {
                        "year": end_exclusive.year,
                        "month": end_exclusive.month,
                        "day": end_exclusive.day,
                    }
                },
            },
            "windowSizeDays": 1,
            "pageSize": page_size,
        }

    def daily_rollup(
        self,
        data_type: str,
        days: int = 30,
        *,
        end_date: Optional[datetime] = None,
    ) -> dict:
        body = self._civil_range_body(days=days, end_date=end_date)
        url = f"{HEALTH_BASE}/dataTypes/{data_type}/dataPoints:dailyRollUp"
        return self._request("POST", url, body=body)

    def _chunked_daily_rollup(
        self,
        data_type: str,
        days: int,
        chunk_days: int,
    ) -> dict:
        """Fetch rollups in chunks ending today (local civil), walking backward."""
        days = max(1, int(days))
        chunk_days = max(1, int(chunk_days))
        # Inclusive last day of this chunk (local calendar)
        end = datetime.now().astimezone()
        remaining = days
        all_pts: List[dict] = []
        while remaining > 0:
            take = min(chunk_days, remaining)
            try:
                data = self.daily_rollup(data_type, days=take, end_date=end)
            except GoogleHealthError:
                # Shrink chunk if API rejects larger windows
                if take > 7:
                    take = 7
                    data = self.daily_rollup(data_type, days=take, end_date=end)
                else:
                    raise
            pts = data.get("rollupDataPoints") or []
            all_pts.extend(pts)
            remaining -= take
            end = end - timedelta(days=take)
        return {"rollupDataPoints": all_pts}

    def fetch_nutrition(self, days: int = 90) -> List[NutritionDay]:
        """Food log macros/calories — needs googlehealth.nutrition.readonly."""
        days = max(1, min(int(days), 90))
        # Paginate first. dailyRollUp POST is HTTP 400 (Unknown name startTime)
        # and burns the Vercel function budget before dataPoints land.
        max_pages = 8 if days >= 60 else 6
        data = self._paginate_data_points("nutrition-log", max_pages=max_pages)
        return parse_nutrition_log_points(data, days=days)

    def fetch_food_logs(self, days: int = 14) -> List[FoodLogEntry]:
        """Meal-level nutrition-log entries (food names + macros + micros).

        Daily rollups only give totals; meal plan / coach commentary need
        individual foodDisplayName points. Bound pages tightly — food logs
        are denser than daily weigh-ins.
        """
        days = max(1, min(int(days), 30))
        # ~100 pts/page; heavy loggers may need more pages for 14d.
        max_pages = 8 if days >= 14 else 4
        data = self._paginate_data_points("nutrition-log", max_pages=max_pages)
        return parse_food_log_entries(data, days=days)

    def fetch_hydration(self, days: int = 90) -> List[HydrationDay]:
        """Water intake — needs googlehealth.nutrition.readonly."""
        days = max(1, min(int(days), 90))
        # Paginate first. dailyRollUp POST is HTTP 400 and burns Vercel time.
        max_pages = 6 if days >= 60 else 4
        data = self._paginate_data_points("hydration-log", max_pages=max_pages)
        return parse_hydration_log_points(data, days=days)

    def fetch_calories_burned(self, days: int = 90) -> List[CaloriesBurnedDay]:
        """Activity total calories — needs activity_and_fitness.readonly.

        Google often caps a single total-calories dailyRollUp window (~14d), so
        we walk backward in chunks to cover a full 90-day chart span.
        """
        days = max(1, min(int(days), 90))
        chunk = 14
        try:
            # Try one shot first (works for small windows)
            if days <= chunk:
                data = self.daily_rollup("total-calories", days=days)
                return parse_total_calories_rollup(data)
            data = self._chunked_daily_rollup("total-calories", days, chunk_days=chunk)
            return parse_total_calories_rollup(data)
        except GoogleHealthError:
            # Last resort: single 14d window
            data = self.daily_rollup("total-calories", days=min(days, chunk))
            return parse_total_calories_rollup(data)

    def fetch_health(self, days: int = 30) -> HealthSnapshot:
        if not self.credentials_present():
            return HealthSnapshot(
                error=(
                    "Missing Google OAuth credentials. Run Connect Google Health "
                    "(or set GOOGLE_CLIENT_ID / SECRET / REFRESH_TOKEN)."
                )
            )
        # Warm token once on this thread before parallel workers share it.
        try:
            self.ensure_access_token()
        except GoogleHealthError as e:
            return HealthSnapshot(error=str(e))

        errors: List[str] = []
        weight: List[WeightSample] = []
        sleep: List[SleepSample] = []
        sleep_intervals: List[Dict[str, Any]] = []
        nutrition: List[NutritionDay] = []
        food_logs: List[FoodLogEntry] = []
        hydration: List[HydrationDay] = []
        calories_burned: List[CaloriesBurnedDay] = []

        def _weight() -> List[WeightSample]:
            return self.fetch_weight(days=days)

        def _sleep() -> Tuple[List[SleepSample], List[Dict[str, Any]]]:
            return self.fetch_sleep_bundle(days=days)

        def _nutrition() -> List[NutritionDay]:
            return self.fetch_nutrition(days=days)

        def _food_logs() -> List[FoodLogEntry]:
            # Meal-level detail for coach + meal plan (shorter window).
            return self.fetch_food_logs(days=min(14, days))

        def _hydration() -> List[HydrationDay]:
            return self.fetch_hydration(days=days)

        def _burned() -> List[CaloriesBurnedDay]:
            return self.fetch_calories_burned(days=days)

        jobs = {
            "weight": _weight,
            "sleep": _sleep,
            "nutrition": _nutrition,
            "food_logs": _food_logs,
            "hydration": _hydration,
            "calories_burned": _burned,
        }
        # Parallel streams — sequential multi-calls was exceeding the dashboard
        # 20s wall timeout even when Google was healthy.
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {pool.submit(fn): name for name, fn in jobs.items()}
            for fut in as_completed(futs):
                name = futs[fut]
                try:
                    result = fut.result()
                except GoogleHealthError as e:
                    errors.append(f"{name}: {e}")
                    continue
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{name}: {e}")
                    continue
                if name == "weight":
                    weight = result  # type: ignore[assignment]
                elif name == "sleep":
                    sleep, sleep_intervals = result  # type: ignore[misc]
                elif name == "nutrition":
                    nutrition = result  # type: ignore[assignment]
                elif name == "food_logs":
                    food_logs = result  # type: ignore[assignment]
                elif name == "hydration":
                    hydration = result  # type: ignore[assignment]
                elif name == "calories_burned":
                    calories_burned = result  # type: ignore[assignment]

        err = "; ".join(errors) if errors else None
        if (
            not weight
            and not sleep
            and not nutrition
            and not food_logs
            and not hydration
            and not calories_burned
            and err
        ):
            return HealthSnapshot(error=err)
        return HealthSnapshot(
            weight=weight,
            sleep=sleep,
            sleep_intervals=sleep_intervals,
            nutrition=nutrition,
            food_logs=food_logs,
            hydration=hydration,
            calories_burned=calories_burned,
            error=err,
        )


def _parse_rfc3339_date(s: str) -> Optional[str]:
    if not s:
        return None
    # 2026-05-12T00:00:00Z or with offset
    try:
        if s.endswith("Z"):
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(s)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
    except ValueError:
        if len(s) >= 10 and s[4] == "-":
            return s[:10]
        return None


def parse_health_api_weight(
    payload: dict, start: Optional[datetime] = None
) -> List[WeightSample]:
    """Parse Google Health API weight dataPoints list.

    Observed shape (Fitbit via Google Health)::
      weight.sampleTime.physicalTime / civilTime.date
      weight.weightGrams
    """
    samples: List[WeightSample] = []
    start = start or datetime.now(timezone.utc) - timedelta(days=365)
    start_date = start.strftime("%Y-%m-%d")
    for pt in payload.get("dataPoints") or []:
        w = pt.get("weight") if isinstance(pt.get("weight"), dict) else {}
        # Date: prefer civil calendar date (local weigh-in day), else physical UTC
        date = None
        st = (w.get("sampleTime") or {}) if isinstance(w, dict) else {}
        civil = ((st.get("civilTime") or {}).get("date")) if isinstance(st, dict) else None
        if isinstance(civil, dict) and civil.get("year"):
            date = f"{int(civil['year']):04d}-{int(civil['month']):02d}-{int(civil['day']):02d}"
        if not date:
            ts = (
                (st.get("physicalTime") if isinstance(st, dict) else None)
                or pt.get("startTime")
                or pt.get("endTime")
            )
            date = _parse_rfc3339_date(str(ts)) if ts else None
        if not date or date < start_date:
            if date and date < start_date:
                continue
            if not date:
                continue

        kg = None
        grams = None
        if isinstance(w, dict):
            grams = w.get("weightGrams") or w.get("weight_grams")
            kg = w.get("weightKg") or w.get("weight_kg") or w.get("kg")
        if grams is not None:
            kg = float(grams) / 1000.0
        if kg is None:
            continue
        lbs = float(kg) * 2.2046226218
        samples.append(
            WeightSample(date=date, weight_lbs=round(lbs, 2), source="google_health")
        )
    by_date: Dict[str, WeightSample] = {}
    for s in samples:
        by_date[s.date] = s
    return [by_date[k] for k in sorted(by_date.keys())]


def _parse_interval_times(pt: dict) -> Optional[Tuple[datetime, datetime]]:
    sleep = pt.get("sleep") or pt.get("data") or pt
    interval = (
        (sleep.get("interval") if isinstance(sleep, dict) else None)
        or pt.get("interval")
        or {}
    )
    st = interval.get("startTime") or pt.get("startTime")
    en = interval.get("endTime") or pt.get("endTime")
    if not st or not en:
        return None
    try:
        st_s, en_s = str(st), str(en)
        if st_s.endswith("Z"):
            st_s = st_s[:-1] + "+00:00"
        if en_s.endswith("Z"):
            en_s = en_s[:-1] + "+00:00"
        start_dt = datetime.fromisoformat(st_s)
        end_dt = datetime.fromisoformat(en_s)
    except ValueError:
        return None
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    if end_dt <= start_dt:
        return None
    return start_dt, end_dt


def parse_sleep_intervals(
    payload: dict, start: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    """Timed sleep sessions [{start, end, source}] — same shape as Time Allocator."""
    cutoff = start or datetime.now(timezone.utc) - timedelta(days=14)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    rows: List[Dict[str, Any]] = []
    for pt in payload.get("dataPoints") or []:
        parsed = _parse_interval_times(pt if isinstance(pt, dict) else {})
        if not parsed:
            continue
        start_dt, end_dt = parsed
        if end_dt < cutoff:
            continue
        if (end_dt - start_dt).total_seconds() > 36 * 3600:
            continue
        rows.append(
            {
                "start": start_dt.isoformat(timespec="seconds"),
                "end": end_dt.isoformat(timespec="seconds"),
                "source": "google_health",
            }
        )
    # Dedupe
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for r in sorted(rows, key=lambda x: x["start"]):
        key = (r["start"], r["end"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def sleep_samples_from_intervals(
    intervals: List[Dict[str, Any]],
) -> List[SleepSample]:
    """Aggregate timed intervals to daily totals by local wake (end) date."""
    by_date: Dict[str, float] = {}
    for iv in intervals or []:
        try:
            en = datetime.fromisoformat(str(iv.get("end") or "").replace("Z", "+00:00"))
            st = datetime.fromisoformat(str(iv.get("start") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if en.tzinfo is None:
            en = en.replace(tzinfo=timezone.utc)
        if st.tzinfo is None:
            st = st.replace(tzinfo=timezone.utc)
        hours = max(0.0, (en - st).total_seconds() / 3600.0)
        if hours <= 0:
            continue
        day = en.astimezone().date().isoformat()  # local civil wake date
        by_date[day] = by_date.get(day, 0.0) + hours
    return [
        SleepSample(date=d, sleep_hours=round(h, 2), source="google_health")
        for d, h in sorted(by_date.items())
    ]


def parse_health_api_sleep(
    payload: dict, start: Optional[datetime] = None
) -> List[SleepSample]:
    """Parse Google Health API sleep dataPoints into daily totals.

    Prefer ``sleep_samples_from_intervals(parse_sleep_intervals(...))`` when
    timed intervals are available (local wake date). This fallback attributes
    hours to the local end date of each session.
    """
    by_date: Dict[str, float] = {}
    start = start or datetime.now(timezone.utc) - timedelta(days=14)
    for pt in payload.get("dataPoints") or []:
        if not isinstance(pt, dict):
            continue
        parsed = _parse_interval_times(pt)
        if not parsed:
            continue
        start_dt, end_dt = parsed
        hours = (end_dt - start_dt).total_seconds() / 3600.0
        if hours <= 0:
            sleep = pt.get("sleep") or pt.get("data") or pt
            mins = None
            if isinstance(sleep, dict):
                mins = sleep.get("durationMinutes") or sleep.get("totalSleepMinutes")
            if mins:
                hours = float(mins) / 60.0
            else:
                continue
        # Local wake date (matches Time Allocator daily attribution)
        date = end_dt.astimezone().strftime("%Y-%m-%d")
        by_date[date] = by_date.get(date, 0.0) + hours
    start_date = start.strftime("%Y-%m-%d")
    return [
        SleepSample(date=d, sleep_hours=round(h, 2), source="google_health")
        for d, h in sorted(by_date.items())
        if d >= start_date
    ]


def _civil_date_str(obj: Optional[dict]) -> Optional[str]:
    if not isinstance(obj, dict):
        return None
    d = obj.get("date") if "date" in obj else obj
    if not isinstance(d, dict) or not d.get("year"):
        return None
    return f"{int(d['year']):04d}-{int(d['month']):02d}-{int(d['day']):02d}"


def _num(*vals) -> Optional[float]:
    for v in vals:
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _deep_find_num(obj: Any, keys: Tuple[str, ...]) -> Optional[float]:
    """Find first numeric field matching any of keys (case-insensitive) in nested dicts."""
    keyset = {k.lower() for k in keys}
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k.lower() in keyset:
                    n = _num(v)
                    if n is not None:
                        return n
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def _mass_grams(obj: Any) -> Optional[float]:
    """Read grams / gramsSum from a mass object or bare number."""
    if obj is None:
        return None
    if isinstance(obj, (int, float)):
        return float(obj)
    if isinstance(obj, dict):
        return _num(obj.get("grams"), obj.get("gramsSum"), obj.get("value"), obj.get("sum"))
    return None


def _energy_kcal(obj: Any) -> Optional[float]:
    if obj is None:
        return None
    if isinstance(obj, (int, float)):
        return float(obj)
    if isinstance(obj, dict):
        return _num(obj.get("kcal"), obj.get("kcalSum"), obj.get("calories"), obj.get("value"))
    return None


def _nutrient_grams_from_list(nlog: dict, name: str) -> Optional[float]:
    want = name.upper()
    aliases = {want}
    if want == "PROTEIN":
        aliases |= {"PROTEIN", "TOTAL_PROTEIN"}
    if want in ("CARBOHYDRATES", "CARBS"):
        aliases |= {"CARBOHYDRATES", "CARBS", "TOTAL_CARBOHYDRATE", "TOTAL_CARBOHYDRATES"}
    if want in ("FAT", "TOTAL_FAT"):
        aliases |= {"FAT", "TOTAL_FAT"}
    for item in nlog.get("nutrients") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("nutrient", "")).upper() in aliases:
            q = item.get("quantity") or {}
            g = _mass_grams(q)
            if g is not None:
                return g
    return None


def _all_nutrients_grams(nlog: dict) -> Dict[str, float]:
    """Map nutrient enum → grams (skip macros already on FoodLogEntry)."""
    skip = {
        "PROTEIN",
        "TOTAL_PROTEIN",
        "CARBOHYDRATES",
        "CARBS",
        "TOTAL_CARBOHYDRATE",
        "TOTAL_CARBOHYDRATES",
        "FAT",
        "TOTAL_FAT",
        "NUTRIENT_UNSPECIFIED",
    }
    out: Dict[str, float] = {}
    for item in nlog.get("nutrients") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("nutrient") or "").upper().strip()
        if not key or key in skip:
            continue
        q = item.get("quantity") or {}
        g = _mass_grams(q)
        if g is None:
            continue
        out[key] = round(float(g), 4)
    return out


def _meal_type_label(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.upper() in ("MEAL_TYPE_UNSPECIFIED", "UNSPECIFIED", ""):
        return None
    # BREAKFAST → Breakfast, BEFORE_LUNCH → Before lunch
    return s.replace("_", " ").title()


def _serving_label(nlog: dict) -> Optional[str]:
    serving = nlog.get("serving") or {}
    if not isinstance(serving, dict):
        return None
    amount = serving.get("amount")
    unit = (
        serving.get("foodMeasurementUnitDisplayName")
        or serving.get("foodMeasurementUnit")
        or ""
    )
    unit = str(unit).strip()
    if amount is None and not unit:
        return None
    if amount is None:
        return unit or None
    try:
        a = float(amount)
        a_s = str(int(a)) if a == int(a) else f"{a:g}"
    except (TypeError, ValueError):
        a_s = str(amount)
    return f"{a_s} {unit}".strip() if unit else a_s


def _civil_time_hm(civil: Any) -> Optional[str]:
    if not isinstance(civil, dict):
        return None
    t = civil.get("time") or {}
    if not isinstance(t, dict):
        return None
    h = t.get("hours")
    m = t.get("minutes")
    if h is None and m is None:
        return None
    try:
        return f"{int(h or 0):02d}:{int(m or 0):02d}"
    except (TypeError, ValueError):
        return None


def _macros_from_nutrition_log(nlog: dict) -> Dict[str, Optional[float]]:
    """Extract calories + macros from a meal log or daily rollup nutritionLog block.

    Meal shape: energy.kcal, totalCarbohydrate.grams, nutrients[].quantity.grams
    Rollup shape: energy.kcalSum, totalCarbohydrate.gramsSum,
                  nutrients[].quantity.gramsSum (PROTEIN often only here)
    """
    # Prefer total food energy — never energyFromFat (that is only fat-derived kcal).
    cal = _energy_kcal(nlog.get("energy"))
    if cal is None:
        cal = _num(nlog.get("calories"), nlog.get("kcal"))

    carbs = _mass_grams(nlog.get("totalCarbohydrate") or nlog.get("total_carbohydrate"))
    fat = _mass_grams(nlog.get("totalFat") or nlog.get("total_fat"))
    protein = _mass_grams(nlog.get("totalProtein") or nlog.get("total_protein"))

    if protein is None:
        protein = _nutrient_grams_from_list(nlog, "PROTEIN")
    if carbs is None:
        carbs = _nutrient_grams_from_list(nlog, "CARBOHYDRATES")
    if fat is None:
        fat = _nutrient_grams_from_list(nlog, "FAT") or _nutrient_grams_from_list(
            nlog, "TOTAL_FAT"
        )

    return {
        "calories": cal,
        "protein_g": protein,
        "carbs_g": carbs,
        "fat_g": fat,
    }


def parse_nutrition_log_points(payload: dict, days: int = 30) -> List[NutritionDay]:
    """Aggregate meal-level nutrition-log points into daily totals.

    Observed Google Health / Fitbit shape::
      nutritionLog.interval.civilStartTime.date
      nutritionLog.energy.kcal
      nutritionLog.totalCarbohydrate.grams / totalFat.grams
      nutritionLog.nutrients[{nutrient: PROTEIN|CARBOHYDRATES, quantity.grams}]
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    by_date: Dict[str, Dict[str, float]] = {}
    for pt in payload.get("dataPoints") or []:
        nlog = pt.get("nutritionLog") or pt.get("nutrition_log") or pt.get("nutrition") or pt
        if not isinstance(nlog, dict):
            continue
        date = None
        interval = nlog.get("interval") or {}
        civil = (interval.get("civilStartTime") or {}).get("date")
        if civil:
            date = _civil_date_str({"date": civil})
        if not date:
            date = _parse_rfc3339_date(str(interval.get("startTime") or pt.get("startTime") or ""))
        if not date or date < cutoff:
            continue

        macros = _macros_from_nutrition_log(nlog)
        bucket = by_date.setdefault(
            date, {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
        )
        for k in ("calories", "protein_g", "carbs_g", "fat_g"):
            if macros.get(k) is not None:
                bucket[k] += float(macros[k])  # type: ignore[arg-type]
    out: List[NutritionDay] = []
    for d in sorted(by_date.keys()):
        b = by_date[d]
        if not any(b.values()):
            continue
        out.append(
            NutritionDay(
                date=d,
                calories=round(b["calories"], 1) if b["calories"] else None,
                protein_g=round(b["protein_g"], 1) if b["protein_g"] else None,
                carbs_g=round(b["carbs_g"], 1) if b["carbs_g"] else None,
                fat_g=round(b["fat_g"], 1) if b["fat_g"] else None,
                source="google_health",
            )
        )
    return out


def parse_food_log_entries(payload: dict, days: int = 14) -> List[FoodLogEntry]:
    """Parse meal-level nutrition-log dataPoints (keep food names + micros).

    Google Health NutritionLog shape::
      foodDisplayName, mealType, serving, energy.kcal,
      totalCarbohydrate/totalFat, nutrients[{nutrient, quantity.grams}],
      interval.civilStartTime.{date,time}
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    entries: List[FoodLogEntry] = []
    for pt in payload.get("dataPoints") or []:
        nlog = pt.get("nutritionLog") or pt.get("nutrition_log") or pt.get("nutrition")
        if not isinstance(nlog, dict):
            continue
        date = None
        time_hm = None
        interval = nlog.get("interval") or {}
        civil = interval.get("civilStartTime") or {}
        if isinstance(civil, dict) and civil.get("date"):
            date = _civil_date_str({"date": civil.get("date")})
            time_hm = _civil_time_hm(civil)
        if not date:
            date = _parse_rfc3339_date(
                str(interval.get("startTime") or pt.get("startTime") or "")
            )
        if not date or date < cutoff:
            continue

        name = (
            str(nlog.get("foodDisplayName") or nlog.get("food_display_name") or "")
            .strip()
        )
        if not name:
            # Identified food resource name is not human-friendly; last resort.
            food_ref = nlog.get("food")
            if food_ref:
                name = str(food_ref).rsplit("/", 1)[-1][:48]
            else:
                name = "Logged food"

        macros = _macros_from_nutrition_log(nlog)
        if all(
            macros.get(k) is None
            for k in ("calories", "protein_g", "carbs_g", "fat_g")
        ):
            # Empty / incomplete point — still keep if named? skip empty.
            continue

        entries.append(
            FoodLogEntry(
                date=date,
                name=name,
                calories=round(macros["calories"], 1)
                if macros["calories"] is not None
                else None,
                protein_g=round(macros["protein_g"], 1)
                if macros["protein_g"] is not None
                else None,
                carbs_g=round(macros["carbs_g"], 1)
                if macros["carbs_g"] is not None
                else None,
                fat_g=round(macros["fat_g"], 1)
                if macros["fat_g"] is not None
                else None,
                meal_type=_meal_type_label(nlog.get("mealType") or nlog.get("meal_type")),
                serving_label=_serving_label(nlog),
                time=time_hm,
                nutrients=_all_nutrients_grams(nlog),
                source="google_health",
            )
        )

    # Chronological: date then time
    def _sort_key(e: FoodLogEntry) -> Tuple[str, str, str]:
        return (e.date, e.time or "99:99", e.name.lower())

    entries.sort(key=_sort_key)
    return entries


def parse_nutrition_rollup(payload: dict) -> List[NutritionDay]:
    """Parse dailyRollUp nutrition-log points.

    Observed Fitbit→Google Health shape (2026)::
      rollupDataPoints[].civilStartTime.date
      nutritionLog.energy.kcalSum
      nutritionLog.totalCarbohydrate.gramsSum / totalFat.gramsSum
      nutritionLog.nutrients[{nutrient: PROTEIN, quantity.gramsSum}]
    """
    by_date: Dict[str, NutritionDay] = {}
    for pt in payload.get("rollupDataPoints") or []:
        date = _civil_date_str(pt.get("civilStartTime"))
        if not date:
            continue
        n = pt.get("nutritionLog") or pt.get("nutrition_log") or {}
        if not isinstance(n, dict):
            continue
        macros = _macros_from_nutrition_log(n)
        if all(macros.get(k) is None for k in ("calories", "protein_g", "carbs_g", "fat_g")):
            continue
        by_date[date] = NutritionDay(
            date=date,
            calories=round(macros["calories"], 1) if macros["calories"] is not None else None,
            protein_g=round(macros["protein_g"], 1)
            if macros["protein_g"] is not None
            else None,
            carbs_g=round(macros["carbs_g"], 1) if macros["carbs_g"] is not None else None,
            fat_g=round(macros["fat_g"], 1) if macros["fat_g"] is not None else None,
            source="google_health",
        )
    return [by_date[k] for k in sorted(by_date.keys())]


def parse_hydration_log_points(payload: dict, days: int = 30) -> List[HydrationDay]:
    """Observed shape: hydrationLog.amountConsumed.milliliters + interval.civilStartTime."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    by_date: Dict[str, float] = {}
    for pt in payload.get("dataPoints") or []:
        h = pt.get("hydrationLog") or pt.get("hydration_log") or pt.get("hydration") or pt
        if not isinstance(h, dict):
            continue
        date = None
        interval = h.get("interval") or {}
        civil = (interval.get("civilStartTime") or {}).get("date")
        if civil:
            date = _civil_date_str({"date": civil})
        if not date:
            date = _parse_rfc3339_date(str(interval.get("startTime") or pt.get("startTime") or ""))
        if not date or date < cutoff:
            continue
        amt = h.get("amountConsumed") or {}
        ml = _num(amt.get("milliliters"), amt.get("ml"), h.get("milliliters"))
        if ml is None:
            liters = _num(amt.get("liters"), amt.get("volumeLiters"))
            if liters is not None:
                ml = liters * 1000.0
        if ml is None:
            continue
        by_date[date] = by_date.get(date, 0.0) + float(ml)
    return [
        HydrationDay(date=d, water_ml=round(v, 1), source="google_health")
        for d, v in sorted(by_date.items())
    ]


def parse_hydration_rollup(payload: dict) -> List[HydrationDay]:
    out: List[HydrationDay] = []
    for pt in payload.get("rollupDataPoints") or []:
        date = _civil_date_str(pt.get("civilStartTime"))
        if not date:
            continue
        h = pt.get("hydrationLog") or pt.get("hydration_log") or {}
        ml = _deep_find_num(h, ("volumeml", "volumemlsum", "waterml", "ml", "mlsum"))
        if ml is None:
            liters = _deep_find_num(h, ("volumeliters", "volumeliterssum", "liters"))
            if liters is not None:
                ml = liters * 1000.0
        if ml is None:
            continue
        out.append(HydrationDay(date=date, water_ml=round(ml, 1), source="google_health"))
    return out


def parse_total_calories_rollup(payload: dict) -> List[CaloriesBurnedDay]:
    by_date: Dict[str, CaloriesBurnedDay] = {}
    for pt in payload.get("rollupDataPoints") or []:
        date = _civil_date_str(pt.get("civilStartTime"))
        if not date:
            continue
        tc = pt.get("totalCalories") or pt.get("total_calories") or {}
        kcal = _num(tc.get("kcalSum"), tc.get("kcal"), tc.get("calories"))
        if kcal is None:
            continue
        by_date[date] = CaloriesBurnedDay(
            date=date, calories=round(kcal, 1), source="google_health"
        )
    return [by_date[k] for k in sorted(by_date.keys())]


def parse_weight_aggregate(payload: dict) -> List[WeightSample]:
    """Parse Google Fit dataset:aggregate response for com.google.weight."""
    samples: List[WeightSample] = []
    for bucket in payload.get("bucket", []):
        for dataset in bucket.get("dataset", []):
            for point in dataset.get("point", []):
                values = point.get("value", [])
                if not values:
                    continue
                # weight in kg as fpVal
                kg = values[0].get("fpVal")
                if kg is None:
                    continue
                lbs = float(kg) * 2.2046226218
                start_ns = int(point.get("startTimeNanos") or bucket.get("startTimeMillis", 0) * 1_000_000)
                if point.get("startTimeNanos"):
                    date = _parse_iso_date_from_ns(int(point["startTimeNanos"]))
                else:
                    ms = int(bucket.get("startTimeMillis", 0))
                    date = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
                        "%Y-%m-%d"
                    )
                samples.append(
                    WeightSample(date=date, weight_lbs=round(lbs, 2), source="google_fit")
                )
    # dedupe by date keeping last
    by_date: Dict[str, WeightSample] = {}
    for s in samples:
        by_date[s.date] = s
    return [by_date[k] for k in sorted(by_date.keys())]


def parse_sleep_sessions(payload: dict) -> List[SleepSample]:
    samples: List[SleepSample] = []
    for sess in payload.get("session", []):
        # activityType 72 = sleep
        if int(sess.get("activityType", 72)) not in (72,):
            continue
        start_ms = int(sess.get("startTimeMillis", 0))
        end_ms = int(sess.get("endTimeMillis", 0))
        if end_ms <= start_ms:
            continue
        hours = (end_ms - start_ms) / 3_600_000
        date = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%d"
        )
        samples.append(
            SleepSample(date=date, sleep_hours=round(hours, 2), source="google_fit")
        )
    # sum multiple sessions per night
    by_date: Dict[str, float] = {}
    for s in samples:
        by_date[s.date] = by_date.get(s.date, 0.0) + s.sleep_hours
    return [
        SleepSample(date=d, sleep_hours=round(h, 2), source="google_fit")
        for d, h in sorted(by_date.items())
    ]


def parse_sleep_from_activity_buckets(payload: dict) -> List[SleepSample]:
    """Best-effort: activity type 72 durations in aggregate buckets."""
    samples: List[SleepSample] = []
    for bucket in payload.get("bucket", []):
        ms = int(bucket.get("startTimeMillis", 0))
        date = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        sleep_ms = 0
        for dataset in bucket.get("dataset", []):
            for point in dataset.get("point", []):
                # activity segment: intVal activity, optional duration
                vals = point.get("value", [])
                activity = None
                for v in vals:
                    if "intVal" in v:
                        activity = v["intVal"]
                if activity == 72:
                    sn = int(point.get("startTimeNanos", 0))
                    en = int(point.get("endTimeNanos", 0))
                    if en > sn:
                        sleep_ms += (en - sn) / 1_000_000
        if sleep_ms > 0:
            samples.append(
                SleepSample(
                    date=date,
                    sleep_hours=round(sleep_ms / 3_600_000, 2),
                    source="google_fit",
                )
            )
    return samples


def parse_recorded_weight_payload(payload: dict) -> List[WeightSample]:
    """Public alias for tests — real wire-format parsing."""
    return parse_weight_aggregate(payload)


def parse_recorded_sleep_payload(payload: dict) -> List[SleepSample]:
    return parse_sleep_sessions(payload)
