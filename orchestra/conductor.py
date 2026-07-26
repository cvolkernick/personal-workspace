"""Conductor: ask Grok about orchestration state from the Orchestra dashboard.

Uses xAI API key (XAI_API_KEY) or SuperGrok session (~/.grok/auth.json),
same pattern as holistic/resistance dashboards.
"""

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
MAX_CONTEXT_CHARS = int(os.environ.get("GROK_ASK_MAX_CONTEXT_CHARS", "28000"))
REQUEST_TIMEOUT = int(os.environ.get("GROK_ASK_TIMEOUT_SEC", "90"))

SYSTEM_PROMPT = """You are the Conductor for the user's personal Orchestrator dashboard.

You help them orchestrate strategy, workflow, finance, fitness, time-allocation,
and home/IoT as one system. You answer using the ORCHESTRATION DATA JSON in the
user message (plus general systems/productivity judgment that does not invent
facts about THIS user).

Rules:
- Ground answers in the provided data: next_action, today focus, strategy brief
  (bets, weightings, directives), domains, recommendations, attention, synergies,
  bridge candidates, freshness.
- If something is missing from the data, say so. Do not invent portfolio balances,
  backlog titles, or initiative status.
- Prefer concise, actionable guidance. Short bullets when helpful.
- Format replies in **GitHub-flavored Markdown** so the dashboard can render them:
  use headings (##), bullet/numbered lists, **bold** for emphasis, and `code`
  for paths/commands. Avoid raw HTML.
- When recommending what to do next, prefer the data's next_action / today_focus
  unless hygiene (stale data, stress, missing domains) clearly blocks it.
- You may suggest edits to strategy/today.md, initiatives/, or which subordinate
  dashboard to open — but do not claim you already edited files.
- Do not discuss secrets, tokens, or how to access private systems.
"""


class ConductorError(Exception):
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


def build_orchestration_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Compact snapshot of orchestra state for the model."""
    rec = payload.get("recommendations") or {}
    nxt = payload.get("next_action") or rec.get("next_action") or {}
    strategy = payload.get("strategy") or {}
    today = payload.get("today_focus") or {}
    domains = payload.get("domains") or []
    domain_slim = []
    for d in domains:
        if not isinstance(d, dict):
            continue
        domain_slim.append(
            {
                "id": d.get("id"),
                "label": d.get("label"),
                "status": d.get("status"),
                "available": d.get("available"),
                "summary": (d.get("summary") or "")[:160],
                "stale": d.get("stale"),
            }
        )
    recs = payload.get("recommended_actions") or rec.get("items") or []
    attention = payload.get("attention") or []
    bridge = payload.get("bridge") or {}
    freshness = payload.get("freshness") or {}
    return {
        "generated_at": payload.get("generated_at"),
        "purpose": payload.get("purpose"),
        "strategy": strategy,
        "today_focus": {
            "count": today.get("count"),
            "items": (today.get("items") or [])[:8],
            "today_path": today.get("today_path"),
            "initiatives": (today.get("initiatives") or [])[:8],
        },
        "next_action": {
            "title": nxt.get("title"),
            "action": nxt.get("action"),
            "why": nxt.get("why"),
            "urgency": nxt.get("urgency"),
            "kind": nxt.get("kind"),
            "selection_reason": nxt.get("selection_reason"),
            "domains": nxt.get("domains"),
        }
        if nxt
        else None,
        "recommendations_top": [
            {
                "rank": r.get("rank"),
                "title": r.get("title"),
                "action": r.get("action"),
                "kind": r.get("kind"),
                "urgency": r.get("urgency"),
            }
            for r in recs[:6]
            if isinstance(r, dict)
        ],
        "recommendations_mode": rec.get("mode"),
        "recommendations_summary": rec.get("summary"),
        "attention": [
            {
                "severity": a.get("severity"),
                "kind": a.get("kind"),
                "title": a.get("title"),
                "detail": (a.get("detail") or "")[:200],
            }
            for a in attention[:8]
            if isinstance(a, dict)
        ],
        "domains": domain_slim,
        "bridge_candidates": (bridge.get("candidates") or [])[:6],
        "freshness": {
            "stale_count": freshness.get("stale_count"),
            "has_stale": freshness.get("has_stale"),
            "sources": freshness.get("sources") or [],
        },
        "counts": payload.get("counts") or {},
        "synergies_high_count": (payload.get("counts") or {}).get("synergies_high"),
    }


def _shrink_context(ctx: dict[str, Any], max_chars: int = MAX_CONTEXT_CHARS) -> dict[str, Any]:
    raw = json.dumps(ctx, default=str, separators=(",", ":"))
    if len(raw) <= max_chars:
        return ctx
    for key, n in (
        ("recommendations_top", 4),
        ("attention", 4),
        ("bridge_candidates", 3),
        ("domains", 6),
    ):
        if isinstance(ctx.get(key), list):
            ctx[key] = ctx[key][:n]
        raw = json.dumps(ctx, default=str, separators=(",", ":"))
        if len(raw) <= max_chars:
            return ctx
    tf = ctx.get("today_focus")
    if isinstance(tf, dict) and isinstance(tf.get("items"), list):
        tf["items"] = tf["items"][:4]
    return ctx


def chat_completions(
    messages: list[dict],
    *,
    model: str | None = None,
    max_tokens: int = 1200,
    temperature: float = 0.35,
) -> dict[str, Any]:
    creds = resolve_xai_credentials()
    token = creds.get("token")
    if not token:
        raise ConductorError(creds.get("error") or "No xAI credentials", status=401)
    if creds.get("expired"):
        raise ConductorError(creds.get("error") or "Session expired", status=401)

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
        raise ConductorError(
            f"xAI API error HTTP {e.code}", status=e.code, body=err_body
        ) from e
    except urllib.error.URLError as e:
        raise ConductorError(f"xAI request failed: {e}", status=0) from e


def ask_conductor(question: str, orchestra_payload: dict[str, Any]) -> dict[str, Any]:
    """Answer a user question grounded in the current orchestra snapshot."""
    q = (question or "").strip()
    if not q:
        raise ConductorError("question is required", status=400)
    if len(q) > 4000:
        raise ConductorError("question too long (max 4000 chars)", status=400)

    ctx = _shrink_context(build_orchestration_context(orchestra_payload))
    user_content = (
        "ORCHESTRATION DATA JSON:\n"
        + json.dumps(ctx, indent=2, default=str)
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


# Suggested prompts for the Conductor UI (no I/O)
CONDUCTOR_SUGGESTIONS = [
    "What should I focus on for the next 2 hours?",
    "Are any domains blocking progress right now?",
    "How do today's items connect to my thematic bets?",
    "What should I update in strategy/today.md?",
    "Summarize cross-domain synergies I should act on.",
]
