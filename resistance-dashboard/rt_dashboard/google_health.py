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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    CaloriesBurnedDay,
    HealthSnapshot,
    HydrationDay,
    NutritionDay,
    SleepSample,
    WeightSample,
)

TOKEN_URL = "https://oauth2.googleapis.com/token"
HEALTH_BASE = "https://health.googleapis.com/v4/users/me"
FIT_BASE = "https://www.googleapis.com/fitness/v1/users/me"

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
        body = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
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
            with urllib.request.urlopen(req, timeout=45) as resp:
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
        max_pages = 12 if days >= 60 else 8
        data = self._paginate_data_points("weight", max_pages=max_pages)
        return parse_health_api_weight(data, start=start)

    def fetch_sleep_health_api(self, days: int = 14) -> List[SleepSample]:
        """Google Health API: GET .../dataTypes/sleep/dataPoints"""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        # More pages for longer windows (e.g. 90d rolling averages)
        max_pages = 12 if days >= 60 else 5
        data = self._paginate_data_points("sleep", max_pages=max_pages)
        return parse_health_api_sleep(data, start=start)

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
        try:
            samples = self.fetch_sleep_health_api(days=days)
            if samples:
                return samples
        except GoogleHealthError:
            pass
        return self.fetch_sleep_fit(days=days)

    def _civil_range_body(self, days: int, page_size: int = 60) -> dict:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=max(1, days - 1))
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
                        "year": end.year,
                        "month": end.month,
                        "day": end.day,
                    }
                },
            },
            "windowSizeDays": 1,
            "pageSize": page_size,
        }

    def daily_rollup(self, data_type: str, days: int = 30) -> dict:
        body = self._civil_range_body(days=days)
        url = f"{HEALTH_BASE}/dataTypes/{data_type}/dataPoints:dailyRollUp"
        return self._request("POST", url, body=body)

    def fetch_nutrition(self, days: int = 30) -> List[NutritionDay]:
        """Food log macros/calories — needs googlehealth.nutrition.readonly."""
        # Prefer raw list (meal-level), fall back to daily rollup
        try:
            data = self._paginate_data_points("nutrition-log", max_pages=6)
            days_list = parse_nutrition_log_points(data, days=days)
            if days_list:
                return days_list
        except GoogleHealthError:
            pass
        data = self.daily_rollup("nutrition-log", days=days)
        return parse_nutrition_rollup(data)

    def fetch_hydration(self, days: int = 30) -> List[HydrationDay]:
        """Water intake — needs googlehealth.nutrition.readonly."""
        try:
            data = self._paginate_data_points("hydration-log", max_pages=4)
            days_list = parse_hydration_log_points(data, days=days)
            if days_list:
                return days_list
        except GoogleHealthError:
            pass
        data = self.daily_rollup("hydration-log", days=days)
        return parse_hydration_rollup(data)

    def fetch_calories_burned(self, days: int = 14) -> List[CaloriesBurnedDay]:
        """Activity total calories — needs activity_and_fitness.readonly."""
        data = self.daily_rollup("total-calories", days=min(days, 14))
        return parse_total_calories_rollup(data)

    def fetch_health(self, days: int = 30) -> HealthSnapshot:
        if not self.credentials_present():
            return HealthSnapshot(
                error=(
                    "Missing Google OAuth credentials. Run Connect Google Health "
                    "(or set GOOGLE_CLIENT_ID / SECRET / REFRESH_TOKEN)."
                )
            )
        errors: List[str] = []
        weight: List[WeightSample] = []
        sleep: List[SleepSample] = []
        nutrition: List[NutritionDay] = []
        hydration: List[HydrationDay] = []
        calories_burned: List[CaloriesBurnedDay] = []
        try:
            weight = self.fetch_weight(days=days)
        except GoogleHealthError as e:
            errors.append(f"weight: {e}")
        try:
            # Sleep needs enough history for rolling averages (e.g. 90d charts)
            sleep = self.fetch_sleep(days=days)
        except GoogleHealthError as e:
            errors.append(f"sleep: {e}")
        try:
            nutrition = self.fetch_nutrition(days=min(days, 30))
        except GoogleHealthError as e:
            errors.append(f"nutrition: {e}")
        try:
            hydration = self.fetch_hydration(days=min(days, 30))
        except GoogleHealthError as e:
            errors.append(f"hydration: {e}")
        try:
            # total-calories rollup max window is 14 days per API
            calories_burned = self.fetch_calories_burned(days=min(days, 14))
        except GoogleHealthError as e:
            errors.append(f"calories_burned: {e}")
        err = "; ".join(errors) if errors else None
        if (
            not weight
            and not sleep
            and not nutrition
            and not hydration
            and not calories_burned
            and err
        ):
            return HealthSnapshot(error=err)
        return HealthSnapshot(
            weight=weight,
            sleep=sleep,
            nutrition=nutrition,
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


def parse_health_api_sleep(
    payload: dict, start: Optional[datetime] = None
) -> List[SleepSample]:
    """Parse Google Health API sleep dataPoints list."""
    by_date: Dict[str, float] = {}
    start = start or datetime.now(timezone.utc) - timedelta(days=14)
    for pt in payload.get("dataPoints") or []:
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
            if str(st).endswith("Z"):
                start_dt = datetime.fromisoformat(str(st).replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(str(en).replace("Z", "+00:00"))
            else:
                start_dt = datetime.fromisoformat(str(st))
                end_dt = datetime.fromisoformat(str(en))
        except ValueError:
            continue
        hours = (end_dt - start_dt).total_seconds() / 3600.0
        if hours <= 0:
            # total sleep minutes field if present
            mins = None
            if isinstance(sleep, dict):
                mins = sleep.get("durationMinutes") or sleep.get("totalSleepMinutes")
            if mins:
                hours = float(mins) / 60.0
            else:
                continue
        date = start_dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
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


def _nutrient_grams_from_list(nlog: dict, name: str) -> Optional[float]:
    for item in nlog.get("nutrients") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("nutrient", "")).upper() == name.upper():
            q = item.get("quantity") or {}
            return _num(q.get("grams"), q.get("value"))
    return None


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

        energy = nlog.get("energy") or {}
        cal = _num(energy.get("kcal"), energy.get("calories"))
        carbs = _num((nlog.get("totalCarbohydrate") or {}).get("grams"))
        fat = _num((nlog.get("totalFat") or {}).get("grams"))
        protein = _num((nlog.get("totalProtein") or {}).get("grams"))
        if protein is None:
            protein = _nutrient_grams_from_list(nlog, "PROTEIN")
        if carbs is None:
            carbs = _nutrient_grams_from_list(nlog, "CARBOHYDRATES")
        if fat is None:
            fat = _nutrient_grams_from_list(nlog, "FAT") or _nutrient_grams_from_list(
                nlog, "TOTAL_FAT"
            )

        bucket = by_date.setdefault(
            date, {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
        )
        if cal is not None:
            bucket["calories"] += cal
        if protein is not None:
            bucket["protein_g"] += protein
        if carbs is not None:
            bucket["carbs_g"] += carbs
        if fat is not None:
            bucket["fat_g"] += fat
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


def parse_nutrition_rollup(payload: dict) -> List[NutritionDay]:
    out: List[NutritionDay] = []
    for pt in payload.get("rollupDataPoints") or []:
        date = _civil_date_str(pt.get("civilStartTime"))
        if not date:
            continue
        n = pt.get("nutritionLog") or pt.get("nutrition_log") or {}
        cal = _deep_find_num(n, ("calories", "calorieskcal", "calorieskcalsum", "kcalsum", "kcal"))
        protein = _deep_find_num(n, ("proteingrams", "proteingramssum", "protein"))
        carbs = _deep_find_num(n, ("carbohydratesgrams", "carbohydratesgramssum", "carbs"))
        fat = _deep_find_num(n, ("fatgrams", "fatgramssum", "fat"))
        if cal is None and protein is None and carbs is None and fat is None:
            continue
        out.append(
            NutritionDay(
                date=date,
                calories=round(cal, 1) if cal is not None else None,
                protein_g=round(protein, 1) if protein is not None else None,
                carbs_g=round(carbs, 1) if carbs is not None else None,
                fat_g=round(fat, 1) if fat is not None else None,
                source="google_health",
            )
        )
    return out


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
    out: List[CaloriesBurnedDay] = []
    for pt in payload.get("rollupDataPoints") or []:
        date = _civil_date_str(pt.get("civilStartTime"))
        if not date:
            continue
        tc = pt.get("totalCalories") or pt.get("total_calories") or {}
        kcal = _num(tc.get("kcalSum"), tc.get("kcal"), tc.get("calories"))
        if kcal is None:
            continue
        out.append(
            CaloriesBurnedDay(date=date, calories=round(kcal, 1), source="google_health")
        )
    return out


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
