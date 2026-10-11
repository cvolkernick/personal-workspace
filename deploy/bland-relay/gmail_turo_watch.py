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
  GOOGLE_APPLICATION_CREDENTIALS  subscriber-only service-account key (run only)

Logs never contain tokens, message bodies, guest names or addresses.
"""
import argparse
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
def relay_url():
    url = env("TURO_EMAIL_RELAY_URL") or DEFAULT_RELAY_URL
    host = urllib.parse.urlsplit(url).hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("TURO_EMAIL_RELAY_URL must be a loopback address")
    return url


def post_to_relay(fields, opener=None):
    """-> True on 2xx. Never raises."""
    opener = opener or urllib.request.urlopen
    tok = env("TURO_EMAIL_TOKEN")
    if not tok:
        log.error("TURO_EMAIL_TOKEN unset: cannot hand off")
        return False
    data = json.dumps(fields, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(relay_url(), data=data, method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
    try:
        with opener(req, timeout=10) as r:
            verdict = json.loads(r.read().decode("utf-8") or "{}")
            log.info("handoff msg=%s -> %d %s", fields.get("message_id"), r.status,
                     verdict.get("turo") or verdict.get("ignored") or "-")
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        log.error("handoff msg=%s -> HTTP %d", fields.get("message_id"), e.code)
    except Exception as e:  # noqa: BLE001
        log.error("handoff msg=%s failed: %s", fields.get("message_id"), type(e).__name__)
    return False


# ---------------------------------------------------------------------------
# Sync: history since the stored historyId -> Turo guest messages -> relay.
def process_ids(gmail, ids, st, poster, now=None):
    """Fetch, filter and hand off. -> True when every message was handled."""
    done = list(st.get("processed") or [])
    seen = set(done)
    ok = True
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
            ok = False
            break
        fields, why = turo_email.parse_gmail_message(msg, now)
        if fields is None:
            if why not in ("not_turo", "label"):
                log.info("skip msg=%s reason=%s", mid, why)
        elif not poster(fields):
            ok = False
            break
        seen.add(mid)
        done.append(mid)
    st["processed"] = done[-PROCESSED_MAX:]
    return ok


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
    if newest > start:
        st["history_id"] = str(newest)
    return True


class Syncer:
    """Serializes syncs (Pub/Sub callbacks run on a thread pool) and persists state."""

    def __init__(self, gmail, poster=post_to_relay):
        self.gmail = gmail
        self.poster = poster
        self.lock = threading.Lock()

    def handle(self, data, now=None):
        """One Pub/Sub payload. -> True to ack, False to nack (redelivery)."""
        try:
            note = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        except (ValueError, UnicodeDecodeError, AttributeError):
            log.warning("bad notification payload; acking")
            return True
        want = env("GMAIL_WATCH_EMAIL").lower()
        if want and str(note.get("emailAddress") or "").lower() != want:
            log.warning("notification for another mailbox; acking")
            return True
        with self.lock:
            st = load_state()
            if _hid(st.get("history_id")) and _hid(note.get("historyId")) <= _hid(st.get("history_id")):
                return True
            try:
                ok = sync(self.gmail, st, self.poster, now)
            except GmailError as e:
                log.error("sync failed: %s", e)
                ok = False
            except Exception as e:  # noqa: BLE001
                log.error("sync crashed: %s", type(e).__name__)
                ok = False
            save_state(st)
            return ok

    def catch_up(self, now=None):
        with self.lock:
            st = load_state()
            try:
                ok = sync(self.gmail, st, self.poster, now)
            except Exception as e:  # noqa: BLE001
                log.error("catch-up failed: %s", type(e).__name__)
                ok = False
            save_state(st)
            return ok


# ---------------------------------------------------------------------------
# Commands
def label_ids():
    return [x.strip() for x in env("GMAIL_WATCH_LABEL_IDS").split(",") if x.strip()]


def cmd_renew(gmail=None):
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
    st = load_state()
    if not _hid(st.get("history_id")) and _hid(res.get("historyId")):
        st["history_id"] = str(res["historyId"])
    exp_ms = _hid(res.get("expiration"))
    st["watch_expiration_ms"] = exp_ms
    st["watch_renewed_at"] = int(time.time())
    save_state(st)
    log.info("watch ok expires=%s labels=%s",
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exp_ms / 1000)) if exp_ms else "?",
             len(label_ids()) or "all")
    return 0


def cmd_sync_once(gmail=None):
    return 0 if Syncer(gmail or Gmail()).catch_up() else 1


def cmd_run():  # pragma: no cover - needs google-cloud-pubsub and a live subscription
    sub = env("GMAIL_WATCH_SUBSCRIPTION")
    if not sub.startswith("projects/") or "/subscriptions/" not in sub:
        log.error("GMAIL_WATCH_SUBSCRIPTION must be projects/<id>/subscriptions/<name>")
        return 2
    relay_url()
    from google.cloud import pubsub_v1  # venv only; the relay stays stdlib

    syncer = Syncer(Gmail())
    syncer.catch_up()

    def callback(message):
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
