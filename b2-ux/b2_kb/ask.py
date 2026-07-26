"""Ask Grok over B2 vault notes: retrieve → pack → live xAI or offline grounded."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .vault import Note, retrieve, resolve_vault_path

XAI_API_BASE = os.environ.get("XAI_API_BASE", "https://api.x.ai/v1").rstrip("/")
DEFAULT_MODEL = os.environ.get("XAI_MODEL", "grok-4.20-non-reasoning")
AUTH_PATH = Path.home() / ".grok" / "auth.json"
REQUEST_TIMEOUT = int(os.environ.get("B2_ASK_TIMEOUT_SEC", "90"))
MAX_CONTEXT_CHARS = int(os.environ.get("B2_ASK_MAX_CONTEXT_CHARS", "24000"))

SYSTEM_PROMPT = """You are Grok assisting with the user's personal knowledge base vault "B2" (Brain 2).

You answer using ONLY the VAULT NOTES provided in the user message (plus general knowledge that does not invent facts about THIS vault).

Rules:
- Ground answers in the provided note text. Cite note titles or paths when you use them.
- If the notes do not contain enough information, say clearly that the vault lacks relevant material.
- Do not invent notes, wikilinks, balances, secrets, or decisions not present in the context.
- Prefer concise, structured answers (short paragraphs or bullets).
- Do not discuss how to obtain API keys or bypass auth.
"""


class B2AskError(Exception):
    def __init__(self, message: str, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def _parse_expires_at(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_grok_auth_entry() -> Optional[dict]:
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


def resolve_xai_credentials() -> Dict[str, Any]:
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
                "No SuperGrok session found. Sign in with `grok login`, or set "
                "XAI_API_KEY from console.x.ai."
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


def auth_status() -> Dict[str, Any]:
    creds = resolve_xai_credentials()
    return {
        "ok": bool(creds.get("token")) and not creds.get("expired"),
        "source": creds.get("source"),
        "email": creds.get("email"),
        "expires_at": creds.get("expires_at"),
        "expired": bool(creds.get("expired")),
        "model": DEFAULT_MODEL,
        "error": creds.get("error"),
        "auth_path": str(AUTH_PATH),
    }


def build_ask_context(
    query: str,
    vault_path: Optional[os.PathLike | str] = None,
    *,
    top_k: int = 5,
    max_chars: int = MAX_CONTEXT_CHARS,
    notes: Optional[Sequence[Note]] = None,
) -> Dict[str, Any]:
    """Retrieve notes and pack a context block for the model / offline answerer."""
    hits = retrieve(
        query,
        vault_path,
        top_k=top_k,
        notes=notes,
    )
    parts: List[str] = []
    sources: List[dict] = []
    used = 0
    for h in hits:
        header = f"### {h['title']} ({h['path']})\n"
        body = h.get("body") or ""
        block = header + body
        if used + len(block) > max_chars and parts:
            break
        if used + len(block) > max_chars:
            remain = max(0, max_chars - used - len(header) - 20)
            block = header + body[:remain] + "\n…[truncated]"
        parts.append(block)
        used += len(block)
        sources.append(
            {
                "path": h["path"],
                "title": h["title"],
                "score": h["score"],
                "snippet": h.get("snippet") or "",
            }
        )
    context_text = "\n\n---\n\n".join(parts)
    return {
        "query": (query or "").strip(),
        "sources": sources,
        "context_text": context_text,
        "context_chars": len(context_text),
        "vault_path": str(resolve_vault_path(vault_path)),
        "hit_count": len(sources),
    }


def offline_grounded_answer(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic answer from retrieved notes only (no LLM).
    Used when live xAI is unavailable or forced offline.
    """
    sources = ctx.get("sources") or []
    q = (ctx.get("query") or "").strip()
    if not sources:
        answer = (
            "The B2 vault does not appear to contain material relevant to this "
            f"question ({q!r}). Try different keywords, or add a note and link it "
            "from the hub."
        )
        return {
            "answer": answer,
            "mode": "offline_grounded",
            "model": None,
            "sources": [],
            "auth_source": "none",
        }

    bullets: List[str] = []
    for s in sources:
        snip = (s.get("snippet") or "").strip()
        title = s.get("title") or s.get("path")
        path = s.get("path")
        if snip:
            bullets.append(f"- **{title}** (`{path}`): {snip}")
        else:
            bullets.append(f"- **{title}** (`{path}`)")

    answer = (
        f"Based on {len(sources)} vault note(s) matching your question "
        f"({q!r}) — offline grounded mode (no live model call):\n\n"
        + "\n".join(bullets)
        + "\n\n_Sources are cited by title/path above. Open them in B2 or Obsidian for full text._"
    )
    return {
        "answer": answer,
        "mode": "offline_grounded",
        "model": None,
        "sources": sources,
        "auth_source": "none",
    }


def chat_completions(
    messages: List[dict],
    *,
    model: Optional[str] = None,
    max_tokens: int = 1200,
    temperature: float = 0.3,
) -> Dict[str, Any]:
    creds = resolve_xai_credentials()
    token = creds.get("token")
    if not token:
        raise B2AskError(creds.get("error") or "No xAI credentials", status=401)
    if creds.get("expired"):
        raise B2AskError(creds.get("error") or "Session expired", status=401)

    body = {
        "model": model or DEFAULT_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    req = urllib.request.Request(
        f"{XAI_API_BASE}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "b2-knowledge-base/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:2000]
        raise B2AskError(
            f"xAI API error HTTP {e.code}: {err_body[:400]}",
            status=e.code,
            body=err_body,
        ) from e
    except urllib.error.URLError as e:
        raise B2AskError(f"xAI network error: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise B2AskError(f"Invalid JSON from xAI: {e}") from e

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise B2AskError(f"Unexpected xAI response shape: {str(data)[:400]}") from e

    return {
        "answer": content,
        "model": data.get("model") or (model or DEFAULT_MODEL),
        "usage": data.get("usage"),
        "auth_source": creds.get("source"),
    }


def ask_grok(
    question: str,
    vault_path: Optional[os.PathLike | str] = None,
    *,
    force_offline: bool = False,
    top_k: int = 5,
    notes: Optional[Sequence[Note]] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Full Ask Grok path: retrieve vault context, then live xAI or offline grounded.

    force_offline=True always uses the offline synthesizer (tests / no network).
    """
    q = (question or "").strip()
    if not q:
        raise B2AskError("question is required", status=400)
    if len(q) > 4000:
        raise B2AskError("question too long (max 4000 chars)", status=400)

    ctx = build_ask_context(q, vault_path, top_k=top_k, notes=notes)

    if force_offline:
        result = offline_grounded_answer(ctx)
        result["context_chars"] = ctx["context_chars"]
        result["hit_count"] = ctx["hit_count"]
        result["vault_path"] = ctx["vault_path"]
        return result

    creds = resolve_xai_credentials()
    can_live = bool(creds.get("token")) and not creds.get("expired")
    if not can_live:
        result = offline_grounded_answer(ctx)
        result["context_chars"] = ctx["context_chars"]
        result["hit_count"] = ctx["hit_count"]
        result["vault_path"] = ctx["vault_path"]
        result["live_error"] = creds.get("error") or "No credentials"
        result["fallback_reason"] = "no_credentials"
        return result

    # No hits: still answer honestly without inventing vault facts
    if ctx["hit_count"] == 0:
        user_block = (
            "VAULT NOTES: (none retrieved for this query)\n\n"
            f"QUESTION:\n{q}\n\n"
            "State that the vault lacks relevant material."
        )
    else:
        user_block = (
            "VAULT NOTES (authoritative for this knowledge base):\n\n"
            f"{ctx['context_text']}\n\n"
            f"QUESTION:\n{q}"
        )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_block},
    ]
    try:
        live = chat_completions(messages, model=model)
    except B2AskError as e:
        result = offline_grounded_answer(ctx)
        result["context_chars"] = ctx["context_chars"]
        result["hit_count"] = ctx["hit_count"]
        result["vault_path"] = ctx["vault_path"]
        result["live_error"] = str(e)
        result["fallback_reason"] = "live_api_failed"
        return result

    return {
        "answer": live["answer"],
        "mode": "live_xai",
        "model": live.get("model"),
        "usage": live.get("usage"),
        "auth_source": live.get("auth_source"),
        "sources": ctx["sources"],
        "context_chars": ctx["context_chars"],
        "hit_count": ctx["hit_count"],
        "vault_path": ctx["vault_path"],
    }
