# Loaded by bland_relay.py into the bland_relay module namespace (#1107).
# Do not import this file directly.
if __name__ not in ("bland_relay", "__main__"):
    raise RuntimeError("load relay_fc.py through bland_relay.py")

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
# Chris types yes with that code. The preferred path is an SMS "YES <code>" to
# 904 from a number in FC_APPROVER_NUMBERS. Chat approval stays off unless
# FC_APPROVE_TOKEN_SHA256 is set. The token itself lives only in the Grok seat;
# the relay keeps the SHA-256 and binds it to the single human seat in
# FC_CHAT_APPROVER. A token with no binding, or a request naming a different
# approver, is refused (#1102 b).
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
# Inbound paths allowed to carry an SMS approval (#1102 c). rcs-number is an
# explicit number field. rcs-name_map is an RCS_NAME_MAP hit. A raw title,
# including a title parsed as a phone number (rcs-title), is not in this list.
FC_APPROVAL_VIAS = ("sms", "forwarder", "smsgate", "rcs-number", "rcs-name_map")
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


def fc_chat_approver():
    """The one human seat the chat approver token is bound to (#1102 b).

    Unset, a list, an agent seat, or a name outside FC_APPROVERS -> None.
    Chat approve is refused until this is a single allowed human seat.
    """
    raw = env("FC_CHAT_APPROVER").strip().lower()
    if not raw or any(sep in raw for sep in ",;"):
        return None
    if not FC_SEAT_RE.fullmatch(raw):
        return None
    if raw not in fc_approvers() or raw in fc_agent_seats():
        return None
    return raw


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
    gateway_state = (result or {}).get("state")
    if isinstance(gateway_state, str) and gateway_state:
        meta["gateway_state"] = gateway_state[:32]
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
    bound = fc_chat_approver()
    if bound is None:
        fc_audit("deny", now, to=to, approver=approver or None, reason="chat_approval_unbound",
                 action="approve")
        log.warning("first-contact chat approve refused: token is not bound to one approver")
        return 403, {"ok": False, "error": "chat_approval_unbound"}
    if approver != bound:
        fc_audit("deny", now, to=to, approver=approver or None, reason="approver_not_allowed",
                 action="approve")
        return 403, {"ok": False, "error": "approver_not_allowed"}
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


