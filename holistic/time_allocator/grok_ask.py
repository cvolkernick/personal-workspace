"""Ask Grok about time-allocator data (xAI API or SuperGrok session)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

XAI_API_BASE = os.environ.get("XAI_API_BASE", "https://api.x.ai/v1").rstrip("/")
DEFAULT_MODEL = os.environ.get("XAI_MODEL", "grok-4-1-fast-non-reasoning")
AUTH_PATH = Path.home() / ".grok" / "auth.json"
MAX_CONTEXT_CHARS = int(os.environ.get("GROK_ASK_MAX_CONTEXT_CHARS", "24000"))
REQUEST_TIMEOUT = int(os.environ.get("GROK_ASK_TIMEOUT_SEC", "90"))

SYSTEM_PROMPT = """You are a personal time-allocation coach for the user's Time Allocator dashboard.

You only answer using the TIME DATA JSON provided in the user message (plus general
productivity/scheduling knowledge that does not invent facts about THIS user).

Rules:
- Ground answers in the provided data: rolling 24h recommended vs actual allocations,
  delta gaps, next actions, sleep battery, Lyft duty cycle (12h drive / 6h break),
  targets/KPIs, logs, walk candidates, and ad-hoc items.
- If something is missing from the data, say so clearly. Do not invent times or logs.
- Prefer concise, practical advice. Use short bullet lists when helpful.
- Cite specific numbers (minutes, hours, %) from the data when discussing progress.
- Respect Lyft rules in the data: 12h driver mode then mandatory uninterrupted 6h offline.
- Do not discuss secrets, tokens, or how to access private systems.
"""


class GrokAskError(Exception):
    def __init__(self, message: str, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def _parse_expires_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_grok_auth_entry() -> dict | None:
    if not AUTH_PATH.is_file():
        return None
    try:
        data = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data:
        return None
    best = None
    best_exp = None
    now = datetime.now(timezone.utc)
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        token = entry.get("key") or entry.get("access_token")
        if not token:
            continue
        exp = _parse_expires_at(entry.get("expires_at"))
        if exp and exp <= now:
            if best is None:
                best = entry
            continue
        if best is None or (exp and (best_exp is None or exp > best_exp)):
            best = entry
            best_exp = exp
    return best


def resolve_xai_credentials() -> dict[str, Any]:
    api_key = (os.environ.get("XAI_API_KEY") or "").strip()
    if api_key:
        return {
            "token": api_key,
            "source": "xai_api_key",
            "email": None,
            "expires_at": None,
            "expired": False,
        }
    entry = _read_grok_auth_entry()
    if not entry:
        return {
            "token": None,
            "source": "none",
            "email": None,
            "expires_at": None,
            "expired": False,
            "error": (
                "No SuperGrok session found. Sign in with `grok login`, "
                "or set XAI_API_KEY from console.x.ai."
            ),
        }
    token = (entry.get("key") or entry.get("access_token") or "").strip()
    exp = _parse_expires_at(entry.get("expires_at"))
    now = datetime.now(timezone.utc)
    expired = bool(exp and exp <= now)
    return {
        "token": token if token else None,
        "source": "supergrok_session",
        "email": entry.get("email"),
        "expires_at": entry.get("expires_at"),
        "expired": expired,
        "error": (
            "SuperGrok session expired. Run `grok login`, then try again."
            if expired
            else (None if token else "Empty session token in ~/.grok/auth.json")
        ),
    }


def auth_status() -> dict[str, Any]:
    creds = resolve_xai_credentials()
    return {
        "ok": bool(creds.get("token")) and not creds.get("expired"),
        "source": creds.get("source"),
        "email": creds.get("email"),
        "expires_at": creds.get("expires_at"),
        "expired": bool(creds.get("expired")),
        "model": DEFAULT_MODEL,
        "error": creds.get("error"),
    }


def build_time_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Compact snapshot of time-allocator state for the model."""
    plan = payload.get("plan") or {}
    rec = payload.get("plan_recommended") or {}
    actual = payload.get("actual") or {}
    sleep = payload.get("sleep_battery") or {}
    lyft = payload.get("lyft_duty") or {}

    def slim_blocks(blocks: Any, n: int = 12) -> list:
        out = []
        for b in (blocks or [])[:n]:
            if not isinstance(b, dict):
                continue
            out.append(
                {
                    "id": b.get("id"),
                    "title": b.get("title"),
                    "minutes": b.get("minutes"),
                    "role": b.get("role"),
                    "done_today": b.get("done_today"),
                }
            )
        return out

    logs = payload.get("logs") or []
    if isinstance(logs, list):
        logs = logs[-40:]

    return {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "path": payload.get("path"),
        "sleep_battery": {
            "asleep_hours": sleep.get("asleep_hours"),
            "target_hours": sleep.get("target_hours"),
            "pct_of_target": sleep.get("pct_of_target"),
            "level": sleep.get("level"),
            "summary": sleep.get("summary"),
            "discharge_next_hour_hours": sleep.get("discharge_next_hour_hours"),
            "data_source": sleep.get("data_source"),
        },
        "lyft_duty": {
            "driven_minutes": lyft.get("driven_minutes"),
            "drive_cap_minutes": lyft.get("drive_cap_minutes"),
            "break_minutes": lyft.get("break_minutes"),
            "remaining_drive_minutes": lyft.get("remaining_drive_minutes"),
            "at_limit": lyft.get("at_limit"),
            "stale": lyft.get("stale"),
            "break_remaining_minutes": lyft.get("break_remaining_minutes"),
            "break_complete": lyft.get("break_complete"),
            "can_drive_again": lyft.get("can_drive_again"),
            "summary": lyft.get("summary"),
            "policy": lyft.get("policy"),
        },
        "plan_remaining": {
            "window_start": plan.get("window_start"),
            "window_end": plan.get("window_end"),
            "blocks": slim_blocks(plan.get("blocks")),
            "notes": (plan.get("notes") or [])[:12],
        },
        "plan_recommended": {
            "blocks": slim_blocks(rec.get("blocks")),
            "sleep_reserve_minutes": rec.get("sleep_reserve_minutes"),
            "active_minutes": rec.get("active_minutes"),
        },
        "actual": {
            "total_logged_minutes": actual.get("total_logged_minutes"),
            "unaccounted_minutes": actual.get("unaccounted_minutes"),
            "blocks": slim_blocks(actual.get("blocks")),
        },
        "allocation_delta": (payload.get("allocation_delta") or [])[:16],
        "suggestions": [
            {
                "id": s.get("id"),
                "title": s.get("title"),
                "reason": s.get("reason"),
                "minutes": s.get("minutes"),
                "role": s.get("role"),
                "urgency": s.get("urgency"),
            }
            for s in (payload.get("suggestions") or [])[:12]
            if isinstance(s, dict)
        ],
        "targets": [
            {
                "id": t.get("id"),
                "title": t.get("title"),
                "kind": t.get("kind"),
                "priority": t.get("priority"),
                "minutes": t.get("minutes"),
            }
            for t in (payload.get("targets") or [])
            if isinstance(t, dict)
        ],
        "kpi_status": (payload.get("kpi_status") or [])[:12],
        "items": (payload.get("items") or [])[:20],
        "walk_candidates": (payload.get("walk_candidates") or [])[:8],
        "recent_logs": logs,
    }


def _shrink_context(context: dict, max_chars: int = MAX_CONTEXT_CHARS) -> dict:
    ctx = json.loads(json.dumps(context))
    raw = json.dumps(ctx, separators=(",", ":"))
    if len(raw) <= max_chars:
        return ctx
    # progressive trim
    for key, n in (
        ("recent_logs", 15),
        ("suggestions", 6),
        ("allocation_delta", 8),
        ("walk_candidates", 3),
        ("items", 8),
    ):
        if isinstance(ctx.get(key), list):
            ctx[key] = ctx[key][:n]
        raw = json.dumps(ctx, separators=(",", ":"))
        if len(raw) <= max_chars:
            return ctx
    ctx["recent_logs"] = (ctx.get("recent_logs") or [])[-5:]
    return ctx


def chat_completions(
    messages: list[dict],
    *,
    model: str | None = None,
    max_tokens: int = 1000,
    temperature: float = 0.3,
) -> dict[str, Any]:
    creds = resolve_xai_credentials()
    token = creds.get("token")
    if not token:
        raise GrokAskError(creds.get("error") or "No xAI credentials", status=401)
    if creds.get("expired"):
        raise GrokAskError(creds.get("error") or "Session expired", status=401)

    body = {
        "model": model or DEFAULT_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    req = urllib.request.Request(
        f"{XAI_API_BASE}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise GrokAskError(f"xAI API error HTTP {e.code}", status=e.code, body=err_body) from e
    except urllib.error.URLError as e:
        raise GrokAskError(f"xAI request failed: {e}", status=0) from e


def ask_about_time(question: str, dashboard_payload: dict[str, Any]) -> dict[str, Any]:
    """Answer a user question grounded in the current time-allocator snapshot."""
    q = (question or "").strip()
    if not q:
        raise GrokAskError("question is required", status=400)
    if len(q) > 4000:
        raise GrokAskError("question too long (max 4000 chars)", status=400)

    ctx = _shrink_context(build_time_context(dashboard_payload))
    user_content = (
        "TIME DATA JSON:\n"
        + json.dumps(ctx, indent=2)
        + "\n\nUSER QUESTION:\n"
        + q
    )
    raw = chat_completions(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
    )
    choice = (raw.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    answer = (message.get("content") or "").strip()
    usage = raw.get("usage") or {}
    return {
        "ok": True,
        "answer": answer or "(empty response)",
        "model": raw.get("model") or DEFAULT_MODEL,
        "usage": usage,
        "auth_source": resolve_xai_credentials().get("source"),
    }
