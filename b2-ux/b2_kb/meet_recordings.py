"""Google Drive Meet Recordings → B2 vault (plan / ingest / status).

Pipeline (agent-assisted Drive fetch; this module is pure vault I/O):

  Drive folder list → plan(manifest) → agent reads Docs → ingest(items)

Dedup:
  1. file_id + modified_time → skip fetch if unchanged
  2. content_sha256 → skip re-stage if identical body already known

Default: stage then auto-promote into inbox/captures/ (same global B2 default).
Opt-out: ingest --no-promote

Quiet mode: channel summary is empty when nothing new (no-spam AC for #59).

State: ~/B2/inbox/meta/meet_recordings_state.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .vault import resolve_vault_path

META_REL = Path("inbox") / "meta"
STAGING_REL = Path("inbox") / "staging"
CAPTURES_REL = Path("inbox") / "captures"
RAW_REL = Path("inbox") / "raw" / "meet"
STATE_FILE = "meet_recordings_state.json"

# Drive Meet Recordings folder (SoT — plan + nest guide)
DEFAULT_FOLDER_ID = "1Xg-gpTN0Hc0TGqEchcNsFRCU0v8HUxBd"
DEFAULT_FOLDER_NAME = "Meet Recordings"
NOTIFY_CHANNEL = "bbc5c4ae-2986-4aa9-9842-9fc62a72a575"  # #b2-drop

INGEST_MIME_PREFIXES = (
    "application/vnd.google-apps.document",
    "application/pdf",
    "text/",
    "application/vnd.openxmlformats-officedocument",
    "application/msword",
    "application/rtf",
)
SKIP_MIME_PREFIXES = (
    "video/",
    "audio/",
    "application/vnd.google-apps.folder",
    "application/vnd.google-apps.shortcut",
)

PREFER_NAME_RE = re.compile(
    r"(?i)(notes by gemini|transcript|gemini notes|zoom call|meet recording|notes$)"
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def meta_dir(vault: Path) -> Path:
    d = vault / META_REL
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_state(vault: Path) -> Dict[str, Any]:
    return load_json(
        meta_dir(vault) / STATE_FILE,
        {"version": 1, "files": {}, "last_plan_at": None, "last_ingest_at": None},
    )


def save_state(vault: Path, state: Dict[str, Any]) -> None:
    save_json(meta_dir(vault) / STATE_FILE, state)


def content_sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def intake_id(file_id: str, modified_time: str, sha: str) -> str:
    raw = f"{file_id}|{modified_time}|{sha}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def mime_policy(mime: str) -> str:
    """Return 'ingest' | 'skip' for a Drive MIME type."""
    m = (mime or "").strip().lower()
    if not m:
        return "ingest"  # unknown: let agent decide via body presence
    for p in SKIP_MIME_PREFIXES:
        if m.startswith(p) or m == p.rstrip("/"):
            return "skip"
    for p in INGEST_MIME_PREFIXES:
        if m.startswith(p) or m == p:
            return "ingest"
    # Google Docs is exact match above; other google-apps often not text-exportable
    if m.startswith("application/vnd.google-apps."):
        return "skip"
    return "skip"


def prefer_name(name: str) -> bool:
    return bool(PREFER_NAME_RE.search(name or ""))


def _parse_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("files", "items", "documents"):
            v = payload.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        if payload.get("file_id") or payload.get("id"):
            return [payload]
    return []


def _norm_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    file_id = str(raw.get("file_id") or raw.get("id") or "").strip()
    name = str(raw.get("name") or raw.get("title") or "untitled").strip()
    modified = str(
        raw.get("modified_time")
        or raw.get("modifiedTime")
        or raw.get("modified")
        or ""
    ).strip()
    mime = str(raw.get("mime_type") or raw.get("mimeType") or raw.get("mime") or "").strip()
    link = str(
        raw.get("web_view_link")
        or raw.get("webViewLink")
        or raw.get("url")
        or ""
    ).strip()
    text = raw.get("text")
    if text is None:
        text = raw.get("body") or raw.get("content")
    text_s = None if text is None else str(text)
    return {
        "file_id": file_id,
        "name": name,
        "modified_time": modified,
        "mime_type": mime,
        "web_view_link": link,
        "text": text_s,
    }


def plan(
    payload: Any,
    vault_path: Optional[os.PathLike | str] = None,
) -> Dict[str, Any]:
    """From Drive metadata (no bodies), decide what needs content fetch."""
    vault = resolve_vault_path(vault_path)
    state = load_state(vault)
    files_state: Dict[str, Any] = state.setdefault("files", {})

    to_fetch: List[Dict[str, Any]] = []
    skip: List[Dict[str, Any]] = []
    unchanged: List[Dict[str, Any]] = []
    prefer: List[str] = []

    for raw in _parse_items(payload):
        item = _norm_item(raw)
        fid = item["file_id"]
        if not fid:
            skip.append({**item, "reason": "missing_file_id"})
            continue
        policy = mime_policy(item["mime_type"])
        if policy == "skip":
            skip.append({**item, "reason": f"mime:{item['mime_type'] or 'empty'}"})
            continue
        prev = files_state.get(fid) or {}
        if (
            prev.get("modified_time")
            and item["modified_time"]
            and prev.get("modified_time") == item["modified_time"]
            and prev.get("status") in ("staged", "promoted")
        ):
            unchanged.append(
                {
                    "file_id": fid,
                    "name": item["name"],
                    "modified_time": item["modified_time"],
                    "status": prev.get("status"),
                    "intake_id": prev.get("intake_id"),
                }
            )
            continue
        rec = {
            "file_id": fid,
            "name": item["name"],
            "modified_time": item["modified_time"],
            "mime_type": item["mime_type"],
            "web_view_link": item["web_view_link"],
            "prefer": prefer_name(item["name"]),
        }
        to_fetch.append(rec)
        if rec["prefer"]:
            prefer.append(fid)

    # Prefer Gemini notes / transcripts first
    to_fetch.sort(key=lambda x: (0 if x.get("prefer") else 1, x.get("name") or ""))

    state["last_plan_at"] = utc_now_iso()
    save_state(vault, state)

    return {
        "ok": True,
        "folder_id": DEFAULT_FOLDER_ID,
        "folder_name": DEFAULT_FOLDER_NAME,
        "to_fetch": to_fetch,
        "to_fetch_count": len(to_fetch),
        "unchanged": unchanged,
        "unchanged_count": len(unchanged),
        "skip": skip,
        "skip_count": len(skip),
        "prefer_file_ids": prefer,
        "as_of": state["last_plan_at"],
        # Quiet signal for agents: no channel post when nothing to do
        "notify": len(to_fetch) > 0,
        "quiet_reason": None if to_fetch else "no_new_or_changed_text_docs",
    }


def _slug_name(name: str, intake: str) -> str:
    base = re.sub(r"[^\w\s.-]+", "", name or "meet", flags=re.UNICODE)
    base = re.sub(r"\s+", "-", base.strip())[:80].strip("-._") or "meet"
    return f"{base}-{intake[:8]}.md"


def _extract_summary(text: str, limit: int = 400) -> str:
    if not text:
        return ""
    # Prefer Summary heading (plain or Gemini **bold** / emoji titles)
    m = re.search(
        r"(?is)(?:^|\n)#{1,3}\s*\**\s*(?:📝\s*)?summary\s*\**\s*\n(.*?)(?:\n#{1,3}\s|\Z)",
        text,
    )
    blob = (m.group(1) if m else text).strip()
    blob = re.sub(r"\s+", " ", blob)
    if len(blob) > limit:
        return blob[: limit - 1] + "…"
    return blob


def _write_capture(
    path: Path,
    *,
    title: str,
    intake: str,
    file_id: str,
    name: str,
    modified_time: str,
    web_view_link: str,
    mime_type: str,
    sha: str,
    body: str,
    promoted: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    status = "promoted" if promoted else "staged"
    fm = (
        f"---\n"
        f'title: "{title.replace(chr(34), chr(39))}"\n'
        f"tags: [meet, gdrive, b2-capture, transcript]\n"
        f"source_type: gdrive_meet\n"
        f"intake_id: {intake}\n"
        f"file_id: {file_id}\n"
        f"drive_modified: {modified_time}\n"
        f"mime_type: {mime_type}\n"
        f"content_sha256: {sha}\n"
        f"status: {status}\n"
        f"as_of: {utc_now_iso()}\n"
    )
    if web_view_link:
        fm += f"source_url: {web_view_link}\n"
    fm += "---\n\n"
    link_line = f"[Open in Drive]({web_view_link})\n\n" if web_view_link else ""
    path.write_text(
        fm + f"# {title}\n\n" + link_line + body.strip() + "\n",
        encoding="utf-8",
    )


def ingest(
    payload: Any,
    vault_path: Optional[os.PathLike | str] = None,
    *,
    auto_promote: bool = True,
) -> Dict[str, Any]:
    """Stage (+ default promote) meet note bodies. Skips empty text and dups."""
    vault = resolve_vault_path(vault_path)
    state = load_state(vault)
    files_state: Dict[str, Any] = state.setdefault("files", {})

    staged: List[Dict[str, Any]] = []
    promoted: List[Dict[str, Any]] = []
    unchanged: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for raw in _parse_items(payload):
        item = _norm_item(raw)
        fid = item["file_id"]
        if not fid:
            skipped.append({"reason": "missing_file_id", "name": item["name"]})
            continue
        if mime_policy(item["mime_type"]) == "skip":
            skipped.append(
                {
                    "file_id": fid,
                    "name": item["name"],
                    "reason": f"mime:{item['mime_type']}",
                }
            )
            continue

        text = item.get("text")
        if text is None:
            skipped.append(
                {
                    "file_id": fid,
                    "name": item["name"],
                    "reason": "no_text_body",
                    "hint": "agent must google_drive read_file before ingest",
                }
            )
            continue
        text = str(text).strip()
        if not text:
            # Empty transcript: quiet skip — never eng Ready spam
            skipped.append(
                {
                    "file_id": fid,
                    "name": item["name"],
                    "reason": "empty_transcript",
                    "notify": False,
                }
            )
            continue

        sha = content_sha256(text)
        prev = files_state.get(fid) or {}
        if prev.get("content_sha256") == sha and prev.get("status") in (
            "staged",
            "promoted",
        ):
            unchanged.append(
                {
                    "file_id": fid,
                    "name": item["name"],
                    "intake_id": prev.get("intake_id"),
                    "reason": "content_sha256_match",
                }
            )
            continue

        iid = intake_id(fid, item["modified_time"] or utc_now_iso(), sha)
        title = item["name"] or f"Meet {fid[:8]}"
        # Strip trailing Gemini boilerplate filename noise for title
        title_clean = re.sub(
            r"\s*-\s*Notes by Gemini\s*$", "", title, flags=re.I
        ).strip() or title

        try:
            raw_dir = vault / RAW_REL
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"{iid}.txt").write_text(text, encoding="utf-8")

            if auto_promote:
                cap_path = vault / CAPTURES_REL / _slug_name(title_clean, iid)
                _write_capture(
                    cap_path,
                    title=title_clean,
                    intake=iid,
                    file_id=fid,
                    name=item["name"],
                    modified_time=item["modified_time"],
                    web_view_link=item["web_view_link"],
                    mime_type=item["mime_type"],
                    sha=sha,
                    body=text,
                    promoted=True,
                )
                rel = cap_path.relative_to(vault).as_posix()
                status = "promoted"
                promoted.append(
                    {
                        "file_id": fid,
                        "name": item["name"],
                        "intake_id": iid,
                        "capture": rel,
                        "summary": _extract_summary(text),
                        "web_view_link": item["web_view_link"],
                    }
                )
            else:
                st_path = vault / STAGING_REL / _slug_name(title_clean, iid)
                _write_capture(
                    st_path,
                    title=title_clean,
                    intake=iid,
                    file_id=fid,
                    name=item["name"],
                    modified_time=item["modified_time"],
                    web_view_link=item["web_view_link"],
                    mime_type=item["mime_type"],
                    sha=sha,
                    body=text,
                    promoted=False,
                )
                rel = st_path.relative_to(vault).as_posix()
                status = "staged"
                staged.append(
                    {
                        "file_id": fid,
                        "name": item["name"],
                        "intake_id": iid,
                        "staging": rel,
                        "summary": _extract_summary(text),
                        "web_view_link": item["web_view_link"],
                    }
                )

            files_state[fid] = {
                "name": item["name"],
                "modified_time": item["modified_time"],
                "mime_type": item["mime_type"],
                "web_view_link": item["web_view_link"],
                "content_sha256": sha,
                "intake_id": iid,
                "status": status,
                "path": rel,
                "updated_at": utc_now_iso(),
            }
        except OSError as e:
            errors.append({"file_id": fid, "name": item["name"], "error": str(e)})

    state["last_ingest_at"] = utc_now_iso()
    save_state(vault, state)

    landed = len(promoted) + len(staged)
    result: Dict[str, Any] = {
        "ok": len(errors) == 0,
        "auto_promote": auto_promote,
        "processed": len(_parse_items(payload)),
        "promoted": promoted,
        "promoted_count": len(promoted),
        "staged": staged,
        "staged_count": len(staged),
        "unchanged": unchanged,
        "unchanged_count": len(unchanged),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "errors": errors,
        "error_count": len(errors),
        "as_of": state["last_ingest_at"],
        # AC #59: only notify when something landed or hard error — never empty-day spam
        "notify": landed > 0 or len(errors) > 0,
        "quiet_reason": None
        if (landed > 0 or len(errors) > 0)
        else "nothing_new_or_empty_transcripts",
    }
    result["channel_summary"] = format_channel_summary(result)
    return result


def format_channel_summary(result: Dict[str, Any]) -> str:
    """Markdown for #b2-drop. Empty string when quiet (no-spam)."""
    if not result.get("notify"):
        return ""
    lines = ["**Meet Recordings → B2 scan**", ""]
    lines.append(
        f"Processed **{result.get('processed', 0)}** · "
        f"promoted **{result.get('promoted_count', 0)}** · "
        f"staged **{result.get('staged_count', 0)}** · "
        f"unchanged **{result.get('unchanged_count', 0)}** · "
        f"skipped **{result.get('skipped_count', 0)}** · "
        f"errors **{result.get('error_count', 0)}**"
    )
    for p in result.get("promoted") or []:
        lines.append("")
        lines.append(f"### Promoted: {p.get('name')}")
        lines.append(f"- intake: `{p.get('intake_id')}`")
        if p.get("capture"):
            lines.append(f"- vault: `{p.get('capture')}`")
        if p.get("web_view_link"):
            lines.append(f"- source: {p.get('web_view_link')}")
        if p.get("summary"):
            lines.append(f"- summary: {p.get('summary')}")
    for s in result.get("staged") or []:
        lines.append("")
        lines.append(f"### Staged: {s.get('name')}")
        lines.append(f"- intake: `{s.get('intake_id')}`")
        lines.append(f"- `promote {str(s.get('intake_id') or '')[:12]}` · `discard …`")
    for e in result.get("errors") or []:
        lines.append("")
        lines.append(f"### Error: {e.get('name') or e.get('file_id')}")
        lines.append(f"- {e.get('error')}")
    return "\n".join(lines).strip() + "\n"


def status(vault_path: Optional[os.PathLike | str] = None) -> Dict[str, Any]:
    vault = resolve_vault_path(vault_path)
    state = load_state(vault)
    files = state.get("files") or {}
    return {
        "vault": str(vault),
        "state_file": str(meta_dir(vault) / STATE_FILE),
        "folder_id": DEFAULT_FOLDER_ID,
        "folder_name": DEFAULT_FOLDER_NAME,
        "notify_channel": NOTIFY_CHANNEL,
        "file_count": len(files),
        "files": [
            {
                "file_id": fid,
                "name": meta.get("name"),
                "status": meta.get("status"),
                "modified_time": meta.get("modified_time"),
                "path": meta.get("path"),
                "intake_id": meta.get("intake_id"),
                "updated_at": meta.get("updated_at"),
            }
            for fid, meta in sorted(files.items(), key=lambda kv: kv[1].get("updated_at") or "")
        ],
        "last_plan_at": state.get("last_plan_at"),
        "last_ingest_at": state.get("last_ingest_at"),
        "standing_order": {
            "cadence": "daily",
            "runner": "Grok (scheduler or @scan meet recordings)",
            "notify": "#b2-drop only when promoted/staged/error — quiet if nothing new",
            "never": "do not open eng Ready/issues for missing or empty transcripts",
        },
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Meet Recordings (Drive) → B2 plan/ingest/status"
    )
    parser.add_argument("--vault", default=None, help="Vault path (default ~/B2)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="From metadata JSON: what needs fetch")
    p_plan.add_argument(
        "--manifest",
        "-m",
        required=True,
        help="JSON file of Drive file metadata (or - for stdin)",
    )
    p_plan.add_argument(
        "--format",
        choices=("json", "channel"),
        default="json",
    )

    p_ing = sub.add_parser("ingest", help="Stage/promote items with text bodies")
    p_ing.add_argument(
        "--manifest",
        "-m",
        required=True,
        help="JSON file of items including text (or - for stdin)",
    )
    p_ing.add_argument(
        "--no-promote",
        action="store_true",
        help="Stage only (hold for human promote/discard)",
    )
    p_ing.add_argument(
        "--format",
        choices=("json", "channel"),
        default="json",
    )

    sub.add_parser("status", help="Show state + standing order")

    args = parser.parse_args(list(argv) if argv is not None else None)
    vault = resolve_vault_path(args.vault)

    def _load_manifest(path: str) -> Any:
        if path == "-":
            raw = sys.stdin.read()
        else:
            raw = Path(path).read_text(encoding="utf-8")
        if not raw.strip():
            return []
        return json.loads(raw)

    if args.cmd == "status":
        print(json.dumps(status(vault), indent=2))
        return 0

    if args.cmd == "plan":
        try:
            payload = _load_manifest(args.manifest)
        except (OSError, json.JSONDecodeError) as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
        result = plan(payload, vault)
        if args.format == "channel":
            # plan is operational — only emit if work remains
            if result.get("notify"):
                print(
                    f"**Meet plan:** {result['to_fetch_count']} to fetch, "
                    f"{result['unchanged_count']} unchanged, "
                    f"{result['skip_count']} skip (video/empty mime)."
                )
            # else: quiet (exit 0, no stdout) — no-spam
        else:
            print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if args.cmd == "ingest":
        try:
            payload = _load_manifest(args.manifest)
        except (OSError, json.JSONDecodeError) as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
        result = ingest(payload, vault, auto_promote=not args.no_promote)
        if args.format == "channel":
            summary = result.get("channel_summary") or ""
            if summary:
                print(summary, end="" if summary.endswith("\n") else "\n")
            # quiet: print nothing when nothing new
        else:
            print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
