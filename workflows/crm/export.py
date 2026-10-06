"""JSON export in the roadside FileStore shape. Read-only for old readers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from workflows.crm.schema import harden, utc_now


def _loads(value: object, fallback: Any) -> Any:
    if not isinstance(value, str) or not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def build_doc(conn: Any) -> dict[str, Any]:
    leads: dict[str, Any] = {}
    for row in conn.execute("SELECT id, source, ext_json FROM party WHERE type = 'lead'"):
        if row["source"] != "roadside":
            continue
        ext = _loads(row["ext_json"], {})
        snap = ext.get("roadside_lead") if isinstance(ext, dict) else None
        if isinstance(snap, dict) and snap.get("id"):
            leads[str(snap["id"])] = snap
    suppression = [
        {"key": row["key_norm"], "reason": row["reason"], "at": row["at"]}
        for row in conn.execute("SELECT key_norm, reason, at FROM suppression ORDER BY at, key_norm")
    ]
    outbox = []
    for row in conn.execute("SELECT payload_json FROM outbox ORDER BY id"):
        payload = _loads(row["payload_json"], None)
        if isinstance(payload, dict):
            outbox.append(payload)
    alerts = []
    for row in conn.execute("SELECT payload_json FROM alert ORDER BY id"):
        payload = _loads(row["payload_json"], None)
        if isinstance(payload, dict):
            alerts.append(payload)
    sms_by_date = {row["day"]: int(row["n"]) for row in conn.execute("SELECT day, n FROM sms_day")}
    processed: dict[str, Any] = {}
    for row in conn.execute("SELECT folder_id, lead_id, reason, at FROM intake_folder"):
        processed[row["folder_id"]] = {
            "lead_id": row["lead_id"],
            "reason": row["reason"],
            "at": row["at"],
        }
    return {
        "version": 1,
        "canonical": "file",
        "leads": leads,
        "suppression": suppression,
        "outbox": outbox,
        "alerts": alerts,
        "sms_by_date": sms_by_date,
        "processed_folders": processed,
        "updated_at": utc_now(),
    }


def write_export(conn: Any, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    payload = json.dumps(build_doc(conn), indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".store.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)
    harden(path)
