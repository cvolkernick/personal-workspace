#!/usr/bin/env python3
"""Bland post-call webhook relay and 904 SMS send route (stdlib only).

POST /<RELAY_PATH> verifies X-Webhook-Signature (hex HMAC-SHA256 of the raw
body, optional "sha256=" prefix) and forwards the raw body to
ALEXANDRA_ALERT_URL. POST /<FWD_PATH> accepts the 904 phone forwarder and
forwards a normalized SMS payload to the same alert URL. POST /<SMSGW_HOOK_PATH>
accepts the SMS Gateway for Android (SMSGate) cloud `sms:received` webhook and
feeds it through the same inbound path (#1054). POST /<RCS_HOOK_PATH>
accepts Google Messages notifications from a notification-listener app on the
904 phone so RCS chats reach the same inbound path, deduped against SMSGate
(#1056). POST /<SEND_PATH> lets Alexandra send one plain SMS back through SMS
Gateway for Android. POST /<FC_PATH> manages first-contact numbers
(add / request / approve / list / revoke, #1100) so the send route can reach a
number that never texted 904. With FC_REQUIRE_APPROVAL=0 (the default) an add
allows the number at once; FC_REQUIRE_APPROVAL=1 restores the per-number
approval gate (Chris approves each number). Turo app notifications posted to
the same RCS route (package com.relayrides.android.relayrides) go to the Turo
relay (#1104): JSONL inbox, Helm webhook, and for urgent messages only a
templated 904 SMS to the one allowed owner number plus a "turo_urgent" push to
ALEXANDRA_ALERT_URL.

On forward or send failure: journal ALERT line plus a GitHub ops issue #701
comment (treasury pi_ops_alert sink, #704), max 1 per 15 min. Never logs
secrets, secret paths, message bodies, or full phone numbers.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="[bland-relay] %(levelname)s %(message)s")
log = logging.getLogger("bland-relay")

HOST = os.environ.get("RELAY_HOST", "127.0.0.1")
PORT = int(os.environ.get("RELAY_PORT", "8799"))
MAX_BODY = 2 * 1024 * 1024
MAX_SMS_CHARS = 640
ALLOWLIST_S = 72 * 60 * 60
RECIPIENT_WINDOW_S = 10 * 60
RECIPIENT_MAX = 5
GLOBAL_WINDOW_S = 60 * 60
GLOBAL_MAX = 30
LINE_904 = "+19043343975"
SMSGATE_SKEW_S = 5 * 60
SMSGATE_SEEN_MAX = 512
RCS_PACKAGE = "com.google.android.apps.messaging"
RCS_DEDUPE_S = 120
RCS_WAIT_DEFAULT_S = 8.0
RCS_RECENT_MAX = 512
RCS_NAME_MAX = 64

# Whole inbound message, after trimming punctuation. A later normal text does
# not clear STOP; only these opt-in words do. Carrier-style keywords.
OPT_OUT_WORDS = frozenset({
    "STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT",
})
OPT_IN_WORDS = frozenset({"START", "UNSTOP", "YES"})

SECRET_ENV_KEYS = (
    "ALEXANDRA_ALERT_URL",
    "ALEXANDRA_ALERT_AUTH",
    "BLAND_WEBHOOK_SECRET",
    "RELAY_PATH",
    "FWD_TOKEN",
    "FWD_PATH",
    "SEND_TOKEN",
    "SEND_PATH",
    "SMSGW_URL",
    "SMSGW_USER",
    "SMSGW_PASS",
    "SMSGW_HOOK_PATH",
    "SMSGW_SIGNING_KEY",
    "RCS_HOOK_PATH",
    "RCS_HOOK_TOKEN",
    "FC_PATH",
    "FC_APPROVE_TOKEN_SHA256",
    "HELM_TURO_WEBHOOK_URL",
    "HELM_TURO_WEBHOOK_KEY",
)

_alert_lock = threading.Lock()
_sms_lock = threading.Lock()
_seen_lock = threading.Lock()
_seen_events = {}  # SMSGate event id -> epoch, successful forwards only (retry dedupe)
_recent_lock = threading.Lock()
# Cross-path dedupe (#1056): {"at", "src" ("sms"|"rcs"), "num", "name", "tkey"}.
# Entries are claimed before forwarding and dropped again if the forward fails.
_recent_msgs = []
ALERT_COOLDOWN_S = 15 * 60
TREASURY_DIR = Path.home() / "personal-workspace"


def env(k):
    return (os.environ.get(k) or "").strip()


def _secret_path(name):
    p = env(name)
    return ("/" + p.lstrip("/")) if p else ""


def relay_path():
    return _secret_path("RELAY_PATH")


def fwd_path():
    return _secret_path("FWD_PATH")


def send_path():
    return _secret_path("SEND_PATH")


def smsgate_path():
    return _secret_path("SMSGW_HOOK_PATH")


def rcs_path():
    return _secret_path("RCS_HOOK_PATH")


def _state_dir():
    override = env("BLAND_RELAY_STATE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".local" / "state" / "bland-relay"


def _alert_state_path():
    return _state_dir() / "alert_state.json"


def _sms_state_path():
    return _state_dir() / "sms_send_state.json"


def _save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _load_json(path):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _post_github(title, text):
    """Same sink as treasury alerts since #704: treasury.pi_ops_alert.post_ops_github
    (issue #701, token from ~/.config/workflow-scheduler.env). Falls back to an
    equivalent inline post if the module can't be imported (e.g. mid workspace-sync)."""
    try:
        if str(TREASURY_DIR) not in sys.path:
            sys.path.insert(0, str(TREASURY_DIR))
        from treasury.pi_ops_alert import post_ops_github, kill_switch  # type: ignore
        if kill_switch():
            return {"ok": True, "posted": False, "skipped": "FCC_ALERT_KILL_SWITCH"}
        return post_ops_github(title, text, as_markdown=True)
    except ImportError as e:
        log.warning("pi_ops_alert import failed (%s); using inline fallback", type(e).__name__)
    envf = Path.home() / ".config" / "workflow-scheduler.env"
    tok = ""
    try:
        for line in envf.read_text().splitlines():
            k, _, v = line.strip().removeprefix("export ").partition("=")
            if k.strip() in ("GITHUB_TOKEN", "GH_TOKEN") and v.strip():
                tok = v.strip().strip("'\"")
                break
    except OSError:
        pass
    if not tok:
        return {"ok": True, "posted": False, "skipped": "no-github-token"}
    issue = (os.environ.get("PI_OPS_ALERT_ISSUE") or "701").strip()
    req = urllib.request.Request(
        f"https://api.github.com/repos/cvolkernick/personal-workspace/issues/{issue}/comments",
        data=json.dumps({"body": f"**{title}**\n\n{text.rstrip()}\n"}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "personal-workspace-pi-alerts",
                 "X-GitHub-Api-Version": "2022-11-28"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return {"ok": True, "posted": True, "status": r.status, "issue": issue}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "posted": False, "error": type(e).__name__, "issue": issue}


def _scrub(msg):
    for k in SECRET_ENV_KEYS:
        v = env(k)
        if v:
            msg = msg.replace(v, "<redacted>")
    return msg[:300]


def alert_failure(call_id, err, attempts, title=None):
    """Journal ALERT always; GitHub #701 comment at most once per 15 min."""
    err = _scrub(err)
    now = time.time()
    ts = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(now))
    log.error("ALERT forward failed: call_id=%s error=%s attempts=%d", call_id or "-", err, attempts)
    with _alert_lock:
        st = _load_json(_alert_state_path())
        last = float(st.get("last_posted_epoch") or 0)
        if now - last < ALERT_COOLDOWN_S:
            st["suppressed"] = int(st.get("suppressed") or 0) + 1
            log.warning("GitHub alert suppressed (cooldown; %d suppressed since last comment)", st["suppressed"])
            _save_state(st)
            return
        suppressed = int(st.get("suppressed") or 0)
        id_label = "Send id" if str(call_id).startswith("sms-") else "Bland call_id"
        text = (
            f"- Time: {ts}\n"
            f"- {id_label}: `{call_id or 'n/a'}`\n"
            f"- Error: {err}\n"
            f"- Forward attempts: {attempts}\n"
            f"- Failures suppressed since previous relay comment (15 min dedupe): {suppressed}\n"
            f"- Source: prism `bland-relay.service` (`journalctl --user-unit bland-relay.service`)"
        )
        res = _post_github(title or "Bland relay: Alexandra alert forward failed", text)
        if res.get("posted"):
            log.info("GitHub alert posted to #%s (status %s)", res.get("issue"), res.get("status"))
            _save_state({"last_posted_epoch": now, "last_posted_at": ts, "suppressed": 0})
        else:
            log.error("GitHub alert NOT posted: %s", res.get("skipped") or res.get("error"))


def _save_state(st):
    try:
        _save_json(_alert_state_path(), st)
    except OSError as e:
        log.error("alert state write failed: %s", type(e).__name__)


def forward(body, timeout=10):
    url, auth = env("ALEXANDRA_ALERT_URL"), env("ALEXANDRA_ALERT_AUTH")
    if not url:
        return None, "ALEXANDRA_ALERT_URL unset", 0
    headers = {"Content-Type": "application/json", "User-Agent": "bland-relay/1"}
    if auth:
        headers["Authorization"] = auth
    last = ""
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if 200 <= r.status < 300:
                    return r.status, "", attempt
                last = f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            last = type(e).__name__ + ": " + str(e)[:200]
        log.warning("forward attempt %d failed: %s", attempt, _scrub(last))
        if attempt == 1:
            time.sleep(1)
    return None, last, 2


def token_from_headers(headers):
    got = (headers.get("X-Relay-Token") or "").strip()
    if not got:
        auth = (headers.get("Authorization") or "").strip()
        got = auth[7:].strip() if auth.lower().startswith("bearer ") else auth
    return got


def path_is(path, secret):
    if not secret:
        return False
    try:
        a = path.encode()
        b = secret.encode()
    except Exception:  # noqa: BLE001
        return False
    if len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)


def token_ok(got, expected):
    if not got or not expected:
        return False
    try:
        a = got.encode()
        b = expected.encode()
    except Exception:  # noqa: BLE001
        return False
    if len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)


def normalize_nanp(raw):
    """Loose inbound forms (spaces, 10-digit, leading 1) -> +1 NANP, or None."""
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    if digits[0] in "01" or digits[3] in "01":
        return None
    return "+1" + digits


def valid_e164(to):
    """Strict send-route form: +1 NXX NXX XXXX."""
    return bool(re.fullmatch(r"\+1[2-9]\d{2}[2-9]\d{6}", to or ""))


def opt_word(text):
    if text is None:
        return ""
    s = str(text).strip().upper().strip(" \t.!?,")
    if not s or any(c.isspace() for c in s):
        return ""
    return s


def note_inbound(sender, text, now=None, via="sms"):
    """Record that this number texted 904. Opt-out sticks until START/UNSTOP/YES.

    Also runs the first-contact hook: a reply converts an active entry to the
    normal inbound window, STOP blocks it, and, only when FC_REQUIRE_APPROVAL=1,
    "YES <code>" from an approver number approves a pending request (#1100).
    """
    num = normalize_nanp(sender)
    if not num:
        return
    now = time.time() if now is None else now
    word = opt_word(text)
    with _sms_lock:
        st = _load_json(_sms_state_path())
        inbound = st.get("inbound") if isinstance(st.get("inbound"), dict) else {}
        rec = dict(inbound.get(num) or {})
        rec["last_seen"] = now
        if word in OPT_OUT_WORDS:
            rec["opt_out"] = True
        elif word in OPT_IN_WORDS:
            rec["opt_out"] = False
        inbound[num] = rec
        st["inbound"] = inbound
        st.setdefault("sends", [])
        try:
            _fc_on_inbound_locked(st, num, word, text, via, now)
        except Exception as e:  # noqa: BLE001 - never lose the inbound record
            log.error("first-contact inbound hook failed: %s", type(e).__name__)
        try:
            _save_json(_sms_state_path(), st)
        except OSError as e:
            log.error("sms state write failed: %s", type(e).__name__)


def reserve_send(to, now=None):
    """Allowlist + rate limit. Returns None when the send may proceed, else a reason."""
    return reserve_send_ex(to, now)[0]


def reserve_send_ex(to, now=None):
    """Allowlist + rate limit. On success, record the attempt before the gateway call.

    A failed gateway call still consumes a slot so a retry storm cannot multiply.
    Returns (reason, grant). reason is None when the send may proceed, otherwise
    not_allowlisted, opt_out, rate_limited, or a first_contact_* refusal. Opt-out
    wins even after the 72-hour window and over any first-contact approval.
    grant is {"kind": "inbound"|"first_contact", "entry": <entry copy or None>}.
    """
    now = time.time() if now is None else now
    with _sms_lock:
        st = _load_json(_sms_state_path())
        inbound = st.get("inbound") if isinstance(st.get("inbound"), dict) else {}
        rec = inbound.get(to)
        if rec and rec.get("opt_out"):
            return "opt_out", None
        last = float(rec.get("last_seen") or 0) if rec else 0.0
        if rec and now - last <= ALLOWLIST_S:
            kind = "inbound"
        else:
            fc_reason = _fc_grant_reason(st, to, now)
            if fc_reason:
                return fc_reason, None
            kind = "first_contact"
        sends = []
        for row in st.get("sends") or []:
            if not isinstance(row, dict):
                continue
            at = float(row.get("at") or 0)
            if now - at < GLOBAL_WINDOW_S:
                sends.append({"to": row.get("to"), "at": at})
        per = sum(1 for row in sends if row.get("to") == to and now - row["at"] < RECIPIENT_WINDOW_S)
        if per >= RECIPIENT_MAX or len(sends) >= GLOBAL_MAX:
            st["sends"] = sends
            try:
                _save_json(_sms_state_path(), st)
            except OSError as e:
                log.error("sms state write failed: %s", type(e).__name__)
            return "rate_limited", None
        sends.append({"to": to, "at": now})
        st["sends"] = sends
        entry = None
        if kind == "first_contact":
            entry = st["first_contact"][to]
            entry["sends"] = int(entry.get("sends") or 0) + 1
            entry["last_send_at_iso"] = _iso(now)
            entry = dict(entry)
        try:
            _save_json(_sms_state_path(), st)
        except OSError as e:
            log.error("sms state write failed: %s", type(e).__name__)
            return "rate_limited", None
        return None, {"kind": kind, "entry": entry}


# ---------------------------------------------------------------------------
# First-contact outbound (#1100)
#
# FC_REQUIRE_APPROVAL=0 (default): an agent `add`s (or `request`s) a number
# with SEND_TOKEN and it is active at once. No code, no approver; the YES-code
# SMS path and the chat approve route are inert and FC_APPROVER_NUMBERS is
# ignored. Opt-out, rate limits, FC_DAILY_MAX, TTL, the sends-before-reply cap,
# both kill switches, the audit log and the CRM record all still apply.
#
# FC_REQUIRE_APPROVAL=1: a number that never texted 904 can be sent to only
# after Chris approves it: an agent `request`s it, gets a one-time code, and
# Chris types yes with that code, either as an SMS "YES <code>" to 904 from a
# number in FC_APPROVER_NUMBERS or in chat, relayed by the seat holding
# FC_APPROVE_TOKEN (the relay keeps only its SHA-256).
#
# Either way opt-out always wins, and only a currently active entry is a
# renewal exempt from FC_DAILY_MAX; re-adding a revoked, expired, converted or
# opted-out number counts as a new number (#1102 a). State lives in
# sms_send_state.json under the same lock as the inbound allowlist.
# ---------------------------------------------------------------------------
FC_PURPOSE_KINDS = ("lead", "renter", "vendor")
FC_PENDING_S = 24 * 60 * 60
FC_DAY_S = 24 * 60 * 60
FC_PENDING_MAX = 50
FC_NAME_MAX = 64
FC_PURPOSE_MAX = 200
FC_TEXT_MAX = 500
FC_REF_MAX = 200
FC_DEFAULT_APPROVERS = "chris"
FC_DEFAULT_AGENT_SEATS = "alexandra,grok,grok.btc,forge,buzz,grokbuild,gvg,muse,bot,agent,relay,bland-relay"
FC_SMS_APPROVE_RE = re.compile(r"^\s*YES\s+(\d{6})\s*[.!]?\s*$", re.IGNORECASE)
FC_YES_RE = re.compile(r"^\s*(yes|y|yep|yeah|approved?|ok|okay)\b", re.IGNORECASE)
FC_SEAT_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,31}")
FC_REFUSALS = ("first_contact_disabled", "first_contact_expired", "first_contact_send_cap")
# Inbound paths allowed to carry an SMS approval. RCS name-map resolution is a
# guess from a contact title, so it never approves anything.
FC_APPROVAL_VIAS = ("sms", "forwarder", "smsgate", "rcs-number", "rcs-title")
CRM_SCHEMA = "panamerica.crm.interaction.v1"
CRM_PARTY_TYPES = {"lead": ("lead", ["904-first-contact"]),
                   "renter": ("customer", ["turo-guest", "904-first-contact"]),
                   "vendor": ("vendor", ["904-first-contact"])}


def _int_env(name, default, lo, hi):
    raw = env(name)
    if not raw:
        return default
    try:
        return max(lo, min(int(raw), hi))
    except ValueError:
        log.warning("%s is not an integer; using %d", name, default)
        return default


def fc_daily_max():
    return _int_env("FC_DAILY_MAX", 20, 0, 200)


def fc_ttl_s():
    return _int_env("FC_TTL_DAYS", 30, 1, 90) * FC_DAY_S


def fc_max_sends():
    return _int_env("FC_MAX_SENDS_BEFORE_REPLY", 3, 1, 20)


def fc_disabled():
    return env("FC_DISABLED") == "1"


def fc_path():
    return _secret_path("FC_PATH")


def fc_require_approval():
    """FC_REQUIRE_APPROVAL: 0/unset = no per-number approval (default), 1 = gated.
    Any other value fails closed to gated."""
    raw = env("FC_REQUIRE_APPROVAL").lower()
    if raw in ("", "0", "false", "no", "off"):
        return False
    if raw not in ("1", "true", "yes", "on"):
        log.warning("FC_REQUIRE_APPROVAL is not 0 or 1; requiring approval")
    return True


def _csv_set(raw):
    return {s.strip().lower() for s in (raw or "").split(",") if s.strip()}


def fc_approvers():
    return _csv_set(env("FC_APPROVERS") or FC_DEFAULT_APPROVERS)


def fc_agent_seats():
    return _csv_set(FC_DEFAULT_AGENT_SEATS) | _csv_set(env("FC_AGENT_SEATS"))


def fc_approver_numbers():
    """FC_APPROVER_NUMBERS "chris=+12075550100;other=+1..." -> {E.164: name}."""
    out = {}
    for part in env("FC_APPROVER_NUMBERS").split(";"):
        name, sep, num = part.rpartition("=")
        num = normalize_nanp(num) if sep else None
        name = name.strip().lower()
        if num and name and name in fc_approvers() and name not in fc_agent_seats():
            out[num] = name
    return out


def _sha256(s):
    return hashlib.sha256(s.encode()).hexdigest()


def approver_token_ok(got):
    """FC_APPROVE_TOKEN check against the stored SHA-256. Never equal to SEND_TOKEN."""
    want = env("FC_APPROVE_TOKEN_SHA256").lower()
    if not got or not re.fullmatch(r"[0-9a-f]{64}", want):
        return False
    send = env("SEND_TOKEN")
    if send and hmac.compare_digest(_sha256(send), want):
        return False  # misconfigured: agents' token must never approve
    return hmac.compare_digest(_sha256(got), want)


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _audit_path():
    override = env("FC_AUDIT_PATH")
    return Path(override) if override else _state_dir() / "first_contact_audit.jsonl"


def _append_jsonl(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def fc_audit(event, now=None, **fields):
    """Append-only audit line. Never message bodies or secrets."""
    now = time.time() if now is None else now
    row = {"at": _iso(now), "event": event}
    row.update({k: v for k, v in fields.items() if v is not None})
    try:
        _append_jsonl(_audit_path(), row)
    except OSError as e:
        log.error("first-contact audit write failed: %s", type(e).__name__)


class CrmOutboxSink:
    """Local stand-in for crm-api POST /v1/interactions (#1097): one JSON line
    per interaction in the #1049 interaction shape. #1097 drains this file,
    idempotent on provider_msg_id, and replaces the sink."""

    def __init__(self, path):
        self.path = Path(path)

    def emit(self, record):
        _append_jsonl(self.path, record)


def crm_sink():
    mode = (env("CRM_INGEST_MODE") or "outbox").lower()
    if mode == "off":
        return None
    if mode != "outbox":
        log.warning("CRM_INGEST_MODE=%s not supported yet; using outbox", re.sub(r"[^a-z_-]", "", mode)[:16])
    path = env("CRM_OUTBOX_PATH")
    return CrmOutboxSink(path if path else _state_dir() / "crm_outbox.jsonl")


def emit_interaction(record):
    """Never raises: the CRM record must not change the send outcome."""
    try:
        sink = crm_sink()
        if sink is not None:
            sink.emit(record)
    except Exception as e:  # noqa: BLE001
        log.error("crm interaction emit failed: %s", type(e).__name__)


def crm_interaction_record(to, grant, result, err, now, purpose=None, placed_by=None):
    """#1049 interaction row + #1097 metadata.purpose/placed_by + party_hint. No body."""
    entry = (grant or {}).get("entry") or {}
    first = (grant or {}).get("kind") == "first_contact"
    msg_id = (result or {}).get("id")
    if first:
        kind = entry.get("purpose_kind") or ""
        purpose = entry.get("purpose") or purpose
        placed_by = placed_by or entry.get("requested_by")
        ptype, tags = CRM_PARTY_TYPES.get(kind, ("other", ["904-first-contact"]))
        summary = f"First-contact text from 904 ({kind}: {purpose})"
        hint = {"phone": to, "display_name": entry.get("name"), "type": ptype,
                "tags": tags, "source": "904-first-contact"}
    else:
        kind = None
        purpose = purpose or "reply to inbound text"
        summary = f"Text reply from 904 ({purpose})"
        hint = {"phone": to, "display_name": None, "type": None, "tags": [], "source": "904-sms"}
    meta = {"purpose": purpose, "purpose_kind": kind, "placed_by": placed_by or "send-route",
            "first_contact": first, "grant": (grant or {}).get("kind")}
    if first:
        meta["approval_required"] = entry.get("approved_via") != "auto"
        meta["approval"] = {"approved_by": entry.get("approved_by"), "approval_ref": entry.get("approval_ref"),
                            "approved_via": entry.get("approved_via"), "approved_at": entry.get("approved_at_iso")}
    return {
        "schema": CRM_SCHEMA,
        "channel": "sms",
        "direction": "outbound",
        "provider": "smsgate",
        "provider_msg_id": ("smsgate:" + msg_id) if msg_id else ("relay:" + os.urandom(8).hex()),
        "from_addr": LINE_904,
        "to_addr": to,
        "summary": summary[:200],
        "outcome": "sent" if result is not None else "failed",
        "error_code": "" if result is not None else _scrub(err or "")[:64],
        "occurred_at": _iso(now),
        "logged_by": "bland-relay",
        "metadata": meta,
        "party_hint": hint,
    }


def _fc_maps(st):
    for key in ("first_contact", "fc_pending"):
        if not isinstance(st.get(key), dict):
            st[key] = {}
    if not isinstance(st.get("fc_approvals"), list):
        st["fc_approvals"] = []
    return st["first_contact"], st["fc_pending"]


def _fc_status(entry, now):
    status = entry.get("status") or "active"
    if status == "active" and now > float(entry.get("expires_at") or 0):
        return "expired"
    return status


def _fc_recent_approvals(st, now):
    rows = [float(t) for t in st.get("fc_approvals") or [] if isinstance(t, (int, float))]
    rows = [t for t in rows if now - t < FC_DAY_S]
    st["fc_approvals"] = rows
    return len(rows)


def _fc_cap_exempt(entries, num, now):
    """Only a currently active entry is a renewal that skips FC_DAILY_MAX (#1102 a)."""
    entry = entries.get(num)
    return entry is not None and _fc_status(entry, now) == "active"


def _fc_opted_out(st, num):
    inbound = st.get("inbound") if isinstance(st.get("inbound"), dict) else {}
    return bool((inbound.get(num) or {}).get("opt_out"))


def _fc_grant_reason(st, to, now):
    """None if an approved first-contact entry allows a send to `to` now."""
    entries, _ = _fc_maps(st)
    entry = entries.get(to)
    if not entry:
        return "not_allowlisted"
    status = _fc_status(entry, now)
    if status == "expired":
        return "first_contact_expired"
    if status != "active":
        return "not_allowlisted"
    if fc_disabled():
        return "first_contact_disabled"
    if int(entry.get("sends") or 0) >= fc_max_sends():
        return "first_contact_send_cap"
    return None


def _fc_approve_locked(st, num, approver, text, ref, via, now):
    """Turn fc_pending[num] into an active entry. Caller holds _sms_lock and has
    checked the code. Returns (error, entry)."""
    entries, pending = _fc_maps(st)
    req = pending.get(num)
    if not req:
        return "no_pending_request", None
    approver = (approver or "").strip().lower()
    if (approver not in fc_approvers() or approver in fc_agent_seats()
            or approver == (req.get("requested_by") or "").lower()):
        return "approver_not_allowed", None
    if _fc_opted_out(st, num):
        pending.pop(num, None)
        return "opt_out", None
    if not _fc_cap_exempt(entries, num, now) and _fc_recent_approvals(st, now) >= fc_daily_max():
        return "daily_cap", None
    return None, _fc_activate_locked(st, num, req, approver, text, ref, via, now)


def _fc_activate_locked(st, num, req, approver, text, ref, via, now):
    """Write the active entry for num (caller holds _sms_lock and has checked
    opt-out and the daily cap). Returns the entry."""
    entries, pending = _fc_maps(st)
    prior = entries.get(num)
    renewal = prior is not None
    cap_exempt = _fc_cap_exempt(entries, num, now)
    history = list((prior or {}).get("history") or [])[-9:]
    if prior:
        history.append({k: prior.get(k) for k in ("status", "approved_by", "approved_at_iso", "expires_at_iso")})
    entry = {
        "name": req.get("name"),
        "purpose_kind": req.get("purpose_kind"),
        "purpose": req.get("purpose"),
        "requested_by": req.get("requested_by"),
        "requested_at_iso": req.get("requested_at_iso"),
        "approved_by": approver or None,
        "approval_text": (text or "")[:FC_TEXT_MAX],
        "approval_ref": (ref or "")[:FC_REF_MAX],
        "approved_via": via,
        "approved_at": now,
        "approved_at_iso": _iso(now),
        "expires_at": now + fc_ttl_s(),
        "expires_at_iso": _iso(now + fc_ttl_s()),
        "status": "active",
        "sends": 0,
        "history": history,
    }
    entries[num] = entry
    pending.pop(num, None)
    if not cap_exempt:
        st["fc_approvals"].append(now)
    if via == "auto":
        fc_audit("add", now, to=num, name=entry["name"], purpose_kind=entry["purpose_kind"],
                 purpose=entry["purpose"], requested_by=entry["requested_by"], approval_required=False,
                 renewal=renewal, expires_at=entry["expires_at_iso"])
        log.info("first-contact added (no approval) to=***%s renewal=%s", _tail4(num), renewal)
    else:
        fc_audit("approve", now, to=num, name=entry["name"], purpose_kind=entry["purpose_kind"],
                 purpose=entry["purpose"], requested_by=entry["requested_by"], approved_by=approver,
                 approval_text=entry["approval_text"], approval_ref=entry["approval_ref"], via=via,
                 renewal=renewal, expires_at=entry["expires_at_iso"])
        log.info("first-contact approved to=***%s via=%s renewal=%s", _tail4(num), via, renewal)
    return entry


def _fc_on_inbound_locked(st, num, word, text, via, now):
    """Inbound hook (caller holds _sms_lock): reply converts, STOP blocks, and
    "YES <code>" from an approver number approves a pending request."""
    entries, pending = _fc_maps(st)
    entry = entries.get(num)
    if entry and (entry.get("status") or "active") in ("active", "converted"):
        if word in OPT_OUT_WORDS:
            entry["status"] = "opted_out"
            fc_audit("opt_out", now, to=num, via=via)
        elif entry.get("status") == "active":
            entry["status"] = "converted"
            entry["converted_at_iso"] = _iso(now)
            fc_audit("convert", now, to=num, via=via)
    if word in OPT_OUT_WORDS and num in pending:
        pending.pop(num, None)
        fc_audit("pending_dropped_opt_out", now, to=num, via=via)
    if not fc_require_approval():
        return  # no approvals: "YES <code>" is ordinary text, FC_APPROVER_NUMBERS ignored
    approver = fc_approver_numbers().get(num)
    m = FC_SMS_APPROVE_RE.match(str(text or ""))
    if not approver or not m:
        return
    if via not in FC_APPROVAL_VIAS:
        fc_audit("deny", now, approver_number_tail=_tail4(num), reason="via_not_allowed", via=via)
        return
    if env("SMS_SEND_DISABLED") == "1" or fc_disabled():
        fc_audit("deny", now, approver=approver, reason="disabled", via=via)
        return
    code_h = _sha256(m.group(1))
    target = None
    for cand, req in pending.items():
        if now - float(req.get("requested_at") or 0) <= FC_PENDING_S and hmac.compare_digest(
                str(req.get("code_sha256") or ""), code_h):
            target = cand
            break
    if target is None:
        fc_audit("deny", now, approver=approver, reason="no_matching_pending", via=via)
        return
    ref = f"904-inbound:{via}:{_iso(now)}:from***{_tail4(num)}"
    err, _ = _fc_approve_locked(st, target, approver, str(text).strip(), ref, "sms:" + via, now)
    if err:
        fc_audit("deny", now, to=target, approver=approver, reason=err, via=via)


def _fc_clean_name(raw):
    s = " ".join(str(raw or "").split())
    if not s or len(s) > FC_NAME_MAX or any(ord(c) < 32 for c in s):
        return None
    return s


def _fc_seat(raw):
    s = str(raw or "").strip().lower()
    return s if FC_SEAT_RE.fullmatch(s) else None


def fc_request(data, now=None):
    """-> (http_code, response)."""
    now = time.time() if now is None else now
    to = normalize_nanp(data.get("to")) if isinstance(data.get("to"), str) else None
    if not to or not valid_e164(to):
        return 400, {"ok": False, "error": "bad to"}
    name = _fc_clean_name(data.get("name"))
    if not name:
        return 400, {"ok": False, "error": "name required (1-64 chars)"}
    kind = str(data.get("purpose_kind") or "").strip().lower()
    if kind not in FC_PURPOSE_KINDS:
        return 400, {"ok": False, "error": "purpose_kind must be lead, renter or vendor"}
    purpose = " ".join(str(data.get("purpose") or "").split())
    if len(purpose) < 3 or len(purpose) > FC_PURPOSE_MAX:
        return 400, {"ok": False, "error": "purpose required (3-200 chars)"}
    seat = _fc_seat(data.get("requested_by"))
    if not seat:
        return 400, {"ok": False, "error": "requested_by required"}
    if not fc_require_approval():
        return _fc_add(to, name, kind, purpose, seat, now)
    code = "%06d" % (int.from_bytes(os.urandom(4), "big") % 1_000_000)
    with _sms_lock:
        st = _load_json(_sms_state_path())
        entries, pending = _fc_maps(st)
        if _fc_opted_out(st, to):
            fc_audit("deny", now, to=to, reason="opt_out", requested_by=seat, action="request")
            return 403, {"ok": False, "error": "opt_out"}
        for k in [k for k, v in pending.items() if now - float(v.get("requested_at") or 0) > FC_PENDING_S]:
            pending.pop(k, None)
        renewal = to in entries
        if not _fc_cap_exempt(entries, to, now) and _fc_recent_approvals(st, now) >= fc_daily_max():
            fc_audit("deny", now, to=to, reason="daily_cap", requested_by=seat, action="request")
            return 429, {"ok": False, "error": "daily_cap"}
        if to not in pending and len(pending) >= FC_PENDING_MAX:
            return 429, {"ok": False, "error": "too_many_pending"}
        pending[to] = {
            "name": name, "purpose_kind": kind, "purpose": purpose, "requested_by": seat,
            "code_sha256": _sha256(code), "requested_at": now, "requested_at_iso": _iso(now),
            "renewal": renewal,
        }
        st.setdefault("inbound", {})
        st.setdefault("sends", [])
        try:
            _save_json(_sms_state_path(), st)
        except OSError as e:
            log.error("sms state write failed: %s", type(e).__name__)
            return 500, {"ok": False, "error": "state write failed"}
    fc_audit("request", now, to=to, name=name, purpose_kind=kind, purpose=purpose,
             requested_by=seat, renewal=renewal)
    log.info("first-contact requested to=***%s kind=%s renewal=%s", _tail4(to), kind, renewal)
    return 200, {
        "ok": True, "status": "pending", "to": to, "name": name, "purpose_kind": kind,
        "purpose": purpose, "renewal": renewal, "code": code,
        "pending_expires_at": _iso(now + FC_PENDING_S),
        "approve_sms": f"YES {code}",
        "ask": (f"Approve a first text from 904 to {name} ({to}), {kind}: {purpose}? "
                f"Reply 'yes {code}', or text YES {code} to 904 from your phone."),
    }


def _fc_add(to, name, kind, purpose, seat, now):
    """FC_REQUIRE_APPROVAL=0: allow `to` now. Opt-out and FC_DAILY_MAX still
    apply. Re-adding an already active number changes nothing (no TTL or
    sends-before-reply reset, so the caps cannot be bypassed by re-adding)."""
    with _sms_lock:
        st = _load_json(_sms_state_path())
        entries, _ = _fc_maps(st)
        if _fc_opted_out(st, to):
            fc_audit("deny", now, to=to, reason="opt_out", requested_by=seat, action="add")
            return 403, {"ok": False, "error": "opt_out"}
        if _fc_cap_exempt(entries, to, now):
            return 200, {"ok": True, "status": "active", "to": to, "approval_required": False,
                         "already_active": True, "entry": _fc_public(entries[to], now)}
        if _fc_recent_approvals(st, now) >= fc_daily_max():
            fc_audit("deny", now, to=to, reason="daily_cap", requested_by=seat, action="add")
            return 429, {"ok": False, "error": "daily_cap"}
        req = {"name": name, "purpose_kind": kind, "purpose": purpose, "requested_by": seat,
               "requested_at_iso": _iso(now)}
        entry = _fc_activate_locked(st, to, req, None, "", "FC_REQUIRE_APPROVAL=0", "auto", now)
        st.setdefault("inbound", {})
        st.setdefault("sends", [])
        try:
            _save_json(_sms_state_path(), st)
        except OSError as e:
            log.error("sms state write failed: %s", type(e).__name__)
            return 500, {"ok": False, "error": "state write failed"}
    return 200, {"ok": True, "status": "active", "to": to, "approval_required": False,
                 "already_active": False, "entry": _fc_public(entry, now)}


def fc_approve(data, now=None):
    now = time.time() if now is None else now
    if not fc_require_approval():
        return 409, {"ok": False, "error": "approval_not_required",
                     "detail": "FC_REQUIRE_APPROVAL=0: numbers are active as soon as they are added"}
    to = normalize_nanp(data.get("to")) if isinstance(data.get("to"), str) else None
    if not to or not valid_e164(to):
        return 400, {"ok": False, "error": "bad to"}
    code = str(data.get("code") or "").strip()
    text = str(data.get("approval_text") or "").strip()
    ref = str(data.get("approval_ref") or "").strip()
    approver = str(data.get("approver") or "").strip().lower()
    if not re.fullmatch(r"\d{6}", code):
        return 400, {"ok": False, "error": "code required"}
    if not ref or len(ref) > FC_REF_MAX:
        return 400, {"ok": False, "error": "approval_ref required"}
    if len(text) > FC_TEXT_MAX or not FC_YES_RE.match(text) or code not in text:
        return 400, {"ok": False, "error": "approval_text must be the user's typed yes including the code"}
    with _sms_lock:
        st = _load_json(_sms_state_path())
        _, pending = _fc_maps(st)
        req = pending.get(to)
        if (not req or now - float(req.get("requested_at") or 0) > FC_PENDING_S
                or not hmac.compare_digest(str(req.get("code_sha256") or ""), _sha256(code))):
            fc_audit("deny", now, to=to, approver=approver, reason="no_matching_pending", action="approve")
            return 403, {"ok": False, "error": "no matching pending request"}
        err, entry = _fc_approve_locked(st, to, approver, text, ref, "chat", now)
        if err:
            if err == "opt_out":
                try:
                    _save_json(_sms_state_path(), st)  # drop the pending request
                except OSError as e:
                    log.error("sms state write failed: %s", type(e).__name__)
            fc_audit("deny", now, to=to, approver=approver, reason=err, action="approve")
            return (429 if err == "daily_cap" else 403), {"ok": False, "error": err}
        try:
            _save_json(_sms_state_path(), st)
        except OSError as e:
            log.error("sms state write failed: %s", type(e).__name__)
            return 500, {"ok": False, "error": "state write failed"}
    return 200, {"ok": True, "status": "active", "to": to, "entry": _fc_public(entry, now)}


def _fc_public(entry, now):
    keys = ("name", "purpose_kind", "purpose", "requested_by", "requested_at_iso", "approved_by",
            "approval_text", "approval_ref", "approved_via", "approved_at_iso", "expires_at_iso",
            "converted_at_iso", "revoked_at_iso", "revoked_by", "revoke_reason", "sends")
    out = {k: entry.get(k) for k in keys if entry.get(k) is not None}
    out["status"] = _fc_status(entry, now)
    return out


def fc_list(data, now=None):
    now = time.time() if now is None else now
    want = str(data.get("status") or "all").strip().lower()
    with _sms_lock:
        st = _load_json(_sms_state_path())
        entries, pending = _fc_maps(st)
        rows = []
        for num, e in sorted(entries.items()):
            row = _fc_public(e, now)
            row["to"] = num
            rows.append(row)
        for num, p in sorted(pending.items()):
            if now - float(p.get("requested_at") or 0) > FC_PENDING_S:
                continue
            rows.append({"to": num, "status": "pending", "name": p.get("name"),
                         "purpose_kind": p.get("purpose_kind"), "purpose": p.get("purpose"),
                         "requested_by": p.get("requested_by"), "requested_at_iso": p.get("requested_at_iso"),
                         "renewal": bool(p.get("renewal"))})
        used = _fc_recent_approvals(st, now)
    if want != "all":
        rows = [r for r in rows if r["status"] == want]
    return 200, {"ok": True, "entries": rows, "daily_used": used, "daily_max": fc_daily_max(),
                 "approval_required": fc_require_approval(),
                 "ttl_days": fc_ttl_s() // FC_DAY_S, "max_sends_before_reply": fc_max_sends()}


def fc_revoke(data, now=None):
    now = time.time() if now is None else now
    to = normalize_nanp(data.get("to")) if isinstance(data.get("to"), str) else None
    if not to or not valid_e164(to):
        return 400, {"ok": False, "error": "bad to"}
    reason = " ".join(str(data.get("reason") or "").split())[:FC_PURPOSE_MAX]
    seat = _fc_seat(data.get("revoked_by"))
    if not reason or not seat:
        return 400, {"ok": False, "error": "reason and revoked_by required"}
    with _sms_lock:
        st = _load_json(_sms_state_path())
        entries, pending = _fc_maps(st)
        had_pending = pending.pop(to, None) is not None
        entry = entries.get(to)
        if entry is None and not had_pending:
            return 404, {"ok": False, "error": "not found"}
        if entry is not None:
            entry["status"] = "revoked"
            entry["revoked_at_iso"] = _iso(now)
            entry["revoked_by"] = seat
            entry["revoke_reason"] = reason
        try:
            _save_json(_sms_state_path(), st)
        except OSError as e:
            log.error("sms state write failed: %s", type(e).__name__)
            return 500, {"ok": False, "error": "state write failed"}
    fc_audit("revoke", now, to=to, revoked_by=seat, reason=reason, pending_dropped=had_pending)
    log.info("first-contact revoked to=***%s", _tail4(to))
    return 200, {"ok": True, "to": to, "status": "revoked"}


def gateway_configured():
    return bool(env("SMSGW_URL") and env("SMSGW_USER") and env("SMSGW_PASS"))


def gateway_message_url():
    """Cloud send URL.

    Live cloud OpenAPI (api.sms-gate.app 1.49.0) exposes POST
    /3rdparty/v1/messages. The device local server still uses /message.
    A base URL gets /messages. A URL that already ends in /message or
    /messages is used as the full endpoint, so local mode can set
    SMSGW_URL=http://<phone>:8080/message.
    """
    base = env("SMSGW_URL").rstrip("/")
    if not base:
        return ""
    if base.endswith("/message") or base.endswith("/messages"):
        return base
    return base + "/messages"


def gateway_send(to, message, opener=None):
    """One POST. No retry: a second attempt can deliver a second SMS."""
    url = gateway_message_url()
    user, password = env("SMSGW_USER"), env("SMSGW_PASS")
    if not url or not user or not password:
        return None, "gateway not configured"
    payload = json.dumps({
        "phoneNumbers": [to],
        "textMessage": {"text": message},
    }).encode()
    basic = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": "Basic " + basic,
            "Content-Type": "application/json",
            "User-Agent": "bland-relay/1",
        },
        method="POST",
    )
    open_ = opener or urllib.request.urlopen
    try:
        with open_(req, timeout=15) as r:
            raw = r.read()
            status = getattr(r, "status", 0)
        if not (200 <= int(status) < 300):
            return None, f"HTTP {status}"
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return None, type(e).__name__
    try:
        data = json.loads(raw.decode() or "{}")
    except Exception:  # noqa: BLE001
        return None, "bad gateway response"
    if not isinstance(data, dict):
        return None, "bad gateway response"
    msg_id = data.get("id")
    state = data.get("state")
    if msg_id is not None and not isinstance(msg_id, str):
        msg_id = str(msg_id)
    if state is not None and not isinstance(state, str):
        state = str(state)
    return {"id": msg_id, "state": state}, ""


def _tail4(to):
    digits = re.sub(r"\D", "", to or "")
    return digits[-4:] if len(digits) >= 4 else "----"


def smsgate_signature_ok(key, body, ts, sig, now=None):
    """SMSGate payload signing: hex HMAC-SHA256(key, raw_body + X-Timestamp).

    X-Timestamp is Unix seconds; reject outside +/- SMSGATE_SKEW_S. Returns
    (ok, reason) where reason is "ok", "missing", "stale" or "bad".
    """
    ts = (ts or "").strip()
    sig = (sig or "").strip().lower()
    if sig.startswith("sha256="):
        sig = sig[7:]
    if not ts or not sig:
        return False, "missing"
    try:
        ts_i = int(ts)
    except ValueError:
        return False, "bad"
    now = time.time() if now is None else now
    if abs(now - ts_i) > SMSGATE_SKEW_S:
        return False, "stale"
    expected = hmac.new(key.encode(), body + ts.encode(), hashlib.sha256).hexdigest()
    if len(sig) != len(expected) or not hmac.compare_digest(expected, sig):
        return False, "bad"
    return True, "ok"


def _seen_event(event_id, now=None):
    if not event_id:
        return False
    now = time.time() if now is None else now
    with _seen_lock:
        for k in [k for k, at in _seen_events.items() if now - at > 2 * 24 * 3600]:
            _seen_events.pop(k, None)
        return event_id in _seen_events


def _mark_event(event_id, now=None):
    if not event_id:
        return
    now = time.time() if now is None else now
    with _seen_lock:
        _seen_events[event_id] = now
        while len(_seen_events) > SMSGATE_SEEN_MAX:
            _seen_events.pop(next(iter(_seen_events)))


def _text_key(text):
    """Whitespace- and case-insensitive body key for cross-path dedupe."""
    return " ".join(str(text or "").split()).casefold()


def _same_sender(a_num, a_name, b_num, b_name):
    if a_num and b_num:
        return a_num == b_num
    if not a_num and not b_num:
        return bool(a_name) and a_name == b_name
    # One side only has an unmapped contact name: the body decides.
    return True


def _recent_find(num, name, tkey, srcs, now=None):
    """Matching entry from srcs within RCS_DEDUPE_S. Caller holds _recent_lock."""
    now = time.time() if now is None else now
    _recent_msgs[:] = [e for e in _recent_msgs if now - e["at"] <= RCS_DEDUPE_S][-RCS_RECENT_MAX:]
    for e in _recent_msgs:
        if e["src"] in srcs and e["tkey"] == tkey and _same_sender(num, name, e["num"], e["name"]):
            return e
    return None


def _recent_claim(src, num, name, tkey, check_srcs, now=None):
    """Atomically: None if a matching entry exists, else add and return a new one."""
    now = time.time() if now is None else now
    with _recent_lock:
        if _recent_find(num, name, tkey, check_srcs, now):
            return None
        entry = {"at": now, "src": src, "num": num, "name": name, "tkey": tkey}
        _recent_msgs.append(entry)
        return entry


def _recent_drop(entry):
    if entry is None:
        return
    with _recent_lock:
        try:
            _recent_msgs.remove(entry)
        except ValueError:
            pass


def _rcs_wait_s():
    raw = env("RCS_DEDUPE_WAIT_S")
    if not raw:
        return RCS_WAIT_DEFAULT_S
    try:
        return max(0.0, min(float(raw), 20.0))
    except ValueError:
        return RCS_WAIT_DEFAULT_S


def rcs_name_map():
    """RCS_NAME_MAP: contact name -> number. Keys match case- and space-insensitively.

    Preferred form is "Name=+1NXXNXXXXXX;Other Name=+1NXXNXXXXXX" because a
    systemd EnvironmentFile strips double quotes from values. A JSON object
    also works when it reaches the process intact.
    """
    raw = env("RCS_NAME_MAP")
    if not raw:
        return {}
    data = None
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
    if not isinstance(data, dict):
        data = {}
        for part in raw.split(";"):
            name, sep, num = part.rpartition("=")
            if sep and name.strip():
                data[name.strip()] = num.strip()
    if not data:
        log.warning("RCS_NAME_MAP has no usable entries; ignoring")
        return {}
    out = {}
    for k, v in data.items():
        num = normalize_nanp(v)
        if num and str(k).strip():
            out[" ".join(str(k).split()).casefold()] = num
    return out


def _lenient_fields(body):
    """Pull "key": "value" pairs out of a template that broke JSON (unescaped quotes)."""
    text = body.decode("utf-8", "replace")
    out = {}
    keys = re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:\s*"', text)
    for i, k in enumerate(keys):
        m = re.search(r'"%s"\s*:\s*"' % re.escape(k), text)
        if not m:
            continue
        rest = text[m.end():]
        nxt = None
        for k2 in keys[i + 1:]:
            m2 = re.search(r'"\s*,\s*"%s"\s*:' % re.escape(k2), rest)
            if m2:
                nxt = m2.start()
                break
        if nxt is None:
            m3 = re.search(r'"\s*}\s*$', rest)
            nxt = m3.start() if m3 else len(rest)
        out.setdefault(k, rest[:nxt])
    return out


def parse_rcs_notification(body):
    """Notification-forwarder POST -> dict(package, title, text, number, posted_at, summary).

    Accepts JSON from a template (package/title/text, plus aliases) and falls
    back to lenient key extraction when the app did not escape quotes.
    Returns None when nothing usable is present.
    """
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        data = None
    if not isinstance(data, dict):
        data = _lenient_fields(body) if body else {}
    if not data:
        return None
    pkg = _first(data, ("package", "packagename", "pkg", "app_package"))
    title = _first(data, ("title", "sender", "conversation", "name", "contact"))
    text = _first(data, ("text", "content", "message", "body", "big_text", "bigtext"))
    number = _first(data, ("number", "phone", "from_number", "address", "from"))
    posted = _first(data, ("postedat", "post_time", "posted_at", "sentstamp", "timestamp", "time"))
    summary = _first(data, ("group_summary", "groupsummary", "is_group_summary"))
    return {
        "package": None if pkg is None else str(pkg).strip(),
        "title": None if title is None else " ".join(str(title).split()),
        "text": None if text is None else str(text).strip(),
        "number": None if number is None else str(number).strip(),
        "posted_at": posted,
        "summary": summary in (True, "true", "True", "1", 1),
        "raw": data,
    }


def resolve_rcs_sender(parsed, name_map=None):
    """-> (number or None, display name or None, how). how: number|title|name_map|unknown."""
    name_map = rcs_name_map() if name_map is None else name_map
    num = normalize_nanp(parsed.get("number"))
    title = " ".join(str(parsed.get("title") or "").split())
    if num:
        return num, (title if title and not normalize_nanp(title) else None), "number"
    num = normalize_nanp(title)
    if num:
        return num, None, "title"
    name = title[:RCS_NAME_MAX] if title else None
    if name:
        mapped = name_map.get(name.casefold())
        if mapped:
            return mapped, name, "name_map"
    return None, name, "unknown"


def _placeholder(v):
    """True for an unfilled template variable like %title% or {text}."""
    s = str(v or "").strip()
    return bool(re.fullmatch(r"%[A-Za-z_]+%|\{[A-Za-z_]+\}", s))


def _spawn(target, args=()):
    """Background alert. Tests replace this so a failure does not race the response."""
    threading.Thread(target=target, args=args, daemon=True).start()


# ---------------------------------------------------------------------------
# Turo notifications (#1104)
#
# The same notification-relay app that posts Google Messages notifications to
# the RCS route also posts Turo app notifications (package
# com.relayrides.android.relayrides). The RCS route hands those to
# turo_handle(): normalize (prefer big_text), dedupe, append to a JSONL inbox,
# drop suppressed (not-our-fleet) vehicles, classify urgency with regexes only,
# then in the background: owner_alert SMS (urgent only, one fixed number),
# Helm webhook (every non-suppressed event) and the Alexandra forward (urgent
# only, kind "turo_urgent"). Each background task is isolated, so a webhook
# failure never blocks the owner alert.
TURO_PACKAGE = "com.relayrides.android.relayrides"
# The only number owner_alert may text. TURO_OWNER_ALERT_TO must equal it;
# any other value is refused. Changing it is a code change on purpose.
TURO_OWNER_ALERT_ALLOWED = "+12073100000"
TURO_DEDUPE_DEFAULT_S = 24 * 60 * 60
TURO_PREFIX_WINDOW_S = 10 * 60
TURO_SEEN_MAX = 2000
TURO_ALERT_TEXT_CHARS = 140
TURO_GUEST_MAX = 64
TURO_TEXT_MAX = 4000
TURO_SCHEMA = "panamerica.turo.event.v1"
TURO_CLASSES = ("safety", "accident_damage", "lockout", "no_start", "charging")  # priority order
TURO_URGENT_DEFAULTS = {
    "lockout": (
        r"locked? (myself )?out|lock ?out|can'?t (get in|unlock|open (the )?(car|door))|"
        r"won'?t (unlock|open)|(doors?|car) (is |are )?(still )?locked|"
        r"(key ?card|phone ?key|key ?fob|digital key|the app|app|key) (is )?(not|isn'?t|won'?t|doesn'?t|didn'?t) work|"
        r"\bno key\b|lost (the |my )?key"
    ),
    "no_start": (
        r"won'?t (start|turn on|move|go into (drive|gear))|(doesn'?t|does not|not|isn'?t|didn'?t) start(ing)?\b|"
        r"dead (battery|12 ?v)|\b12 ?v\b|no power|screen (is )?(black|dead|blank)|stuck in park|"
        r"(car|it|truck|rivian) (is )?dead\b"
    ),
    "accident_damage": (
        r"accident|crash(ed)?\b|collision|(got|was|been|i) hit\b|hit (a|by|another|me)\b|rear.?ended|"
        r"fender|damage(d)?\b|\bdents?\b|\bdented\b|scratch|broken (window|glass|mirror|windshield)|"
        r"cracked (window|glass|windshield)|police|\btow(ed|ing| truck)?\b|flat tire|blown tire|airbags?\b"
    ),
    "charging": (
        r"won'?t charge|not charging|can'?t charge|doesn'?t charge|"
        r"charg(er|ing) (fail|error|issue|problem|broken|not work)|supercharger|"
        r"(battery|range|charge) (is )?(very |really |super )?(low|at \d)|\b\d{1,2} ?% (left|battery|charge)|"
        r"ran out of (charge|battery|range)|out of (charge|battery)|stranded"
    ),
    "safety": (
        r"emergency|\b911\b|unsafe|danger|\bsmoke\b|smoking (from|under|out)|\bfire\b|on fire|burning|"
        r"injur|\bhurt\b|ambulance|hospital|\bhelp!|\burgent\b|\basap\b|stuck on (the )?(road|highway|freeway|interstate)"
    ),
}
TURO_URGENT_NEG_DEFAULT = (
    r"before (i |we )?(return|drop.?off|pick.?up)|already (there|on file|documented|noted|in the photos)|"
    r"pre.?existing|instructions?|no smoking|smoking (allowed|policy)|non.?smok|"
    r"(thanks|thank you) (so much )?for (the|your|all the) help|"
    r"\bno (damage|scratch(es)?|dents?|issues?|problems?)\b|in case of (an )?(emergency|accident)"
)
TURO_SUPPRESS_DEFAULT = r"safe ?wheels"
TURO_TRIP_CTX_RE = re.compile(
    r"(?:trip|reservation|booking|res)\b\s*(?:#|id|no\.?|number|num)?\s*[:#]?\s*(\d{7,9})(?!\d)", re.IGNORECASE)
TURO_TRIP_BARE_RE = re.compile(r"(?<![\d$.,:/-])(\d{7,9})(?![\d.,:/-]?\d)")
TURO_PLATES = {"24EWUH": "Toyota Corolla 24EWUH", "25EWUH": "Toyota Corolla 25EWUH"}
TURO_VEHICLE_RE = re.compile(
    r"\b(?:(?:19|20)\d{2}\s+)?(?:"
    r"rivian(?:\s+r1[st])?|r1[st]|toyota(?:\s+\w+)?|corolla|tesla(?:\s+model\s*[3sxy])?|model\s*[3sxy]\b|"
    r"honda(?:\s+\w+)?|nissan(?:\s+\w+)?|hyundai(?:\s+\w+)?|kia(?:\s+\w+)?|ford(?:\s+\w+)?|"
    r"chevy(?:\s+\w+)?|chevrolet(?:\s+\w+)?|bmw(?:\s+\w+)?|mercedes(?:-benz)?(?:\s+\w+)?|"
    r"jeep(?:\s+\w+)?|subaru(?:\s+\w+)?|mazda(?:\s+\w+)?|volkswagen(?:\s+\w+)?|vw\s+\w+|audi(?:\s+\w+)?|"
    r"lexus(?:\s+\w+)?|polestar(?:\s+\d)?|lucid(?:\s+air)?)\b", re.IGNORECASE)
TURO_TITLE_SPLIT_RE = re.compile(r"\s+(?:•|·|\||–|—|-)\s+")
TURO_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
TURO_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TURO_SENTENCE_RE = re.compile(r"(?<=[.!?\n])\s+|\n+")

_turo_lock = threading.Lock()
_turo_prune = {"at": 0.0}
_turo_regex_cache = {}


def _turo_int(name, default, lo, hi):
    return _int_env(name, default, lo, hi)


def turo_disabled():
    return env("TURO_HOOK_DISABLED") == "1"


def _turo_state_path():
    return _state_dir() / "turo_state.json"


def turo_inbox_path():
    override = env("TURO_INBOX_PATH")
    return Path(override) if override else _state_dir() / "turo_inbox.jsonl"


def _turo_compile(name, default):
    """Env override (case-insensitive) or the code default. A bad override logs and falls back."""
    raw = env(name) or default
    hit = _turo_regex_cache.get((name, raw))
    if hit is not None:
        return hit
    try:
        rx = re.compile(raw, re.IGNORECASE) if raw else None
    except re.error:
        log.warning("%s is not a valid regex; using the default", name)
        rx = re.compile(default, re.IGNORECASE) if default else None
    _turo_regex_cache[(name, raw)] = rx
    return rx


def turo_urgent_patterns():
    return {c: _turo_compile("TURO_URGENT_RE_" + c.upper(), TURO_URGENT_DEFAULTS[c]) for c in TURO_CLASSES}


def classify_turo(text):
    """-> (class or None, matched term or None). Regex only. A sentence matching
    TURO_URGENT_NEG is skipped, so "the scratch was already there" is not urgent
    but "already there. I just got rear-ended" still is."""
    if not text:
        return None, None
    neg = _turo_compile("TURO_URGENT_NEG", TURO_URGENT_NEG_DEFAULT)
    sentences = [s for s in TURO_SENTENCE_RE.split(str(text)) if s and s.strip()]
    kept = [s for s in sentences if not (neg and neg.search(s))]
    pats = turo_urgent_patterns()
    for cls in TURO_CLASSES:
        rx = pats.get(cls)
        if rx is None:
            continue
        for s in kept:
            m = rx.search(s)
            if m:
                return cls, m.group(0)[:40]
    return None, None


def turo_suppressed(*fields):
    rx = _turo_compile("TURO_SUPPRESS_RE", TURO_SUPPRESS_DEFAULT)
    if rx is None:
        return False
    hay = " \n".join(str(f) for f in fields if f)
    return bool(hay and rx.search(hay))


def parse_turo_reservation(*fields):
    """7-9 digit trip number. Labeled ("Trip #12345678") anywhere first, then a
    bare number in the order given (title, sub_text, text). None if absent."""
    for f in fields:
        if f:
            m = TURO_TRIP_CTX_RE.search(str(f))
            if m:
                return m.group(1)
    for f in fields:
        if f:
            m = TURO_TRIP_BARE_RE.search(str(f))
            if m:
                return m.group(1)
    return None


def parse_turo_vehicle(*fields):
    """-> (vehicle or None, plate or None). Our plates map to a canonical name."""
    hay = " \n".join(str(f) for f in fields if f)
    if not hay:
        return None, None
    up = re.sub(r"[\s-]", "", hay.upper())
    for plate, name in TURO_PLATES.items():
        if plate in up:
            return name, plate
    m = TURO_VEHICLE_RE.search(hay)
    if not m:
        return None, None
    v = " ".join(m.group(0).split())
    low = v.lower()
    if re.search(r"rivian|r1s|r1t", low):
        return ("Rivian R1T" if "r1t" in low else "Rivian R1S"), None
    if "corolla" in low:
        return "Toyota Corolla", None
    return v[:40], None


def _turo_guest(title):
    """Title minus a " • vehicle" style suffix. -> (guest or None, suffix or None)."""
    t = " ".join(str(title or "").split())
    if not t or _placeholder(t):
        return None, None
    parts = TURO_TITLE_SPLIT_RE.split(t, maxsplit=1)
    guest = parts[0].strip()[:TURO_GUEST_MAX] or None
    rest = parts[1].strip() if len(parts) > 1 else None
    return guest, rest


def _turo_posted_iso(posted):
    """postedAt (epoch ms or s) -> UTC ISO; any other string passes through."""
    if posted in (None, ""):
        return None
    try:
        v = float(posted)
    except (TypeError, ValueError):
        return str(posted)[:40]
    if v > 1e12:
        v /= 1000.0
    if v <= 0:
        return None
    return _iso(v)


def turo_redact(text):
    s = TURO_EMAIL_RE.sub("[email]", str(text or ""))
    return TURO_PHONE_RE.sub("[phone]", s)


def _turo_field(raw, keys):
    v = _first(raw, keys) if isinstance(raw, dict) else None
    if v is None or _placeholder(v):
        return None
    s = str(v).strip()
    return s or None


def normalize_turo(parsed, now=None):
    """Notification -> Turo event dict, or None when there is no text."""
    now = time.time() if now is None else now
    raw = parsed.get("raw") if isinstance(parsed.get("raw"), dict) else {}
    text = _turo_field(raw, ("big_text", "bigtext")) or _turo_field(raw, ("text", "content", "message", "body"))
    if not text:
        return None
    text = text[:TURO_TEXT_MAX]
    title = _turo_field(raw, ("title", "sender", "conversation", "name", "contact"))
    sub_text = _turo_field(raw, ("sub_text", "subtext", "summary_text"))
    category = _turo_field(raw, ("category",))
    nkey = _turo_field(raw, ("key", "notification_key", "id"))
    guest, title_rest = _turo_guest(title)
    vehicle, plate = parse_turo_vehicle(title_rest, sub_text, text)
    cls, term = classify_turo(text)
    tkey = _text_key(text)
    eid = hashlib.sha256(f"{nkey or ''}|{tkey}".encode("utf-8")).hexdigest()[:16]
    return {
        "kind": "turo_event",
        "schema": TURO_SCHEMA,
        "src": "turo",
        "event_id": eid,
        "guest": guest,
        "reservation_id": parse_turo_reservation(title, sub_text, text),
        "vehicle": vehicle,
        "plate": plate,
        "text": text,
        "sub_text": sub_text,
        "category": category,
        "posted_at": _turo_posted_iso(parsed.get("posted_at")),
        "received_at": _iso(now),
        "key": nkey,
        "urgent": cls is not None,
        "urgent_class": cls,
        "matched_term": term,
        "line": LINE_904,
    }


def _turo_dup_locked(st, ev, now):
    """Caller holds _turo_lock. True when ev repeats a recent event; else records it."""
    window = _turo_int("TURO_DEDUPE_WINDOW_S", TURO_DEDUPE_DEFAULT_S, 60, 7 * 24 * 3600)
    tkey = _text_key(ev["text"])
    h = hashlib.sha256(f"{ev.get('key') or ''}|{tkey}".encode("utf-8")).hexdigest()
    th = hashlib.sha256(tkey.encode("utf-8")).hexdigest()
    gkey = (ev.get("guest") or "").casefold()
    seen = [r for r in (st.get("seen") or []) if isinstance(r, dict) and now - float(r.get("at") or 0) <= window]
    for r in seen:
        if r.get("h") == h:
            st["seen"] = seen
            return True
        if r.get("g") == gkey and r.get("th") == th:
            st["seen"] = seen
            return True
        # A shorter copy of a text already seen from this guest (re-post of a
        # truncated notification). A longer text is new content and passes.
        if (r.get("g") == gkey and now - float(r.get("at") or 0) <= TURO_PREFIX_WINDOW_S
                and len(tkey) >= 8 and str(r.get("p") or "").startswith(tkey[:200])
                and len(tkey) < int(r.get("n") or 0)):
            st["seen"] = seen
            return True
    seen.append({"at": now, "h": h, "th": th, "g": gkey, "p": tkey[:200], "n": len(tkey)})
    st["seen"] = seen[-TURO_SEEN_MAX:]
    return False


def turo_claim(ev, now=None):
    """Atomically record ev. False when it is a duplicate."""
    now = time.time() if now is None else now
    with _turo_lock:
        st = _load_json(_turo_state_path())
        dup = _turo_dup_locked(st, ev, now)
        try:
            _save_json(_turo_state_path(), st)
        except OSError as e:
            log.error("turo state write failed: %s", type(e).__name__)
    return not dup


def turo_prune_inbox(now=None, force=False):
    """Drop inbox lines older than TURO_RETENTION_DAYS (default 30). Never raises."""
    now = time.time() if now is None else now
    if not force and now - _turo_prune["at"] < 24 * 3600:
        return 0
    _turo_prune["at"] = now
    days = _turo_int("TURO_RETENTION_DAYS", 30, 1, 3650)
    cutoff = _iso(now - days * 24 * 3600)
    p = turo_inbox_path()
    try:
        with _turo_lock:
            if not p.exists():
                return 0
            keep, dropped = [], 0
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    at = str(json.loads(line).get("received_at") or "")
                except (ValueError, AttributeError):
                    at = ""
                if at and at < cutoff:
                    dropped += 1
                else:
                    keep.append(line)
            if dropped:
                tmp = p.with_suffix(".tmp")
                tmp.write_text("".join(x + "\n" for x in keep), encoding="utf-8")
                os.chmod(tmp, 0o600)
                os.replace(tmp, p)
            return dropped
    except OSError as e:
        log.error("turo inbox prune failed: %s", type(e).__name__)
        return 0


def turo_append_inbox(ev, suppressed, now=None):
    """JSONL inbox line (0600). Phone numbers and emails in text are redacted."""
    row = dict(ev)
    row["text"] = turo_redact(ev.get("text"))
    row["sub_text"] = turo_redact(ev.get("sub_text")) if ev.get("sub_text") else ev.get("sub_text")
    row["suppressed"] = bool(suppressed)
    try:
        with _turo_lock:
            _append_jsonl(turo_inbox_path(), row)
    except OSError as e:
        log.error("turo inbox write failed: %s", type(e).__name__)
    turo_prune_inbox(now)


def owner_alert_message(ev):
    text = " ".join(str(ev.get("text") or "").split())[:TURO_ALERT_TEXT_CHARS]
    guest = ev.get("guest") or "guest"
    return f"URGENT Turo [{ev.get('urgent_class')}]: {guest}: {text}"


def owner_alert(ev, now=None):
    """Templated 904 SMS to the one allowed owner number, urgent events only.

    Skips only the 72-hour inbound window, and only for TURO_OWNER_ALERT_ALLOWED.
    Respects SMS_SEND_DISABLED, TURO_OWNER_ALERT_DISABLED, STOP, alert dedupe,
    TURO_ALERT_GUEST_MAX per TURO_ALERT_GUEST_WINDOW_S, TURO_ALERT_HOUR_MAX and the
    relay-wide RECIPIENT_MAX/GLOBAL_MAX. Returns "sent" or the skip reason.
    """
    now = time.time() if now is None else now
    if not ev.get("urgent") or not ev.get("urgent_class"):
        return "not_urgent"
    if env("TURO_OWNER_ALERT_DISABLED") == "1":
        log.warning("turo owner alert skipped: TURO_OWNER_ALERT_DISABLED=1")
        return "alert_disabled"
    raw_to = env("TURO_OWNER_ALERT_TO")
    if not raw_to:
        log.warning("turo owner alert skipped: TURO_OWNER_ALERT_TO unset")
        return "owner_unset"
    to = normalize_nanp(raw_to)
    if to != TURO_OWNER_ALERT_ALLOWED:
        log.error("!!! turo owner alert refused: TURO_OWNER_ALERT_TO is not the allowed owner number !!!")
        return "owner_not_allowed"
    if env("SMS_SEND_DISABLED") == "1":
        log.warning("turo owner alert skipped: SMS_SEND_DISABLED=1")
        return "send_disabled"
    if not gateway_configured():
        log.error("turo owner alert skipped: gateway not configured")
        return "gateway_unconfigured"
    guest_w = _turo_int("TURO_ALERT_GUEST_WINDOW_S", 600, 0, 24 * 3600)
    guest_max = _turo_int("TURO_ALERT_GUEST_MAX", 1, 1, 100)
    hour_max = _turo_int("TURO_ALERT_HOUR_MAX", 6, 1, 100)
    dedupe_w = _turo_int("TURO_DEDUPE_WINDOW_S", TURO_DEDUPE_DEFAULT_S, 60, 7 * 24 * 3600)
    gkey = (ev.get("guest") or "").casefold()
    th = hashlib.sha256(_text_key(ev.get("text")).encode("utf-8")).hexdigest()
    with _sms_lock:
        st = _load_json(_sms_state_path())
        inbound = st.get("inbound") if isinstance(st.get("inbound"), dict) else {}
        if (inbound.get(to) or {}).get("opt_out"):
            log.warning("turo owner alert skipped: owner opted out (STOP)")
            return "opt_out"
        keep_s = max(dedupe_w, GLOBAL_WINDOW_S, guest_w)
        alerts = [a for a in (st.get("turo_alerts") or [])
                  if isinstance(a, dict) and now - float(a.get("at") or 0) < keep_s]
        reason = None
        if any(a.get("g") == gkey and a.get("th") == th and now - float(a["at"]) < dedupe_w for a in alerts):
            reason = "duplicate"
        elif sum(1 for a in alerts if a.get("g") == gkey and now - float(a["at"]) < guest_w) >= guest_max:
            reason = "rate_limited_guest"
        elif sum(1 for a in alerts if now - float(a["at"]) < GLOBAL_WINDOW_S) >= hour_max:
            reason = "rate_limited_hour"
        sends = [{"to": r.get("to"), "at": float(r.get("at") or 0)} for r in (st.get("sends") or [])
                 if isinstance(r, dict) and now - float(r.get("at") or 0) < GLOBAL_WINDOW_S]
        if reason is None:
            per = sum(1 for r in sends if r.get("to") == to and now - r["at"] < RECIPIENT_WINDOW_S)
            if per >= RECIPIENT_MAX or len(sends) >= GLOBAL_MAX:
                reason = "rate_limited"
        if reason:
            log.warning("turo owner alert skipped: %s class=%s", reason, ev.get("urgent_class"))
            return reason
        # Reserve before the gateway call, like reserve_send_ex: a failed call
        # still consumes the slot so retries cannot multiply SMS.
        alerts.append({"at": now, "g": gkey, "th": th})
        sends.append({"to": to, "at": now})
        st["turo_alerts"] = alerts
        st["sends"] = sends
        try:
            _save_json(_sms_state_path(), st)
        except OSError as e:
            log.error("sms state write failed: %s", type(e).__name__)
            return "state_write_failed"
    msg = owner_alert_message(ev)
    result, err = gateway_send(to, msg)
    if result is None:
        log.error("turo owner alert gateway failed to=***%s error=%s", _tail4(to), _scrub(err))
        alert_failure("turo-owner-alert", err, 1, "Turo owner alert SMS failed")
        return "gateway_failed"
    log.info("turo owner alert sent to=***%s class=%s len=%d id=%s", _tail4(to), ev.get("urgent_class"),
             len(msg), result.get("id") or "-")
    return "sent"


def _turo_timeout():
    return float(_turo_int("TURO_WEBHOOK_TIMEOUT_S", 5, 1, 15))


def helm_auth_header():
    """-> (header name, value) or None. Default mirrors ALEXANDRA_ALERT_AUTH:
    "Authorization: Bearer <key>" (a key that already has a scheme is sent as is).
    HELM_TURO_WEBHOOK_HEADER picks another header (value = the bare key)."""
    key = env("HELM_TURO_WEBHOOK_KEY")
    if not key:
        return None
    name = env("HELM_TURO_WEBHOOK_HEADER") or "Authorization"
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", name):
        log.warning("HELM_TURO_WEBHOOK_HEADER invalid; using Authorization")
        name = "Authorization"
    if name.lower() == "authorization" and " " not in key:
        return name, "Bearer " + key
    return name, key


def post_json(url, obj, headers=None, timeout=5.0, tries=2):
    """-> (status or None, error, attempts). Short timeouts; never raises."""
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    hdrs = {"Content-Type": "application/json", "User-Agent": "bland-relay/1"}
    hdrs.update(headers or {})
    last = ""
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if 200 <= r.status < 300:
                    return r.status, "", attempt
                last = f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            last = type(e).__name__
        if attempt < tries:
            time.sleep(1)
    return None, last, tries


def helm_push(ev):
    """POST one non-suppressed event to HELM_TURO_WEBHOOK_URL. Unset URL = JSONL only."""
    url = env("HELM_TURO_WEBHOOK_URL")
    if not url:
        return "unset"
    hdr = helm_auth_header()
    try:
        status, err, attempts = post_json(url, ev, dict([hdr]) if hdr else None, _turo_timeout())
    except Exception as e:  # noqa: BLE001
        status, err, attempts = None, type(e).__name__, 1
    if status is not None:
        log.info("turo helm push ok event=%s urgent=%s -> %d", ev.get("event_id"), ev.get("urgent"), status)
        return "ok"
    log.error("turo helm push failed event=%s error=%s", ev.get("event_id"), _scrub(err))
    alert_failure("turo-helm-webhook", err, attempts, "Turo Helm webhook failed")
    return "failed"


def alexandra_turo_push(ev):
    """Urgent events to Alexandra through the existing forward()/ALEXANDRA_ALERT_URL."""
    if not ev.get("urgent"):
        return "not_urgent"
    if env("TURO_ALEX_PUSH_DISABLED") == "1":
        return "disabled"
    if not env("ALEXANDRA_ALERT_URL"):
        return "unset"
    obj = dict(ev)
    obj["kind"] = "turo_urgent"
    try:
        status, err, attempts = forward(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                                        timeout=_turo_timeout())
    except Exception as e:  # noqa: BLE001
        status, err, attempts = None, type(e).__name__, 1
    if status is not None:
        log.info("turo alexandra push ok event=%s -> %d", ev.get("event_id"), status)
        return "ok"
    log.error("turo alexandra push failed event=%s error=%s", ev.get("event_id"), _scrub(err))
    alert_failure("turo-alexandra-push", err, attempts, "Turo urgent push to Alexandra failed")
    return "failed"


def _turo_safe(fn, *args):
    try:
        fn(*args)
    except Exception as e:  # noqa: BLE001 - one task must never take down another
        log.error("turo %s crashed: %s", getattr(fn, "__name__", "task"), type(e).__name__)


def turo_handle(parsed, now=None):
    """-> (http status, response body). Never texts for non-urgent events."""
    now = time.time() if now is None else now
    if turo_disabled():
        log.info("turo notification ignored (TURO_HOOK_DISABLED=1)")
        return 200, {"ok": True, "ignored": "turo_disabled"}
    if parsed.get("summary"):
        return 200, {"ok": True, "ignored": "empty"}
    ev = normalize_turo(parsed, now)
    if ev is None:
        log.info("turo notification ignored (empty text)")
        return 200, {"ok": True, "ignored": "empty"}
    if not turo_claim(ev, now):
        log.info("turo duplicate event=%s", ev["event_id"])
        return 200, {"ok": True, "turo": "duplicate"}
    raw = parsed.get("raw") if isinstance(parsed.get("raw"), dict) else {}
    title = _turo_field(raw, ("title",))
    if turo_suppressed(title, ev.get("sub_text"), ev.get("vehicle"), ev.get("text")):
        turo_append_inbox(ev, True, now)
        log.info("turo suppressed (not our fleet) event=%s", ev["event_id"])
        return 200, {"ok": True, "turo": "suppressed"}
    turo_append_inbox(ev, False, now)
    log.info("turo event=%s urgent=%s class=%s len=%d trip=%s vehicle=%s",
             ev["event_id"], ev["urgent"], ev["urgent_class"] or "-", len(ev["text"]),
             bool(ev["reservation_id"]), bool(ev["vehicle"]))
    if ev["urgent"]:
        _spawn(_turo_safe, (owner_alert, ev, now))
        _spawn(_turo_safe, (alexandra_turo_push, ev))
    _spawn(_turo_safe, (helm_push, ev))
    return 200, {"ok": True, "turo": "accepted", "urgent": ev["urgent"]}


class H(BaseHTTPRequestHandler):
    server_version = "relay"
    sys_version = ""

    def log_message(self, fmt, *args):  # no paths (secret) in logs
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _nf(self):
        self._send(404, {"ok": False, "error": "not found"})

    do_GET = do_PUT = do_DELETE = do_PATCH = do_HEAD = _nf

    def _read_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            return None
        return self.rfile.read(n)

    def _fwd_904(self):
        tok = env("FWD_TOKEN")
        if not tok:
            log.error("!!! FWD_TOKEN unset - rejecting 904 forwarder (503) !!!")
            return self._send(503, {"ok": False, "error": "relay not configured"})
        got = token_from_headers(self.headers)
        if not token_ok(got, tok):
            log.warning("904 forwarder: bad/missing token (len %d)", len(got))
            return self._send(401, {"ok": False, "error": "unauthorized"})
        body = self._read_body()
        if body is None:
            return self._send(413, {"ok": False, "error": "bad length"})
        out_obj = normalize_904(body, self.headers.get("Content-Type") or "")
        if not out_obj.get("test"):
            try:
                note_inbound(out_obj.get("from"), out_obj.get("body"), via="forwarder")
            except Exception as e:  # noqa: BLE001
                log.error("inbound note failed: %s", type(e).__name__)
        out = json.dumps(out_obj, ensure_ascii=False).encode("utf-8")
        status, err, attempts = forward(out)
        if status is not None:
            log.info("904 forwarder ok: %d bytes in, %d forwarded -> %d", len(body), len(out), status)
            return self._send(200, {"ok": True})
        _spawn(alert_failure, ("904-sms-forwarder", err, attempts))
        return self._send(502, {"ok": False, "error": "forward failed"})

    def _smsgate_inbound(self):
        body = self._read_body()
        if body is None:
            return self._send(413, {"ok": False, "error": "bad length"})
        key = env("SMSGW_SIGNING_KEY")
        if key:
            ok, why = smsgate_signature_ok(
                key, body, self.headers.get("X-Timestamp"), self.headers.get("X-Signature"),
            )
            if not ok:
                log.warning("smsgate webhook: signature %s (%d bytes)", why, len(body))
                return self._send(401, {"ok": False, "error": "bad signature"})
        try:
            data = json.loads(body)
        except Exception:  # noqa: BLE001
            data = None
        if not isinstance(data, dict):
            log.warning("smsgate webhook: bad json (%d bytes)", len(body))
            return self._send(400, {"ok": False, "error": "bad json"})
        event = data.get("event")
        if event != "sms:received":
            # 2xx so the phone does not retry an event this route does not handle.
            log.info("smsgate webhook: ignored event=%s", re.sub(r"[^a-z:_-]", "", str(event))[:32] or "-")
            return self._send(200, {"ok": True, "ignored": True})
        out_obj = normalize_smsgate(data)
        if out_obj is None:
            log.warning("smsgate webhook: sms:received without sender")
            return self._send(400, {"ok": False, "error": "bad payload"})
        event_id = re.sub(r"[^A-Za-z0-9_.:-]", "", str(data.get("id") or ""))[:64]
        if _seen_event(event_id):
            log.info("smsgate webhook: duplicate event id=%s", event_id)
            return self._send(200, {"ok": True, "duplicate": True})
        try:
            note_inbound(out_obj.get("from"), out_obj.get("body"), via="smsgate")
        except Exception as e:  # noqa: BLE001
            log.error("inbound note failed: %s", type(e).__name__)
        tail = _tail4(out_obj.get("from") or "")
        sg_num = normalize_nanp(out_obj.get("from")) or str(out_obj.get("from"))
        claim = _recent_claim("sms", sg_num, None, _text_key(out_obj.get("body")), ("rcs",))
        if claim is None:
            _mark_event(event_id)
            log.info("smsgate inbound duplicate of rcs: from=***%s id=%s", tail, event_id or "-")
            return self._send(200, {"ok": True, "duplicate": True})
        out = json.dumps(out_obj, ensure_ascii=False).encode("utf-8")
        status, err, attempts = forward(out)
        if status is not None:
            _mark_event(event_id)
            log.info(
                "smsgate inbound ok: from=***%s len=%d signed=%s id=%s -> %d",
                tail, len(out_obj.get("body") or ""), bool(key), event_id or "-", status,
            )
            return self._send(200, {"ok": True})
        _recent_drop(claim)
        _spawn(alert_failure, ("904-smsgate-webhook", err, attempts))
        return self._send(502, {"ok": False, "error": "forward failed"})

    def _rcs_inbound(self):
        if env("RCS_HOOK_DISABLED") == "1":
            log.warning("rcs inbound disabled (RCS_HOOK_DISABLED=1)")
            return self._send(503, {"ok": False, "error": "rcs inbound disabled"})
        tok = env("RCS_HOOK_TOKEN")
        if tok:
            got = token_from_headers(self.headers)
            if not token_ok(got, tok):
                log.warning("rcs webhook: bad/missing token (len %d)", len(got))
                return self._send(401, {"ok": False, "error": "unauthorized"})
        body = self._read_body()
        if body is None:
            return self._send(413, {"ok": False, "error": "bad length"})
        parsed = parse_rcs_notification(body)
        if parsed is None:
            log.warning("rcs webhook: unparseable body (%d bytes)", len(body))
            return self._send(400, {"ok": False, "error": "bad payload"})
        pkg = parsed.get("package")
        if pkg == TURO_PACKAGE:
            return self._send(*turo_handle(parsed))
        if pkg and not _placeholder(pkg) and pkg != RCS_PACKAGE:
            log.info("rcs webhook: ignored package")
            return self._send(200, {"ok": True, "ignored": "package"})
        text = parsed.get("text")
        if parsed.get("summary") or not text or _placeholder(text):
            log.info("rcs webhook: ignored (summary or empty text)")
            return self._send(200, {"ok": True, "ignored": "empty"})
        if _placeholder(parsed.get("title")):
            parsed["title"] = None
        num, name, how = resolve_rcs_sender(parsed)
        if not num and not name:
            log.warning("rcs webhook: no sender")
            return self._send(400, {"ok": False, "error": "bad payload"})
        tkey = _text_key(text)
        tail = _tail4(num or "")
        with _recent_lock:
            seen = _recent_find(num, name, tkey, ("sms", "rcs"))
        if seen is not None:
            log.info("rcs inbound duplicate of %s: from=***%s how=%s", seen["src"], tail, how)
            return self._send(200, {"ok": True, "duplicate": True})
        # Google Messages also posts a notification for plain SMS. Give the
        # SMSGate webhook a short head start so that path wins and keeps its
        # event id, and this one is dropped (#1056 AC2).
        deadline = time.time() + _rcs_wait_s()
        while time.time() < deadline:
            with _recent_lock:
                seen = _recent_find(num, name, tkey, ("sms",))
            if seen is not None:
                log.info("rcs inbound duplicate of sms: from=***%s how=%s", tail, how)
                return self._send(200, {"ok": True, "duplicate": True})
            time.sleep(0.25)
        claim = _recent_claim("rcs", num, name, tkey, ("sms", "rcs"))
        if claim is None:
            log.info("rcs inbound duplicate (late): from=***%s how=%s", tail, how)
            return self._send(200, {"ok": True, "duplicate": True})
        if num:
            try:
                note_inbound(num, text, via="rcs-" + how)
            except Exception as e:  # noqa: BLE001
                log.error("inbound note failed: %s", type(e).__name__)
        out_obj = normalize_rcs(parsed, num, name)
        out = json.dumps(out_obj, ensure_ascii=False).encode("utf-8")
        status, err, attempts = forward(out)
        if status is not None:
            log.info(
                "rcs inbound ok: from=***%s how=%s len=%d token=%s -> %d",
                tail, how, len(text), bool(tok), status,
            )
            return self._send(200, {"ok": True})
        _recent_drop(claim)
        _spawn(alert_failure, ("904-rcs-notification", err, attempts))
        return self._send(502, {"ok": False, "error": "forward failed"})

    def _send_sms(self):
        if env("SMS_SEND_DISABLED") == "1":
            log.warning("sms send disabled")
            return self._send(503, {"ok": False, "error": "send disabled"})
        if not env("SEND_TOKEN") or not gateway_configured():
            log.error("sms send not configured")
            return self._send(503, {"ok": False, "error": "relay not configured"})
        got = token_from_headers(self.headers)
        if not token_ok(got, env("SEND_TOKEN")):
            log.warning("sms send: bad/missing token (len %d)", len(got))
            return self._send(401, {"ok": False, "error": "unauthorized"})
        body = self._read_body()
        if body is None:
            return self._send(413, {"ok": False, "error": "bad length"})
        try:
            data = json.loads(body)
        except Exception:  # noqa: BLE001
            data = None
        if not isinstance(data, dict):
            return self._send(400, {"ok": False, "error": "bad json"})
        to = data.get("to") if isinstance(data.get("to"), str) else ""
        message = data.get("message")
        if not isinstance(message, str):
            return self._send(400, {"ok": False, "error": "bad message"})
        message = message.strip()
        if not valid_e164(to):
            log.warning("sms send: invalid to")
            return self._send(400, {"ok": False, "error": "bad to"})
        if not message or len(message) > MAX_SMS_CHARS:
            log.warning("sms send: bad message len=%d", len(message))
            return self._send(400, {"ok": False, "error": "bad message"})
        purpose = data.get("purpose") if isinstance(data.get("purpose"), str) else None
        purpose = " ".join(purpose.split())[:FC_PURPOSE_MAX] if purpose else None
        placed_by = _fc_seat(data.get("placed_by"))
        now = time.time()
        reason, grant = reserve_send_ex(to, now)
        tail = _tail4(to)
        if reason:
            log.warning("sms send refused to=***%s reason=%s", tail, reason)
            if reason in FC_REFUSALS:
                fc_audit("send_refused", now, to=to, reason=reason)
            code = 429 if reason in ("rate_limited", "first_contact_send_cap") else 403
            return self._send(code, {"ok": False, "error": reason})
        result, err = gateway_send(to, message)
        emit_interaction(crm_interaction_record(to, grant, result, err, now, purpose, placed_by))
        if grant and grant.get("kind") == "first_contact":
            fc_audit("send", now, to=to, outcome="sent" if result is not None else "failed",
                     length=len(message), sends=(grant.get("entry") or {}).get("sends"),
                     placed_by=placed_by, gateway_id=(result or {}).get("id"))
        if result is None:
            log.error("sms send gateway failed to=***%s len=%d error=%s", tail, len(message), _scrub(err))
            _spawn(alert_failure, (f"sms-****{tail}", err, 1, "904 SMS send failed"))
            return self._send(502, {"ok": False, "error": "gateway failed"})
        log.info(
            "sms send ok to=***%s len=%d id=%s state=%s grant=%s",
            tail, len(message), result.get("id") or "-", result.get("state") or "-",
            (grant or {}).get("kind") or "-",
        )
        return self._send(200, {"ok": True, "id": result.get("id"), "state": result.get("state")})

    def _first_contact(self):
        got = token_from_headers(self.headers)
        if approver_token_ok(got):
            role = "approver"
        elif env("SEND_TOKEN") and token_ok(got, env("SEND_TOKEN")):
            role = "agent"
        else:
            log.warning("first-contact: bad/missing token (len %d)", len(got))
            return self._send(401, {"ok": False, "error": "unauthorized"})
        body = self._read_body()
        if body is None:
            return self._send(413, {"ok": False, "error": "bad length"})
        try:
            data = json.loads(body)
        except Exception:  # noqa: BLE001
            data = None
        if not isinstance(data, dict):
            return self._send(400, {"ok": False, "error": "bad json"})
        action = str(data.get("action") or "").strip().lower()
        if action == "list":
            return self._send(*fc_list(data))
        if action == "revoke":
            return self._send(*fc_revoke(data))
        if action not in ("add", "request", "approve"):
            return self._send(400, {"ok": False, "error": "action must be add, request, approve, list or revoke"})
        if action == "approve" and not fc_require_approval():
            fc_audit("deny", None, to=str(data.get("to") or "")[:20], reason="approval_not_required",
                     action="approve")
            return self._send(*fc_approve(data))  # 409 approval_not_required
        if env("SMS_SEND_DISABLED") == "1":
            log.warning("first-contact %s refused: sms send disabled", action)
            return self._send(503, {"ok": False, "error": "send disabled"})
        if fc_disabled():
            log.warning("first-contact %s refused: FC_DISABLED=1", action)
            return self._send(503, {"ok": False, "error": "first contact disabled"})
        if action in ("add", "request"):
            return self._send(*fc_request(data))
        if role != "approver":
            fc_audit("deny", None, to=str(data.get("to") or "")[:20], reason="approve_without_approver_token",
                     action="approve")
            log.warning("first-contact approve refused: not the approver token")
            return self._send(403, {"ok": False, "error": "approve requires the approver token"})
        return self._send(*fc_approve(data))

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path_is(path, send_path()):
            return self._send_sms()
        if path_is(path, fc_path()):
            return self._first_contact()
        rp = relay_path()
        if path_is(path, smsgate_path()):
            return self._smsgate_inbound()
        if path_is(path, rcs_path()):
            return self._rcs_inbound()
        if path_is(path, fwd_path()):
            return self._fwd_904()
        if not path_is(path, rp):
            return self._nf()
        body = self._read_body()
        if body is None:
            return self._send(413, {"ok": False, "error": "bad length"})
        secret = env("BLAND_WEBHOOK_SECRET")
        if not secret:
            log.error("!!! BLAND_WEBHOOK_SECRET is UNSET - rejecting webhook (503, fail closed) !!!")
            return self._send(503, {"ok": False, "error": "relay not configured"})
        sig = (self.headers.get("X-Webhook-Signature") or "").strip()
        if sig.lower().startswith("sha256="):
            sig = sig[7:]
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        _mode = "raw" if sig and hmac.compare_digest(expected, sig.lower()) else None
        if sig and not _mode:
            try:
                _compact = json.dumps(json.loads(body), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                if hmac.compare_digest(hmac.new(secret.encode(), _compact, hashlib.sha256).hexdigest(), sig.lower()):
                    _mode = "compact-json"
            except Exception:
                pass
        if _mode:
            log.info("signature ok (mode=%s)", _mode)
        if not _mode:
            _b64sig = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
            log.warning(
                "bad/missing signature (%d bytes) diag: sig_len=%d sig_hex=%s b64_match=%s hdrs=%s",
                len(body), len(sig), bool(re.fullmatch(r"[0-9a-fA-F]+", sig or "")),
                bool(sig) and hmac.compare_digest(_b64sig, sig),
                sorted(k.lower() for k in self.headers.keys()),
            )
            return self._send(401, {"ok": False, "error": "bad signature"})
        status, err, attempts = forward(body)
        if status is not None:
            log.info("forwarded %d bytes -> %d", len(body), status)
            return self._send(200, {"ok": True})
        call_id = ""
        try:
            call_id = str(json.loads(body).get("call_id") or "")
        except Exception:  # noqa: BLE001
            pass
        call_id = re.sub(r"[^A-Za-z0-9_.:-]", "", call_id)[:64]
        _spawn(alert_failure, (call_id, err, attempts))
        return self._send(502, {"ok": False, "error": "forward failed"})


def _first(d, keys):
    for k in keys:
        for dk in d:
            if dk.lower() == k and d[dk] not in (None, ""):
                return d[dk]
    return None


def normalize_904(body, ctype):
    """Phone SMS-forwarder payload -> stable shape for Alexandra's routine."""
    del ctype  # form vs json is decided by parsing, same as the live relay
    raw = None
    try:
        raw = json.loads(body)
    except Exception:  # noqa: BLE001
        try:
            from urllib.parse import parse_qs
            q = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
            raw = {k: v[0] if len(v) == 1 else v for k, v in q.items()} if q else None
        except Exception:  # noqa: BLE001
            raw = None
    if raw is None:
        raw = {"text": body.decode("utf-8", "replace")}
    d = raw if isinstance(raw, dict) else {"payload": raw}
    sender = _first(d, ("from", "sender", "phone", "number", "address", "originatingaddress", "msisdn"))
    text = _first(d, ("text", "body", "message", "msg", "content", "sms"))
    rcv = _first(d, ("receivedstamp", "received_at", "received", "timestamp", "time", "date", "sentstamp", "sent_at"))
    return {
        "source": "t-mobile-904-forwarder",
        "channel": "sms",
        "line": LINE_904,
        "from": None if sender is None else str(sender),
        "body": None if text is None else str(text),
        "received_at": rcv,
        "relayed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "test": bool(d.get("test") or d.get("forge_test")),
        "raw": raw,
    }


def normalize_smsgate(data):
    """SMSGate sms:received envelope -> the same record normalize_904 produces.

    Envelope: {deviceId, event, id, webhookId, payload: {messageId, message,
    sender, recipient, simNumber, receivedAt}}. Returns None without a sender.
    """
    p = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    sender = p.get("sender") if p.get("sender") not in (None, "") else p.get("phoneNumber")
    if sender in (None, ""):
        return None
    text = p.get("message")
    return {
        "source": "t-mobile-904-forwarder",
        "channel": "sms",
        "line": LINE_904,
        "from": str(sender),
        "body": None if text is None else str(text),
        "received_at": p.get("receivedAt"),
        "relayed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "test": False,
        "raw": data,
    }


def normalize_rcs(parsed, num, name):
    """RCS notification -> the same record normalize_904 produces (plus via/sender_name).

    Unmapped contact names are forwarded as "unknown sender <name>" and are
    never recorded for the reply allowlist.
    """
    return {
        "source": "t-mobile-904-forwarder",
        "channel": "sms",
        "line": LINE_904,
        "from": num if num else f"unknown sender {name}",
        "body": parsed.get("text"),
        "received_at": parsed.get("posted_at"),
        "relayed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "test": False,
        "via": "rcs-notification",
        "sender_name": name,
        "raw": parsed.get("raw"),
    }


def _warn_paths():
    seen = {}
    for name in ("RELAY_PATH", "FWD_PATH", "SEND_PATH", "SMSGW_HOOK_PATH", "RCS_HOOK_PATH", "FC_PATH"):
        val = _secret_path(name)
        if not val:
            continue
        if val in seen:
            log.error("secret path collision between %s and %s", seen[val], name)
        seen[val] = name


def main():
    if not env("BLAND_WEBHOOK_SECRET"):
        log.error("!!! BLAND_WEBHOOK_SECRET is UNSET - all webhooks will get 503 until set !!!")
    if not relay_path():
        log.error("!!! RELAY_PATH unset - every request 404s !!!")
    for k in ("ALEXANDRA_ALERT_URL", "ALEXANDRA_ALERT_AUTH"):
        if not env(k):
            log.warning("%s is unset", k)
    if send_path() and (not env("SEND_TOKEN") or not gateway_configured()):
        log.warning("SEND_PATH is set but the send route is not fully configured")
    if env("SMS_SEND_DISABLED") == "1":
        log.warning("SMS_SEND_DISABLED=1")
    if smsgate_path() and not env("SMSGW_SIGNING_KEY"):
        log.warning("SMSGW_HOOK_PATH set without SMSGW_SIGNING_KEY: SMSGate webhook authenticated by secret path only")
    if rcs_path() and not env("RCS_HOOK_TOKEN"):
        log.warning("RCS_HOOK_PATH set without RCS_HOOK_TOKEN: RCS route authenticated by secret path only")
    if env("RCS_HOOK_DISABLED") == "1":
        log.warning("RCS_HOOK_DISABLED=1")
    if turo_disabled():
        log.warning("TURO_HOOK_DISABLED=1")
    owner = env("TURO_OWNER_ALERT_TO")
    if owner and normalize_nanp(owner) != TURO_OWNER_ALERT_ALLOWED:
        log.error("!!! TURO_OWNER_ALERT_TO is not the allowed owner number - Turo owner alerts refused !!!")
    log.info("turo: disabled=%s owner_alert=%s helm_webhook=%s alexandra_push=%s",
             turo_disabled(), bool(owner) and env("TURO_OWNER_ALERT_DISABLED") != "1",
             bool(env("HELM_TURO_WEBHOOK_URL")),
             bool(env("ALEXANDRA_ALERT_URL")) and env("TURO_ALEX_PUSH_DISABLED") != "1")
    turo_prune_inbox(force=True)
    if fc_path() and not fc_require_approval():
        log.warning("first-contact: FC_REQUIRE_APPROVAL=0 - numbers are allowed as soon as they are added")
        if env("FC_APPROVER_NUMBERS") or env("FC_APPROVE_TOKEN_SHA256"):
            log.info("first-contact: approver numbers/token ignored while approval is off")
    elif fc_path():
        if not env("FC_APPROVE_TOKEN_SHA256"):
            log.warning("FC_PATH set without FC_APPROVE_TOKEN_SHA256: chat approvals off (SMS approvals only)")
        elif env("SEND_TOKEN") and _sha256(env("SEND_TOKEN")) == env("FC_APPROVE_TOKEN_SHA256").lower():
            log.error("!!! FC_APPROVE_TOKEN equals SEND_TOKEN - chat approvals refused !!!")
        if not fc_approver_numbers():
            log.warning("FC_APPROVER_NUMBERS empty: SMS approvals off")
    if fc_path():
        log.info("first-contact: require_approval=%s daily_max=%d ttl_days=%d max_sends_before_reply=%d "
                 "disabled=%s", fc_require_approval(), fc_daily_max(), fc_ttl_s() // FC_DAY_S,
                 fc_max_sends(), fc_disabled())
    _warn_paths()
    srv = ThreadingHTTPServer((HOST, PORT), H)
    log.info("listening on %s:%d", HOST, PORT)
    srv.serve_forever()


if __name__ == "__main__":
    main()
