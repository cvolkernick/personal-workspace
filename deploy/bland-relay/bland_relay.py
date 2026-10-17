#!/usr/bin/env python3
"""Bland post-call webhook relay and 904 SMS send route (stdlib only).

POST /<RELAY_PATH> verifies X-Webhook-Signature (hex HMAC-SHA256 of the raw
body, optional "sha256=" prefix) and forwards the raw body to
ALEXANDRA_ALERT_URL. POST /<FWD_PATH> accepts the 904 phone forwarder and
forwards a normalized SMS payload to the same alert URL. POST /<SMSGW_HOOK_PATH>
accepts the SMS Gateway for Android (SMSGate) cloud `sms:received` webhook and
feeds it through the same inbound path (#1054). The same hook accepts
`sms:sent`, `sms:delivered`, and `sms:failed` and records send state (#1140).
POST /<RCS_HOOK_PATH>
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
templated 904 SMS to TURO_OWNER_ALERT_TO (the only recipient) plus a "turo_urgent" push to
ALEXANDRA_ALERT_URL. Turo guest-message emails (Gmail push, #1142) arrive from
gmail_turo_watch.py on a loopback-only listener (TURO_EMAIL_PORT) and run
through the same normalizer and turo_ingest() path, deduped across sources.
GET /bland/caller-context returns a short caller history for Bland dynamic_data
(#1096). DELETE on that path forgets one number in the local fallback index.
POST /bland/meeting-check (#1230) answers match or no-match for a caller's stated
meeting; it needs MEETING_CHECK_TOKEN and stays no-match without calendar keys.
A signed post-call webhook also schedules post_call.handle (#1231) before the
raw forward. That hook does not change the HTTP status or call Bland.

On forward or send failure: journal ALERT line plus a GitHub ops issue #701
comment (treasury pi_ops_alert sink, #704), max 1 per 15 min. Never logs
secrets, secret paths, message bodies, or full phone numbers.
"""

from pathlib import Path

# Implementation lives in the relay_*.py parts (#1107). They are loaded into
# this module so `import bland_relay` and monkeypatches of its names keep
# working. Each part stays under ~40 KB.
_PARTS = (
    "relay_config.py",
    "relay_audit.py",
    "relay_sms.py",
    "relay_fc.py",
    "relay_send.py",
    "relay_rcs.py",
    "relay_turo.py",
    "relay_http.py",
)


def _load_relay_parts():
    here = Path(__file__).resolve().parent
    namespace = globals()
    for name in _PARTS:
        path = here / name
        if not path.is_file():
            raise ImportError("missing %s next to bland_relay.py" % name)
        code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
        exec(code, namespace)


_load_relay_parts()

if __name__ == "__main__":
    main()
