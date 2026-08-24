"""Read-only Auto Fleet / turo_inbox brief for Helm (#295).

Mirror FitDash agent-read (#293): service-token HTTP or a published
snapshot file. Payload is dump / email-ingest derived — no invented trips.
Invoice-ready Google Tasks stay on ``/api/turo-tasks``. No venue keys.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from . import car_cards, fleet, service_auth, turo_inbox, turo_media
except ImportError:  # script / unittest path
    import car_cards  # type: ignore
    import fleet  # type: ignore
    import service_auth  # type: ignore
    import turo_inbox  # type: ignore
    import turo_media  # type: ignore

PKG_DIR = Path(__file__).resolve().parent
DATA_DIR = PKG_DIR / "data"
CONFIG_SNAPSHOT = Path.home() / ".config" / "auto-fleet" / "agent_fleet.json"
SHIPPED_SNAPSHOT = DATA_DIR / "agent_fleet_latest.json"
SNAPSHOT_NAME = "agent_fleet.json"

BOOKING_KEEP = (
    "message_id",
    "subject",
    "from",
    "date",
    "kind",
    "status",
    "trip_id",
    "guest",
    "vehicle",
    "start",
    "end",
    "pickup",
    "drop_off",
    "payout",
    "phone",
    "extra_drivers",
    "guest_asks",
    "host_label",
    "unit_id",
    "claims_photos",
    "photos_missing",
    "phase",
)
ATTACHMENT_KEEP = (
    "filename",
    "mime",
    "size",
    "sha256",
    "relpath",
    "inline",
    "content_id",
)
IDENTITY_KEEP = ("year", "make", "model", "role", "host_label")
SECRET_FIELD_MARKERS = (
    "dimo_api_key",
    "dimo_private_key",
    "dimo_developer_jwt",
    "gmail_refresh_token",
    "gmail_client_secret",
    "google_tasks_refresh_token",
    "google_tasks_client_secret",
    "auto_fleet_service_token",
    "private_key",
    "client_secret",
)
# Absolute dump paths and env files must not leak in the export.
_ABS_HOME_RE = re.compile(r"(?:/home/|/Users/)[^\s\"']+/\.config/auto-fleet/")
_SECRET_LEAK_RE = re.compile(
    r"("
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|AKIA[0-9A-Z]{16}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|DIMO_API_KEY"
    r"|DIMO_PRIVATE_KEY"
    r"|DIMO_DEVELOPER_JWT"
    r"|GMAIL_REFRESH_TOKEN"
    r"|GMAIL_CLIENT_SECRET"
    r"|GOOGLE_TASKS_"
    r"|AUTO_FLEET_SERVICE_TOKEN"
    r")",
    re.MULTILINE,
)
# Lender account numbers from the shipped roster — never copy into the packet.
_ROSTER_ACCOUNT_RE = re.compile(r"\b(?:111088614673|6201049298207|28312877)\b")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_text(value: Any) -> Any:
    """Replace expanded home config paths so the packet stays seat-safe."""
    if not isinstance(value, str):
        return value
    text = value.replace(str(turo_inbox.CONFIG_INBOX), "~/.config/auto-fleet/turo_inbox.json")
    text = _ABS_HOME_RE.sub("~/.config/auto-fleet/", text)
    return text


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def snapshot_path(
    explicit: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    if explicit is not None:
        return Path(explicit)
    environ = env if env is not None else os.environ
    override = (environ.get("AUTO_FLEET_AGENT_SNAPSHOT") or "").strip()
    if override:
        return Path(override).expanduser()
    if CONFIG_SNAPSHOT.is_file():
        return CONFIG_SNAPSHOT
    return SHIPPED_SNAPSHOT


def snapshot_present(
    explicit: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    environ = env if env is not None else os.environ
    if (environ.get("AUTO_FLEET_AGENT_SNAPSHOT_JSON") or "").strip():
        return True
    path = snapshot_path(explicit, environ)
    return path.is_file() and path.stat().st_size > 2


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def sanitize_attachment(raw: Any) -> Optional[dict[str, Any]]:
    """Keep metadata only. Drop absolute path and image bytes."""
    rec = turo_media.attachment_public(raw) if isinstance(raw, dict) else None
    if not rec:
        return None
    out: dict[str, Any] = {}
    for key in ATTACHMENT_KEEP:
        if rec.get(key) is not None:
            out[key] = rec[key]
    return out or None


def sanitize_attachments(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        rec = sanitize_attachment(item)
        if rec:
            out.append(rec)
    return out


def sanitize_booking(raw: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in BOOKING_KEEP:
        if raw.get(key) is not None:
            out[key] = raw[key]
    atts = sanitize_attachments(raw.get("attachments"))
    if atts:
        out["attachments"] = atts
    return out


def sanitize_photo(raw: Mapping[str, Any]) -> dict[str, Any]:
    out = sanitize_booking(raw)
    atts = sanitize_attachments(raw.get("attachments"))
    if atts:
        out["attachments"] = atts
    return out


def identity_public(unit: Mapping[str, Any]) -> dict[str, Any]:
    ident = fleet.identity_for(unit)
    return {key: ident.get(key) for key in IDENTITY_KEEP}


def _using_shipped_inbox(inbox_path: Path | None) -> bool:
    if inbox_path is None:
        return True
    try:
        return Path(inbox_path).resolve() == (DATA_DIR / turo_inbox.DEFAULT_INBOX_NAME).resolve()
    except OSError:
        return False


def _inbox_age_s(
    inbox_path: Path | None,
    *,
    as_of: Any = None,
    now: Optional[datetime] = None,
) -> Optional[float]:
    clock = now or datetime.now(timezone.utc)
    stamped = _parse_ts(as_of)
    if stamped is not None:
        return max(0.0, (clock - stamped).total_seconds())
    if inbox_path is None:
        return None
    path = Path(inbox_path)
    if not path.is_file():
        return None
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None
    return max(0.0, (clock - mtime).total_seconds())


def _stale_for(
    *,
    inbox_state: str,
    inbox_path: Path | None,
    age_s: Optional[float],
    poll_s: int,
    source: str,
) -> tuple[bool, Optional[str]]:
    if source == "empty_fixture" or _using_shipped_inbox(inbox_path):
        return True, "prism/writer dark — shipped empty fixture, no invented trips"
    if inbox_state in ("unconfigured", "error"):
        return True, (
            "prism/writer dark — no live turo_inbox dump"
            if inbox_state == "unconfigured"
            else "turo_inbox parse error — empty bookings, not invented trips"
        )
    if source == "snapshot" and age_s is not None and age_s > (2 * poll_s):
        return True, "published snapshot older than two writer polls"
    if age_s is not None and age_s > (2 * poll_s) and source == "inbox":
        return True, "turo_inbox dump older than two writer polls"
    return False, None


def secret_leaks(payload: Mapping[str, Any]) -> list[str]:
    """Names of leak classes found in a packet. Empty = clean."""
    blob = json.dumps(payload, default=str)
    found: list[str] = []
    if _SECRET_LEAK_RE.search(blob):
        found.append("secret_marker")
    if _ABS_HOME_RE.search(blob):
        found.append("config_home_path")
    if _ROSTER_ACCOUNT_RE.search(blob):
        found.append("lender_account")
    lowered = blob.lower()
    for marker in SECRET_FIELD_MARKERS:
        if marker in lowered:
            found.append(marker)
    # Absolute attachment paths / raw image bytes never belong in the export.
    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("path") and str(node.get("path")).startswith(("/", "\\")):
                found.append("absolute_path")
            if node.get("data") or node.get("bytes"):
                found.append("image_bytes")
            if node.get("vin"):
                found.append("vin")
            if node.get("account"):
                found.append("account")
            for child in node.values():
                _walk(child)
        elif isinstance(node, list):
            for child in node:
                _walk(child)

    _walk(payload)
    # Dedup while keeping order
    seen: set[str] = set()
    out: list[str] = []
    for item in found:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def assert_no_secrets(payload: Mapping[str, Any]) -> None:
    leaks = secret_leaks(payload)
    if leaks:
        raise RuntimeError(f"refusing to publish; secret-like payload in {leaks}")


def empty_stale_packet(
    *,
    units: Sequence[Mapping[str, Any]] | None = None,
    reason: str = "prism/writer dark — no snapshot, no invented trips",
    now: str | None = None,
) -> dict[str, Any]:
    roster_units = [
        u
        for u in (units if units is not None else fleet.load_roster()["units"])
        if isinstance(u, dict) and u.get("id")
    ]
    inbox_status = (
        "no live dump / snapshot — empty bookings, not invented trips. "
        + turo_inbox.PAYOUT_DEST_NOTE
    )
    assembled = []
    for unit in roster_units:
        assembled.append(
            {
                "id": unit["id"],
                "identity": identity_public(unit),
                "bookings": [],
                "schedule": [],
                "photos": [],
                "inbox_status": inbox_status,
            }
        )
    return {
        "ok": True,
        "read_only": True,
        "as_of": now or _now(),
        "stale": True,
        "stale_reason": reason,
        "source": "empty",
        "unit_count": len(assembled),
        "units": assembled,
        "unmatched": [],
        "unmatched_photos": [],
        "inbox": {
            "state": "unconfigured",
            "status": inbox_status,
            "kind": "missing",
            "refreshed_at": None,
            "forward_since": turo_inbox.FORWARD_SINCE_ISO,
            "poll_interval_s": turo_inbox.POLL_INTERVAL_S,
            "message_count": 0,
            "photo_count": 0,
            "payout_destination": turo_inbox.PAYOUT_DESTINATION,
        },
    }


def export_agent_fleet(
    *,
    roster_path: Path | None = None,
    inbox_path: Path | None = None,
    now: str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Slice dump-derived units + bookings paint + inbox. No GT / DIMO keys."""
    environ = env if env is not None else os.environ
    roster = fleet.load_roster(roster_path)
    units = [u for u in roster["units"] if isinstance(u, dict) and u.get("id")]
    resolved = turo_inbox.resolve_inbox_path(inbox_path, DATA_DIR, env=environ)
    turo = turo_inbox.turo_payload(inbox_path=resolved, units=units, env=environ)
    now_s = now or _now()
    poll_s = int(turo.get("poll_interval_s") or turo_inbox.POLL_INTERVAL_S)
    inbox_state = str(turo.get("inbox_state") or "empty")
    age_s = _inbox_age_s(resolved, now=_parse_ts(now_s))
    source = "inbox"
    if inbox_state == "unconfigured" or _using_shipped_inbox(resolved):
        source = "empty_fixture" if _using_shipped_inbox(resolved) else "empty"
    stale, stale_reason = _stale_for(
        inbox_state=inbox_state,
        inbox_path=resolved,
        age_s=age_s,
        poll_s=poll_s,
        source=source,
    )
    assembled = []
    for unit in units:
        turo_unit = turo_inbox.turo_for_unit(str(unit["id"]), turo)
        bookings = [sanitize_booking(b) for b in (turo_unit.get("bookings") or [])]
        photos = [sanitize_photo(p) for p in (turo_unit.get("photos") or [])]
        assembled.append(
            {
                "id": unit["id"],
                "identity": identity_public(unit),
                "bookings": bookings,
                "schedule": car_cards.schedule_for_bookings(bookings, now_s),
                "photos": photos,
                "inbox_status": _public_text(turo_unit.get("inbox_status")),
            }
        )
    packet = {
        "ok": True,
        "read_only": True,
        "as_of": now_s,
        "stale": stale,
        "stale_reason": stale_reason,
        "source": source,
        "unit_count": len(assembled),
        "units": assembled,
        "unmatched": [sanitize_booking(b) for b in (turo.get("unmatched") or [])],
        "unmatched_photos": [
            sanitize_photo(p) for p in (turo.get("unmatched_photos") or [])
        ],
        "inbox": {
            "state": inbox_state,
            "status": _public_text(turo.get("inbox_status")),
            "kind": turo.get("inbox_kind"),
            "refreshed_at": turo.get("refreshed_at"),
            "forward_since": turo.get("forward_since"),
            "poll_interval_s": poll_s,
            "message_count": turo.get("message_count", 0),
            "photo_count": len(turo.get("photo_messages") or []),
            "payout_destination": turo.get("payout_destination")
            or turo_inbox.PAYOUT_DESTINATION,
        },
    }
    assert_no_secrets(packet)
    return packet


def write_snapshot(packet: Mapping[str, Any], dest: Path) -> Path:
    assert_no_secrets(packet)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(json.dumps(packet, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(dest)
    try:
        dest.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return dest


def _load_snapshot_json(raw: str) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    if data.get("read_only") is False:
        return None
    leaks = secret_leaks(data)
    if leaks:
        return None
    return data


def load_snapshot(
    explicit: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Optional[dict[str, Any]]:
    """Published packet if present and clean. None = deny / missing."""
    environ = env if env is not None else os.environ
    inline = (environ.get("AUTO_FLEET_AGENT_SNAPSHOT_JSON") or "").strip()
    if inline:
        return _load_snapshot_json(inline)
    path = snapshot_path(explicit, environ)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return _load_snapshot_json(raw)


def publish_from_inbox(
    *,
    inbox_path: Path | None = None,
    roster_path: Path | None = None,
    dest: Path | None = None,
    env: Mapping[str, str] | None = None,
    now: str | None = None,
) -> Path:
    environ = env if env is not None else os.environ
    packet = export_agent_fleet(
        roster_path=roster_path,
        inbox_path=inbox_path,
        now=now,
        env=environ,
    )
    if dest is None:
        override = (environ.get("AUTO_FLEET_AGENT_SNAPSHOT") or "").strip()
        if override:
            dest = Path(override).expanduser()
        elif inbox_path is not None:
            dest = Path(inbox_path).expanduser().resolve().parent / SNAPSHOT_NAME
        else:
            dest = CONFIG_SNAPSHOT
    packet = dict(packet)
    packet["source"] = "snapshot"
    packet["published_at"] = now or _now()
    return write_snapshot(packet, dest)


def maybe_publish_from_inbox(
    inbox_path: Path,
    env: Mapping[str, str] | None = None,
) -> Optional[Path]:
    """Best-effort publish after turo_gmail write. Never fails the writer."""
    environ = env if env is not None else os.environ
    flag = (environ.get("AUTO_FLEET_PUBLISH_AGENT") or "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return None
    try:
        return publish_from_inbox(inbox_path=inbox_path, env=environ)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[auto-fleet] agent snapshot publish skipped: {exc}\n")
        return None


def serve_agent_fleet(
    *,
    roster_path: Path | None = None,
    inbox_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """HTTP body after auth. Live dump if present; else last snapshot; else empty."""
    environ = env if env is not None else os.environ
    resolved = turo_inbox.resolve_inbox_path(inbox_path, DATA_DIR, env=environ)
    live_dump = (
        resolved is not None
        and Path(resolved).exists()
        and not _using_shipped_inbox(resolved)
    )
    if not live_dump:
        snap = load_snapshot(env=environ)
        if snap is not None:
            out = dict(snap)
            out.setdefault(
                "stale_reason",
                "prism/writer dark — serving last published snapshot",
            )
            out["stale"] = True
            out["source"] = "snapshot"
            return out
        if resolved is None or not Path(resolved).exists():
            return empty_stale_packet(now=now)
    return export_agent_fleet(
        roster_path=roster_path,
        inbox_path=inbox_path,
        now=now,
        env=environ,
    )


def handle_agent_fleet_http(
    headers,
    client_host: Optional[str] = None,
    *,
    roster_path: Path | None = None,
    inbox_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Allow (token / loopback) vs deny. Snapshot is the no-Tailscale packet."""
    if not service_auth.service_auth_ok(headers, client_host):
        return 401, service_auth.service_auth_denied("agents")
    try:
        return 200, serve_agent_fleet(
            roster_path=roster_path,
            inbox_path=inbox_path,
            env=env,
        )
    except Exception as exc:  # noqa: BLE001
        return 500, {"ok": False, "error": str(exc)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Auto Fleet / turo_inbox brief for Helm"
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--export",
        action="store_true",
        help="Print dump-derived brief (JSON) to stdout",
    )
    action.add_argument(
        "--publish",
        action="store_true",
        help="Write sanitized snapshot (default ~/.config/auto-fleet/agent_fleet.json)",
    )
    action.add_argument(
        "--read",
        action="store_true",
        help="Read published snapshot (shared path / shipped fixture)",
    )
    parser.add_argument("--turo-inbox", type=Path, default=None)
    parser.add_argument("--roster", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None, help="Snapshot path")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.read:
        packet = load_snapshot(args.out)
        if packet is None:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "snapshot_missing",
                        "message": (
                            "No published snapshot. Set AUTO_FLEET_AGENT_SNAPSHOT "
                            "or run --publish after the inbox dump."
                        ),
                    }
                )
            )
            return 2
        print(json.dumps(packet, indent=2, default=str))
        return 0
    if args.publish:
        dest = publish_from_inbox(
            inbox_path=args.turo_inbox,
            roster_path=args.roster,
            dest=args.out,
        )
        print(f"published {dest}")
        return 0
    packet = export_agent_fleet(
        roster_path=args.roster,
        inbox_path=args.turo_inbox,
    )
    print(json.dumps(packet, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
