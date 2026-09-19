"""Write a Turo inbox dump the dashboard can parse.

The :8796 process never talks to Gmail. A Pi 15m systemd timer (or an
operator) writes ~/.config/auto-fleet/turo_inbox.json:

  python3 -m auto-fleet.turo_gmail --fetch
  python3 auto-fleet/turo_gmail.py --from-json dump.json
  python3 auto-fleet/turo_gmail.py --from-json -

Query: after:2026/08/18 from:(turo.com OR mail.turo.com OR transactional.turo.com)
Forward-only — do not dump historical / label:Turo 2024 mail.

Default output: ~/.config/auto-fleet/turo_inbox.json (mode 600, not git).
Image MIME parts → ~/.config/auto-fleet/turo_inbox_media/ (not git).
Missing Gmail creds → source=gmail_unconfigured (keeps last-good messages).
Fetch errors → source=gmail_error (keeps last-good messages; never wipe a good dump).
Auth death (invalid_grant / refresh fail / missing token) → AUTH_DEAD + ntfy
+ non-zero exit. Never report success/empty. Remint is ops, not this writer.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

try:
    from . import turo_inbox, turo_media
except ImportError:  # script path
    import turo_inbox  # type: ignore
    import turo_media  # type: ignore

DEFAULT_OUT = turo_inbox.CONFIG_INBOX
GMAIL_QUERY = turo_inbox.GMAIL_QUERY
GMAIL_INBOX_ADDR = turo_inbox.GMAIL_INBOX_ADDR
DEFAULT_TOKEN_PATH = Path.home() / ".config" / "auto-fleet" / "gmail-token.json"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_RESULTS = 50
AUTH_DEAD_NAME = "AUTH_DEAD"
AUTH_DEAD_ALERT = "panamerica Gmail feeder blind — remint needed."
AUTH_DEAD_TITLE = "auto-fleet AUTH_DEAD"
NTFY_HOST = "https://ntfy.sh"
EXIT_AUTH_DEAD = 2
EXIT_FETCH_ERROR = 1
REMINDER_AFTER = timedelta(hours=24)
AUTH_DEAD_MARKERS = (
    "invalid_grant",
    "invalid_token",
    "unauthorized",
    "refresherror",
    "token has been expired or revoked",
    "account has been deleted",
    "token endpoint returned no access_token",
)

HttpFn = Callable[[str, Optional[bytes], Mapping[str, str]], Any]
NtfyFn = Callable[..., dict[str, Any]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_messages(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [m for m in raw if isinstance(m, dict)]
    if isinstance(raw, dict):
        msgs = raw.get("messages")
        if isinstance(msgs, list):
            return [m for m in msgs if isinstance(m, dict)]
        if any(k in raw for k in ("subject", "body", "from")):
            return [raw]
    raise ValueError("expected a list of messages or an object with messages[]")


def write_dump(
    messages: Sequence[Mapping[str, Any]],
    path: Path | None = None,
    *,
    inbox: str = GMAIL_INBOX_ADDR,
    query: str = GMAIL_QUERY,
    source: str = "gmail_dump",
    note: str | None = None,
    error: str | None = None,
    media_dir: Path | None = None,
    auth_dead: bool = False,
    auth_dead_reason: str | None = None,
) -> Path:
    dest = Path(path) if path is not None else DEFAULT_OUT
    dest.parent.mkdir(parents=True, exist_ok=True)
    media = media_dir if media_dir is not None else turo_media.media_dir_for(dest)
    payload: dict[str, Any] = {
        "as_of": _now(),
        "source": source,
        "inbox": inbox,
        "query": query,
        "forward_since": turo_inbox.FORWARD_SINCE_ISO,
        "poll_interval_s": turo_inbox.POLL_INTERVAL_S,
        "messages": [dict(m) for m in messages],
        "auth_dead": bool(auth_dead),
    }
    if media is not None:
        payload["media_dir"] = str(media)
    if note:
        payload["note"] = note
    if error:
        payload["error"] = error
    if auth_dead_reason:
        payload["auth_dead_reason"] = auth_dead_reason
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    return dest


def _prior_dump_messages(path: Path) -> list[dict[str, Any]]:
    """Keep last-good messages when Gmail fetch/auth fails. Never invent trips."""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return [m for m in data if isinstance(m, dict)]
    if isinstance(data, dict):
        msgs = data.get("messages")
        if isinstance(msgs, list):
            return [m for m in msgs if isinstance(m, dict)]
    return []


def auth_dead_path_for(dump: Path) -> Path:
    return Path(dump).resolve().parent / AUTH_DEAD_NAME


def sanitize_auth_error(exc: BaseException | str | None) -> str:
    """First-line public error. Never copy tokens, env keys, or home paths."""
    text = str(exc or "")
    lowered = text.lower()
    if "invalid_grant" in lowered:
        return "invalid_grant"
    if "invalid_token" in lowered:
        return "invalid_token"
    if "unauthorized" in lowered or "http 401" in lowered:
        return "unauthorized"
    line = text.splitlines()[0].strip() if text else "gmail_auth_failed"
    for needle in (
        "refresh_token",
        "client_secret",
        "client_id",
        "bearer ",
        "gmail_refresh_token",
        "gmail_client_secret",
    ):
        if needle in line.lower():
            return "gmail_auth_failed"
    return line[:80]


def is_auth_dead_error(exc: BaseException | str | None) -> bool:
    text = str(exc or "").lower()
    if not text:
        return False
    if any(marker in text for marker in AUTH_DEAD_MARKERS):
        return True
    if "http 401" in text:
        return True
    if "http 400" in text and (
        "oauth2.googleapis.com/token" in text or "token endpoint" in text
    ):
        return True
    return False


def auth_dead_reason(source: str | None, error: str | None) -> str | None:
    src = (source or "").strip()
    if src == "gmail_unconfigured":
        return "missing_token"
    if src == "gmail_error" and is_auth_dead_error(error):
        cleaned = sanitize_auth_error(error)
        if cleaned == "invalid_grant":
            return "invalid_grant"
        return "refresh_fail"
    return None


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def clear_auth_dead(dump: Path) -> None:
    flag = auth_dead_path_for(dump)
    try:
        flag.unlink()
    except OSError:
        pass


def ntfy_topic(env: Mapping[str, str] | None = None) -> str:
    merged = env if env is not None else os.environ
    for key in ("AUTO_FLEET_NTFY_TOPIC", "NTFY_TOPIC", "FCC_NTFY_TOPIC"):
        topic = str(merged.get(key) or "").strip()
        if topic:
            return topic
    return ""


def alerts_enabled(env: Mapping[str, str] | None = None) -> bool:
    merged = env if env is not None else os.environ
    raw = str(merged.get("AUTO_FLEET_GMAIL_ALERT") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _http_post_bytes(
    url: str,
    data: bytes | None,
    headers: Mapping[str, str] | None,
) -> Any:
    req = urllib.request.Request(url, data=data, headers=dict(headers or {}))
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()
        return {"ok": True, "status": getattr(resp, "status", None) or resp.getcode()}


def post_ntfy_auth_dead(
    *,
    topic: str,
    dry_run: bool = False,
    http: HttpFn | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Page ntfy. Topic is a shared secret — never write it into dumps/logs."""
    if not topic:
        return {"ok": True, "notified": False, "skipped": "no-topic"}
    if dry_run or not alerts_enabled(env):
        return {"ok": True, "notified": False, "skipped": "dry-run"}
    headers = {
        "Title": AUTH_DEAD_TITLE,
        "Priority": "5",
        "Tags": "warning,rotating_light",
    }
    merged = env if env is not None else os.environ
    token = (
        str(merged.get("NTFY_TOKEN") or "").strip()
        or str(merged.get("FCC_NTFY_TOKEN") or "").strip()
        or str(merged.get("AUTO_FLEET_NTFY_TOKEN") or "").strip()
    )
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{NTFY_HOST}/{topic}"
    fn = http or _http_post_bytes
    try:
        fn(url, AUTH_DEAD_ALERT.encode("utf-8"), headers)
        return {"ok": True, "notified": True, "title": AUTH_DEAD_TITLE}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "notified": False,
            "error": sanitize_auth_error(exc),
        }


def record_auth_dead(
    dump: Path,
    reason: str,
    *,
    env: Mapping[str, str] | None = None,
    ntfy: NtfyFn | None = None,
    http: HttpFn | None = None,
    now: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write AUTH_DEAD (mode 600) and ntfy on transition / 24h reminder."""
    flag = auth_dead_path_for(dump)
    stamp = now or _now()
    prev = _load_json(flag)
    last_alert = str(prev.get("last_alert_at") or "")
    should_alert = True
    if last_alert:
        try:
            prev_dt = datetime.fromisoformat(last_alert.replace("Z", "+00:00"))
            now_dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if now_dt - prev_dt < REMINDER_AFTER:
                should_alert = False
        except ValueError:
            should_alert = True
    topic = ntfy_topic(env)
    ntfy_out: dict[str, Any]
    if not should_alert:
        ntfy_out = {"ok": True, "notified": False, "skipped": "cooldown"}
    elif ntfy is not None:
        ntfy_out = ntfy(
            topic=topic,
            title=AUTH_DEAD_TITLE,
            text=AUTH_DEAD_ALERT,
            dry_run=dry_run,
        )
    else:
        ntfy_out = post_ntfy_auth_dead(
            topic=topic, dry_run=dry_run, http=http, env=env
        )
    documented = ntfy_out.get("skipped") in {"dry-run", "no-topic", "cooldown"}
    posted = bool(ntfy_out.get("notified"))
    # Advance cooldown only when a page went out or the documented skip
    # (no-topic / dry-run) already counted as the cycle's alert. Failed
    # POSTs retry next 15m.
    if posted or (should_alert and documented):
        last_alert_stamp = stamp
    else:
        last_alert_stamp = last_alert
    payload = {
        "state": "AUTH_DEAD",
        "at": stamp,
        "reason": reason,
        "alert": AUTH_DEAD_ALERT,
        "last_alert_at": last_alert_stamp,
        "last_alert_kind": (
            "ntfy" if posted else ntfy_out.get("skipped") or "stderr"
        ),
    }
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(flag, 0o600)
    except OSError:
        pass
    sys.stderr.write(f"AUTH_DEAD {reason} — {AUTH_DEAD_ALERT}\n")
    return {"flag": str(flag), "reason": reason, "ntfy": ntfy_out, "alerted": should_alert}


def _file_env(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    try:
        from envfile import load_env_file
    except ImportError:
        try:
            from .envfile import load_env_file  # type: ignore
        except ImportError:
            return {}
    return load_env_file(path)


def resolve_gmail_creds(
    *,
    env: Mapping[str, str] | None = None,
    token_path: Path | None = None,
    env_file: Path | None = None,
) -> Optional[dict[str, str]]:
    """Return {refresh_token, client_id, client_secret} or None.

    Sources, first win: process env, ~/.config/auto-fleet/env, token JSON.
    """
    merged: dict[str, str] = {}
    merged.update(_file_env(env_file if env_file is not None else Path.home() / ".config" / "auto-fleet" / "env"))
    if env:
        merged.update({k: v for k, v in env.items() if v})

    refresh = (
        merged.get("GMAIL_REFRESH_TOKEN")
        or merged.get("AUTO_FLEET_GMAIL_REFRESH_TOKEN")
        or ""
    ).strip()
    client_id = (
        merged.get("GMAIL_CLIENT_ID")
        or merged.get("GOOGLE_CLIENT_ID")
        or merged.get("AUTO_FLEET_GMAIL_CLIENT_ID")
        or ""
    ).strip()
    client_secret = (
        merged.get("GMAIL_CLIENT_SECRET")
        or merged.get("GOOGLE_CLIENT_SECRET")
        or merged.get("AUTO_FLEET_GMAIL_CLIENT_SECRET")
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


def _http_json(
    url: str,
    data: bytes | None = None,
    headers: Mapping[str, str] | None = None,
) -> Any:
    req = urllib.request.Request(url, data=data, headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url.split('?', 1)[0]}: {body[:240]}") from exc
    if not raw:
        return {}
    return json.loads(raw)


def _b64url_decode_bytes(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _b64url_decode(data: str) -> str:
    return _b64url_decode_bytes(data).decode("utf-8", errors="replace")


def _iter_payload_parts(payload: Mapping[str, Any]):
    yield payload
    parts = payload.get("parts") if isinstance(payload.get("parts"), list) else []
    for part in parts:
        if isinstance(part, dict):
            yield from _iter_payload_parts(part)


def _payload_text(payload: Mapping[str, Any]) -> str:
    mime = str(payload.get("mimeType") or "")
    if mime.lower().startswith("image/"):
        return ""
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    data = str(body.get("data") or "") if isinstance(body, dict) else ""
    parts = payload.get("parts") if isinstance(payload.get("parts"), list) else []
    if data and (mime.startswith("text/plain") or mime.startswith("text/html") or not parts):
        return _b64url_decode(data)
    texts: list[str] = []
    htmls: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        child_mime = str(part.get("mimeType") or "")
        if child_mime.lower().startswith("image/"):
            continue
        text = _payload_text(part)
        if not text:
            continue
        if child_mime.startswith("text/html"):
            htmls.append(text)
        else:
            texts.append(text)
    if texts:
        return "\n".join(texts)
    return "\n".join(htmls)


def _headers_map(payload: Mapping[str, Any]) -> dict[str, str]:
    headers = payload.get("headers")
    out: dict[str, str] = {}
    if not isinstance(headers, list):
        return out
    for item in headers:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip().lower()
        if name:
            out[name] = str(item.get("value") or "")
    return out


def _part_disposition(part: Mapping[str, Any]) -> str:
    headers = _headers_map(part)
    return str(headers.get("content-disposition") or "").lower()


def _part_content_id(part: Mapping[str, Any]) -> str:
    headers = _headers_map(part)
    return str(headers.get("content-id") or "").strip()


def _fetch_gmail_attachment(
    fn: HttpFn,
    auth: Mapping[str, str],
    message_id: str,
    attachment_id: str,
) -> bytes:
    url = (
        f"{GMAIL_API}/messages/{urllib.parse.quote(str(message_id))}"
        f"/attachments/{urllib.parse.quote(str(attachment_id))}"
    )
    raw = fn(url, None, auth) or {}
    data = str((raw or {}).get("data") or "") if isinstance(raw, dict) else ""
    if not data:
        return b""
    return _b64url_decode_bytes(data)


def extract_gmail_images(
    raw: Mapping[str, Any],
    media_dir: Path | None,
    *,
    http: HttpFn | None = None,
    auth: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Persist image MIME parts from a Gmail users.messages resource.

    Inline body.data is written locally. attachmentId parts are fetched
    only when http + auth are provided (the live --fetch path). Missing
    bytes stay missing — no invented photos.
    """
    if media_dir is None:
        return []
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    if not isinstance(payload, dict):
        return []
    message_id = str(raw.get("id") or raw.get("threadId") or "msg")
    out: list[dict[str, Any]] = []
    index = 0
    for part in _iter_payload_parts(payload):
        mime = str(part.get("mimeType") or "")
        filename = str(part.get("filename") or "")
        cid = _part_content_id(part)
        if not turo_media.keep_image_part(mime, filename=filename, content_id=cid):
            continue
        body = part.get("body") if isinstance(part.get("body"), dict) else {}
        data_b64 = str(body.get("data") or "") if isinstance(body, dict) else ""
        att_id = str(body.get("attachmentId") or "") if isinstance(body, dict) else ""
        blob = b""
        if data_b64:
            try:
                blob = _b64url_decode_bytes(data_b64)
            except Exception:  # noqa: BLE001
                blob = b""
        elif att_id and http is not None and auth is not None:
            try:
                blob = _fetch_gmail_attachment(http, auth, message_id, att_id)
            except Exception:  # noqa: BLE001
                blob = b""
        if not blob:
            continue
        index += 1
        rec = turo_media.write_image_bytes(
            media_dir,
            message_id,
            blob,
            filename=filename,
            mime=mime,
            index=index,
            inline="inline" in _part_disposition(part) or bool(cid),
            content_id=cid or None,
        )
        if rec:
            out.append(rec)
    return out


def gmail_message_to_record(
    raw: Mapping[str, Any],
    *,
    media_dir: Path | None = None,
    http: HttpFn | None = None,
    auth: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    headers = _headers_map(payload if isinstance(payload, dict) else {})
    body = turo_inbox.flatten_mail_text(
        _payload_text(payload if isinstance(payload, dict) else {})
    )
    snippet = turo_inbox.flatten_mail_text(str(raw.get("snippet") or ""))
    rec: dict[str, Any] = {
        "id": raw.get("id") or raw.get("threadId"),
        "from": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "body": body or snippet,
        "snippet": snippet,
    }
    attachments = extract_gmail_images(
        raw, media_dir, http=http, auth=auth
    )
    if attachments:
        rec["attachments"] = attachments
    return rec


def materialize_message_images(
    raw: Mapping[str, Any],
    media_dir: Path | None,
    *,
    http: HttpFn | None = None,
    auth: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Normalize one dump message, writing image bytes when present.

    Gmail API-shaped objects (payload.parts) are converted. Already-flat
    records keep text and persist any attachments[].data / path.
    """
    if isinstance(raw.get("payload"), dict):
        return gmail_message_to_record(raw, media_dir=media_dir, http=http, auth=auth)
    rec = dict(raw)
    message_id = str(rec.get("id") or rec.get("message_id") or rec.get("message-id") or "msg")
    existing = rec.get("attachments")
    if media_dir is not None and isinstance(existing, list) and existing:
        written: list[dict[str, Any]] = []
        for i, item in enumerate(existing, start=1):
            if not isinstance(item, dict):
                continue
            att = turo_media.materialize_attachment_data(
                item, media_dir, message_id, index=i
            )
            if att:
                written.append(att)
        if written:
            rec["attachments"] = written
        else:
            rec.pop("attachments", None)
    elif "attachments" in rec:
        atts = turo_media.normalize_attachments(rec.get("attachments"))
        if atts:
            rec["attachments"] = atts
        else:
            rec.pop("attachments", None)
    return rec


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


def fetch_gmail_messages(
    query: str,
    creds: Mapping[str, str],
    *,
    http: HttpFn | None = None,
    max_results: int = MAX_RESULTS,
    media_dir: Path | None = None,
) -> list[dict[str, Any]]:
    fn = http or _http_json
    access = refresh_access_token(creds, http=fn)
    auth = {"Authorization": f"Bearer {access}"}
    list_url = (
        f"{GMAIL_API}/messages?"
        + urllib.parse.urlencode({"q": query, "maxResults": max_results})
    )
    listed = fn(list_url, None, auth) or {}
    refs = listed.get("messages") if isinstance(listed, dict) else None
    if not isinstance(refs, list):
        return []
    out: list[dict[str, Any]] = []
    for ref in refs:
        if not isinstance(ref, dict) or not ref.get("id"):
            continue
        get_url = (
            f"{GMAIL_API}/messages/{urllib.parse.quote(str(ref['id']))}"
            f"?{urllib.parse.urlencode({'format': 'full'})}"
        )
        raw = fn(get_url, None, auth)
        if isinstance(raw, dict):
            out.append(
                gmail_message_to_record(
                    raw, media_dir=media_dir, http=fn, auth=auth
                )
            )
    return out


def fetch_and_write(
    path: Path | None = None,
    *,
    query: str = GMAIL_QUERY,
    inbox: str = GMAIL_INBOX_ADDR,
    env: Mapping[str, str] | None = None,
    token_path: Path | None = None,
    env_file: Path | None = None,
    http: HttpFn | None = None,
    ntfy: NtfyFn | None = None,
    alert: bool = True,
    dry_run_alert: bool = False,
) -> Path:
    dest = Path(path) if path is not None else DEFAULT_OUT
    creds = resolve_gmail_creds(env=env, token_path=token_path, env_file=env_file)
    token_hint = str(token_path or DEFAULT_TOKEN_PATH)
    prior = _prior_dump_messages(dest)
    kept_bit = (
        f"Kept last-good messages ({len(prior)}). "
        if prior
        else ""
    )
    alert_env = dict(_file_env(env_file if env_file is not None else Path.home() / ".config" / "auto-fleet" / "env"))
    if env:
        alert_env.update({k: v for k, v in env.items() if v})

    def _dead(source: str, reason: str, error: str | None = None) -> Path:
        if source == "gmail_error":
            note = (
                "Pi writer: Gmail AUTH_DEAD. "
                f"{kept_bit}"
                "Last-good dump held, not invented trips."
            )
        else:
            note = (
                "Pi writer: no Gmail refresh token. Put gmail.readonly OAuth at "
                f"{token_hint} or GMAIL_REFRESH_TOKEN + GMAIL_CLIENT_ID + "
                "GMAIL_CLIENT_SECRET in ~/.config/auto-fleet/env. "
                f"{kept_bit}"
                "Empty bookings, not invented trips."
            )
        path_out = write_dump(
            prior,
            dest,
            inbox=inbox,
            query=query,
            source=source,
            note=note,
            error=error,
            media_dir=turo_media.media_dir_for(dest) if source == "gmail_error" else None,
            auth_dead=True,
            auth_dead_reason=reason,
        )
        if alert:
            record_auth_dead(
                dest,
                reason,
                env=alert_env,
                ntfy=ntfy,
                dry_run=dry_run_alert,
            )
        return path_out

    if creds is None:
        return _dead("gmail_unconfigured", "missing_token")
    media = turo_media.media_dir_for(dest)
    try:
        messages = fetch_gmail_messages(
            query, creds, http=http, media_dir=media
        )
    except Exception as exc:  # noqa: BLE001
        public = sanitize_auth_error(exc)
        if is_auth_dead_error(exc):
            return _dead("gmail_error", auth_dead_reason("gmail_error", str(exc)) or "refresh_fail", public)
        return write_dump(
            prior,
            dest,
            inbox=inbox,
            query=query,
            source="gmail_error",
            note=(
                "Pi writer: Gmail fetch failed. "
                f"{kept_bit}"
                "Empty bookings, not invented trips."
            ),
            error=public,
            media_dir=media,
            auth_dead=False,
        )
    clear_auth_dead(dest)
    return write_dump(
        messages,
        dest,
        inbox=inbox,
        query=query,
        source="gmail_api",
        media_dir=media,
        auth_dead=False,
    )


def _writer_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if k.startswith(
            (
                "GMAIL_",
                "GOOGLE_CLIENT_",
                "AUTO_FLEET_GMAIL_",
                "AUTO_FLEET_NTFY_",
                "NTFY_",
                "FCC_NTFY_",
                "AUTO_FLEET_GMAIL_ALERT",
            )
        )
    }


def check_auth(
    *,
    token_path: Path | None = None,
    env_file: Path | None = None,
    env: Mapping[str, str] | None = None,
    http: HttpFn | None = None,
    ntfy: NtfyFn | None = None,
    dump: Path | None = None,
    dry_run_alert: bool = False,
) -> int:
    """Cheap Bot probe. 0 healthy, 2 AUTH_DEAD, 1 other fetch/auth transport error."""
    dest = Path(dump) if dump is not None else DEFAULT_OUT
    creds = resolve_gmail_creds(env=env, token_path=token_path, env_file=env_file)
    alert_env = dict(_file_env(env_file if env_file is not None else Path.home() / ".config" / "auto-fleet" / "env"))
    if env:
        alert_env.update({k: v for k, v in env.items() if v})
    if creds is None:
        record_auth_dead(
            dest,
            "missing_token",
            env=alert_env,
            ntfy=ntfy,
            dry_run=dry_run_alert,
        )
        return EXIT_AUTH_DEAD
    try:
        token = refresh_access_token(creds, http=http)
    except Exception as exc:  # noqa: BLE001
        if is_auth_dead_error(exc):
            record_auth_dead(
                dest,
                auth_dead_reason("gmail_error", str(exc)) or "refresh_fail",
                env=alert_env,
                ntfy=ntfy,
                dry_run=dry_run_alert,
            )
            return EXIT_AUTH_DEAD
        sys.stderr.write(f"gmail check-auth failed: {sanitize_auth_error(exc)}\n")
        return EXIT_FETCH_ERROR
    if not token:
        record_auth_dead(
            dest,
            "refresh_fail",
            env=alert_env,
            ntfy=ntfy,
            dry_run=dry_run_alert,
        )
        return EXIT_AUTH_DEAD
    clear_auth_dead(dest)
    print("gmail auth ok")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--from-json",
        help="Path to a JSON list/object, or '-' for stdin",
    )
    src.add_argument(
        "--fetch",
        action="store_true",
        help="Pull Gmail (AUTH_DEAD + ntfy + exit 2 if OAuth is dead)",
    )
    src.add_argument(
        "--check-auth",
        action="store_true",
        help="Cheap token refresh probe for Bot cron (0 ok, 2 AUTH_DEAD, 1 other)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Dump path (default {DEFAULT_OUT})",
    )
    parser.add_argument("--inbox", default=GMAIL_INBOX_ADDR)
    parser.add_argument(
        "--token",
        type=Path,
        default=DEFAULT_TOKEN_PATH,
        help=f"Gmail OAuth token JSON (default {DEFAULT_TOKEN_PATH})",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Env file for GMAIL_* / AUTO_FLEET_NTFY_TOPIC (default ~/.config/auto-fleet/env)",
    )
    parser.add_argument(
        "--dry-run-alert",
        action="store_true",
        help="Write AUTH_DEAD but do not POST ntfy",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    env = _writer_env()
    if args.check_auth:
        return check_auth(
            token_path=args.token,
            env_file=args.env_file,
            env=env,
            dump=args.out,
            dry_run_alert=args.dry_run_alert,
        )
    if args.fetch:
        dest = fetch_and_write(
            args.out,
            inbox=args.inbox,
            token_path=args.token,
            env_file=args.env_file,
            env=env,
            dry_run_alert=args.dry_run_alert,
        )
        data = json.loads(dest.read_text(encoding="utf-8"))
        n = len(data.get("messages") or [])
        _publish_agent_snapshot(dest)
        reason = auth_dead_reason(data.get("source"), data.get("error"))
        if data.get("auth_dead") or reason:
            # Snapshot published so the dashboard shows AUTH_DEAD. Do not
            # treat last-good as a fresh ingest (no trip-change apply).
            print(
                f"AUTH_DEAD source={data.get('source')} kept={n} "
                f"reason={data.get('auth_dead_reason') or reason}",
                file=sys.stderr,
            )
            return EXIT_AUTH_DEAD
        if data.get("source") == "gmail_error":
            print(
                f"gmail fetch error kept={n} source=gmail_error",
                file=sys.stderr,
            )
            return EXIT_FETCH_ERROR
        print(f"wrote {n} message(s) to {dest} source={data.get('source')}")
        _sync_trip_changes(dest)
        return 0
    if args.from_json == "-":
        raw = json.load(sys.stdin)
    else:
        raw = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
    dest = Path(args.out)
    media = turo_media.media_dir_for(dest)
    messages = [
        materialize_message_images(m, media)
        for m in normalize_messages(raw)
    ]
    dest = write_dump(messages, dest, inbox=args.inbox, media_dir=media)
    n_photos = sum(len(m.get("attachments") or []) for m in messages)
    print(f"wrote {len(messages)} message(s) ({n_photos} photo(s)) to {dest}")
    _publish_agent_snapshot(dest)
    _sync_trip_changes(dest)
    return 0


def _publish_agent_snapshot(inbox_path: Path) -> None:
    try:
        from . import agent_fleet
    except ImportError:  # script path
        import agent_fleet  # type: ignore
    agent_fleet.maybe_publish_from_inbox(inbox_path)


def _sync_trip_changes(inbox_path: Path) -> None:
    try:
        from . import turo_changes
    except ImportError:  # script path
        import turo_changes  # type: ignore
    turo_changes.maybe_sync_from_dump(inbox_path)


if __name__ == "__main__":
    raise SystemExit(main())
