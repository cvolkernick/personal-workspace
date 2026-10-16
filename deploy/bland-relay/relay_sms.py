# Loaded by bland_relay.py into the bland_relay module namespace (#1107).
# Do not import this file directly.
if __name__ not in ("bland_relay", "__main__"):
    raise RuntimeError("load relay_sms.py through bland_relay.py")

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


