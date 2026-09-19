"""Rivian Turo Bookings sheet upsert. Never invent Totals."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
TOKEN_URL = "https://oauth2.googleapis.com/token"
RIVIAN_SHEET_ID = "1H4hjK7hNOyUHAIekWwxuqf3NgZOpSdyezHA7rQ3Zafc"
TURO_BOOKINGS_TAB = "Turo Bookings"
DEFAULT_TOKEN_PATH = Path.home() / ".config" / "auto-fleet" / "gsheets-token.json"
RIVIAN_UNIT_IDS = frozenset({"r1s-2023"})

HttpFn = Callable[[str, Optional[bytes], Mapping[str, str]], Any]

_HEADER_FIELDS = {
    "reservation": "trip_id",
    "reservation id": "trip_id",
    "reservationid": "trip_id",
    "trip": "trip_id",
    "trip id": "trip_id",
    "tripid": "trip_id",
    "booking": "trip_id",
    "guest": "guest",
    "renter": "guest",
    "start": "start",
    "trip start": "start",
    "pickup": "start",
    "begin": "start",
    "end": "end",
    "trip end": "end",
    "return": "end",
    "drop-off": "end",
    "drop off": "end",
    "pickup location": "pickup",
    "delivery": "pickup",
    "delivery location": "pickup",
    "meet": "pickup",
    "return location": "drop_off",
    "drop-off location": "drop_off",
    "drop off location": "drop_off",
    "vehicle": "vehicle",
    "car": "vehicle",
    "payout": "payout",
    "host payout": "payout",
    "host copy": "payout",
}
_TOTALS_MARKERS = ("total", "totals")


def _http_json(
    url: str,
    data: bytes | None = None,
    headers: Mapping[str, str] | None = None,
) -> Any:
    hdrs = dict(headers or {})
    method = hdrs.pop("X-HTTP-Method-Override", None)
    req = urllib.request.Request(
        url, data=data, headers=hdrs, method=method or ("POST" if data else "GET")
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url.split('?', 1)[0]}: {body[:240]}") from exc
    if not raw:
        return {}
    return json.loads(raw)


def resolve_sheets_creds(
    *,
    env: Mapping[str, str] | None = None,
    token_path: Path | None = None,
) -> Optional[dict[str, str]]:
    merged: dict[str, str] = {}
    if env:
        merged.update({k: str(v) for k, v in env.items() if v})
    refresh = (
        merged.get("GSHEETS_REFRESH_TOKEN")
        or merged.get("AUTO_FLEET_GSHEETS_REFRESH_TOKEN")
        or merged.get("GCAL_REFRESH_TOKEN")
        or ""
    ).strip()
    client_id = (
        merged.get("GSHEETS_CLIENT_ID")
        or merged.get("GOOGLE_CLIENT_ID")
        or merged.get("AUTO_FLEET_GSHEETS_CLIENT_ID")
        or merged.get("GCAL_CLIENT_ID")
        or ""
    ).strip()
    client_secret = (
        merged.get("GSHEETS_CLIENT_SECRET")
        or merged.get("GOOGLE_CLIENT_SECRET")
        or merged.get("AUTO_FLEET_GSHEETS_CLIENT_SECRET")
        or merged.get("GCAL_CLIENT_SECRET")
        or ""
    ).strip()
    token_file = Path(token_path) if token_path is not None else DEFAULT_TOKEN_PATH
    if token_file.is_file():
        try:
            data = json.loads(token_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict):
            refresh = refresh or str(data.get("refresh_token") or "").strip()
            client_id = client_id or str(data.get("client_id") or "").strip()
            client_secret = client_secret or str(data.get("client_secret") or "").strip()
            installed = data.get("installed") or data.get("web")
            if isinstance(installed, dict):
                client_id = client_id or str(installed.get("client_id") or "").strip()
                client_secret = client_secret or str(
                    installed.get("client_secret") or ""
                ).strip()
    if refresh and client_id and client_secret:
        return {
            "refresh_token": refresh,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    return None


def refresh_access_token(creds: Mapping[str, str], *, http: HttpFn | None = None) -> str:
    fn = http or _http_json
    body = urllib.parse.urlencode(
        {
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    data = fn(
        TOKEN_URL,
        body,
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    token = str((data or {}).get("access_token") or "").strip()
    if not token:
        raise RuntimeError("token endpoint returned no access_token")
    return token


def is_rivian_change(change: Mapping[str, Any]) -> bool:
    uid = str(change.get("unit_id") or "").strip()
    if uid in RIVIAN_UNIT_IDS:
        return True
    vehicle = str(change.get("vehicle") or "").lower()
    return "rivian" in vehicle or "r1s" in vehicle


def is_totals_header(name: str) -> bool:
    return (name or "").strip().lower() in _TOTALS_MARKERS


def map_headers(headers: Sequence[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, raw in enumerate(headers):
        key = str(raw or "").strip().lower()
        if not key or is_totals_header(key):
            continue
        field = _HEADER_FIELDS.get(key)
        if field and field not in out:
            out[field] = i
    return out


def _cell(row: Sequence[Any], idx: Optional[int]) -> str:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return str(row[idx] or "").strip()


def find_row(
    rows: Sequence[Sequence[Any]],
    headers: Mapping[str, int],
    change: Mapping[str, Any],
) -> Optional[int]:
    """Return 0-based data-row index (not including header). None = append."""
    trip = str(change.get("trip_id") or "").strip()
    start = str(change.get("prior_start") or change.get("start") or "").strip()
    end = str(change.get("prior_end") or change.get("end") or "").strip()
    trip_idx = headers.get("trip_id")
    start_idx = headers.get("start")
    end_idx = headers.get("end")
    for i, row in enumerate(rows):
        if not isinstance(row, (list, tuple)):
            continue
        first = str(row[0] if row else "").strip().lower()
        if first in _TOTALS_MARKERS:
            continue
        if trip and trip_idx is not None and trip in _cell(row, trip_idx):
            return i
        if start and end and start_idx is not None and end_idx is not None:
            if start[:10] in _cell(row, start_idx) and end[:10] in _cell(row, end_idx):
                return i
    return None


def _write_value(field: str, change: Mapping[str, Any]) -> Optional[str]:
    if field == "payout":
        payout = change.get("payout")
        if payout is None or payout == "":
            return None
        return str(payout)
    val = change.get(field)
    if val is None or val == "":
        return None
    return str(val)


def build_row(
    headers_row: Sequence[Any],
    header_map: Mapping[str, int],
    change: Mapping[str, Any],
    existing: Sequence[Any] | None,
) -> list[Any]:
    width = max(len(headers_row), max(header_map.values(), default=-1) + 1)
    out = [""] * width
    if existing:
        for i, cell in enumerate(existing):
            if i < width:
                out[i] = cell
    field_at = {idx: field for field, idx in header_map.items()}
    for i in range(width):
        header_name = str(headers_row[i] if i < len(headers_row) else "")
        if is_totals_header(header_name):
            continue
        field = field_at.get(i)
        if not field:
            continue
        written = _write_value(field, change)
        if written is not None:
            out[i] = written
    return out


class HttpSheets:
    def __init__(
        self,
        *,
        http: HttpFn | None = None,
        access_token: str | None = None,
        creds: Mapping[str, str] | None = None,
        spreadsheet_id: str = RIVIAN_SHEET_ID,
        tab: str = TURO_BOOKINGS_TAB,
    ) -> None:
        self.http = http or _http_json
        self.access_token = access_token
        self.creds = dict(creds) if creds else None
        self.spreadsheet_id = spreadsheet_id
        self.tab = tab

    def _auth(self) -> dict[str, str]:
        token = self.access_token
        if not token and self.creds:
            token = refresh_access_token(self.creds, http=self.http)
            self.access_token = token
        if not token:
            raise RuntimeError("Google Sheets not configured")
        return {"Authorization": f"Bearer {token}"}

    def get_values(self, a1: str) -> list[list[Any]]:
        sid = urllib.parse.quote(self.spreadsheet_id)
        rng = urllib.parse.quote(a1)
        url = f"{SHEETS_API}/{sid}/values/{rng}"
        data = self.http(url, None, self._auth()) or {}
        values = data.get("values") if isinstance(data, dict) else None
        if not isinstance(values, list):
            return []
        return [v for v in values if isinstance(v, list)]

    def update_values(self, a1: str, rows: Sequence[Sequence[Any]]) -> dict[str, Any]:
        sid = urllib.parse.quote(self.spreadsheet_id)
        rng = urllib.parse.quote(a1)
        url = (
            f"{SHEETS_API}/{sid}/values/{rng}"
            f"?{urllib.parse.urlencode({'valueInputOption': 'USER_ENTERED'})}"
        )
        payload = json.dumps({"range": a1, "majorDimension": "ROWS", "values": list(rows)}).encode(
            "utf-8"
        )
        headers = {
            **self._auth(),
            "Content-Type": "application/json",
            "X-HTTP-Method-Override": "PUT",
        }
        data = self.http(url, payload, headers) or {}
        return {"ok": True, "result": data if isinstance(data, dict) else {}}


def col_letter(index: int) -> str:
    n = index + 1
    letters = []
    while n:
        n, rem = divmod(n - 1, 26)
        letters.append(chr(65 + rem))
    return "".join(reversed(letters))


def apply_rivian_sheet(
    change: Mapping[str, Any],
    sheets: Any,
) -> dict[str, Any]:
    if not is_rivian_change(change):
        return {"ok": True, "skipped": True, "reason": "not_rivian"}
    if sheets is None:
        return {"ok": False, "skipped": True, "error": "Google Sheets not configured"}
    tab = getattr(sheets, "tab", TURO_BOOKINGS_TAB)
    try:
        values = sheets.get_values(f"'{tab}'!A1:Z200")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc) or "sheets read failed"}
    if not values:
        return {"ok": False, "error": f"empty tab {tab}"}
    headers = values[0]
    header_map = map_headers(headers)
    if "trip_id" not in header_map and "start" not in header_map:
        return {"ok": False, "error": "Turo Bookings headers missing reservation/window"}
    data_rows = values[1:]
    match = find_row(data_rows, header_map, change)
    if match is None:
        existing = None
        row_number = len(values) + 1
        op = "append"
        if data_rows:
            last = data_rows[-1]
            first = str(last[0] if last else "").strip().lower()
            if first in _TOTALS_MARKERS:
                return {
                    "ok": False,
                    "error": "refusing to overwrite Totals row; Helm must insert above Totals",
                    "wrote_totals": False,
                }
    else:
        existing = data_rows[match]
        row_number = match + 2  # header row 1
        op = "update"
        first = str(existing[0] if existing else "").strip().lower()
        if first in _TOTALS_MARKERS:
            return {"ok": False, "error": "refusing to write Totals row", "wrote_totals": False}
    row = build_row(headers, header_map, change, existing)
    last_col = col_letter(max(len(headers) - 1, 0))
    a1 = f"'{tab}'!A{row_number}:{last_col}{row_number}"
    try:
        result = sheets.update_values(a1, [row])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc) or "sheets write failed"}
    return {
        "ok": bool(result.get("ok", True)),
        "op": op,
        "row": row_number,
        "spreadsheet_id": getattr(sheets, "spreadsheet_id", RIVIAN_SHEET_ID),
        "tab": tab,
        "wrote_totals": False,
    }


def load_sheets(
    *,
    env: Mapping[str, str] | None = None,
    http: HttpFn | None = None,
    token_path: Path | None = None,
) -> Any | None:
    environ = env if env is not None else os.environ
    creds = resolve_sheets_creds(env=environ, token_path=token_path)
    if creds is None:
        return None
    return HttpSheets(http=http, creds=creds)
