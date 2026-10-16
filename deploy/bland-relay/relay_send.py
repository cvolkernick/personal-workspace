# Loaded by bland_relay.py into the bland_relay module namespace (#1107).
# Do not import this file directly.
if __name__ not in ("bland_relay", "__main__"):
    raise RuntimeError("load relay_send.py through bland_relay.py")

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
        "withDeliveryReport": True,
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


def _safe_id(raw):
    return re.sub(r"[^A-Za-z0-9_.:-]", "", str(raw or ""))[:64]


def _parts_count(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > 100:
        return None
    return value


def _clean_reason(raw):
    text = " ".join(str(raw or "").split())
    text = _PHONE_RE.sub("***", text)
    text = _scrub(text)
    return text[:120]


def _outbox_path():
    override = env("CRM_OUTBOX_PATH")
    return Path(override) if override else _state_dir() / "crm_outbox.jsonl"


def _jsonl_rows(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _send_index():
    """gateway id -> to, placed_by, latest state, whether sms:failed was alerted."""
    sends = {}

    def touch(gid):
        return sends.setdefault(gid, {
            "to": None, "placed_by": None, "state": "", "alerted": False, "at": "", "base": None,
        })

    for row in _jsonl_rows(_audit_path()):
        gid = _safe_id(row.get("gateway_id"))
        if not gid:
            continue
        rec = touch(gid)
        if row.get("to"):
            rec["to"] = row.get("to")
        if row.get("placed_by"):
            rec["placed_by"] = row.get("placed_by")
        if row.get("event") == "send":
            rec["state"] = row.get("gateway_state") or rec["state"]
            rec["at"] = row.get("at") or rec["at"]
        elif row.get("event") == "send_state":
            rec["state"] = row.get("state") or rec["state"]
            rec["at"] = row.get("at") or rec["at"]
        elif row.get("event") == "send_failed":
            rec["alerted"] = True
    for row in _jsonl_rows(_outbox_path()):
        pid = str(row.get("provider_msg_id") or "")
        if not pid.startswith("smsgate:"):
            continue
        gid = _safe_id(pid[len("smsgate:"):])
        if not gid:
            continue
        rec = touch(gid)
        rec["base"] = row
        if not rec["to"]:
            rec["to"] = row.get("to_addr")
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if not rec["placed_by"]:
            rec["placed_by"] = meta.get("placed_by")
        if not rec["state"]:
            rec["state"] = meta.get("gateway_state") or ""
        if not rec["at"]:
            rec["at"] = row.get("occurred_at") or ""
    return sends


def send_record(gateway_id):
    gid = _safe_id(gateway_id)
    if not gid:
        return None
    rec = _send_index().get(gid)
    if not rec or (not rec.get("to") and rec.get("base") is None):
        return None
    out = dict(rec)
    out["gateway_id"] = gid
    return out


def latest_open_send():
    """Newest send with no send_failed alert yet.

    Pending and Processed are the normal case. Failed is included so a notice
    whose Alexandra forward returned 502 can be retried; an alerted id is not.
    """
    best = None
    for gid, rec in _send_index().items():
        if rec.get("alerted") or rec.get("state") not in ("Pending", "Processed", "Failed"):
            continue
        if best is None or (rec.get("at") or "") >= (best.get("at") or ""):
            best = dict(rec)
            best["gateway_id"] = gid
    return best


def apply_send_state(gateway_id, state, parts_count=None, reason=None, now=None):
    """Append audit send_state and a CRM outbox update. -> 'updated' or 'unknown'."""
    now = time.time() if now is None else now
    rec = send_record(gateway_id)
    if rec is None:
        return "unknown"
    clean = _clean_reason(reason) if reason else ""
    parts = _parts_count(parts_count)
    fc_audit(
        "send_state", now, gateway_id=rec["gateway_id"], state=state,
        parts_count=parts, reason=clean or None, to=rec.get("to"), placed_by=rec.get("placed_by"),
    )
    base = rec.get("base")
    if isinstance(base, dict):
        updated = json.loads(json.dumps(base))
        updated.pop("body", None)
        updated["outcome"] = {"Sent": "sent", "Delivered": "delivered", "Failed": "failed"}.get(state, "sent")
        updated["error_code"] = clean if state == "Failed" else ""
        updated["occurred_at"] = _iso(now)
        meta = updated.get("metadata") if isinstance(updated.get("metadata"), dict) else {}
        meta = dict(meta)
        meta["gateway_state"] = state
        if parts is not None:
            meta["parts_count"] = parts
        if clean:
            meta["reason"] = clean
        updated["metadata"] = meta
        emit_interaction(updated)
    return "updated"


def notify_send_failed(rec, reason):
    """One Alexandra send_failed plus one #701 alert. No full number, no body."""
    tail = _tail4(rec.get("to") or "")
    clean = _clean_reason(reason) or "sms failed"
    placed = str(rec.get("placed_by") or "")[:32]
    obj = {
        "kind": "send_failed",
        "gateway_id": rec["gateway_id"],
        "to_tail": tail,
        "reason": clean,
        "placed_by": placed,
    }
    status, err, attempts = forward(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    alert_failure(
        rec["gateway_id"], clean, 1, "904 SMS send failed",
        "Gateway id", "State events", "prism bland-relay.service sms:failed",
    )
    if status is None:
        log.error("send_failed forward failed id=%s error=%s", rec["gateway_id"], _scrub(err))
    else:
        log.info("send_failed forwarded id=%s to=***%s -> %d", rec["gateway_id"], tail, status)
    return status, err, attempts


def send_failure_notice_re():
    raw = env("SEND_FAILURE_NOTICE_RE")
    if not raw:
        return _DEFAULT_FAILURE_NOTICE_RE
    try:
        return re.compile(raw, re.I)
    except re.error:
        return _DEFAULT_FAILURE_NOTICE_RE


def send_failure_notice(parsed):
    if not isinstance(parsed, dict):
        return False
    blob = " ".join(str(parsed.get(k) or "") for k in ("title", "text"))
    if not blob.strip():
        return False
    return send_failure_notice_re().search(blob) is not None


def handle_send_failure_notice(text_len=0, now=None):
    """Log a no-sender failure notice. Alert only when no sms:failed was recorded."""
    now = time.time() if now is None else now
    log.info("rcs webhook: send_failure_notice len=%d", int(text_len or 0))
    rec = latest_open_send()
    if rec is None:
        log.info("rcs webhook: send_failure_notice no open send")
        return 200, {"ok": True, "ignored": "send_failure_notice"}
    apply_send_state(rec["gateway_id"], "Failed", reason="send_failure_notice", now=now)
    status, err, _attempts = notify_send_failed(rec, "send_failure_notice")
    if status is None:
        return 502, {"ok": False, "error": "forward failed"}
    fc_audit(
        "send_failed", now, gateway_id=rec["gateway_id"], to_tail=_tail4(rec.get("to") or ""),
        placed_by=rec.get("placed_by"), reason="send_failure_notice",
    )
    log.info("rcs webhook: send_failure_notice fallback id=%s", rec["gateway_id"])
    return 200, {"ok": True, "ignored": "send_failure_notice"}


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


