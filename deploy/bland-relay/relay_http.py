# Loaded by bland_relay.py into the bland_relay module namespace (#1107).
# Do not import this file directly.
if __name__ not in ("bland_relay", "__main__"):
    raise RuntimeError("load relay_http.py through bland_relay.py")

class TuroEmailHandler(BaseHTTPRequestHandler):
    """Loopback-only listener for gmail_turo_watch.py. Never behind Serve/Funnel."""
    server_version = "relay"
    sys_version = ""

    def log_message(self, fmt, *args):
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

    def do_POST(self):
        if self.path.split("?", 1)[0] != TURO_EMAIL_PATH:
            return self._nf()
        if self.headers.get("Tailscale-Funnel-Request") or self.headers.get("X-Forwarded-For"):
            log.warning("turo email listener: proxied request refused")
            return self._send(403, {"ok": False, "error": "forbidden"})
        tok = env("TURO_EMAIL_TOKEN")
        if not tok:
            return self._send(503, {"ok": False, "error": "not configured"})
        got = token_from_headers(self.headers)
        if not token_ok(got, tok):
            log.warning("turo email listener: bad/missing token (len %d)", len(got))
            return self._send(401, {"ok": False, "error": "unauthorized"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n <= 0 or n > TURO_EMAIL_MAX_BODY:
            return self._send(413, {"ok": False, "error": "bad length"})
        try:
            obj = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._send(400, {"ok": False, "error": "bad payload"})
        try:
            return self._send(*turo_email_handle(obj))
        except Exception as e:  # noqa: BLE001
            log.error("turo email handle failed: %s", type(e).__name__)
            return self._send(500, {"ok": False, "error": "internal"})


def start_turo_email_listener():
    """Start the loopback listener when TURO_EMAIL_PORT and TURO_EMAIL_TOKEN are set. -> server or None."""
    raw = env("TURO_EMAIL_PORT")
    if not raw:
        return None
    if not env("TURO_EMAIL_TOKEN"):
        log.warning("TURO_EMAIL_PORT set without TURO_EMAIL_TOKEN: turo email listener off")
        return None
    try:
        port = int(raw)
    except ValueError:
        log.warning("TURO_EMAIL_PORT invalid: turo email listener off")
        return None
    if not 1 <= port <= 65535 or port == PORT:
        log.warning("TURO_EMAIL_PORT out of range or equal to RELAY_PORT: turo email listener off")
        return None
    srv = ThreadingHTTPServer(("127.0.0.1", port), TuroEmailHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("turo email listener on 127.0.0.1:%d shadow=%s", port, env("TURO_EMAIL_SHADOW") == "1")
    return srv


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

    do_PUT = do_PATCH = do_HEAD = _nf

    def _read_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            return None
        return self.rfile.read(n)

    def _caller_query(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        return parsed.path, (query.get("pn") or [""])[0]

    def _caller_context(self):
        path, pn = self._caller_query()
        if path != "/bland/caller-context":
            return self._nf()
        ip = self.client_address[0] if self.client_address else ""
        if not caller_context.allow_ip(ip, time.time()):
            return self._send(429, {"ok": False, "error": "rate_limited"})
        got = (self.headers.get("X-Caller-Context-Token") or "").strip() or token_from_headers(self.headers)
        if not token_ok(got, env("CALLER_CONTEXT_TOKEN")):
            log.warning("caller-context: bad/missing token (len %d)", len(got))
            return self._send(401, {"ok": False, "error": "unauthorized"})
        try:
            return self._send(200, caller_context.lookup_caller(pn))
        except Exception as exc:  # noqa: BLE001 — Bland must still get a 200
            log.error("caller-context failed: %s", type(exc).__name__)
            return self._send(200, caller_context.empty_context())

    def _caller_forget(self):
        path, pn = self._caller_query()
        if path != "/bland/caller-context":
            return self._nf()
        ip = self.client_address[0] if self.client_address else ""
        if not caller_context.allow_ip(ip, time.time()):
            return self._send(429, {"ok": False, "error": "rate_limited"})
        got = (self.headers.get("X-Caller-Forget-Token") or "").strip() or token_from_headers(self.headers)
        if not token_ok(got, env("CALLER_FORGET_TOKEN")):
            log.warning("caller-context forget: bad/missing token (len %d)", len(got))
            return self._send(401, {"ok": False, "error": "unauthorized"})
        try:
            deleted = caller_context.CallerIndex().forget(pn)
        except Exception as exc:  # noqa: BLE001
            log.error("caller-context forget failed: %s", type(exc).__name__)
            return self._send(200, {"ok": False, "deleted": 0})
        return self._send(200, {"ok": True, "deleted": deleted})

    def _meeting_check(self):
        ip = self.client_address[0] if self.client_address else ""
        got = (self.headers.get("X-Meeting-Check-Token") or "").strip() or token_from_headers(self.headers)

        def body():
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if 0 < n <= meeting_check.MAX_BODY else None

        return self._send(*meeting_check.handle(got, body, ip))

    def do_GET(self):
        return self._caller_context()

    def do_DELETE(self):
        return self._caller_forget()

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
        if event in SEND_STATE_EVENTS:
            return self._smsgate_state(data, event)
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

    def _smsgate_state(self, data, event):
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        event_id = _safe_id(data.get("id"))
        gid = _safe_id(payload.get("messageId"))
        state = SEND_STATE_EVENTS[event]
        if event_id and _seen_event(event_id):
            log.info("smsgate webhook: duplicate event id=%s", event_id)
            return self._send(200, {"ok": True, "duplicate": True})
        if not gid:
            log.info("smsgate webhook: %s without message id", event)
            if event_id:
                _mark_event(event_id)
            return self._send(200, {"ok": True, "ignored": True})
        rec = send_record(gid)
        if rec is None:
            log.info("smsgate webhook: unknown message id=%s event=%s", gid, event)
            if event_id:
                _mark_event(event_id)
            return self._send(200, {"ok": True, "ignored": True})
        parts = _parts_count(payload.get("partsCount"))
        reason = payload.get("reason") if isinstance(payload.get("reason"), str) else ""
        apply_send_state(gid, state, parts, reason)
        tail = _tail4(rec.get("to") or "")
        log.info(
            "smsgate webhook: send_state id=%s state=%s to=***%s parts=%s",
            gid, state, tail, "-" if parts is None else parts,
        )
        if state == "Failed" and not rec.get("alerted"):
            status, err, _attempts = notify_send_failed(rec, reason)
            if status is None:
                return self._send(502, {"ok": False, "error": "forward failed"})
            fc_audit(
                "send_failed", time.time(), gateway_id=gid, to_tail=tail,
                placed_by=rec.get("placed_by"), reason=_clean_reason(reason) or "sms failed",
            )
        if event_id:
            _mark_event(event_id)
        return self._send(200, {"ok": True, "state": state})

    def _rcs_inbound(self):
        if env("RCS_HOOK_DISABLED") == "1":
            log.warning("rcs inbound disabled (RCS_HOOK_DISABLED=1)")
            return self._send(503, {"ok": False, "error": "rcs inbound disabled"})
        tok = env("RCS_HOOK_TOKEN")
        if not tok:
            # No unauthenticated mode: the RCS route (and the Turo branch on it)
            # refuses everything until RCS_HOOK_TOKEN is set.
            log.warning("rcs webhook refused: RCS_HOOK_TOKEN unset")
            return self._send(503, {"ok": False, "error": "rcs token not configured"})
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
        if not num and send_failure_notice(parsed):
            return self._send(*handle_send_failure_notice(len(text or "")))
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
                     placed_by=placed_by, gateway_id=(result or {}).get("id"),
                     gateway_state=(result or {}).get("state"))
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
        if path == meeting_check.ROUTE:
            return self._meeting_check()
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
        _spawn(post_call.handle, (body,))
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


def warn_fc_config():
    """Startup notes for first-contact. Chat approval warns when the hash is set (#1102 b)."""
    if not fc_path():
        return
    if not fc_require_approval():
        log.warning("first-contact: FC_REQUIRE_APPROVAL=0 - numbers are allowed as soon as they are added")
        if env("FC_APPROVER_NUMBERS") or env("FC_APPROVE_TOKEN_SHA256"):
            log.info("first-contact: approver numbers/token ignored while approval is off")
    else:
        token_hash = env("FC_APPROVE_TOKEN_SHA256").lower()
        send = env("SEND_TOKEN")
        if not token_hash:
            log.warning("FC_PATH set without FC_APPROVE_TOKEN_SHA256: chat approvals off (SMS approvals only)")
        elif send and _sha256(send) == token_hash:
            log.error("!!! FC_APPROVE_TOKEN equals SEND_TOKEN - chat approvals refused !!!")
        else:
            bound = fc_chat_approver()
            if bound:
                log.warning(
                    "first-contact: chat approval is enabled and bound to %s. "
                    "SMS YES from FC_APPROVER_NUMBERS is the preferred path. "
                    "The approver token belongs only in the Grok seat",
                    bound,
                )
            else:
                log.warning(
                    "FC_APPROVE_TOKEN_SHA256 is set without one FC_CHAT_APPROVER: "
                    "chat approvals refused until the token is bound to one approver"
                )
        if not fc_approver_numbers():
            log.warning("FC_APPROVER_NUMBERS empty: SMS approvals off")
    log.info(
        "first-contact: require_approval=%s daily_max=%d ttl_days=%d max_sends_before_reply=%d "
        "disabled=%s", fc_require_approval(), fc_daily_max(), fc_ttl_s() // FC_DAY_S,
        fc_max_sends(), fc_disabled(),
    )


def caller_context_startup_line():
    """One startup line (#1229). No token, phone number, or path."""
    token_state = "set" if env("CALLER_CONTEXT_TOKEN") else "unset"
    backend = "index"
    if caller_context.loopback_http_base(env("CRM_API_BASE")) and env("CRM_API_TOKEN"):
        backend = "crm"
    rows = 0
    try:
        store = caller_context.CallerIndex()
        try:
            rows = store.count()
        finally:
            store.close()
    except Exception:  # noqa: BLE001 — do not log the index path
        rows = 0
    if not isinstance(rows, int) or isinstance(rows, bool) or rows < 0:
        rows = 0
    return "caller-context: token=%s backend=%s index_rows=%d" % (token_state, backend, rows)


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
        log.warning("RCS_HOOK_PATH set without RCS_HOOK_TOKEN: RCS route (incl. Turo) refuses all posts (503)")
    if env("RCS_HOOK_DISABLED") == "1":
        log.warning("RCS_HOOK_DISABLED=1")
    if turo_disabled():
        log.warning("TURO_HOOK_DISABLED=1")
    owner, why = turo_owner_alert_to()
    if why == "owner_invalid":
        log.warning("TURO_OWNER_ALERT_TO is not valid E.164 (+1NXXNXXXXXX): Turo owner alerts disabled")
    log.info("turo: disabled=%s owner_alert=%s owner=***%s helm_webhook=%s alexandra_push=%s",
             turo_disabled(), bool(owner) and env("TURO_OWNER_ALERT_DISABLED") != "1",
             _tail4(owner) if owner else "----",
             bool(env("HELM_TURO_WEBHOOK_URL")),
             bool(env("ALEXANDRA_ALERT_URL")) and env("TURO_ALEX_PUSH_DISABLED") != "1")
    turo_prune_inbox(force=True)
    warn_fc_config()
    _warn_paths()
    start_turo_email_listener()
    log.info("%s", caller_context_startup_line())
    meeting_check.ALERT_SINK = lambda title, detail: _spawn(
        alert_failure, ("meeting-check", detail, 1, title, "Component", "Attempts", "prism `bland-relay.service` meeting-check"))
    log.info("%s", meeting_check.startup_line())
    log.info(
        "post-call: ingest=%s push=%s",
        "off" if env("POST_CALL_INGEST").lower() == "off" else "on",
        "off" if env("POST_CALL_PUSH_DISABLED") == "1" else "on",
    )
    meeting_check.start_sync()
    srv = ThreadingHTTPServer((HOST, PORT), H)
    log.info("listening on %s:%d", HOST, PORT)
    srv.serve_forever()


