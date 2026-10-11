#!/usr/bin/env python3
"""Gmail push -> Turo guest messages -> bland-relay (#1142).

No polling. Gmail users.watch publishes mailbox changes to a Pub/Sub topic.
`run` keeps one Pub/Sub StreamingPull connection open (outbound gRPC, no
public endpoint), and on each notification fetches Gmail history since the
stored historyId, keeps Turo guest-message emails (turo_email.py) and hands
them to the relay's loopback listener. The relay runs the same normalizer,
suppress rules, urgent classes, owner alert, Helm webhook and dedupe as the
904 phone notifications (bland_relay.turo_email_handle -> turo_ingest).

Commands:
  run        streaming-pull subscriber (systemd: gmail-turo-watch.service)
  renew      users.watch, daily (systemd: gmail-turo-watch-renew.timer)
  sync-once  one manual catch-up from the stored historyId (ops only)

Env (systemd EnvironmentFile; nothing here is printed):
  GMAIL_TOKEN_PATH          OAuth token JSON (refresh_token, client_id, client_secret), gmail.readonly
  GMAIL_WATCH_TOPIC         projects/<oauth-client-project>/topics/<topic>
  GMAIL_WATCH_SUBSCRIPTION  projects/<project>/subscriptions/<sub> (run only)
  GMAIL_WATCH_LABEL_IDS     optional comma list; default watches all mail (co-host mail skips INBOX)
  GMAIL_WATCH_EMAIL         optional; notifications for any other mailbox are acked and dropped
  GMAIL_WATCH_STATE_DIR     default ~/.local/state/gmail-turo-watch
  TURO_EMAIL_RELAY_URL      default http://127.0.0.1:8797/turo/email (loopback only)
  TURO_EMAIL_TOKEN          bearer for the relay listener
  GOOGLE_APPLICATION_CREDENTIALS  subscriber-only service-account key (run only; must be 0600)
  TURO_EMAIL_DISABLED       1 = watcher idles: no streaming pull, no acks, no Gmail reads
  TURO_EMAIL_MAX_ATTEMPTS   per-message tries for relay 5xx/timeouts and fetch errors before skip (default 5)
  TURO_EMAIL_ATTEMPT_SPACING_S  count at most one attempt per message per this many seconds (default 60)
  TURO_EMAIL_MAX_DOWN_ATTEMPTS  spaced connection-refused attempts before a message is skipped (default 30)
  TURO_EMAIL_DOWN_ALERT_S   relay-down / Gmail-access stall alert after this many seconds (default 600)

Logs never contain tokens, message bodies, guest names or addresses.
"""
import argparse
import contextlib
import fcntl
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import turo_email  # noqa: E402

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="[gmail-turo-watch] %(levelname)s %(message)s")
log = logging.getLogger("gmail-turo-watch")

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_RELAY_URL = "http://127.0.0.1:8797/turo/email"
PROCESSED_MAX = 1000
HISTORY_PAGES_MAX = 20
RECOVERY_QUERY = 'from:turo.com "sent you a message" newer_than:1d'
HTTP_TIMEOUT_S = 15
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_ATTEMPT_SPACING_S = 60
DEFAULT_MAX_DOWN_ATTEMPTS = 30
DEFAULT_DOWN_ALERT_S = 600
STALL_ALERT_EVERY_S = 24 * 3600
AUTH_ALERT_EVERY_S = 24 * 3600
SERVICE = "gmail-turo-watch.service"
STATE_KEEP_S = 7 * 24 * 3600


def env(k):
    return (os.environ.get(k) or "").strip()


class GmailError(Exception):
    def __init__(self, status, what):
        super().__init__(f"{what} -> {status}")
        self.status = status


# ---------------------------------------------------------------------------
# State (historyId + recently handed-off message ids), 0600, atomic writes.
def state_dir():
    return Path(env("GMAIL_WATCH_STATE_DIR") or (Path.home() / ".local" / "state" / "gmail-turo-watch"))


def state_path():
    return state_dir() / "state.json"


def load_state():
    try:
        st = json.loads(state_path().read_text(encoding="utf-8"))
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(st):
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


@contextlib.contextmanager
def state_lock():
    """Cross-process lock (run service vs renew/sync-once) around read-sync-write."""
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(d / "state.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def prune_state(st, now):
    """Bound per-message bookkeeping to STATE_KEEP_S (7 days)."""
    for key in ("attempts", "skipped"):
        rows = st.get(key) if isinstance(st.get(key), dict) else {}
        st[key] = {k: v for k, v in rows.items()
                   if isinstance(v, dict) and now - float(v.get("at") or 0) <= STATE_KEEP_S}
    return st


def disabled():
    return env("TURO_EMAIL_DISABLED") == "1"


def max_attempts():
    try:
        return max(1, min(int(env("TURO_EMAIL_MAX_ATTEMPTS") or DEFAULT_MAX_ATTEMPTS), 50))
    except ValueError:
        return DEFAULT_MAX_ATTEMPTS


def _int_env(key, default, lo, hi):
    try:
        return max(lo, min(int(env(key) or default), hi))
    except ValueError:
        return default


def max_down_attempts():
    return _int_env("TURO_EMAIL_MAX_DOWN_ATTEMPTS", DEFAULT_MAX_DOWN_ATTEMPTS, 1, 1000)


def down_alert_s():
    return _int_env("TURO_EMAIL_DOWN_ALERT_S", DEFAULT_DOWN_ALERT_S, 0, 7 * 24 * 3600)


def attempt_spacing_s():
    try:
        return max(0, min(int(env("TURO_EMAIL_ATTEMPT_SPACING_S") or DEFAULT_ATTEMPT_SPACING_S), 3600))
    except ValueError:
        return DEFAULT_ATTEMPT_SPACING_S


def _hid(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Gmail REST (stdlib). The token file is parsed as JSON, never sourced or logged.
class Gmail:
    def __init__(self, token_path=None, opener=None):
        self.token_path = Path(token_path or env("GMAIL_TOKEN_PATH")
                               or (Path.home() / ".config" / "auto-fleet" / "gmail-token.json"))
        self.opener = opener or urllib.request.urlopen
        self._access = None
        self._exp = 0.0

    def _creds(self):
        d = json.loads(self.token_path.read_text(encoding="utf-8"))
        inst = d.get("installed") if isinstance(d.get("installed"), dict) else {}
        out = {
            "refresh_token": str(d.get("refresh_token") or "").strip(),
            "client_id": str(d.get("client_id") or inst.get("client_id") or "").strip(),
            "client_secret": str(d.get("client_secret") or inst.get("client_secret") or "").strip(),
        }
        if not all(out.values()):
            raise GmailError("missing", "token file incomplete")
        return out

    def access_token(self):
        if self._access and time.time() < self._exp - 60:
            return self._access
        data = urllib.parse.urlencode(dict(self._creds(), grant_type="refresh_token")).encode()
        req = urllib.request.Request(TOKEN_URL, data=data, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with self.opener(req, timeout=HTTP_TIMEOUT_S) as r:
                body = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise GmailError(e.code, "token refresh") from None
        tok = body.get("access_token")
        if not tok:
            raise GmailError("no_token", "token refresh")
        self._access = tok
        self._exp = time.time() + float(body.get("expires_in") or 3000)
        return tok

    def call(self, method, path, params=None, body=None):
        url = GMAIL_API + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        hdrs = {"Authorization": "Bearer " + self.access_token()}
        if data is not None:
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with self.opener(req, timeout=HTTP_TIMEOUT_S) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            raise GmailError(e.code, f"{method} {path.split('/')[1] if '/' in path else path}") from None

    def history(self, start, page_token=None):
        params = {"startHistoryId": str(start), "historyTypes": "messageAdded", "maxResults": "500"}
        if page_token:
            params["pageToken"] = page_token
        return self.call("GET", "/history", params)

    def message(self, mid):
        return self.call("GET", "/messages/" + urllib.parse.quote(mid, safe=""), {"format": "full"})

    def list_ids(self, query):
        out = self.call("GET", "/messages", {"q": query, "maxResults": "100"})
        return [m.get("id") for m in out.get("messages") or [] if m.get("id")]

    def profile(self):
        return self.call("GET", "/profile")

    def watch(self, topic, label_ids=None):
        body = {"topicName": topic}
        if label_ids:
            body["labelIds"] = list(label_ids)
            body["labelFilterBehavior"] = "INCLUDE"
        return self.call("POST", "/watch", body=body)


# ---------------------------------------------------------------------------
# Relay handoff (loopback only).
# Relay verdicts.
#   OK      handled.
#   REJECT  4xx (not 401/403): skip now.
#   RETRY   5xx, timeout, reset/abort/RemoteDisconnected after the request went
#           out (what a message that crashes the relay looks like), anything
#           else: counted toward TURO_EMAIL_MAX_ATTEMPTS.
#   DOWN    connection refused (relay stopped/restarting, wrong or unset port,
#           bind failure): counted toward the much higher
#           TURO_EMAIL_MAX_DOWN_ATTEMPTS; a stall alert after TURO_EMAIL_DOWN_ALERT_S.
#   AUTH    401/403 or no token: retried, never counted, one deduped alert.
OK, REJECT, RETRY, DOWN, AUTH = "ok", "reject", "retry", "down", "auth"


def relay_url():
    url = env("TURO_EMAIL_RELAY_URL") or DEFAULT_RELAY_URL
    host = urllib.parse.urlsplit(url).hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("TURO_EMAIL_RELAY_URL must be a loopback address")
    return url


def post_to_relay(fields, opener=None):
    """-> OK (2xx), AUTH (401/403/no token), REJECT (other 4xx), RETRY (5xx, timeout,
    reset/RemoteDisconnected, other errors) or DOWN (connection refused). Never raises."""
    opener = opener or urllib.request.urlopen
    tok = env("TURO_EMAIL_TOKEN")
    if not tok:
        log.error("TURO_EMAIL_TOKEN unset: cannot hand off")
        return AUTH
    data = json.dumps(fields, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(relay_url(), data=data, method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
    try:
        with opener(req, timeout=10) as r:
            verdict = json.loads(r.read().decode("utf-8") or "{}")
            log.info("handoff msg=%s -> %d %s", fields.get("message_id"), r.status,
                     verdict.get("turo") or verdict.get("ignored") or "-")
            return OK if 200 <= r.status < 300 else RETRY
    except urllib.error.HTTPError as e:
        log.error("handoff msg=%s -> HTTP %d", fields.get("message_id"), e.code)
        if e.code in (401, 403):
            return AUTH
        return REJECT if 400 <= e.code < 500 else RETRY
    except Exception as e:  # noqa: BLE001
        reason = getattr(e, "reason", e) if isinstance(e, urllib.error.URLError) else e
        if isinstance(reason, ConnectionRefusedError):
            log.warning("handoff msg=%s: relay not listening (connection refused)", fields.get("message_id"))
            return DOWN
        log.error("handoff msg=%s failed: %s", fields.get("message_id"), type(reason).__name__)
    return RETRY


def ops_alert(mid, reason, attempts, title="Turo Gmail watcher skipped a message (#1142)"):
    """One #701 comment via the relay's alert_failure (journal ALERT + 15 min GitHub
    dedupe, alert state under a file lock), worded for this service."""
    try:
        import bland_relay  # stdlib; sits next to this file in ~/bin
        bland_relay.alert_failure(mid or "n/a", reason, attempts, title,
                                  id_label="Gmail message id", attempts_label="Attempts",
                                  source=f"prism `{SERVICE}` (`journalctl --user-unit {SERVICE}`)")
    except Exception as e:  # noqa: BLE001 - alerting must never block the stream
        log.error("ops alert failed: %s", type(e).__name__)


STALLS = {
    "relay_down": ("relay_down_since", "Turo Gmail watcher: relay unreachable (#1142)",
                   "relay unreachable (connection refused) for {m} min: check bland-relay.service is running "
                   "and TURO_EMAIL_PORT matches TURO_EMAIL_RELAY_URL; Turo emails are queued, not delivered"),
    "gmail": ("gmail_stall_since", "Turo Gmail watcher: Gmail access failing (#1142)",
              "Gmail API access failing for {m} min ({why}): check the Gmail token (refresh/consent) "
              "and API quota; Turo emails are not being read"),
}


def _stall(st, kind, now, mid=None, why=""):
    """Note a stall that retries can't fix by themselves. After TURO_EMAIL_DOWN_ALERT_S,
    one #701 alert a day; re-armed by _stall_clear on the next success."""
    key, title, text = STALLS[kind]
    since = float(st.get(key) or 0) or now
    st[key] = since
    alerted = st.setdefault("stall_alert_at", {})
    if now - since < down_alert_s():
        return
    if now - float(alerted.get(kind) or 0) < STALL_ALERT_EVERY_S:
        return
    alerted[kind] = now
    ops_alert(mid, text.format(m=int((now - since) // 60), why=why or "error"), 0, title)


def _stall_clear(st, kind):
    key = STALLS[kind][0]
    if st.pop(key, None) is not None:
        log.info("%s stall cleared", kind)
    (st.get("stall_alert_at") or {}).pop(kind, None)


def _auth_alert(st, mid, now):
    """Relay rejected our token: retry forever (never skip), alert at most once a day."""
    if now - float(st.get("auth_alert_at") or 0) < AUTH_ALERT_EVERY_S:
        return
    st["auth_alert_at"] = now
    ops_alert(mid, "relay auth mismatch: check TURO_EMAIL_TOKEN (watcher env vs bland-relay env)", 0,
              "Turo Gmail watcher: relay auth mismatch (#1142)")


def _verdict(v):
    if v is True:
        return OK
    if v is False or v is None:
        return RETRY
    return v


# ---------------------------------------------------------------------------
# Sync: history since the stored historyId -> Turo guest messages -> relay.
def _skip(st, mid, reason, attempts, now):
    """Give up on one message: record it, alert once, let historyId advance."""
    skipped = st.setdefault("skipped", {})
    first = mid not in skipped
    skipped[mid] = {"at": now, "reason": reason, "attempts": attempts}
    st.get("attempts", {}).pop(mid, None)
    log.error("skip msg=%s reason=%s attempts=%d", mid, reason, attempts)
    if first:
        ops_alert(mid, reason, attempts)


def _failed(st, mid, reason, now, count="n"):
    """A retryable failure. count: "n" (normal cap TURO_EMAIL_MAX_ATTEMPTS), "d"
    (relay refused; cap TURO_EMAIL_MAX_DOWN_ATTEMPTS) or None (not counted).
    At most one counted attempt per message per TURO_EMAIL_ATTEMPT_SPACING_S.
    -> True when the message was skipped (a cap was reached)."""
    att = st.setdefault("attempts", {})
    rec = dict(att.get(mid)) if isinstance(att.get(mid), dict) else {}
    n, d = int(rec.get("n") or 0), int(rec.get("d") or 0)
    last = rec.get("last_attempt_at")
    counted = bool(count) and (last is None or now - float(last) >= attempt_spacing_s())
    if counted:
        if count == "d":
            d += 1
        else:
            n += 1
        rec["last_attempt_at"] = now
    rec.update(n=n, d=d, at=now, reason=reason)
    att[mid] = rec
    if n >= max_attempts() or d >= max_down_attempts():
        _skip(st, mid, reason, n + d, now)
        return True
    log.warning("retry later msg=%s reason=%s attempts=%d/%d down=%d/%d%s", mid, reason, n, max_attempts(),
                d, max_down_attempts(), "" if counted else " (not counted)")
    return False


def _gmail_counts(status):
    """Per-message Gmail errors count; auth/quota/token problems hit every message and don't."""
    return isinstance(status, int) and status not in (401, 403, 429)


def process_ids(gmail, ids, st, poster, now=None):
    """Fetch, filter and hand off. -> True when every message was handled or skipped.

    A 4xx from the relay (except 401/403) skips the message at once. Relay
    5xx/timeouts and per-message Gmail fetch errors are retried (nack) and
    counted, at most once per TURO_EMAIL_ATTEMPT_SPACING_S, up to
    TURO_EMAIL_MAX_ATTEMPTS, then skipped, so one poison message can never pin
    historyId. Relay connection refused counts toward the higher
    TURO_EMAIL_MAX_DOWN_ATTEMPTS and raises a stall alert after
    TURO_EMAIL_DOWN_ALERT_S. Relay 401/403 and Gmail auth/quota errors are
    retried, never counted, with a once-a-day alert."""
    now = time.time() if now is None else now
    prune_state(st, now)
    done = list(st.get("processed") or [])
    seen = set(done) | set(st.get("skipped") or {})
    for mid in ids:
        if not mid or mid in seen:
            continue
        try:
            msg = gmail.message(mid)
        except GmailError as e:
            if e.status == 404:  # deleted before we got to it
                seen.add(mid)
                done.append(mid)
                continue
            log.error("message fetch failed: %s", e)
            counts = _gmail_counts(e.status)
            if not counts:
                _stall(st, "gmail", now, mid, f"messages.get {e.status}")
            if _failed(st, mid, f"gmail fetch {e.status}", now, count="n" if counts else None):
                seen.add(mid)
                continue
            st["processed"] = done[-PROCESSED_MAX:]
            return False
        _stall_clear(st, "gmail")
        fields, why = turo_email.parse_gmail_message(msg, now)
        if fields is None:
            if why not in ("not_turo", "label"):
                log.info("skip msg=%s reason=%s", mid, why)
        else:
            v = _verdict(poster(fields))
            if v == DOWN:
                _stall(st, "relay_down", now, mid)
            else:
                _stall_clear(st, "relay_down")  # the relay answered (or at least accepted the connection)
            if v == REJECT:
                _skip(st, mid, "relay 4xx", 1, now)
                seen.add(mid)
                continue
            if v == AUTH:
                _auth_alert(st, mid, now)
            if v != OK:
                why = {AUTH: "relay auth", DOWN: "relay down"}.get(v, "relay 5xx/timeout")
                if _failed(st, mid, why, now, count={RETRY: "n", DOWN: "d"}.get(v)):
                    seen.add(mid)
                    continue
                st["processed"] = done[-PROCESSED_MAX:]
                return False
        st.get("attempts", {}).pop(mid, None)
        if fields is not None:
            st.pop("auth_alert_at", None)  # token works again; a future mismatch re-alerts
        seen.add(mid)
        done.append(mid)
    st["processed"] = done[-PROCESSED_MAX:]
    return True


def _skip_added(item):
    labels = set(((item or {}).get("message") or {}).get("labelIds") or [])
    return bool(labels & turo_email.SKIP_LABELS)


def recover(gmail, st, poster, now=None):
    """historyId too old or missing: one-time catch-up by query, then restart from now."""
    current = _hid(gmail.profile().get("historyId"))
    ids = gmail.list_ids(env("TURO_EMAIL_RECOVERY_QUERY") or RECOVERY_QUERY)
    log.warning("history recovery: %d candidate messages", len(ids))
    ok = process_ids(gmail, list(reversed(ids)), st, poster, now)
    if ok and current:
        st["history_id"] = str(current)
    return ok


def sync(gmail, st, poster, now=None):
    """-> True when the mailbox is handled up to the newest historyId (state updated)."""
    start = _hid(st.get("history_id"))
    if not start:
        return recover(gmail, st, poster, now)
    ids, newest, page = [], start, None
    for _ in range(HISTORY_PAGES_MAX):
        try:
            res = gmail.history(start, page)
        except GmailError as e:
            if e.status == 404:
                log.warning("history id expired; recovering")
                return recover(gmail, st, poster, now)
            log.error("history fetch failed: %s", e)
            _stall(st, "gmail", now if now is not None else time.time(), None, str(e))
            return False
        for h in res.get("history") or []:
            for added in h.get("messagesAdded") or []:
                if not _skip_added(added):
                    ids.append(((added or {}).get("message") or {}).get("id"))
        newest = max(newest, _hid(res.get("historyId")))
        page = res.get("nextPageToken")
        if not page:
            break
    ordered = list(dict.fromkeys(i for i in ids if i))
    if not process_ids(gmail, ordered, st, poster, now):
        return False
    _stall_clear(st, "gmail")  # a full pass: history and every message fetch worked
    if newest > start:
        st["history_id"] = str(newest)
    return True


class Syncer:
    """Serializes syncs (Pub/Sub callbacks run on a thread pool, renew runs in
    another process) and persists state."""

    def __init__(self, gmail, poster=post_to_relay):
        self.gmail = gmail
        self.poster = poster
        self.lock = threading.Lock()

    def handle(self, data, now=None):
        """One Pub/Sub payload. -> True to ack, False to nack (redelivery)."""
        if disabled():
            return False
        try:
            note = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        except (ValueError, UnicodeDecodeError, AttributeError):
            log.warning("bad notification payload; acking")
            return True
        want = env("GMAIL_WATCH_EMAIL").lower()
        if want and str(note.get("emailAddress") or "").lower() != want:
            log.warning("notification for another mailbox; acking")
            return True
        with self.lock, state_lock():
            st = load_state()
            if _hid(st.get("history_id")) and _hid(note.get("historyId")) <= _hid(st.get("history_id")):
                return True
            ok = self._sync(st, now)
            save_state(st)
            return ok

    def _sync(self, st, now):
        t = time.time() if now is None else now
        try:
            return sync(self.gmail, st, self.poster, now)
        except GmailError as e:  # token refresh, profile, list: nothing per-message to count
            log.error("sync failed: %s", e)
            _stall(st, "gmail", t, None, f"{e} (non-numeric = token refresh)" if not isinstance(e.status, int)
                   else str(e))
        except Exception as e:  # noqa: BLE001 - e.g. Gmail/token endpoint unreachable
            log.error("sync crashed: %s", type(e).__name__)
            _stall(st, "gmail", t, None, type(e).__name__)
        return False

    def catch_up(self, now=None):
        if disabled():
            log.warning("TURO_EMAIL_DISABLED=1: catch-up skipped")
            return False
        with self.lock, state_lock():
            st = load_state()
            ok = self._sync(st, now)
            save_state(st)
            return ok


# ---------------------------------------------------------------------------
# Commands
def label_ids():
    return [x.strip() for x in env("GMAIL_WATCH_LABEL_IDS").split(",") if x.strip()]


def cmd_renew(gmail=None, poster=post_to_relay, now=None):
    """users.watch. If the stored watch had already lapsed, run one catch-up
    from the stored historyId (expired-historyId recovery still applies)."""
    now = time.time() if now is None else now
    topic = env("GMAIL_WATCH_TOPIC")
    if not topic.startswith("projects/") or "/topics/" not in topic:
        log.error("GMAIL_WATCH_TOPIC must be projects/<id>/topics/<name>")
        return 2
    gmail = gmail or Gmail()
    try:
        res = gmail.watch(topic, label_ids())
    except GmailError as e:
        log.error("watch failed: %s", e)
        return 1
    with state_lock():
        st = load_state()
        prev_exp = _hid(st.get("watch_expiration_ms"))
        lapsed = bool(prev_exp) and prev_exp <= now * 1000
        if not _hid(st.get("history_id")) and _hid(res.get("historyId")):
            st["history_id"] = str(res["historyId"])
        exp_ms = _hid(res.get("expiration"))
        st["watch_expiration_ms"] = exp_ms
        st["watch_renewed_at"] = int(now)
        save_state(st)
    log.info("watch ok expires=%s labels=%s lapsed=%s",
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exp_ms / 1000)) if exp_ms else "?",
             len(label_ids()) or "all", lapsed)
    if lapsed:
        log.warning("watch had lapsed; catching up from the stored historyId")
        if not Syncer(gmail, poster).catch_up(now):
            log.error("post-lapse catch-up incomplete; the next notification retries it")
    return 0


def cmd_sync_once(gmail=None):
    return 0 if Syncer(gmail or Gmail()).catch_up() else 1


def check_key_file(path):
    """-> None when the service-account key is a regular file readable by the owner only, else the reason."""
    if not path:
        return "GOOGLE_APPLICATION_CREDENTIALS unset"
    try:
        stt = os.stat(path)
    except OSError:
        return "service-account key file missing"
    import stat as _stat
    if not _stat.S_ISREG(stt.st_mode):
        return "service-account key is not a regular file"
    if stt.st_mode & 0o077:
        return f"service-account key mode is {oct(stt.st_mode & 0o777)}; run chmod 600 on it"
    return None


def idle(wait=None):
    """Disabled: hold no stream and read nothing, so Pub/Sub keeps the messages queued."""
    log.warning("TURO_EMAIL_DISABLED=1: not pulling or acking; messages stay queued")
    (wait or threading.Event().wait)()
    return 0


def cmd_run(wait=None):
    if disabled():
        return idle(wait)
    sub = env("GMAIL_WATCH_SUBSCRIPTION")
    if not sub.startswith("projects/") or "/subscriptions/" not in sub:
        log.error("GMAIL_WATCH_SUBSCRIPTION must be projects/<id>/subscriptions/<name>")
        return 2
    bad = check_key_file(env("GOOGLE_APPLICATION_CREDENTIALS"))
    if bad:
        log.error("refusing to start: %s", bad)
        return 2
    relay_url()
    return _stream(sub)  # pragma: no cover


def _stream(sub):  # pragma: no cover - needs google-cloud-pubsub and a live subscription
    from google.cloud import pubsub_v1  # venv only; the relay stays stdlib

    syncer = Syncer(Gmail())
    syncer.catch_up()

    def callback(message):
        if disabled():
            return  # neither ack nor nack: lease expires, message stays queued
        if syncer.handle(message.data):
            message.ack()
        else:
            message.nack()

    client = pubsub_v1.SubscriberClient()
    flow = pubsub_v1.types.FlowControl(max_messages=4)
    future = client.subscribe(sub, callback=callback, flow_control=flow)
    log.info("streaming pull open")
    with client:
        try:
            future.result()
        except Exception as e:  # noqa: BLE001
            log.error("streaming pull ended: %s", type(e).__name__)
            future.cancel()
            return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("cmd", choices=("run", "renew", "sync-once"))
    a = ap.parse_args(argv)
    if a.cmd == "renew":
        return cmd_renew()
    if a.cmd == "sync-once":
        return cmd_sync_once()
    return cmd_run()


if __name__ == "__main__":
    sys.exit(main())
