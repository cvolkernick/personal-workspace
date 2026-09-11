#!/usr/bin/env python3
"""Recurring /learn-style harness analysis (#575).

Read-only. Collects Grok Build traces + skill/MCP inventory, writes a
create/fix/delete report. Never edits skills, memory, config, or git.

Inspired by Grok Build /learn (aksheyd 2026-09-09) but scheduled and
non-interactive: apply is always a human + git step, not this job.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

SOURCES = {
    "reads": [
        "<grok-home>/sessions/<cwd>/<id>/summary.json",
        "<grok-home>/sessions/<cwd>/<id>/chat_history.jsonl",
        "<grok-home>/skills/*/SKILL.md",
        "<grok-home>/bundled/skills/*/SKILL.md",
        "<grok-home>/config.toml (MCP / plugin *names* only)",
        "<grok-home>/settings.json mcpServers keys only",
        "<grok-home>/slash-mru.json command names only",
    ],
    "never_touches": [
        "SKILL.md contents in place",
        "GROK_HOME/config.toml values (tokens, env, keys)",
        "GROK_HOME/learn/state.json",
        "memory / buzz mem",
        "git (no commit, push, checkout)",
        "skills, plugins, MCP server tables",
    ],
}

DEFAULT_DAYS = 14
DEFAULT_MIN_PHRASE_SESSIONS = 2
DEFAULT_CADENCE = "weekly"
DEFAULT_ON_CALENDAR = "Mon *-*-* 16:00:00 UTC"
USER_QUERY_RE = re.compile(r"<user_query>(.*?)</user_query>", re.S)
SKILL_PATH_RE = re.compile(r"[/\\]skills[/\\]([^/\\]+)[/\\]SKILL\.md$")
SLASH_RE = re.compile(r"(?<![\w/~.])/([a-z][a-z0-9-]{1,63})\b(?!/)")
WORD_RE = re.compile(r"\s+")
NAME_FM_RE = re.compile(r"(?m)^name:\s*['\"]?([A-Za-z0-9_-]+)")
MCP_TABLE_RE = re.compile(r"(?m)^\[mcp_servers\.([^\]]+)\]")
BACKTICK_PATH_RE = re.compile(r"`([A-Za-z0-9_./-]{3,120})`")
FRICTION_RE = re.compile(
    r"(?i)\b(no,? don't|don't|do not|wrong|not that|stop doing|i said|instead|never do)\b"
)
HARNESS_BLOCK_RES = [
    re.compile(r"<user_info>.*?</user_info>", re.S | re.I),
    re.compile(r"<rules>.*?</rules>", re.S | re.I),
    re.compile(r"<base>.*?</base>", re.S | re.I),
    re.compile(r"<agent-instructions>.*?</agent-instructions>", re.S | re.I),
    re.compile(r"<team-instructions>.*?</team-instructions>", re.S | re.I),
    re.compile(r"<core-memory>.*?</core-memory>", re.S | re.I),
    re.compile(r"<channel-canvas>.*?</channel-canvas>", re.S | re.I),
    re.compile(r"<thread-context\b.*?</thread-context>", re.S | re.I),
    re.compile(r"<context>.*?</context>", re.S | re.I),
    re.compile(r"<what-you-were-working-on>.*?</what-you-were-working-on>", re.S | re.I),
    re.compile(r"<system-reminder>.*?</system-reminder>", re.S | re.I),
    re.compile(r"<always_applied_workspace_rules>.*?</always_applied_workspace_rules>", re.S | re.I),
]
BUZZ_CONTENT_RE = re.compile(r"Content:\s*(.+?)(?:\nTags:|\nParsed:|$)", re.S)
HARNESS_PREFIX_RE = re.compile(
    r"(?i)^(\[base\]|<base>|<context>|<agent-instructions>|you are an agent operating inside)"
)
SECRET_RES = [
    re.compile(r"\b(xai|sk|ghp|gho|ghu|ghs|glpat|npm)[-_](?=(?:[A-Za-z_\-]*\d){3})[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-./+=]{20,}"),
    re.compile(r"\b[a-f0-9]{64,}\b"),
]
SUBAGENT_KINDS = {"subagent", "subagent_resume", "subagent_fork"}
TURN_CAP = 400
SKIP_PATH_PREFIXES = (
    "http://",
    "https://",
    "www.",
    "mailto:",
)
SKIP_PATH_NAMES = {
    "SKILL.md",
    "README.md",
    "true",
    "false",
    "null",
}


def redact(text: str) -> str:
    for rx in SECRET_RES:
        text = rx.sub(lambda m: m.group(0)[:8] + "…[redacted]", text)
    return text


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_time(s: Any) -> Optional[dt.datetime]:
    if not s or not isinstance(s, str):
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def normalize(s: str) -> str:
    return WORD_RE.sub(" ", s.strip().lower())


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text") or "")
            elif isinstance(c, str):
                parts.append(c)
        return "\n".join(parts)
    return ""


def human_turns(content: Any) -> list[str]:
    return [t.strip() for t in USER_QUERY_RE.findall(text_of(content)) if t.strip()]


def strip_harness(text: str) -> str:
    """Drop agent/TUI wrappers so phrases are the human ask, not the platform prompt."""
    s = text
    for rx in HARNESS_BLOCK_RES:
        s = rx.sub("\n", s)
    m = BUZZ_CONTENT_RE.search(s)
    if m:
        s = m.group(1)
    s = WORD_RE.sub(" ", s).strip()
    return s


def intent_text(turn: str) -> str:
    stripped = strip_harness(turn)
    if not stripped or HARNESS_PREFIX_RE.match(stripped):
        return ""
    if "nostr-based messaging platform" in stripped.lower() and len(stripped) > 400:
        return ""
    return stripped


def skill_frontmatter_name(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return path.parent.name
    m = NAME_FM_RE.search(text[:800])
    return m.group(1) if m else path.parent.name


def list_skills(root: Path, source: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for child in sorted(root.iterdir()):
        skill = child / "SKILL.md"
        if skill.is_file():
            out.append(
                {
                    "name": skill_frontmatter_name(skill),
                    "path": str(skill),
                    "source": source,
                    "protected": "bundled" if source == "bundled" else None,
                }
            )
    return out


def mcp_names(grok_home: Path) -> list[str]:
    names: set[str] = set()
    cfg = grok_home / "config.toml"
    if cfg.is_file():
        try:
            text = cfg.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        names.update(MCP_TABLE_RE.findall(text))
    settings = grok_home / "settings.json"
    if settings.is_file():
        try:
            data = load_json(settings)
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            servers = data.get("mcpServers") or {}
            if isinstance(servers, dict):
                names.update(str(k) for k in servers)
    return sorted(names)


def slash_mru(grok_home: Path) -> set[str]:
    path = grok_home / "slash-mru.json"
    if not path.is_file():
        return set()
    try:
        data = load_json(path)
    except (OSError, ValueError):
        return set()
    by = (data or {}).get("by_command") or {}
    return set(by) if isinstance(by, dict) else set()


def scan_session(sess_dir: Path, summary: dict[str, Any]) -> Optional[dict[str, Any]]:
    chat = sess_dir / "chat_history.jsonl"
    if not chat.is_file():
        return None
    turns: list[str] = []
    skill_loads: collections.Counter[str] = collections.Counter()
    mcp_tools: collections.Counter[str] = collections.Counter()
    slash: collections.Counter[str] = collections.Counter()
    tools: collections.Counter[str] = collections.Counter()
    friction: list[str] = []
    with chat.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            kind = rec.get("type")
            if kind == "user" and not rec.get("synthetic_reason"):
                for q in human_turns(rec.get("content")):
                    intent = intent_text(q) or q
                    turns.append(intent)
                    for m in SLASH_RE.findall(intent):
                        slash[m] += 1
                    if FRICTION_RE.search(intent):
                        friction.append(intent[:TURN_CAP])
            elif kind == "assistant":
                for call in rec.get("tool_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    name = call.get("name") or ""
                    tools[name] += 1
                    try:
                        args = json.loads(call.get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    if not isinstance(args, dict):
                        args = {}
                    if name == "use_tool":
                        tn = str(args.get("tool_name") or "")
                        mcp_tools[tn.split("__", 1)[0] if "__" in tn else tn] += 1
                    target = args.get("target_file") or args.get("file_path") or args.get("path")
                    if isinstance(target, str) and target:
                        sm = SKILL_PATH_RE.search(target)
                        if sm:
                            skill_loads[sm.group(1)] += 1
    if not turns:
        return None
    real_tools = sum(n for name, n in tools.items() if name != "send_feedback")
    if real_tools == 0 and all(len(t.split()) < 3 for t in turns):
        return None
    info = summary.get("info") or {}
    sid = info.get("id") or sess_dir.name
    return {
        "id": sid,
        "cwd": info.get("cwd") or "",
        "title": summary.get("generated_title") or "",
        "session_kind": summary.get("session_kind"),
        "updated_at": summary.get("updated_at"),
        "human_turns": len(turns),
        "turns": [redact(t)[:TURN_CAP] for t in turns],
        "slash_commands": dict(slash),
        "skills_loaded": dict(skill_loads),
        "mcp_servers_used": dict(mcp_tools),
        "friction": [redact(x) for x in friction[:8]],
        "trace_ref": f"sessions/…/{sess_dir.name}/chat_history.jsonl",
    }


def collect(
    grok_home: Path,
    *,
    days: int = DEFAULT_DAYS,
    include_headless: bool = False,
    now: Optional[dt.datetime] = None,
) -> dict[str, Any]:
    """Read-only scan. Writes nothing under grok_home."""
    now = now or utc_now()
    cutoff = now - dt.timedelta(days=days) if days > 0 else None
    sessions_root = grok_home / "sessions"
    seen = 0
    dropped: collections.Counter[str] = collections.Counter()
    kept: list[dict[str, Any]] = []
    if sessions_root.is_dir():
        for cwd_dir in sorted(p for p in sessions_root.iterdir() if p.is_dir()):
            for sess_dir in sorted(p for p in cwd_dir.iterdir() if p.is_dir()):
                seen += 1
                summ_path = sess_dir / "summary.json"
                if not summ_path.is_file():
                    dropped["no_summary"] += 1
                    continue
                try:
                    summary = load_json(summ_path)
                except (OSError, ValueError):
                    dropped["summary_unreadable"] += 1
                    continue
                if not isinstance(summary, dict):
                    dropped["summary_unreadable"] += 1
                    continue
                kind = summary.get("session_kind")
                if kind in SUBAGENT_KINDS:
                    dropped["subagent"] += 1
                    continue
                if kind == "headless" and not include_headless:
                    dropped["headless"] += 1
                    continue
                if cutoff is not None:
                    ts = parse_time(summary.get("updated_at"))
                    if ts is None or ts < cutoff:
                        dropped["older_than_window"] += 1
                        continue
                rec = scan_session(sess_dir, summary)
                if rec is None:
                    dropped["no_human_turns"] += 1
                    continue
                kept.append(rec)
    skills = list_skills(grok_home / "skills", "user") + list_skills(
        grok_home / "bundled" / "skills", "bundled"
    )
    usage: collections.Counter[str] = collections.Counter()
    mcp_usage: collections.Counter[str] = collections.Counter()
    phrases: dict[str, list[str]] = collections.defaultdict(list)
    friction_rows: list[dict[str, str]] = []
    for rec in kept:
        for name, n in (rec.get("skills_loaded") or {}).items():
            usage[name] += int(n)
        for name, n in (rec.get("mcp_servers_used") or {}).items():
            if name:
                mcp_usage[name] += int(n)
        for turn in rec.get("turns") or []:
            n = normalize(turn)
            if len(n.split()) >= 4:
                phrases[n].append(rec["id"])
        for quote in rec.get("friction") or []:
            friction_rows.append({"session": rec["id"], "quote": quote[:300]})
    return {
        "grok_home": str(grok_home),
        "generated_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "params": {
            "days": days,
            "include_headless": include_headless,
            "min_phrase_sessions": DEFAULT_MIN_PHRASE_SESSIONS,
            "unused_threshold": f"0 invocations in last {days} days",
            "cadence": DEFAULT_CADENCE,
        },
        "sessions_seen": seen,
        "sessions_kept": len(kept),
        "dropped": dict(dropped),
        "sessions": kept,
        "skills": skills,
        "mcp_servers": mcp_names(grok_home),
        "slash_mru": sorted(slash_mru(grok_home)),
        "usage": dict(usage),
        "mcp_usage": dict(mcp_usage),
        "phrases": {k: v for k, v in phrases.items() if len(set(v)) >= DEFAULT_MIN_PHRASE_SESSIONS},
        "friction": friction_rows[:40],
        "sources": SOURCES,
    }


def stale_paths_in_skill(skill: dict[str, Any], workspace: Optional[Path]) -> list[str]:
    path = Path(skill["path"])
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    missing: list[str] = []
    for token in BACKTICK_PATH_RE.findall(text):
        if "/" not in token and "." not in token:
            continue
        if token.startswith(SKIP_PATH_PREFIXES) or token in SKIP_PATH_NAMES:
            continue
        if token.startswith("~") or token.startswith("/"):
            if not Path(os.path.expanduser(token)).exists():
                missing.append(token)
            continue
        if workspace is not None and not (workspace / token).exists():
            missing.append(token)
    return missing[:8]


def build_actions(
    collected: dict[str, Any],
    *,
    workspace: Optional[Path] = None,
    min_phrase_sessions: int = DEFAULT_MIN_PHRASE_SESSIONS,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    n = 1
    skill_names = {s["name"] for s in collected.get("skills") or []}
    usage = collected.get("usage") or {}
    mru = set(collected.get("slash_mru") or [])

    def next_id() -> str:
        nonlocal n
        aid = f"A{n}"
        n += 1
        return aid

    for phrase, sids in sorted(
        (collected.get("phrases") or {}).items(),
        key=lambda kv: (-len(set(kv[1])), kv[0]),
    ):
        sessions = sorted(set(sids))
        if len(sessions) < min_phrase_sessions:
            continue
        owner = next((name for name in skill_names if name.replace("-", " ") in phrase), None)
        aid = next_id()
        if owner:
            actions.append(
                {
                    "id": aid,
                    "kind": "skill",
                    "action": "edit",
                    "target": owner,
                    "path": next(
                        (s["path"] for s in collected["skills"] if s["name"] == owner),
                        "",
                    ),
                    "protected": next(
                        (s.get("protected") for s in collected["skills"] if s["name"] == owner),
                        None,
                    ),
                    "reversible": True,
                    "requires_confirmation": True,
                    "evidence": {
                        "sessions": sessions[:8],
                        "count": len(sessions),
                        "quote": phrase[:300],
                    },
                    "reason": "Repeated phrase already has an owner skill; add it as a trigger if missing.",
                }
            )
        else:
            slug = re.sub(r"[^a-z0-9]+", "-", "-".join(phrase.split()[:4]))[:40].strip("-")
            actions.append(
                {
                    "id": aid,
                    "kind": "skill",
                    "action": "create",
                    "target": slug or "new-skill",
                    "path": "",
                    "protected": None,
                    "reversible": True,
                    "requires_confirmation": True,
                    "evidence": {
                        "sessions": sessions[:8],
                        "count": len(sessions),
                        "quote": phrase[:300],
                    },
                    "reason": "Repeated across sessions with no owning skill.",
                }
            )

    for skill in collected.get("skills") or []:
        missing = stale_paths_in_skill(skill, workspace)
        if not missing:
            continue
        aid = next_id()
        actions.append(
            {
                "id": aid,
                "kind": "skill",
                "action": "propose" if skill.get("protected") else "edit",
                "target": skill["name"],
                "path": skill["path"],
                "protected": skill.get("protected"),
                "reversible": True,
                "requires_confirmation": True,
                "evidence": {
                    "sessions": [],
                    "count": len(missing),
                    "quote": "; ".join(missing)[:300],
                },
                "reason": "SKILL.md cites paths that do not exist on this machine/workspace.",
            }
        )

    for skill in collected.get("skills") or []:
        name = skill["name"]
        count = int(usage.get(name, 0))
        if count > 0 or name in mru:
            continue
        aid = next_id()
        actions.append(
            {
                "id": aid,
                "kind": "skill",
                "action": "ask",
                "target": name,
                "path": skill["path"],
                "protected": skill.get("protected"),
                "reversible": True,
                "requires_confirmation": True,
                "evidence": {
                    "sessions": [],
                    "count": 0,
                    "quote": collected["params"]["unused_threshold"],
                },
                "reason": "Zero skill-file reads in the window. Listed only — never auto-deleted.",
            }
        )

    for mcp in collected.get("mcp_servers") or []:
        if int((collected.get("mcp_usage") or {}).get(mcp, 0)) > 0:
            continue
        aid = next_id()
        actions.append(
            {
                "id": aid,
                "kind": "mcp",
                "action": "ask",
                "target": mcp,
                "path": "",
                "protected": None,
                "reversible": True,
                "requires_confirmation": True,
                "evidence": {
                    "sessions": [],
                    "count": 0,
                    "quote": collected["params"]["unused_threshold"],
                },
                "reason": "MCP server name present in config; zero use_tool hits in the window. Listed only.",
            }
        )
    return actions


def render_report(collected: dict[str, Any], actions: list[dict[str, Any]]) -> str:
    creates = [a for a in actions if a["action"] == "create"]
    fixes = [a for a in actions if a["action"] in ("edit", "propose")]
    deletes = [a for a in actions if a["action"] == "ask"]
    lines = [
        f"# /learn report — {collected['generated_at'][:10]}",
        "",
        "## Overview",
        f"Problem: scanned {collected['sessions_kept']} kept / {collected['sessions_seen']} seen Grok traces "
        f"over {collected['params']['days']}d. "
        f"{len(creates)} repeated-phrase skill candidates, {len(fixes)} stale-path fixes, "
        f"{len(deletes)} unused list-only items.",
        "Proposed change: none in this job. Every action requires explicit user confirmation, then a git PR.",
        f"Actions: {len(actions)} total — {len(creates)} create, {len(fixes)} fix, {len(deletes)} delete/ask. "
        "Apply count: 0 (this runner cannot apply).",
        "",
        "## 1. Repeated phrases -> skills",
        "| # | Phrase (quoted) | Sessions | Owner | Action |",
        "|---|---|---:|---|---|",
    ]
    for a in creates + [x for x in fixes if (x.get("evidence") or {}).get("quote") and x["action"] == "edit" and x.get("kind") == "skill" and "Repeated phrase" in (x.get("reason") or "")]:
        ev = a["evidence"]
        owner = a["target"] if a["action"] != "create" else f"NEW {a['target']}"
        lines.append(
            f"| {a['id']} | {ev['quote'][:80]!r} | {ev['count']} | {owner} | {a['action']} |"
        )
    if not creates and not any("Repeated phrase" in (a.get("reason") or "") for a in fixes):
        lines.append("| — | none | 0 | — | — |")
    lines += [
        "",
        "## 2. Skills to update",
        "| # | Skill (path) | Stale line (quoted) | Evidence | Action |",
        "|---|---|---|---|---|",
    ]
    stale = [a for a in fixes if "paths that do not exist" in (a.get("reason") or "")]
    if not stale:
        lines.append("| — | none | — | — | — |")
    for a in stale:
        lines.append(
            f"| {a['id']} | {a['target']} (`{a['path']}`) | {a['evidence']['quote'][:80]!r} | "
            f"missing paths={a['evidence']['count']} | {a['action']} |"
        )
    lines += [
        "",
        "## 3. Unused -> delete or disable",
        "| # | Name | Kind | Count | Why listed | Action |",
        "|---|---|---|---:|---|---|",
    ]
    if not deletes:
        lines.append("| — | none | — | 0 | — | — |")
    for a in deletes:
        lines.append(
            f"| {a['id']} | {a['target']} | {a['kind']} | {a['evidence']['count']} | "
            f"{a['evidence']['quote']} — never auto-deleted | {a['action']} |"
        )
    lines += [
        "",
        "## 4. Gaps",
    ]
    friction = collected.get("friction") or []
    if not friction:
        lines.append("No friction phrases matched in this window.")
    else:
        lines.append("User-correction phrases (heuristic). Discussion only — no action ids.")
        for row in friction[:12]:
            lines.append(f"- `{row['session']}`: {row['quote'][:160]!r}")
    dropped = collected.get("dropped") or {}
    dropped_s = ", ".join(f"{k}={v}" for k, v in sorted(dropped.items())) or "none"
    lines += [
        "",
        "## Coverage",
        f"- sessions seen {collected['sessions_seen']} / kept {collected['sessions_kept']} / dropped: {dropped_s}",
        f"- window: last {collected['params']['days']} days; unused threshold: {collected['params']['unused_threshold']}",
        f"- cadence default: {collected['params']['cadence']} (`{DEFAULT_ON_CALENDAR}`)",
        "- reads: " + "; ".join(SOURCES["reads"]),
        "- never touches: " + "; ".join(SOURCES["never_touches"]),
        "- this run could not see: headless sessions (unless included), hook bodies, MCP secret values, other machines' GROK_HOME",
        "",
        "## Apply",
        "Zero autonomous edits. Re-run with a human picking action ids, then land harness changes via git PR (#569).",
        "This CLI rejects `--apply`.",
        "",
    ]
    return "\n".join(lines)


def short_summary(collected: dict[str, Any], actions: list[dict[str, Any]], report_path: Path) -> str:
    creates = sum(1 for a in actions if a["action"] == "create")
    fixes = sum(1 for a in actions if a["action"] in ("edit", "propose"))
    asks = sum(1 for a in actions if a["action"] == "ask")
    return (
        f"**Harness /learn** {collected['generated_at'][:10]} — "
        f"kept {collected['sessions_kept']}/{collected['sessions_seen']} sessions "
        f"({collected['params']['days']}d). "
        f"Propose {creates} create / {fixes} fix / {asks} unused-ask. "
        f"Applied **0**. Full report: `{report_path}`"
    )


def write_run(out: Path, collected: dict[str, Any], actions: list[dict[str, Any]]) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    stamp = collected["generated_at"].replace(":", "").replace("-", "")[:15]
    run_dir = out / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(run_dir, 0o700)
    except OSError:
        pass
    manifest_path = run_dir / "manifest.json"
    slim = {k: v for k, v in collected.items() if k != "sessions"}
    slim["session_ids"] = [s["id"] for s in collected.get("sessions") or []]
    manifest_path.write_text(json.dumps(slim, indent=1) + "\n", encoding="utf-8")
    actions_path = run_dir / "actions.json"
    actions_path.write_text(
        json.dumps(
            {
                "run_dir": str(run_dir),
                "generated_at": collected["generated_at"],
                "apply": False,
                "actions": actions,
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    report_path = run_dir / "report.md"
    report_path.write_text(render_report(collected, actions), encoding="utf-8")
    summary_path = run_dir / "summary.txt"
    summary_path.write_text(short_summary(collected, actions, report_path) + "\n", encoding="utf-8")
    return {
        "run_dir": run_dir,
        "report": report_path,
        "actions": actions_path,
        "summary": summary_path,
        "manifest": manifest_path,
    }


def maybe_post_buzz(summary: str, channel: str) -> tuple[bool, str]:
    if not channel:
        return False, "no channel"
    exe = shutil_which("buzz")
    if not exe:
        return False, "buzz CLI not on PATH"
    try:
        proc = subprocess.run(
            ["buzz", "messages", "send", "--channel", channel, "--content", summary],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except OSError as e:
        return False, str(e)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "buzz failed")[:300]
    return True, "ok"


def shutil_which(name: str) -> Optional[str]:
    from shutil import which

    return which(name)


def load_config(path: Optional[Path]) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data = load_json(path)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--grok-home", default=os.environ.get("GROK_HOME") or str(Path.home() / ".grok"))
    ap.add_argument("--out", default=None, help="directory for reports (default: <repo>/ops/learn/reports)")
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--workspace", default=None, help="repo root for stale-path checks")
    ap.add_argument("--config", default=None, help="JSON config (cadence, days, channel)")
    ap.add_argument("--include-headless", action="store_true")
    ap.add_argument("--post-buzz", action="store_true")
    ap.add_argument("--channel", default="")
    ap.add_argument("--apply", action="store_true", help="rejected: this job never applies")
    args = ap.parse_args(list(argv) if argv is not None else None)
    if args.apply:
        print("refusing --apply: #575 is report-only; harness edits go through git after confirmation", file=sys.stderr)
        return 2
    cfg = load_config(Path(args.config) if args.config else None)
    grok_home = Path(cfg.get("grok_home") or args.grok_home).expanduser()
    days = int(args.days if args.days is not None else cfg.get("days") or DEFAULT_DAYS)
    workspace = Path(args.workspace or cfg.get("workspace") or Path(__file__).resolve().parents[2])
    out = Path(args.out or cfg.get("out") or (Path(__file__).resolve().parent / "reports")).expanduser()
    collected = collect(
        grok_home,
        days=days,
        include_headless=bool(args.include_headless or cfg.get("include_headless")),
    )
    actions = build_actions(collected, workspace=workspace if workspace.is_dir() else None)
    paths = write_run(out, collected, actions)
    summary = paths["summary"].read_text(encoding="utf-8").strip()
    print(summary)
    print(f"report={paths['report']}")
    channel = args.channel or cfg.get("channel") or ""
    if args.post_buzz or cfg.get("post_buzz"):
        ok, msg = maybe_post_buzz(summary, str(channel))
        print(f"buzz_post={'ok' if ok else 'skip'} {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
