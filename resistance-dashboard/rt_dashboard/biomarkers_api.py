"""Shared request handlers for /api/biomarkers* (Pi server.py + Vercel).

POST /api/biomarkers/import  body:
  {"kind": "json"|"csv"|"pdf",
   "data": <obj or JSON text>            (json)
   "csv": "<text>", "draw_date", "lab", "panel_id"   (csv)
   "content_base64": "...", "filename"   (pdf)
   "context": {...per-draw context...}   (optional, merged)
   "commit": false}                      → dry-run preview (default)
  Send the same body again with "commit": true to write. Duplicates
  (marker+draw_date+panel) are skipped, never overwritten.
POST /api/biomarkers/context {"panel_id", "context": {...}}
POST /api/biomarkers/delete  {"panel_id"}
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict, Tuple

from . import biomarkers_config as cfg
from .biomarkers_import import ImportError_, commit_preview, preview_csv, preview_json, preview_pdf

_EDITABLE = tuple(f["key"] for f in cfg.CONTEXT_FIELDS if f["type"] != "auto")


def clean_context(ctx: Any) -> Dict[str, Any]:
    if not isinstance(ctx, dict):
        return {}
    out: Dict[str, Any] = {}
    for k in _EDITABLE:
        v = ctx.get(k)
        if v is None or v == "":
            continue
        out[k] = v if isinstance(v, (bool, int, float)) else str(v)[:40]
    return out


def handle_import(body: dict, *, user_id: str = "") -> Tuple[int, dict]:
    from .biomarkers_store import existing_keys
    from .labs_parse import LabParseError

    body = body if isinstance(body, dict) else {}
    kind = str(body.get("kind") or "json").lower()
    try:
        existing = existing_keys(user_id)
        if kind == "pdf":
            b64 = str(body.get("content_base64") or "").strip()
            if not b64:
                raise ImportError_("Missing content_base64.")
            try:
                raw = base64.b64decode(b64)
            except Exception as e:  # noqa: BLE001
                raise ImportError_(f"Invalid base64: {e}") from e
            pv = preview_pdf(raw, filename=str(body.get("filename") or "lab.pdf"), existing=existing)
        elif kind == "csv":
            pv = preview_csv(str(body.get("csv") or ""), draw_date=str(body.get("draw_date") or ""),
                             lab=str(body.get("lab") or "manual"), panel_id=str(body.get("panel_id") or ""),
                             existing=existing)
        elif kind == "json":
            data = body.get("data")
            if isinstance(data, str):
                data = json.loads(data)
            pv = preview_json(data, existing=existing)
        else:
            raise ImportError_(f"unknown kind {kind!r}")
    except (ImportError_, LabParseError, ValueError) as e:
        return 400, {"ok": False, "error": str(e)}
    pv["panel"]["context"] = {**pv["panel"].get("context", {}), **clean_context(body.get("context"))}
    if not body.get("commit"):
        return 200, {"ok": True, "preview": pv}
    res = commit_preview(pv, user_id=user_id)
    return 200, {"ok": True, "result": res, "preview": pv}


def handle_context(body: dict, *, user_id: str = "") -> Tuple[int, dict]:
    from .biomarkers_store import update_context

    pid = str((body or {}).get("panel_id") or "")
    if not pid:
        return 400, {"ok": False, "error": "panel_id required"}
    ctx = clean_context((body or {}).get("context"))
    update_context(pid, ctx, user_id=user_id)
    return 200, {"ok": True, "panel_id": pid, "context": ctx}


def handle_delete(body: dict, *, user_id: str = "") -> Tuple[int, dict]:
    from .biomarkers_store import delete_panel

    pid = str((body or {}).get("panel_id") or "")
    if not pid:
        return 400, {"ok": False, "error": "panel_id required"}
    return 200, {"ok": True, "deleted_rows": delete_panel(pid, user_id=user_id)}


def handle_post(route: str, body: dict, *, user_id: str = "") -> Tuple[int, dict]:
    if route == "biomarkers_import":
        return handle_import(body, user_id=user_id)
    if route == "biomarkers_context":
        return handle_context(body, user_id=user_id)
    if route == "biomarkers_delete":
        return handle_delete(body, user_id=user_id)
    return 404, {"ok": False, "error": "unknown_biomarkers_route"}


def route_for_path(path: str) -> str:
    p = (path or "").rstrip("/")
    return {
        "/api/biomarkers": "biomarkers",
        "/api/biomarkers/import": "biomarkers_import",
        "/api/biomarkers/context": "biomarkers_context",
        "/api/biomarkers/delete": "biomarkers_delete",
    }.get(p, "")
