# Loaded by bland_relay.py into the bland_relay module namespace (#1107).
# Do not import this file directly.
if __name__ not in ("bland_relay", "__main__"):
    raise RuntimeError("load relay_autoreply.py through bland_relay.py")

# Fixed reply for inbound SMS on the Bland 906 number (the line ending 6221).
# Bland's auto-reply rewrites this sentence; the relay sends these exact
# characters. AUTO_REPLY_906_ENABLED defaults off, so a deploy does not send.
LINE_906_DEFAULT = "+19062406221"
AUTO_REPLY_906_TEXT_DEFAULT = (
    "Thanks for reaching out to Panamerica Auto! This is an automated reply. "
    "You can reach us by text or phone at (904) 334-3975, and we'll follow up with you there."
)
AUTO_REPLY_906_COOLDOWN_DEFAULT_S = 24 * 60 * 60
AUTO_REPLY_906_COOLDOWN_MAX_S = 30 * 24 * 60 * 60
AUTO_REPLY_906_COOLDOWN_KEY = "auto_reply_906"
AUTO_REPLY_906_HOLD_KEY = "auto_reply_906_hold"
_SMS_STATUSES = frozenset({
    "ended", "timeout", "blacklisted", "inactive", "human_handoff",
    "human_handoff_ended", "read", "link_clicked", "timeout_warning",
})
_NO_AUTO_PHRASES = ("no auto-replies", "no auto replies", "no_auto_replies", "no-auto-replies")


def auto_reply_906_enabled():
    return env("AUTO_REPLY_906_ENABLED") == "1"


def line_906():
    """Configured 906 line, or None when AUTO_REPLY_906_NUMBER is set and invalid."""
    raw = env("AUTO_REPLY_906_NUMBER")
    if not raw:
        return LINE_906_DEFAULT
    num = normalize_nanp(raw)
    if num and valid_e164(num):
        return num
    return None


def auto_reply_906_text():
    """Exact reply. Unset uses the current 906 sentence. An empty value sends nothing."""
    if "AUTO_REPLY_906_TEXT" not in os.environ:
        return AUTO_REPLY_906_TEXT_DEFAULT
    return env("AUTO_REPLY_906_TEXT")


def auto_reply_906_cooldown_s():
    raw = env("AUTO_REPLY_906_COOLDOWN_S")
    if not raw:
        return AUTO_REPLY_906_COOLDOWN_DEFAULT_S
    try:
        n = int(raw)
    except ValueError:
        return AUTO_REPLY_906_COOLDOWN_DEFAULT_S
    if n < 0 or n > AUTO_REPLY_906_COOLDOWN_MAX_S:
        return AUTO_REPLY_906_COOLDOWN_DEFAULT_S
    return n


def auto_reply_906_startup_line():
    """One journal line. No reply text and no full number."""
    if not auto_reply_906_enabled():
        return "906 auto-reply: disabled"
    line = line_906()
    if not line:
        return "906 auto-reply: AUTO_REPLY_906_NUMBER invalid; replies off"
    return "906 auto-reply: enabled cooldown_s=%d text_len=%d line=***%s" % (
        auto_reply_906_cooldown_s(), len(auto_reply_906_text()), _tail4(line),
    )


def _with_sms_state(mutate):
    with _sms_lock:
        st = _load_json(_sms_state_path())
        if not mutate(st):
            return
        try:
            _save_json(_sms_state_path(), st)
        except OSError as e:
            log.error("sms state write failed: %s", type(e).__name__)


def _set_opt_out(num, opted_out):
    """Flip the shared STOP flag without the 904 inbound last_seen or approval hook."""
    def mutate(st):
        inbound = st.get("inbound") if isinstance(st.get("inbound"), dict) else {}
        if not opted_out and num not in inbound:
            return False
        rec = dict(inbound.get(num) or {})
        if bool(rec.get("opt_out")) == bool(opted_out) and num in inbound:
            return False
        rec["opt_out"] = bool(opted_out)
        inbound[num] = rec
        st["inbound"] = inbound
        st.setdefault("sends", [])
        return True
    _with_sms_state(mutate)


def _is_opted_out(num):
    with _sms_lock:
        st = _load_json(_sms_state_path())
        inbound = st.get("inbound") if isinstance(st.get("inbound"), dict) else {}
        return bool((inbound.get(num) or {}).get("opt_out"))


def _hold_reason(num):
    with _sms_lock:
        st = _load_json(_sms_state_path())
        hold = st.get(AUTO_REPLY_906_HOLD_KEY) if isinstance(st.get(AUTO_REPLY_906_HOLD_KEY), dict) else {}
        reason = hold.get(num)
        return reason if reason in ("handoff", "no_auto_replies") else None


def _set_hold(num, reason):
    def mutate(st):
        hold = dict(st.get(AUTO_REPLY_906_HOLD_KEY) or {}) if isinstance(st.get(AUTO_REPLY_906_HOLD_KEY), dict) else {}
        if hold.get(num) == reason:
            return False
        hold[num] = reason
        st[AUTO_REPLY_906_HOLD_KEY] = hold
        return True
    _with_sms_state(mutate)


def _clear_hold(num):
    def mutate(st):
        hold = st.get(AUTO_REPLY_906_HOLD_KEY)
        if not isinstance(hold, dict) or num not in hold:
            return False
        hold = dict(hold)
        hold.pop(num, None)
        st[AUTO_REPLY_906_HOLD_KEY] = hold
        return True
    _with_sms_state(mutate)


def _meta(payload):
    meta = payload.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _has_no_auto_phrase(value):
    if not isinstance(value, str):
        return False
    folded = " ".join(value.lower().split())
    return any(phrase in folded for phrase in _NO_AUTO_PHRASES)


def _marked_no_auto(payload):
    """Contact or conversation marked no auto-replies. Does not read message text."""
    meta = _meta(payload)
    if meta.get("no_auto_replies") in (True, "true", "1", 1, "yes"):
        return True
    if meta.get("auto_replies") in (False, "false", "0", 0, "no"):
        return True
    chunks = [payload.get("reason"), payload.get("human_handoff_reason")]
    for key in ("human_handoff", "last_human_handoff"):
        blob = meta.get(key)
        if isinstance(blob, dict):
            chunks.append(blob.get("reason"))
            if blob.get("no_auto_replies") in (True, "true", "1", 1, "yes"):
                return True
        elif isinstance(blob, str):
            chunks.append(blob)
    variables = payload.get("variables") if isinstance(payload.get("variables"), dict) else {}
    if variables.get("no_auto_replies") in (True, "true", "1", 1, "yes"):
        return True
    if isinstance(variables.get("no_auto_replies"), str):
        chunks.append(variables.get("no_auto_replies"))
    if variables.get("auto_replies") in (False, "false", "0", 0, "no"):
        return True
    contact = payload.get("contact") if isinstance(payload.get("contact"), dict) else {}
    if contact.get("no_auto_replies") in (True, "true", "1", 1, "yes"):
        return True
    tags = []
    for raw in (payload.get("pathway_tags"), payload.get("tags"), contact.get("tags")):
        if isinstance(raw, str):
            tags.append(raw)
        elif isinstance(raw, list):
            tags.extend(item for item in raw if isinstance(item, str))
    chunks.extend(tags)
    return any(_has_no_auto_phrase(item) for item in chunks)


def _handoff_active(payload):
    status = str(payload.get("status") or "").strip().lower()
    if status == "human_handoff":
        return True
    meta = _meta(payload)
    hh = meta.get("human_handoff")
    if isinstance(hh, dict) and hh:
        return True
    if isinstance(hh, str) and hh.strip():
        return True
    last = meta.get("last_human_handoff")
    if isinstance(last, dict) and last and status != "human_handoff_ended":
        return True
    if str(payload.get("sender") or "").strip().upper() == "HUMAN":
        return True
    return False


def _handoff_ended(payload):
    if str(payload.get("status") or "").strip().lower() == "human_handoff_ended":
        return True
    return _meta(payload).get("human_handoff_ended") in (True, "true", "1", 1)


def _skip_marker(payload):
    if _marked_no_auto(payload):
        return "no_auto_replies"
    if _handoff_active(payload):
        return "handoff"
    return None


def _user_number(payload):
    line = line_906()
    for key in ("phone_number", "user_number", "from"):
        num = normalize_nanp(payload.get(key))
        if num and num != line and valid_e164(num):
            return num
    return None


def _concerns_906_sms(payload):
    if str(payload.get("channel") or "").strip().lower() != "sms":
        return False
    line = line_906()
    if not line:
        return False
    return normalize_nanp(payload.get("agent_number")) == line


def _sync_hold(payload):
    num = _user_number(payload)
    if not num:
        return
    if _handoff_ended(payload):
        _clear_hold(num)
        return
    reason = _skip_marker(payload)
    if reason:
        _set_hold(num, reason)


def _inbound_user_sms(payload):
    if str(payload.get("type") or "").strip().lower() == "status":
        return None
    if str(payload.get("status") or "").strip().lower() in _SMS_STATUSES:
        return None
    if str(payload.get("sender") or "").strip().upper() != "USER":
        return None
    if payload.get("call_id") or payload.get("c_id"):
        return None
    message = payload.get("message")
    if not isinstance(message, str):
        return None
    user = _user_number(payload)
    if not user:
        log.warning("906 auto-reply skipped reason=bad_to")
        return None
    return user, message


def _log_skip(num, reason):
    log.info("906 auto-reply skipped to=***%s reason=%s", _tail4(num), reason)


def maybe_auto_reply_906(body):
    """Send one fixed reply for an inbound 906 SMS webhook.

    Returns a short reason, or None when the flag is off or the body is not
    for that line. Does not change the webhook HTTP response. Logs last-4 and
    length only.
    """
    if not auto_reply_906_enabled():
        return None
    try:
        payload = json.loads(body)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(payload, dict) or not _concerns_906_sms(payload):
        return None
    _sync_hold(payload)
    inbound = _inbound_user_sms(payload)
    if inbound is None:
        return "not_inbound"
    to, text = inbound
    word = opt_word(text)
    if word in OPT_OUT_WORDS:
        _set_opt_out(to, True)
        _log_skip(to, "opt_out")
        return "opt_out"
    if word in OPT_IN_WORDS:
        _set_opt_out(to, False)
    hold = _hold_reason(to)
    if hold:
        _log_skip(to, hold)
        return hold
    if _is_opted_out(to):
        _log_skip(to, "opt_out")
        return "opt_out"
    if env("SMS_SEND_DISABLED") == "1":
        _log_skip(to, "send_disabled")
        return "send_disabled"
    reply = auto_reply_906_text()
    if not reply or len(reply) > MAX_SMS_CHARS:
        _log_skip(to, "bad_text")
        return "bad_text"
    if not gateway_configured():
        log.error("906 auto-reply skipped to=***%s reason=gateway_unconfigured", _tail4(to))
        return "gateway_unconfigured"
    # Same rule as the send route: only a JSON boolean true is a dry run.
    dry_run = payload.get("dry_run") is True
    _code, result = deliver_outbound_sms(
        to, reply, dry_run=dry_run, purpose="906 fixed auto-reply", placed_by="906-auto-reply",
        cooldown_key=AUTO_REPLY_906_COOLDOWN_KEY, cooldown_s=auto_reply_906_cooldown_s(),
        log_tag="906 auto-reply", alert_title="906 auto-reply SMS failed",
    )
    if result.get("ok"):
        return "dry_run" if result.get("dry_run") else "sent"
    return result.get("error") or "refused"
