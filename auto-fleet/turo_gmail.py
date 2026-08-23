"""Write a Turo inbox dump the dashboard can parse.

The :8796 process never talks to Gmail. A Pi 15m systemd timer (or an
operator) writes ~/.config/auto-fleet/turo_inbox.json:

  python3 -m auto-fleet.turo_gmail --fetch
  python3 auto-fleet/turo_gmail.py --from-json dump.json
  python3 auto-fleet/turo_gmail.py --from-json -

Query: after:2026/08/18 from:(turo.com OR mail.turo.com OR transactional.turo.com)
Forward-only — do not dump historical / label:Turo 2024 mail.

Default output: ~/.config/auto-fleet/turo_inbox.json (mode 600, not git).
Missing Gmail creds → honest empty dump (source=gmail_unconfigured), exit 0.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

try:
    from . import turo_inbox
except ImportError:  # script path
    import turo_inbox  # type: ignore

DEFAULT_OUT = turo_inbox.CONFIG_INBOX
GMAIL_QUERY = turo_inbox.GMAIL_QUERY
GMAIL_INBOX_ADDR = turo_inbox.GMAIL_INBOX_ADDR
DEFAULT_TOKEN_PATH = Path.home() / ".config" / "auto-fleet" / "gmail-token.json"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_RESULTS = 50

HttpFn = Callable[[str, Optional[bytes], Mapping[str, str]], Any]


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
) -> Path:
    dest = Path(path) if path is not None else DEFAULT_OUT
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "as_of": _now(),
        "source": source,
        "inbox": inbox,
        "query": query,
        "forward_since": turo_inbox.FORWARD_SINCE_ISO,
        "poll_interval_s": turo_inbox.POLL_INTERVAL_S,
        "messages": [dict(m) for m in messages],
    }
    if note:
        payload["note"] = note
    if error:
        payload["error"] = error
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    return dest


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


def _b64url_decode(data: str) -> str:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad).decode("utf-8", errors="replace")


def _payload_text(payload: Mapping[str, Any]) -> str:
    mime = str(payload.get("mimeType") or "")
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    data = str(body.get("data") or "") if isinstance(body, dict) else ""
    parts = payload.get("parts") if isinstance(payload.get("parts"), list) else []
    if data and (mime.startswith("text/plain") or not parts):
        return _b64url_decode(data)
    texts: list[str] = []
    htmls: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        child_mime = str(part.get("mimeType") or "")
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


def gmail_message_to_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    headers = _headers_map(payload if isinstance(payload, dict) else {})
    body = turo_inbox.flatten_mail_text(
        _payload_text(payload if isinstance(payload, dict) else {})
    )
    snippet = turo_inbox.flatten_mail_text(str(raw.get("snippet") or ""))
    return {
        "id": raw.get("id") or raw.get("threadId"),
        "from": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "body": body or snippet,
        "snippet": snippet,
    }


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
            out.append(gmail_message_to_record(raw))
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
) -> Path:
    dest = Path(path) if path is not None else DEFAULT_OUT
    creds = resolve_gmail_creds(env=env, token_path=token_path, env_file=env_file)
    token_hint = str(token_path or DEFAULT_TOKEN_PATH)
    if creds is None:
        return write_dump(
            [],
            dest,
            inbox=inbox,
            query=query,
            source="gmail_unconfigured",
            note=(
                "Pi writer: no Gmail refresh token. Put gmail.readonly OAuth at "
                f"{token_hint} or GMAIL_REFRESH_TOKEN + GMAIL_CLIENT_ID + "
                "GMAIL_CLIENT_SECRET in ~/.config/auto-fleet/env. "
                "Empty bookings, not invented trips."
            ),
        )
    try:
        messages = fetch_gmail_messages(query, creds, http=http)
    except Exception as exc:  # noqa: BLE001
        return write_dump(
            [],
            dest,
            inbox=inbox,
            query=query,
            source="gmail_error",
            note="Pi writer: Gmail fetch failed. Empty bookings, not invented trips.",
            error=str(exc),
        )
    return write_dump(
        messages,
        dest,
        inbox=inbox,
        query=query,
        source="gmail_api",
    )


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
        help="Pull Gmail (or write an honest empty dump if creds are missing)",
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
        help="Env file for GMAIL_* keys (default ~/.config/auto-fleet/env)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.fetch:
        dest = fetch_and_write(
            args.out,
            inbox=args.inbox,
            token_path=args.token,
            env_file=args.env_file,
            env={
                k: v
                for k, v in os.environ.items()
                if k.startswith(("GMAIL_", "GOOGLE_CLIENT_", "AUTO_FLEET_GMAIL_"))
            },
        )
        data = json.loads(dest.read_text(encoding="utf-8"))
        n = len(data.get("messages") or [])
        print(f"wrote {n} message(s) to {dest} source={data.get('source')}")
        return 0
    if args.from_json == "-":
        raw = json.load(sys.stdin)
    else:
        raw = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
    messages = normalize_messages(raw)
    dest = write_dump(messages, args.out, inbox=args.inbox)
    print(f"wrote {len(messages)} message(s) to {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
