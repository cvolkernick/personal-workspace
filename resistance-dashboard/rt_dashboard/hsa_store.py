"""SOUND HSA ledger for FitDash — manual/CSV v1 (no SOUND API).

Durable store:
- Turso ``hsa_accounts`` when ``TURSO_*`` is set (Vercel prod).
- ``~/.config/resistance-dashboard/hsa/<user>.json`` on Pi.
  Override with ``FITDASH_HSA_DIR``.

IRS contribution limits are looked up by calendar year. 2026 defaults
ship here (Rev. Proc. 2025-19) and are overridable per account — never
use a single hardcoded cap in pace math.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SATS_PER_BTC = 100_000_000

# IRS Rev. Proc. 2025-19 (calendar year 2026). Catch-up is statutory $1,000.
DEFAULT_IRS_LIMITS: Dict[str, Dict[str, Any]] = {
    "2026": {
        "self_only": 4400,
        "family": 8750,
        "catch_up": 1000,
        "hdhp_min_deductible_self": 1700,
        "hdhp_min_deductible_family": 3400,
        "hdhp_oop_max_self": 8500,
        "hdhp_oop_max_family": 17000,
        "source": "IRS Rev. Proc. 2025-19",
    }
}

# Public SOUND HSA fee schedule as of 2026-09 (soundhsa.com/support).
DEFAULT_FEES: Dict[str, Any] = {
    "provider": "SOUND HSA",
    "annual_admin_usd": 300,
    "activation_usd": 50,
    "trade_pct": 1.0,
    "btc_pay_discount_pct": 10.0,
    "termination_usd": 250,
    "source": "soundhsa.com/support 2026-09",
    "note": "Fees do not reduce the IRS annual contribution limit. 10% off if paid in BTC.",
}

ELIGIBILITY = ("unknown", "eligible", "ineligible")
COVERAGE = ("self_only", "family")
RECEIPT_STATUS = ("missing", "pending", "on_file")
ENTRY_KINDS = ("contribution", "shoebox", "spend", "reward", "challenge")
SPEND_CATEGORIES = (
    "doctor",
    "prescription",
    "dental",
    "vision",
    "therapy",
    "lab",
    "other",
)

CSV_COLUMNS = (
    "kind",
    "date",
    "amount_usd",
    "sats",
    "category",
    "merchant",
    "receipt",
    "counts_toward_deductible",
    "steps",
    "challenge",
    "notes",
)

ENSURE_HSA_SQL = """
CREATE TABLE IF NOT EXISTS hsa_accounts (
  user_id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hsa_uid(user_id: str) -> str:
    return (user_id or "").strip() or "local"


def _ephemeral_host() -> bool:
    return bool(
        (os.environ.get("VERCEL") or "").strip()
        or (os.environ.get("VERCEL_ENV") or "").strip()
    )


def hsa_root() -> Path:
    override = os.environ.get("FITDASH_HSA_DIR")
    if override:
        return Path(override).expanduser()
    cfg = os.environ.get("RESISTANCE_DASHBOARD_CONFIG_DIR")
    if cfg:
        return Path(cfg).expanduser() / "hsa"
    return Path.home() / ".config" / "resistance-dashboard" / "hsa"


def _user_path(user_id: str) -> Path:
    uid = re.sub(r"[^a-zA-Z0-9._-]+", "_", _hsa_uid(user_id))
    return hsa_root() / f"{uid}.json"


def empty_store() -> dict:
    return {
        "version": 1,
        "provider": "SOUND HSA",
        "eligibility": "unknown",
        "coverage": "self_only",
        "catch_up_55": False,
        "hdhp_deductible_usd": None,
        "position": {
            "btc_sats": 0,
            "cash_usd": 0.0,
            "btc_usd_mark": None,
            "as_of": "",
        },
        "contributions": [],
        "shoebox": [],
        "medical_spend": [],
        "sats_rewards": [],
        "challenges": [],
        "irs_limits": deepcopy(DEFAULT_IRS_LIMITS),
        "fees": deepcopy(DEFAULT_FEES),
        "api": {
            "available": False,
            "note": (
                "SOUND HSA has no public account API as of 2026-09. "
                "v1 is manual entry + CSV import."
            ),
        },
        "updated_at": "",
        "storage": "empty",
    }


def _as_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    n = _as_float(value, None)
    if n is None:
        return default
    return int(round(n))


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _clean_date(value: Any) -> str:
    s = str(value or "").strip()[:10]
    if not _DATE_RE.match(s):
        raise ValueError(f"date must be YYYY-MM-DD, got {value!r}")
    return s


def _clean_id(value: Any) -> str:
    s = str(value or "").strip()
    return s or uuid.uuid4().hex[:12]


def _money_usd(value: Any) -> float:
    n = _as_float(value, 0.0) or 0.0
    return round(n, 2)


def _receipt(value: Any) -> str:
    s = str(value or "missing").strip().lower().replace(" ", "_")
    aliases = {
        "filed": "on_file",
        "have": "on_file",
        "vaulted": "on_file",
        "yes": "on_file",
        "wait": "pending",
        "no": "missing",
        "": "missing",
    }
    s = aliases.get(s, s)
    if s not in RECEIPT_STATUS:
        raise ValueError(f"receipt must be one of {RECEIPT_STATUS}, got {value!r}")
    return s


def _eligibility(value: Any) -> str:
    s = str(value or "unknown").strip().lower()
    if s in ("hdhp", "yes", "true", "1"):
        s = "eligible"
    if s in ("no", "false", "0", "none"):
        s = "ineligible"
    if s not in ELIGIBILITY:
        raise ValueError(f"eligibility must be one of {ELIGIBILITY}")
    return s


def _coverage(value: Any) -> str:
    s = str(value or "self_only").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "self": "self_only",
        "individual": "self_only",
        "single": "self_only",
        "family_coverage": "family",
    }
    s = aliases.get(s, s)
    if s not in COVERAGE:
        raise ValueError(f"coverage must be one of {COVERAGE}")
    return s


def _kind(value: Any) -> str:
    s = str(value or "").strip().lower()
    aliases = {
        "contrib": "contribution",
        "deposit": "contribution",
        "receipt": "shoebox",
        "qme": "shoebox",
        "medical": "spend",
        "visit": "spend",
        "sats": "reward",
        "steps_reward": "reward",
        "move_to_earn": "reward",
    }
    s = aliases.get(s, s)
    if s not in ENTRY_KINDS:
        raise ValueError(f"kind must be one of {ENTRY_KINDS}, got {value!r}")
    return s


def _normalize_position(raw: Any) -> dict:
    src = raw if isinstance(raw, dict) else {}
    btc_sats = _as_int(src.get("btc_sats"), 0)
    btc = _as_float(src.get("btc"), None)
    if btc is not None and btc_sats == 0 and btc > 0:
        btc_sats = int(round(btc * SATS_PER_BTC))
    mark = _as_float(src.get("btc_usd_mark"), None)
    if mark is not None and mark <= 0:
        mark = None
    return {
        "btc_sats": max(0, btc_sats),
        "cash_usd": max(0.0, _money_usd(src.get("cash_usd"))),
        "btc_usd_mark": mark,
        "as_of": str(src.get("as_of") or "")[:10],
    }


def _normalize_contribution(raw: dict) -> dict:
    return {
        "id": _clean_id(raw.get("id")),
        "date": _clean_date(raw.get("date")),
        "amount_usd": _money_usd(raw.get("amount_usd")),
        "source": str(raw.get("source") or "manual").strip() or "manual",
        "notes": str(raw.get("notes") or "").strip(),
    }


def _normalize_shoebox(raw: dict) -> dict:
    cat = str(raw.get("category") or "other").strip().lower() or "other"
    return {
        "id": _clean_id(raw.get("id")),
        "date": _clean_date(raw.get("date")),
        "amount_usd": _money_usd(raw.get("amount_usd")),
        "category": cat,
        "merchant": str(raw.get("merchant") or "").strip(),
        "receipt": _receipt(raw.get("receipt")),
        "notes": str(raw.get("notes") or "").strip(),
    }


def _normalize_spend(raw: dict) -> dict:
    cat = str(raw.get("category") or "other").strip().lower() or "other"
    if cat not in SPEND_CATEGORIES:
        cat = "other"
    return {
        "id": _clean_id(raw.get("id")),
        "date": _clean_date(raw.get("date")),
        "amount_usd": _money_usd(raw.get("amount_usd")),
        "category": cat,
        "merchant": str(raw.get("merchant") or "").strip(),
        "counts_toward_deductible": _as_bool(
            raw.get("counts_toward_deductible"), True
        ),
        "notes": str(raw.get("notes") or "").strip(),
    }


def _normalize_reward(raw: dict) -> dict:
    return {
        "id": _clean_id(raw.get("id")),
        "date": _clean_date(raw.get("date")),
        "sats": max(0, _as_int(raw.get("sats"), 0)),
        "steps": max(0, _as_int(raw.get("steps"), 0)),
        "challenge": str(raw.get("challenge") or "").strip(),
        "source": str(raw.get("source") or "manual").strip() or "manual",
        "notes": str(raw.get("notes") or "").strip(),
    }


def _normalize_challenge(raw: dict) -> dict:
    start = str(raw.get("start") or raw.get("date") or "").strip()[:10]
    end = str(raw.get("end") or "").strip()[:10]
    if start and not _DATE_RE.match(start):
        raise ValueError(f"challenge start must be YYYY-MM-DD, got {start!r}")
    if end and not _DATE_RE.match(end):
        raise ValueError(f"challenge end must be YYYY-MM-DD, got {end!r}")
    return {
        "id": _clean_id(raw.get("id")),
        "label": str(raw.get("label") or raw.get("challenge") or "Move to Earn").strip(),
        "goal_steps": max(0, _as_int(raw.get("goal_steps") or raw.get("steps"), 0)),
        "reward_sats": max(0, _as_int(raw.get("reward_sats") or raw.get("sats"), 0)),
        "start": start,
        "end": end,
        "notes": str(raw.get("notes") or "").strip(),
    }


def _normalize_irs_limits(raw: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = deepcopy(DEFAULT_IRS_LIMITS)
    if not isinstance(raw, dict):
        return out
    for year, blob in raw.items():
        y = str(year).strip()
        if not y.isdigit() or len(y) != 4:
            continue
        src = blob if isinstance(blob, dict) else {}
        seed = deepcopy(DEFAULT_IRS_LIMITS.get(y) or {})
        row = {
            "self_only": _as_int(
                src.get("self_only"), int(seed.get("self_only") or 0)
            ),
            "family": _as_int(src.get("family"), int(seed.get("family") or 0)),
            "catch_up": _as_int(
                src.get("catch_up"), int(seed.get("catch_up") or 1000)
            ),
            "hdhp_min_deductible_self": _as_int(
                src.get("hdhp_min_deductible_self"),
                int(seed.get("hdhp_min_deductible_self") or 0),
            ),
            "hdhp_min_deductible_family": _as_int(
                src.get("hdhp_min_deductible_family"),
                int(seed.get("hdhp_min_deductible_family") or 0),
            ),
            "hdhp_oop_max_self": _as_int(
                src.get("hdhp_oop_max_self"),
                int(seed.get("hdhp_oop_max_self") or 0),
            ),
            "hdhp_oop_max_family": _as_int(
                src.get("hdhp_oop_max_family"),
                int(seed.get("hdhp_oop_max_family") or 0),
            ),
            "source": str(src.get("source") or seed.get("source") or "user").strip(),
        }
        if row["self_only"] <= 0 or row["family"] <= 0:
            continue
        out[y] = row
    return out


def _list_of(raw: Any, normalizer) -> List[dict]:
    if not isinstance(raw, list):
        return []
    out: List[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.append(normalizer(item))
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda r: (str(r.get("date") or r.get("start") or ""), str(r.get("id") or "")))
    return out


def normalize_store(raw: Any) -> dict:
    src = raw if isinstance(raw, dict) else {}
    out = empty_store()
    out["provider"] = str(src.get("provider") or "SOUND HSA").strip() or "SOUND HSA"
    out["eligibility"] = _eligibility(src.get("eligibility") or "unknown")
    out["coverage"] = _coverage(src.get("coverage") or "self_only")
    out["catch_up_55"] = _as_bool(src.get("catch_up_55"), False)
    deductible = _as_float(src.get("hdhp_deductible_usd"), None)
    out["hdhp_deductible_usd"] = (
        round(deductible, 2) if deductible is not None and deductible > 0 else None
    )
    out["position"] = _normalize_position(src.get("position"))
    out["contributions"] = _list_of(src.get("contributions"), _normalize_contribution)
    out["shoebox"] = _list_of(src.get("shoebox"), _normalize_shoebox)
    out["medical_spend"] = _list_of(src.get("medical_spend"), _normalize_spend)
    out["sats_rewards"] = _list_of(src.get("sats_rewards"), _normalize_reward)
    out["challenges"] = _list_of(src.get("challenges"), _normalize_challenge)
    out["irs_limits"] = _normalize_irs_limits(src.get("irs_limits"))
    fees = src.get("fees") if isinstance(src.get("fees"), dict) else {}
    merged_fees = deepcopy(DEFAULT_FEES)
    merged_fees.update({k: fees[k] for k in fees if k in DEFAULT_FEES or k == "note"})
    out["fees"] = merged_fees
    api = src.get("api") if isinstance(src.get("api"), dict) else {}
    out["api"] = {
        "available": False,
        "note": str(api.get("note") or empty_store()["api"]["note"]),
    }
    out["updated_at"] = str(src.get("updated_at") or "")
    out["storage"] = str(src.get("storage") or "empty")
    out["version"] = 1
    return out


def irs_limits_for(store: dict, year: str) -> Optional[dict]:
    """Year-keyed lookup. Never falls back to another year's numbers."""
    y = str(year or "").strip()
    blob = (store or {}).get("irs_limits") if isinstance(store, dict) else None
    if isinstance(blob, dict) and isinstance(blob.get(y), dict):
        row = blob[y]
        if int(row.get("self_only") or 0) > 0 and int(row.get("family") or 0) > 0:
            return dict(row)
    seed = DEFAULT_IRS_LIMITS.get(y)
    if seed:
        return dict(seed)
    return None


def contribution_limit_usd(store: dict, year: str) -> Optional[int]:
    limits = irs_limits_for(store, year)
    if not limits:
        return None
    coverage = _coverage((store or {}).get("coverage") or "self_only")
    base = int(limits["family"] if coverage == "family" else limits["self_only"])
    if _as_bool((store or {}).get("catch_up_55"), False):
        base += int(limits.get("catch_up") or 0)
    return base


def parse_hsa_csv(text: str) -> Dict[str, List[dict]]:
    """Parse a SOUND/manual CSV into typed ledgers.

    Required header includes ``kind`` and ``date``. Amounts are USD except
    ``sats`` (integer) for rewards / position BTC.
    """
    raw = (text or "").lstrip("\ufeff")
    if not raw.strip():
        raise ValueError("CSV is empty")
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames:
        raise ValueError("CSV is missing a header row")
    fields = [str(h or "").strip().lower() for h in reader.fieldnames]
    if "kind" not in fields or "date" not in fields:
        raise ValueError("CSV needs kind and date columns")
    buckets: Dict[str, List[dict]] = {
        "contributions": [],
        "shoebox": [],
        "medical_spend": [],
        "sats_rewards": [],
        "challenges": [],
        "position": [],
    }
    for i, row in enumerate(reader, start=2):
        if not isinstance(row, dict):
            continue
        clean = {
            str(k or "").strip().lower(): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
        }
        kind_raw = str(clean.get("kind") or "").strip()
        if not kind_raw:
            continue
        try:
            kind = kind_raw.lower()
            if kind == "position":
                buckets["position"].append(
                    {
                        "btc_sats": _as_int(clean.get("sats"), 0),
                        "cash_usd": _money_usd(clean.get("amount_usd")),
                        "as_of": _clean_date(clean.get("date")),
                    }
                )
                continue
            kind = _kind(kind_raw)
            if kind == "contribution":
                buckets["contributions"].append(_normalize_contribution(clean))
            elif kind == "shoebox":
                buckets["shoebox"].append(_normalize_shoebox(clean))
            elif kind == "spend":
                buckets["medical_spend"].append(_normalize_spend(clean))
            elif kind == "reward":
                buckets["sats_rewards"].append(_normalize_reward(clean))
            elif kind == "challenge":
                buckets["challenges"].append(_normalize_challenge(clean))
        except ValueError as exc:
            raise ValueError(f"CSV row {i}: {exc}") from exc
    return buckets


def apply_csv(store: dict, text: str) -> Tuple[dict, dict]:
    buckets = parse_hsa_csv(text)
    out = normalize_store(store)
    counts = {k: len(v) for k, v in buckets.items() if k != "position"}
    for key in ("contributions", "shoebox", "medical_spend", "sats_rewards", "challenges"):
        existing = {str(r.get("id")): r for r in out[key]}
        for row in buckets[key]:
            existing[str(row["id"])] = row
        out[key] = sorted(
            existing.values(),
            key=lambda r: (str(r.get("date") or r.get("start") or ""), str(r.get("id"))),
        )
    if buckets["position"]:
        pos = buckets["position"][-1]
        merged = dict(out["position"])
        merged.update(pos)
        out["position"] = _normalize_position(merged)
        counts["position"] = 1
    else:
        counts["position"] = 0
    return out, counts


def upsert_entry(store: dict, kind: str, payload: dict) -> dict:
    k = _kind(kind)
    out = normalize_store(store)
    key = {
        "contribution": "contributions",
        "shoebox": "shoebox",
        "spend": "medical_spend",
        "reward": "sats_rewards",
        "challenge": "challenges",
    }[k]
    normalizer = {
        "contribution": _normalize_contribution,
        "shoebox": _normalize_shoebox,
        "spend": _normalize_spend,
        "reward": _normalize_reward,
        "challenge": _normalize_challenge,
    }[k]
    row = normalizer(payload if isinstance(payload, dict) else {})
    rows = [r for r in out[key] if str(r.get("id")) != str(row["id"])]
    rows.append(row)
    rows.sort(key=lambda r: (str(r.get("date") or r.get("start") or ""), str(r.get("id"))))
    out[key] = rows
    return out


def delete_entry(store: dict, kind: str, entry_id: str) -> dict:
    k = _kind(kind)
    out = normalize_store(store)
    key = {
        "contribution": "contributions",
        "shoebox": "shoebox",
        "spend": "medical_spend",
        "reward": "sats_rewards",
        "challenge": "challenges",
    }[k]
    eid = str(entry_id or "").strip()
    if not eid:
        raise ValueError("id required")
    before = len(out[key])
    out[key] = [r for r in out[key] if str(r.get("id")) != eid]
    if len(out[key]) == before:
        raise ValueError("entry not found")
    return out


def apply_settings(store: dict, payload: dict) -> dict:
    out = normalize_store(store)
    src = payload if isinstance(payload, dict) else {}
    if "eligibility" in src:
        out["eligibility"] = _eligibility(src.get("eligibility"))
    if "coverage" in src:
        out["coverage"] = _coverage(src.get("coverage"))
    if "catch_up_55" in src:
        out["catch_up_55"] = _as_bool(src.get("catch_up_55"), False)
    if "hdhp_deductible_usd" in src:
        d = _as_float(src.get("hdhp_deductible_usd"), None)
        out["hdhp_deductible_usd"] = (
            round(d, 2) if d is not None and d > 0 else None
        )
    if "provider" in src and str(src.get("provider") or "").strip():
        out["provider"] = str(src.get("provider")).strip()
    if isinstance(src.get("position"), dict):
        merged = dict(out["position"])
        merged.update(src["position"])
        out["position"] = _normalize_position(merged)
    else:
        pos_keys = ("btc_sats", "cash_usd", "btc_usd_mark", "btc")
        if any(k in src for k in pos_keys):
            merged = dict(out["position"])
            for k in pos_keys:
                if k in src:
                    merged[k] = src[k]
            if src.get("position_as_of"):
                merged["as_of"] = src.get("position_as_of")
            out["position"] = _normalize_position(merged)
    if isinstance(src.get("irs_limits"), dict):
        out["irs_limits"] = _normalize_irs_limits(
            {**out["irs_limits"], **src["irs_limits"]}
        )
    if isinstance(src.get("fees"), dict):
        fees = dict(out["fees"])
        fees.update(src["fees"])
        out["fees"] = fees
    return out


def _open_blob(user_id: str, raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    blob = str(raw or "").strip()
    if not blob:
        raise ValueError("empty hsa payload")
    if blob.startswith("{"):
        data = json.loads(blob)
        if not isinstance(data, dict):
            raise ValueError("hsa payload is not an object")
        return data
    from .crypto_box import open_str

    plain = open_str(blob, aad=f"user:{user_id}:hsa")
    data = json.loads(plain)
    if not isinstance(data, dict):
        raise ValueError("hsa payload is not an object")
    return data


def _turso_get(user_id: str) -> Optional[dict]:
    from .turso_http import connect, turso_enabled

    if not turso_enabled():
        return None
    uid = _hsa_uid(user_id)
    with connect() as conn:
        conn.execute(ENSURE_HSA_SQL)
        row = conn.execute(
            "SELECT payload FROM hsa_accounts WHERE user_id = ?",
            (uid,),
        ).fetchone()
    if not row:
        return None
    payload = row["payload"] if isinstance(row, dict) else row[0]
    if not payload:
        return None
    data = _open_blob(uid, payload)
    store = normalize_store(data)
    store["storage"] = "turso"
    return store


def _turso_put(user_id: str, store: dict) -> None:
    from .crypto_box import seal_str
    from .turso_http import connect, turso_enabled

    if not turso_enabled():
        raise RuntimeError("turso env missing")
    uid = _hsa_uid(user_id)
    payload = normalize_store(store)
    now = payload["updated_at"] or _utc_now()
    payload["updated_at"] = now
    payload["storage"] = "turso"
    blob = seal_str(
        json.dumps(payload, separators=(",", ":")),
        aad=f"user:{uid}:hsa",
    )
    with connect() as conn:
        conn.execute(ENSURE_HSA_SQL)
        conn.execute(
            """
            INSERT INTO hsa_accounts(user_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              payload = excluded.payload,
              updated_at = excluded.updated_at
            """,
            (uid, blob, now),
        )


def _load_disk(user_id: str) -> dict:
    path = _user_path(user_id)
    if not path.is_file():
        return empty_store()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_store()
    store = normalize_store(raw)
    store["storage"] = "config"
    return store


def _save_disk(user_id: str, store: dict) -> None:
    path = _user_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = normalize_store(store)
    payload["updated_at"] = _utc_now()
    payload["storage"] = "config"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_hsa(user_id: str = "") -> dict:
    from .turso_http import turso_enabled

    if turso_enabled():
        existing = _turso_get(user_id)
        if existing is not None:
            return existing
        if _ephemeral_host():
            return empty_store()
        return _load_disk(user_id)
    if _ephemeral_host():
        raise RuntimeError("turso env missing")
    return _load_disk(user_id)


def save_hsa(store: dict, user_id: str = "") -> dict:
    from .turso_http import turso_enabled

    payload = normalize_store(store)
    payload["updated_at"] = _utc_now()
    if turso_enabled():
        payload["storage"] = "turso"
        _turso_put(user_id, payload)
        existing = _turso_get(user_id)
        if existing is None:
            raise RuntimeError("turso write not visible on readback")
        return existing
    if _ephemeral_host():
        raise RuntimeError("turso env missing")
    payload["storage"] = "config"
    _save_disk(user_id, payload)
    return load_hsa(user_id)


def csv_template() -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(CSV_COLUMNS))
    writer.writeheader()
    writer.writerow(
        {
            "kind": "contribution",
            "date": "2026-01-15",
            "amount_usd": "400",
            "sats": "",
            "category": "",
            "merchant": "",
            "receipt": "",
            "counts_toward_deductible": "",
            "steps": "",
            "challenge": "",
            "notes": "payroll",
        }
    )
    writer.writerow(
        {
            "kind": "shoebox",
            "date": "2026-03-01",
            "amount_usd": "85.00",
            "sats": "",
            "category": "dental",
            "merchant": "Smile Co",
            "receipt": "on_file",
            "counts_toward_deductible": "",
            "steps": "",
            "challenge": "",
            "notes": "cleaning paid OOP",
        }
    )
    writer.writerow(
        {
            "kind": "spend",
            "date": "2026-03-01",
            "amount_usd": "85.00",
            "sats": "",
            "category": "dental",
            "merchant": "Smile Co",
            "receipt": "",
            "counts_toward_deductible": "true",
            "steps": "",
            "challenge": "",
            "notes": "",
        }
    )
    writer.writerow(
        {
            "kind": "reward",
            "date": "2026-04-09",
            "amount_usd": "",
            "sats": "7000",
            "category": "",
            "merchant": "",
            "receipt": "",
            "counts_toward_deductible": "",
            "steps": "50000",
            "challenge": "Spring Move to Earn",
            "notes": "",
        }
    )
    return buf.getvalue()
