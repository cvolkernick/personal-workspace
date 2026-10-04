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
Gateway for Android.

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


def forward(body):
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
            with urllib.request.urlopen(req, timeout=10) as r:
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


def note_inbound(sender, text, now=None):
    """Record that this number texted 904. Opt-out sticks until START/UNSTOP/YES."""
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
            _save_json(_sms_state_path(), st)
        except OSError as e:
            log.error("sms state write failed: %s", type(e).__name__)


def reserve_send(to, now=None):
    """Allowlist + rate limit. On success, record the attempt before the gateway call.

    A failed gateway call still consumes a slot so a retry storm cannot multiply.
    Returns None when the send may proceed, otherwise not_allowlisted, opt_out,
    or rate_limited. Opt-out wins even after the 72-hour window.
    """
    now = time.time() if now is None else now
    with _sms_lock:
        st = _load_json(_sms_state_path())
        inbound = st.get("inbound") if isinstance(st.get("inbound"), dict) else {}
        rec = inbound.get(to)
        if rec and rec.get("opt_out"):
            return "opt_out"
        last = float(rec.get("last_seen") or 0) if rec else 0.0
        if not rec or now - last > ALLOWLIST_S:
            return "not_allowlisted"
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
            return "rate_limited"
        sends.append({"to": to, "at": now})
        st["sends"] = sends
        try:
            _save_json(_sms_state_path(), st)
        except OSError as e:
            log.error("sms state write failed: %s", type(e).__name__)
            return "rate_limited"
        return None


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
                note_inbound(out_obj.get("from"), out_obj.get("body"))
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
            note_inbound(out_obj.get("from"), out_obj.get("body"))
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
                note_inbound(num, text)
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
        reason = reserve_send(to)
        tail = _tail4(to)
        if reason:
            log.warning("sms send refused to=***%s reason=%s", tail, reason)
            code = 429 if reason == "rate_limited" else 403
            return self._send(code, {"ok": False, "error": reason})
        result, err = gateway_send(to, message)
        if result is None:
            log.error("sms send gateway failed to=***%s len=%d error=%s", tail, len(message), _scrub(err))
            _spawn(alert_failure, (f"sms-****{tail}", err, 1, "904 SMS send failed"))
            return self._send(502, {"ok": False, "error": "gateway failed"})
        log.info(
            "sms send ok to=***%s len=%d id=%s state=%s",
            tail, len(message), result.get("id") or "-", result.get("state") or "-",
        )
        return self._send(200, {"ok": True, "id": result.get("id"), "state": result.get("state")})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path_is(path, send_path()):
            return self._send_sms()
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
    for name in ("RELAY_PATH", "FWD_PATH", "SEND_PATH", "SMSGW_HOOK_PATH", "RCS_HOOK_PATH"):
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
    _warn_paths()
    srv = ThreadingHTTPServer((HOST, PORT), H)
    log.info("listening on %s:%d", HOST, PORT)
    srv.serve_forever()


if __name__ == "__main__":
    main()
