# Loaded by bland_relay.py into the bland_relay module namespace (#1107).
# Do not import this file directly.
if __name__ not in ("bland_relay", "__main__"):
    raise RuntimeError("load relay_helm_sms.py through bland_relay.py")

# Inbound 904 SMS -> Helm (kind=inbound_sms). Purely additive: gated by
# HELM_INBOUND_SMS_FORWARD=1, runs in a background thread, never changes the
# Alexandra forward or the HTTP response. Uses the same webhook URL and auth
# as the Turo Helm push (HELM_TURO_WEBHOOK_URL / _KEY / _HEADER).
# message_id: the source id when one exists ("smsgate:<messageId>"), else
# "sha256:<hex>" of from, body and the source timestamp (the 10-minute bucket
# of relay time when the source sent none). Seen ids persist in
# <state>/helm_sms_seen.json so phone retries do not wake Helm twice.
# Content dedupe (below) also drops phone re-sends that arrive with NEW ids.

HELM_SMS_SEEN_TTL_S = 14 * 24 * 3600
HELM_SMS_SEEN_MAX = 2000
HELM_SMS_BODY_MAX = 4000
HELM_SMS_TZ = "America/New_York"
_helm_sms_lock = threading.Lock()


def helm_sms_enabled():
    return env("HELM_INBOUND_SMS_FORWARD") == "1" and bool(env("HELM_TURO_WEBHOOK_URL"))


def _helm_sms_seen_path():
    return _state_dir() / "helm_sms_seen.json"


def _helm_sms_epoch(raw):
    """Source timestamp (epoch s/ms, digits, or ISO 8601) -> epoch seconds or None."""
    if raw is None or isinstance(raw, bool):
        return None
    s = str(raw).strip()
    if not s:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", s):
        v = float(s)
        if v > 1e12:
            v /= 1000.0
        return v if v > 1e9 else None
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        from zoneinfo import ZoneInfo
        dt = dt.replace(tzinfo=ZoneInfo(HELM_SMS_TZ))
    return dt.timestamp()


def _helm_sms_et_iso(epoch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(epoch, ZoneInfo(HELM_SMS_TZ)).isoformat(timespec="seconds")


def helm_sms_message_id(source_id, frm, body, ts_raw, now=None):
    """-> (message_id, id_source). id_source is "source" or "sha256"."""
    sid = re.sub(r"[^A-Za-z0-9_.:-]", "", str(source_id or ""))[:96]
    if sid:
        return sid, "source"
    ts = str(ts_raw).strip() if ts_raw not in (None, "") else ""
    if not ts:
        now = time.time() if now is None else now
        ts = "bucket:%d" % (int(now) // 600 * 600)
    digest = hashlib.sha256(("%s\n%s\n%s" % (frm or "", body or "", ts)).encode("utf-8")).hexdigest()
    return "sha256:" + digest, "sha256"


def helm_sms_payload(rec, source_id=None, now=None):
    """Normalized inbound record (normalize_904 / _smsgate / _rcs) -> Helm payload."""
    now = time.time() if now is None else now
    frm = rec.get("from")
    body = rec.get("body") or ""
    ts_raw = rec.get("received_at")
    mid, id_src = helm_sms_message_id(source_id, frm, body, ts_raw, now)
    epoch = _helm_sms_epoch(ts_raw)
    payload = {
        "kind": "inbound_sms",
        "from": frm,
        "body": body[:HELM_SMS_BODY_MAX],
        "received_at": _helm_sms_et_iso(epoch if epoch is not None else now),
        "message_id": mid,
        "message_id_source": id_src,
        "line": LINE_904,
        "via": rec.get("via") or "sms",
        "test": bool(rec.get("test")),
    }
    return payload


@contextlib.contextmanager
def _helm_sms_file_lock():
    path = _helm_sms_seen_path().with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _helm_sms_mutate(fn, now):
    with _helm_sms_lock, _helm_sms_file_lock():
        seen = _load_json(_helm_sms_seen_path())
        seen = {k: v for k, v in seen.items()
                if isinstance(v, (int, float)) and now - v <= HELM_SMS_SEEN_TTL_S}
        result = fn(seen)
        if len(seen) > HELM_SMS_SEEN_MAX:
            for k in sorted(seen, key=seen.get)[:len(seen) - HELM_SMS_SEEN_MAX]:
                seen.pop(k, None)
        _save_json(_helm_sms_seen_path(), seen)
        return result


def helm_sms_claim(mid, now=None):
    """True when mid is new (and is now recorded), False when already seen."""
    now = time.time() if now is None else now

    def fn(seen):
        if mid in seen:
            return False
        seen[mid] = now
        return True
    return _helm_sms_mutate(fn, now)


def helm_sms_unclaim(mid, now=None):
    now = time.time() if now is None else now
    return _helm_sms_mutate(lambda seen: seen.pop(mid, None), now)


# Content dedupe (2026-10-25). When the 904 phone reconnects it can re-send
# old inbound texts with NEW ids (new SMSGate messageId, fresh Messages
# notification post time), so the id check above misses them. Keys are salted
# HMAC-SHA256 hashes, never text, in <state>/helm_sms_content.json, kept for
# HELM_SMS_CONTENT_WINDOW_D days (default and minimum 7).
#   sender = normalize_nanp(from) or the case-folded name; body = _text_key().
#   "e" key = sender + body + source timestamp (whole seconds)
#   "c" key = sender + body, with last sighting time and path class
# Rules:
#   timestamped, not a notification (SMSGate receivedAt = carrier time,
#   forwarder timestamp): duplicate if the "e" key was seen, or if the "c"
#   key was seen via a notification at or after (source time - 300 s), i.e.
#   the notification copy of this same text already went through. A genuine
#   repeat has a new carrier time and passes.
#   notification, or no timestamp (post time changes on every re-post, so it
#   cannot identify a message): duplicate if the "c" key was seen in the
#   window and no outbound 904 send to that sender is recorded after that
#   sighting. A reply in between means the same words may be a new turn
#   ("Yes" again) and the text passes.
# First-contact approvals ("YES <6 digits>") are exempt so a retry after a
# denied attempt still counts. Test records skip content dedupe. Outbound sending is never touched (sends
# are only read to apply the reply exception).
HELM_SMS_CONTENT_MAX = 5000
HELM_SMS_XPATH_TOL_S = 300


def _helm_sms_content_path():
    return _state_dir() / "helm_sms_content.json"


def _helm_sms_content_window_s():
    try:
        days = float(env("HELM_SMS_CONTENT_WINDOW_D") or 7)
    except ValueError:
        days = 7.0
    return max(7.0, days) * 86400


def _helm_sms_sender_key(frm):
    return normalize_nanp(frm) or " ".join(str(frm or "").split()).casefold()


def _helm_sms_hash(salt, *parts):
    import hmac
    msg = "\x1f".join(str(p) for p in parts).encode("utf-8")
    return hmac.new(salt.encode("ascii"), msg, hashlib.sha256).hexdigest()[:32]


def _helm_sms_last_send(num):
    """Latest outbound 904 send time to num (read-only), or None."""
    if not num:
        return None
    best = None
    for e in _load_json(_sms_state_path()).get("sends") or []:
        if isinstance(e, dict) and isinstance(e.get("at"), (int, float)) and normalize_nanp(e.get("to")) == num:
            best = e["at"] if best is None else max(best, e["at"])
    return best


def helm_sms_content_keys(rec, salt):
    """-> (ckey, ekey or None, src_epoch or None, is_notification)."""
    sender = _helm_sms_sender_key(rec.get("from"))
    body = _text_key(rec.get("body"))
    notif = str(rec.get("via") or "").startswith("rcs")
    epoch = _helm_sms_epoch(rec.get("received_at"))
    ckey = _helm_sms_hash(salt, "c", sender, body)
    ekey = None if (notif or epoch is None) else _helm_sms_hash(salt, "e", sender, body, int(round(epoch)))
    return ckey, ekey, epoch, notif


def helm_sms_content_check(rec, now=None, claim=True):
    """-> (verdict, rule, undo). verdict is "new" or "duplicate"; rule names why.

    With claim=True the keys are recorded (a duplicate refreshes the "c"
    sighting) under the same locks as the id store. undo() restores the
    previous entries, for a failed Helm post.
    """
    now = time.time() if now is None else now
    if FC_SMS_APPROVE_RE.match(str(rec.get("body") or "")):
        return "new", "exempt-approval", (lambda: None)  # a retried "YES <code>" must reach first-contact
    window = _helm_sms_content_window_s()
    num = normalize_nanp(rec.get("from"))
    last_send = _helm_sms_last_send(num)
    result = {}

    def fn(store):
        keys = store.setdefault("keys", {})
        ckey, ekey, epoch, notif = helm_sms_content_keys(rec, store["salt"])
        c = keys.get(ckey)
        verdict, rule = "new", "new"
        if ekey is not None:
            if ekey in keys:
                verdict, rule = "duplicate", "exact"
            elif c and c.get("v") == "rcs" and c.get("t", 0) >= epoch - HELM_SMS_XPATH_TOL_S:
                verdict, rule = "duplicate", "xpath-notification"
        elif c and now - c.get("t", 0) <= window:
            if last_send is not None and last_send > c.get("t", 0):
                rule = "content-after-reply"
            else:
                verdict, rule = "duplicate", "content"
        prev = {k: (dict(keys[k]) if k in keys else None) for k in (ckey, ekey) if k}
        if claim:
            if verdict == "new" and ekey is not None:
                keys[ekey] = {"t": now}
            if verdict == "new" or rule == "content":
                keys[ckey] = {"t": now, "v": "rcs" if notif else "src"}
        result.update(verdict=verdict, rule=rule, prev=prev)

    _helm_sms_content_mutate(fn, now, window)
    prev = result["prev"]

    def undo():
        def restore(store):
            keys = store.setdefault("keys", {})
            for k, v in prev.items():
                if v is None:
                    keys.pop(k, None)
                else:
                    keys[k] = v
        _helm_sms_content_mutate(restore, time.time(), window)
    return result["verdict"], result["rule"], (undo if claim else (lambda: None))


def _helm_sms_content_mutate(fn, now, window):
    with _helm_sms_lock, _helm_sms_file_lock():
        path = _helm_sms_content_path()
        store = _load_json(path)
        if not isinstance(store.get("salt"), str) or len(store["salt"]) < 32:
            import secrets
            store = {"v": 1, "salt": secrets.token_hex(16), "keys": {}}
        keys = store.get("keys") if isinstance(store.get("keys"), dict) else {}
        store["keys"] = {k: v for k, v in keys.items()
                         if isinstance(v, dict) and isinstance(v.get("t"), (int, float)) and now - v["t"] <= window}
        fn(store)
        if len(store["keys"]) > HELM_SMS_CONTENT_MAX:
            for k in sorted(store["keys"], key=lambda k: store["keys"][k]["t"])[:len(store["keys"]) - HELM_SMS_CONTENT_MAX]:
                store["keys"].pop(k, None)
        _save_json(path, store)


def inbound_content_gate(rec, now=None):
    """One content decision per inbound text, shared by the Alexandra forward
    and Helm. Handlers call it before forwarding. -> (duplicate, undo, checked).

    duplicate=True: drop the text for both sinks. On a new text the keys are
    recorded; the handler calls undo() only when the Alexandra forward fails
    (the phone then retries, and Helm's id store stops a second Helm wake).
    A Helm failure after this gate never rolls the content keys back.
    Test records and INBOUND_CONTENT_DEDUPE=0 skip the gate (checked=False, so
    Helm falls back to its own content check). A store error fails open.
    """
    noop = lambda: None  # noqa: E731
    if rec.get("test") or env("INBOUND_CONTENT_DEDUPE") == "0":
        return False, noop, False
    try:
        verdict, rule, undo = helm_sms_content_check(rec, now)
    except Exception as e:  # noqa: BLE001 - never block an inbound text on the store
        log.error("!!! inbound content check failed: %s (forwarding) !!!", type(e).__name__)
        return False, noop, False
    if verdict == "duplicate":
        log.info("inbound content duplicate rule=%s from=***%s via=%s: dropped for Alexandra and Helm",
                 rule, _tail4(str(rec.get("from") or "")), rec.get("via") or "sms")
        return True, noop, True
    return False, undo, True


def inbound_content_undo(undo):
    try:
        undo()
    except Exception as e:  # noqa: BLE001
        log.error("inbound content undo failed: %s", type(e).__name__)


def helm_forward_inbound_sms(rec, source_id=None, now=None, content_checked=False):
    """POST one inbound SMS to Helm. -> disabled|duplicate|ok|failed. Never raises.

    content_checked=True: the handler's inbound_content_gate already decided
    this text is new, so Helm skips its own content check and never rolls the
    shared content keys back on a failed post.
    """
    if not helm_sms_enabled():
        return "disabled"
    t0 = time.time()
    try:
        payload = helm_sms_payload(rec, source_id, now)
        mid = payload["message_id"]
        content_undo = lambda: None  # noqa: E731
        if not rec.get("test") and not content_checked:
            verdict, rule, content_undo = helm_sms_content_check(rec, now)
            if verdict == "duplicate":
                log.info("helm inbound_sms duplicate content rule=%s id=%s from=***%s via=%s",
                         rule, mid[:40], _tail4(str(payload.get("from") or "")), payload["via"])
                return "duplicate"
        if not helm_sms_claim(mid, now):
            content_undo()
            log.info("helm inbound_sms duplicate id=%s", mid[:40])
            return "duplicate"
    except Exception as e:  # noqa: BLE001
        log.error("!!! helm inbound_sms prepare failed: %s !!!", type(e).__name__)
        return "failed"
    src_at, lag_s = "-", "-"
    try:  # lag telemetry for relay-lag-alarm; never affects delivery
        _se = _helm_sms_epoch(rec.get("received_at"))
        if _se is not None:
            src_at = _helm_sms_et_iso(_se)
            lag_s = str(int(time.time() - _se))
    except Exception:  # noqa: BLE001
        pass
    hdr = helm_auth_header()
    try:
        status, err, attempts = post_json(env("HELM_TURO_WEBHOOK_URL"), payload,
                                          dict([hdr]) if hdr else None, _turo_timeout(), tries=2)
    except Exception as e:  # noqa: BLE001
        status, err, attempts = None, type(e).__name__, 1
    ms = int((time.time() - t0) * 1000)
    if status is not None:
        log.info("helm inbound_sms ok id=%s from=***%s via=%s len=%d id_src=%s attempts=%d ms=%d src_at=%s lag_s=%s -> %d",
                 mid[:40], _tail4(str(payload.get("from") or "")), payload["via"], len(payload["body"]),
                 payload["message_id_source"], attempts, ms, src_at, lag_s, status)
        return "ok"
    log.error("!!! helm inbound_sms FAILED id=%s via=%s attempts=%d ms=%d src_at=%s lag_s=%s error=%s !!!",
              mid[:40], payload["via"], attempts, ms, src_at, lag_s, _scrub(err))
    try:
        content_undo()
    except Exception as e:  # noqa: BLE001
        log.error("helm inbound_sms content undo failed: %s", type(e).__name__)
    try:
        helm_sms_unclaim(mid)  # a phone retry may try again
    except Exception as e:  # noqa: BLE001
        log.error("helm inbound_sms unclaim failed: %s", type(e).__name__)
    return "failed"


def helm_sms_spawn(rec, source_id=None, content_checked=False):
    """Handler hook: background, exception-proof, no-op unless the flag is on."""
    try:
        if helm_sms_enabled():
            _spawn(helm_forward_inbound_sms, (dict(rec), source_id, None, content_checked))
    except Exception as e:  # noqa: BLE001 - never affect the Alexandra path
        log.error("!!! helm inbound_sms spawn failed: %s !!!", type(e).__name__)
