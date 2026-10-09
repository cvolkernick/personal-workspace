# bland-relay

Stdlib HTTP relay on prism. Listens on `127.0.0.1:8799` (user unit `bland-relay.service`). Tailscale Funnel `:8443` is the existing public front. Secret paths stay unguessable. This tree is the source for `~/bin`. The unit keeps running the copy in `~/bin`, not this checkout.

## Routes

| Route | Auth | What it does |
|---|---|---|
| `POST /<RELAY_PATH>` | `X-Webhook-Signature` HMAC | Bland webhook. Forwards the raw body to Alexandra. |
| `POST /<FWD_PATH>` | `FWD_TOKEN` as `Authorization: Bearer` or `X-Relay-Token` | 904 inbound SMS from the phone forwarder. Forwards the normalized payload. Records the sender for the reply allowlist unless `test: true`. |
| `POST /<SMSGW_HOOK_PATH>` | Secret path, plus SMSGate `X-Signature`/`X-Timestamp` HMAC when `SMSGW_SIGNING_KEY` is set | SMSGate cloud `sms:received` webhook (#1054). Same inbound path as the forwarder: allowlist record, STOP/opt-out, same payload to Alexandra. Replaces the SMS Forwarder app. |
| `POST /<RCS_HOOK_PATH>` | Secret path, plus `RCS_HOOK_TOKEN` as `Authorization: Bearer` or `X-Relay-Token` when set | Google Messages notifications from the 904 phone (#1056), so RCS chats arrive too. Same allowlist, STOP, and payload. Deduped against SMSGate. Kill switch `RCS_HOOK_DISABLED=1`. Turo app notifications on the same route go to the Turo relay (#1104, below). |
| `POST /<SEND_PATH>` | `SEND_TOKEN`, same headers | Alexandra sends one SMS. |
| `POST /<FC_PATH>` | `SEND_TOKEN` (add/request/list/revoke) or the approver token (approve, only with `FC_REQUIRE_APPROVAL=1`) | First-contact numbers (#1100). See below. |

Send body:

```json
{"to": "+19045550199", "message": "text", "in_reply_to": "optional", "purpose": "optional", "placed_by": "optional seat"}
```

`to` is strict E.164 US NANP (`+1` then NXX NXX XXXX). `message` is required, stripped, and at most 640 characters. `in_reply_to` is accepted and not sent to the gateway.

Success is `{"ok": true, "id": "...", "state": "Pending"}`. The id and state come from the gateway.

| Result | Status |
|---|---|
| Kill switch `SMS_SEND_DISABLED=1` | 503 `send disabled` |
| Send token or gateway settings missing | 503 `relay not configured` |
| Bad or missing token | 401 |
| Bad `to` or `message` | 400 |
| Number has not texted 904 in the last 72 hours and has no active first-contact entry | 403 `not_allowlisted` |
| First-contact entry expired / `FC_DISABLED=1` | 403 `first_contact_expired` / `first_contact_disabled` |
| First-contact number already got `FC_MAX_SENDS_BEFORE_REPLY` texts without replying | 429 `first_contact_send_cap` |
| Inbound STOP (or UNSUBSCRIBE, CANCEL, END, QUIT, STOPALL, REVOKE, OPTOUT) | 403 `opt_out` |
| More than 5 sends to one number in 10 minutes, or 30 sends in an hour | 429 `rate_limited` |
| Gateway error | 502 `gateway failed`, plus the existing #701 alert (15-minute dedupe shared with Bland) |

Opt-out matches the whole inbound text, punctuation ignored. A later normal text does not clear it. `START`, `UNSTOP`, or `YES` does. Opt-out still blocks after 72 hours. The gateway is called once. A retry would risk a second SMS, so a failed attempt still uses a rate-limit slot.

Logs show the last 4 digits and the message length. They do not show the body, the secret paths, or the gateway password. State lives in `~/.local/state/bland-relay/sms_send_state.json` (mode 0600) and stores numbers and timestamps only.

## Gateway mode

**Cloud server.** The 904 line is a T-Mobile handset. It is not on the house LAN, so the Pi cannot depend on the phone's local HTTP port. Cloud mode keeps working when the phone is on cellular.

App: [SMS Gateway for Android](https://sms-gate.app) (`me.capcom.smsgateway`). API base, no trailing path:

`SMSGW_URL=https://api.sms-gate.app/3rdparty/v1`

The relay POSTs `{phoneNumbers, textMessage}` to `{SMSGW_URL}/messages` with HTTP Basic auth (the app's cloud username and password). Cloud OpenAPI 1.49.0 lists `POST /3rdparty/v1/messages` only. The device local server still uses `/message`. If `SMSGW_URL` already ends in `/message` or `/messages`, that full URL is the endpoint, so local mode is `SMSGW_URL=http://<phone>:8080/message` once the phone is reachable. Inbound now comes from the SMSGate `sms:received` webhook (see below). The forwarder route stays available.

## SMSGate inbound webhook (#1054)

The SMS Gateway app (`me.capcom.smsgateway`, cloud mode) posts `sms:received` events:

```json
{"deviceId": "...", "event": "sms:received", "id": "...", "webhookId": "...",
 "payload": {"messageId": "...", "message": "text", "sender": "+19045550199",
             "recipient": "+19043343975", "simNumber": 1, "receivedAt": "2026-10-03T23:00:00.000-04:00"}}
```

The route maps `payload.sender` to `from`, `payload.message` to `body` and `payload.receivedAt` to `received_at`, in the same record the forwarder route builds (`source` stays `t-mobile-904-forwarder`; `raw` holds the SMSGate envelope). It records the sender in `sms_send_state.json` inbound (72-hour allowlist), applies STOP/START exactly like the forwarder, and forwards to `ALEXANDRA_ALERT_URL`. Other events get 200 `ignored`, so the phone does not retry them. A repeated event `id` that was already forwarded gets 200 `duplicate`. A forward failure returns 502, so the app retries with backoff, and raises the usual #701 alert.

Auth:

- `SMSGW_HOOK_PATH` is a long random path generated on prism. A wrong or missing path gets 404.
- `SMSGW_SIGNING_KEY`, when set, requires `X-Signature` = hex HMAC-SHA256(key, raw body + `X-Timestamp`), with `X-Timestamp` (Unix seconds) within 5 minutes. A missing, bad or stale signature gets 401. When it is unset, the secret path alone authenticates and the relay logs a startup warning.

The signing key lives in the app: **Settings → Webhooks → Signing Key**. The app generates it at first use, and you can view or change it there. To turn HMAC on, copy the key from that screen and enter it on prism with a hidden prompt: `~/bin/bland-relay-setkey SMSGW_SIGNING_KEY`. Do not paste it into chat.

Setup on prism:

```bash
python3 -c 'import secrets; print("smsgate-" + secrets.token_urlsafe(32), end="")' | ~/bin/bland-relay-setkey SMSGW_HOOK_PATH
~/bin/bland-relay-setkey SMSGW_SIGNING_KEY   # optional; hidden prompt
```

Register the webhook (cloud API, HTTP Basic `SMSGW_USER`/`SMSGW_PASS`). Python urllib needs a non-default `User-Agent`, because the default gets Cloudflare error 1010:

```
POST https://api.sms-gate.app/3rdparty/v1/webhooks
{"url": "https://prism-gateway.tailb1085a.ts.net:8443/<SMSGW_HOOK_PATH>", "event": "sms:received", "device_id": "<optional>"}
```

`GET /3rdparty/v1/webhooks` lists them, and `DELETE /3rdparty/v1/webhooks/<id>` removes one. The app also lists them under Settings → Webhooks → Registered webhooks.

## RCS inbound via Google Messages notifications (#1056)

SMSGate only sees SMS/MMS. Its docs say "RCS messages don't trigger SMS webhooks," and no event type covers RCS. To catch RCS chats, a notification-listener app on the 904 Pixel posts each Google Messages notification (`com.google.android.apps.messaging`) to `POST /<RCS_HOOK_PATH>`.

**App: Notification Relay Webhook** (`com.notifrelay.app`, AGPL-3.0, [github.com/cobanov/notification-relay-webhook](https://github.com/cobanov/notification-relay-webhook), APK from [Releases](https://github.com/cobanov/notification-relay-webhook/releases/latest)). Why this app:

- It reads notification text with `getCharSequence`, so styled or emoji text is not dropped.
- It builds real JSON, so quotes and newlines in a message can't corrupt the body.
- It flags group summaries and has a per-app allowlist and custom headers.
- It's free and open source.

The trade-off is that it doesn't retry automatically. You can resend failed items by hand under Logs. Android Nomad Gateway (`tech.wdg.incomingactivitygateway`) was the runner-up. It has a template and retries, but it reads text with `getString`, which loses spannable text, and it doesn't escape template values. The relay still accepts that app's template bodies through a lenient parser. Neither app is on Play or F-Droid, so sideload the APK.

Body (fixed by the app; the relay also takes `title`/`text`/`package` from any template):

```json
{"package": "com.google.android.apps.messaging", "title": "Chris V", "text": "need a tow",
 "big_text": null, "postedAt": "2026-10-04T03:59:00Z", "group_summary": false, "...": "..."}
```

Handling:

- Requests without the secret path get 404. When `RCS_HOOK_TOKEN` is set, a missing or wrong token gets 401. `RCS_HOOK_DISABLED=1` returns 503 and is the kill switch for this route only. `SMS_SEND_DISABLED` still controls replies.
- Other packages, group summaries, and empty or placeholder text get 200 `ignored`, so the app doesn't retry them.
- Sender: Google Messages shows the contact **name** as the title for saved contacts and a formatted number for unsaved ones. The relay uses an explicit `number` field when the template has one. Otherwise it parses the title as a NANP number, then looks the title up in `RCS_NAME_MAP`, matched without regard to case or spacing. Use the `Name=+1NXXNXXXXXX;Other Name=+1NXXNXXXXXX` form, because the systemd `EnvironmentFile` strips double quotes and that breaks raw JSON. A JSON object is accepted only if it reaches the process intact. A resolved number is recorded for the 72-hour allowlist and STOP/START exactly like SMS. An unmapped name is forwarded with `from` set to `unknown sender <name>`. It is **not** recorded or allowlisted, so Alexandra can't reply to it.
- Payload: same keys as the SMSGate and forwarder record (`source` `t-mobile-904-forwarder`, `channel` `sms`, `line`, `from`, `body`, `received_at`, `relayed_at`, `test`, `raw`), plus `via: "rcs-notification"` and `sender_name`.
- Dedupe: Google Messages also posts a notification for every plain SMS. Within 120 s, the same sender and body (whitespace and case ignored) are forwarded once. When the RCS side only has an unmapped name, the body alone decides. The RCS route waits up to `RCS_DEDUPE_WAIT_S` (default 8 s, max 20) for SMSGate to report the same SMS, so the SMSGate path normally wins. If the RCS notification is forwarded first, a later SMSGate event for the same message gets 200 `duplicate`. It still records the number for the allowlist and STOP. Repeated notification updates for the same message also collapse. A failed forward releases its claim and returns 502.
- Logs show `rcs inbound ok: from=***1234 how=title|number|name_map|unknown len=N`. They do not show the body, contact names, the token, or the path.

Setup on prism (each `setkey` restarts the unit):

```bash
python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(48)), end="")' | ~/bin/bland-relay-setkey RCS_HOOK_PATH
python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(40)), end="")' | ~/bin/bland-relay-setkey RCS_HOOK_TOKEN
printf %s 'Contact Name=+1NXXNXXXXXX;Other Name=+1NXXNXXXXXX' | ~/bin/bland-relay-setkey RCS_NAME_MAP   # optional
```

Phone setup (904 Pixel):

1. Download `notification-relay-webhook-v1.0.0.apk` from the Releases link above and install it. Allow "install unknown apps" for the browser when Android asks.
2. Android 13+ blocks notification access for sideloaded apps at first. Go to **Settings → Apps → Notification Relay Webhook → ⋮ → Allow restricted settings**.
3. Open the app and grant **Notification access** (Settings → Notifications → Device & app notifications → Notification Relay Webhook → Allow). This is the only permission the route needs.
4. Add a webhook. URL: `https://prism-gateway.tailb1085a.ts.net:8443/<RCS_HOOK_PATH>`. Header: `Authorization` = `Bearer <RCS_HOOK_TOKEN>`. Tap **Test**. The relay answers 200 `ignored` because a test is not a Messages notification. Then tap **Save**.
5. Set forwarding mode to **Allowlist** and select only **Messages** (`com.google.android.apps.messaging`). Turn on "ignore group summaries" and "ignore ongoing" if the app offers them.
6. Battery: **Settings → Apps → Notification Relay Webhook → Battery → Unrestricted**.
7. Keep Google Messages notifications **on**, including for conversations. Don't mute the senders whose texts must reach Alexandra. A muted conversation posts no notification.
8. Keep RCS on. Don't change SMSGate.
9. For saved contacts, add their names to `RCS_NAME_MAP` on prism, exactly as Google Messages shows them. Otherwise they arrive as `unknown sender <name>` and can't be replied to.

## Turo notifications (#1104)

The same notification-relay app posts Turo app notifications (package `com.relayrides.android.relayrides`) to the RCS route on the same path and token. The RCS route sends them to the Turo relay instead of ignoring them. Google Messages handling is unchanged, and other packages still get `200 ignored: package`.

Pipeline (stdlib, no model):
1. **Normalize**, preferring `big_text` over `text`, to `guest` (title, with any ` • vehicle` suffix split off), `text`, `sub_text`, `category`, `posted_at` (from `postedAt`, UTC ISO), `key` and `received_at` (relay time, UTC ISO). Also parsed: `reservation_id` (a 7–9 digit trip number; a labeled "Trip/Reservation/Booking #" first, then a bare number in title, sub_text, text), `vehicle` and `plate` (24EWUH/25EWUH map to "Toyota Corolla <plate>", Rivian/R1S to "Rivian R1S", Corolla to "Toyota Corolla", plus common makes). Unknown fields are `null`.
2. **Dedupe** within `TURO_DEDUPE_WINDOW_S` (default 24 h) when any of these repeat: the same `key` + text, the same guest + text, or a shorter prefix of a text seen from the same guest within 10 min (truncated re-posts). This covers re-posts on update and the app's 3 retries. State is in `turo_state.json`, so it survives restarts.
3. **Inbox:** one JSON line per event in `~/.local/state/bland-relay/turo_inbox.jsonl` (`TURO_INBOX_PATH`), mode 0600. Phone numbers and emails in `text`/`sub_text` are replaced with `[phone]`/`[email]`. Lines older than `TURO_RETENTION_DAYS` (default 30) are pruned at startup and daily. Suppressed events are written with `suppressed: true`. Full text goes only to the Helm/Alexandra webhooks. The journal never has bodies, guest names, trip numbers or full numbers.
4. **Suppress** events matching `TURO_SUPPRESS_RE` (default `safe ?wheels`, case-insensitive; matched against title, sub_text, vehicle and text). These are Alex/SafeWheels vehicles, not our fleet (our fleet is the Rivian R1S, Corolla 24EWUH and Corolla 25EWUH). Suppressed events are written to the inbox only: no SMS, no webhook. **Set the Alex vehicle names here**, e.g. `safe ?wheels|camry|model 3`.
5. **Urgent filter**, in priority order: `safety`, `accident_damage`, `lockout`, `no_start`, `charging`. Defaults live in `TURO_URGENT_DEFAULTS` in code. Override a class with `TURO_URGENT_RE_<CLASS>` (e.g. `TURO_URGENT_RE_NO_START`). A sentence that matches the negative list `TURO_URGENT_NEG` (default: "before return", "already there", "pre-existing", "instructions", "no smoking", "thanks for the help", "no damage", …) is skipped. So "the scratch was already there" is not urgent, but "already there. I just got rear-ended" is. `matched_term` is logged in the inbox for tuning. An invalid override regex logs a warning and the default is used. Note: a systemd EnvironmentFile may eat backslashes, so check `journalctl` after changing a pattern.
6. **owner_alert** (urgent only): a 904 SMS `URGENT Turo [<class>]: <guest>: <first 140 chars>` to `TURO_OWNER_ALERT_TO`. The only number accepted is `+12073100000`, hard-coded as `TURO_OWNER_ALERT_ALLOWED`. Any other value is refused by the relay and by `bland-relay-setkey`. The alert skips only the 72 h inbound window, and only for that number. It still respects `SMS_SEND_DISABLED`, STOP/opt-out, `TURO_OWNER_ALERT_DISABLED`, an alert dedupe (same guest + text), `TURO_ALERT_GUEST_MAX` (1) per `TURO_ALERT_GUEST_WINDOW_S` (600 s), `TURO_ALERT_HOUR_MAX` (6/h overall), and the relay-wide `RECIPIENT_MAX`/`GLOBAL_MAX`. A gateway failure goes to `alert_failure` (journal + #701). **Non-urgent Turo traffic never texts anyone.**
7. **Handoff**, each in its own background task with a `TURO_WEBHOOK_TIMEOUT_S` (default 5 s) timeout and 2 tries. A failure is logged and sent to #701, and never blocks the owner alert or the 200 to the phone:
   - **Helm:** every non-suppressed event is POSTed as JSON to `HELM_TURO_WEBHOOK_URL`. Auth is `Authorization: Bearer <HELM_TURO_WEBHOOK_KEY>`, the same scheme as `ALEXANDRA_ALERT_AUTH` on the live Alexandra routine webhook. A key that already contains a space (e.g. `Bearer …`) is sent as is. `HELM_TURO_WEBHOOK_HEADER` (e.g. `X-Webhook-Key`) sends the bare key in that header instead.
   - **Alexandra:** urgent events only, through the existing `forward()` to `ALEXANDRA_ALERT_URL`/`ALEXANDRA_ALERT_AUTH`, with `"kind": "turo_urgent"`. Turn it off with `TURO_ALEX_PUSH_DISABLED=1`.
   - With no URL set, the event is only in the JSONL. Helm reads it in its daily pass.

Kill switch: `TURO_HOOK_DISABLED=1` answers Turo posts with `200 ignored: turo_disabled` (200, so the app doesn't retry), with no inbox write, SMS or webhook. Messages keep working. `RCS_HOOK_DISABLED=1` still stops the whole route.

Event JSON (Helm body; the Alexandra body is the same with `kind: "turo_urgent"`):

```json
{"kind": "turo_event", "schema": "panamerica.turo.event.v1", "src": "turo", "event_id": "16 hex",
 "guest": "Sam", "reservation_id": "12345678", "vehicle": "Rivian R1S", "plate": null,
 "text": "full message (big_text preferred)", "sub_text": null, "category": "msg",
 "posted_at": "2026-10-09T20:00:00Z", "received_at": "2026-10-09T20:00:02Z", "key": "notification key",
 "urgent": true, "urgent_class": "lockout", "matched_term": "locked out", "line": "+19043343975"}
```

Helm matches on `reservation_id` first, then `guest` + `vehicle`. `event_id` is stable for a key + text, so it can serve as an idempotency key.

Setup on prism (after deploy):

```bash
printf %s "+12073100000" | ~/bin/bland-relay-setkey TURO_OWNER_ALERT_TO
printf %s "$HELM_URL" | ~/bin/bland-relay-setkey HELM_TURO_WEBHOOK_URL    # from Helm's "Turo inbound" routine
printf %s "$HELM_KEY" | ~/bin/bland-relay-setkey HELM_TURO_WEBHOOK_KEY
printf %s 'safe ?wheels|<alex vehicle names>' | ~/bin/bland-relay-setkey TURO_SUPPRESS_RE
# optional: TURO_HOOK_DISABLED TURO_OWNER_ALERT_DISABLED TURO_ALEX_PUSH_DISABLED HELM_TURO_WEBHOOK_HEADER
#   TURO_URGENT_RE_{LOCKOUT,NO_START,ACCIDENT_DAMAGE,CHARGING,SAFETY} TURO_URGENT_NEG TURO_DEDUPE_WINDOW_S
#   TURO_ALERT_GUEST_WINDOW_S TURO_ALERT_GUEST_MAX TURO_ALERT_HOUR_MAX TURO_INBOX_PATH TURO_RETENTION_DAYS TURO_WEBHOOK_TIMEOUT_S
```

Phone: in Notification Relay Webhook → Allowlist → select apps, also check **Turo** (keep Messages checked). See the #718 design comment §3 for the full checklist. The payload format is assumed until the first real notification. Check the app's Logs tab and tune the parser and regexes from `turo_inbox.jsonl`.

## First contact (#1100)

The send route only reaches numbers that texted 904 in the last 72 hours. To text a number that never texted 904 (a GVG lead, Turo renter or vendor), an agent first adds it to the first-contact allowlist. Sending is unchanged: once a number is active, use `send-904` (or `POST /<SEND_PATH>`) as usual.

**Approval is off by default** (`FC_REQUIRE_APPROVAL=0`, Chris's decision 2026-10-09): Alexandra and the other agents may text new numbers without a per-number yes from Chris. Every other guardrail below still applies. Set `FC_REQUIRE_APPROVAL=1` to restore the per-number approval gate from #1101 (rollback: `printf 1 | ~/bin/bland-relay-setkey FC_REQUIRE_APPROVAL`). The only change in gated mode is the #1102 (a) daily-cap fix below.

### Flow with approval off (default)

1. **Add** (any agent, `SEND_TOKEN`):
   ```bash
   send-904-fc add --to "+19045550123" --name "Dana Lead" --kind lead \
     --purpose "GVG lead asked about fleet rental" --by alexandra
   ```
   All of `--to`, `--name`, `--kind lead|renter|vendor`, `--purpose` and `--by` are required. The relay normalizes the number to E.164, refuses opted-out numbers (403 `opt_out`) and the daily cap (429 `daily_cap`), and otherwise returns `200 {"status": "active", "approval_required": false, ...}`. No code. `request` is an alias of `add` while approval is off. Re-adding a number that is already active returns `already_active: true` and changes nothing (it does not extend the TTL or reset the sends-before-reply count).
2. **Send** with `send-904` as usual.
3. **List / revoke:** `send-904-fc list [--status active|expired|converted|revoked|opted_out]`, `send-904-fc revoke --to ... --reason ... --by <seat>`. Both work even with a kill switch on.

While approval is off, the approval paths are inert: `send-904-fc approve` / `{"action": "approve"}` returns 409 `approval_not_required` (audited as `deny`), an SMS `YES <code>` to 904 is ordinary inbound text (it never activates anything, from any number or inbound route, including RCS titles), and `FC_APPROVER_NUMBERS` / `FC_APPROVE_TOKEN_SHA256` are ignored. Any value other than `0`/`1` (or `false/no/off`, `true/yes/on`) fails closed to approval required.

### Flow with approval on (`FC_REQUIRE_APPROVAL=1`)

1. **Request** (any agent, `SEND_TOKEN`):
   ```bash
   send-904-fc request --to "+19045550123" --name "Dana Lead" --kind lead \
     --purpose "GVG lead asked about fleet rental" --by alexandra
   ```
   The relay normalizes the number to E.164, refuses opted-out numbers (403 `opt_out`) and the daily cap (429 `daily_cap`), and returns a one-time 6-digit `code` (valid 24 h; only its hash is stored) and an `ask` sentence to show Chris. (`add` is the same call.)
2. **Chris approves** by typing yes with the code, one of:
   - **SMS (preferred):** Chris texts `YES 123456` to 904 from a number in `FC_APPROVER_NUMBERS`. The SMSGate (signed) or forwarder inbound route matches it. RCS notifications resolved only through `RCS_NAME_MAP` never approve.
   - **Chat:** the user-facing seat (Grok) relays his typed text:
     ```bash
     printf '%s\n' "$FC_APPROVE_TOKEN" | send-904-fc approve --to "+19045550123" --code 123456 \
       --approver chris --text "yes 123456" --ref "<chat message id/link>" --token-stdin
     ```
   The approver must be in `FC_APPROVERS` (default `chris`), must not be an agent seat (`FC_AGENT_SEATS` plus built-ins alexandra, grok, forge, buzz, grokbuild, gvg, muse…) and must not be the requester. The text must start with yes/approve/ok and contain the code. `approval_ref` is required. `SEND_TOKEN` can never approve, and an approver token equal to `SEND_TOKEN` is refused.
3. **Send** with `send-904` as usual. The entry stores approver, approval text, ref, via, `approved_at` and `expires_at`.
4. **List / revoke** as above (`--status pending` also lists open requests).

Route body for `POST /<FC_PATH>`: `{"action": "add"|"request"|"approve"|"list"|"revoke", ...}` with the fields above (`to`, `name`, `purpose_kind`, `purpose`, `requested_by`; `code`, `approver`, `approval_text`, `approval_ref`; `status`; `reason`, `revoked_by`). `list` also returns `approval_required`.

### Guardrails

| Rule | Setting |
|---|---|
| Opt-out wins: STOP blocks add/request, approve and send, even for an active entry, until START/UNSTOP/YES | always |
| Existing 5 / number / 10 min and 30 / hour global | always |
| New first-contact numbers added/approved per rolling 24 h. Only renewing a currently `active` entry is exempt; re-adding a revoked, expired, converted or opted-out number counts (#1102 a) | `FC_DAILY_MAX` (default 20) |
| Entry lifetime. Approval on: renew with a new request + approval (resets the send count). Approval off: once expired, add again (counts against the daily cap) | `FC_TTL_DAYS` (default 30, max 90) |
| Texts per entry until the contact replies | `FC_MAX_SENDS_BEFORE_REPLY` (default 3) |
| A reply converts the entry (`converted`) to the normal 72 h inbound window | always |
| `SMS_SEND_DISABLED=1`: send 503, add/request/approve 503, SMS approvals ignored | kill switch |
| `FC_DISABLED=1`: first-contact only (add/request/approve 503, active entries stop granting sends) | kill switch |
| Per-number approval gate (`0` = off, default; `1` = gated as in #1101) | `FC_REQUIRE_APPROVAL` |

### Audit and CRM

- `~/.local/state/bland-relay/first_contact_audit.jsonl` (0600, append-only; `FC_AUDIT_PATH` overrides): `add` (approval off, `approval_required: false`), `request`, `approve`, `deny`, `revoke`, `convert`, `opt_out`, `send`, `send_refused`. Full E.164 numbers and the approval text, never message bodies or secrets.
- `~/.local/state/bland-relay/crm_outbox.jsonl` (0600; `CRM_OUTBOX_PATH` overrides, `CRM_INGEST_MODE=off` disables): one record per send through the relay (first contact and replies), schema `panamerica.crm.interaction.v1` = the #1049 `interaction` columns (`channel`, `direction`, `provider`, `provider_msg_id`, `from_addr`, `to_addr`, `summary`, `outcome`, `error_code`, `occurred_at`, `logged_by`) plus `metadata` (`purpose`, `purpose_kind`, `placed_by`, `first_contact`, `approval_required`, `approval`; `approval.approved_via` is `auto` for no-approval adds) and `party_hint` (`phone`, `display_name`, `type`, `tags`, `source`) for #1097 auto-create. No `body`. crm-api is not deployed on prism yet (#1097 step 0); `CrmOutboxSink` is the adapter #1097 replaces with a `POST /v1/interactions` sink, draining this file idempotently on `provider_msg_id`.

### Setup on prism (after merge)

```bash
python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(48)), end="")' | ~/bin/bland-relay-setkey FC_PATH
# That is all with approval off (default). The rest is only for FC_REQUIRE_APPROVAL=1:
printf 1 | ~/bin/bland-relay-setkey FC_REQUIRE_APPROVAL
# Approver token: generate it in the approving seat's own secret store. Only its hash goes on prism.
printf %s "$FC_APPROVE_TOKEN" | sha256sum | cut -d' ' -f1 | tr -d '\n' | ~/bin/bland-relay-setkey FC_APPROVE_TOKEN_SHA256
printf %s 'chris=+1NXXNXXXXXX' | ~/bin/bland-relay-setkey FC_APPROVER_NUMBERS   # Chris's own cell(s)
# optional: FC_REQUIRE_APPROVAL FC_APPROVERS FC_AGENT_SEATS FC_DAILY_MAX FC_TTL_DAYS FC_MAX_SENDS_BEFORE_REPLY FC_DISABLED CRM_INGEST_MODE CRM_OUTBOX_PATH
```

## Secrets

Values go in `~/.config/bland-relay/env` only, through `bland-relay-setkey` (stdin, not argv). Not in git, chat, or logs.

```bash
printf %s "$VALUE" | ~/bin/bland-relay-setkey SMSGW_URL
printf %s "$VALUE" | ~/bin/bland-relay-setkey SMSGW_USER
printf %s "$VALUE" | ~/bin/bland-relay-setkey SMSGW_PASS
printf %s "$VALUE" | ~/bin/bland-relay-setkey SEND_PATH
printf %s "$VALUE" | ~/bin/bland-relay-setkey SEND_TOKEN
printf %s "$VALUE" | ~/bin/bland-relay-setkey SMSGW_HOOK_PATH
printf %s "$VALUE" | ~/bin/bland-relay-setkey SMSGW_SIGNING_KEY
printf %s 1 | ~/bin/bland-relay-setkey SMS_SEND_DISABLED   # kill switch
printf %s 0 | ~/bin/bland-relay-setkey SMS_SEND_DISABLED
printf %s "$VALUE" | ~/bin/bland-relay-setkey RCS_HOOK_PATH
printf %s "$VALUE" | ~/bin/bland-relay-setkey RCS_HOOK_TOKEN
printf %s "Name=+1NXXNXXXXXX;Name 2=+1NXXNXXXXXX" | ~/bin/bland-relay-setkey RCS_NAME_MAP
printf %s 1 | ~/bin/bland-relay-setkey RCS_HOOK_DISABLED   # RCS route kill switch
printf %s 1 | ~/bin/bland-relay-setkey TURO_HOOK_DISABLED  # Turo relay kill switch (#1104)
printf %s "$VALUE" | ~/bin/bland-relay-setkey HELM_TURO_WEBHOOK_KEY
```

`SEND_PATH` and `SEND_TOKEN` are long random strings. Alexandra gets the Funnel URL plus the token in her own secret store.

Phone setup, before any live send: install the app, turn Cloud Server on, tap Online, grant SMS, and set battery to Unrestricted. Keep **RCS on** in Google Messages. With RCS off, SMSGate outbound sends stall at Processed (#1023). RCS chats never fire `sms:received`, so they come in through the RCS notification route (see the #1056 section above).

## Deploy

Not done by the PR. After merge, on prism:

```bash
cp deploy/bland-relay/bland_relay.py deploy/bland-relay/bland-relay.py ~/bin/
cp deploy/bland-relay/bland-relay-setkey deploy/bland-relay/send-904 deploy/bland-relay/send-904-fc ~/bin/
chmod 755 ~/bin/bland-relay.py ~/bin/bland-relay-setkey ~/bin/send-904 ~/bin/send-904-fc
# unit file already matches ~/.config/systemd/user/bland-relay.service
systemctl --user restart bland-relay.service
```

Back up first (`for f in bland_relay.py bland-relay.py bland-relay-setkey send-904 send-904-fc; do cp ~/bin/$f ~/bin/$f.bak-pre1104; done`). Rollback is `~/bin/*.bak-pre1104` (latest), `~/bin/*.bak-1103`, `~/bin/*.bak-pre1100`, `~/bin/*.bak-pre1056`, `~/bin/*.bak-pre1054`, `~/bin/bland-relay.py.bak-fwd904`, or the kill switch. The Bland route and the 904 inbound route stay on the same paths and tokens.

## Tests

From this directory, with pytest on the path:

```bash
python3 -m pytest tests -q
```

`tests/test_first_contact.py` (#1100, SMSGate mocked, no network) covers, with `FC_REQUIRE_APPROVAL=1`: approval required, chat and SMS approval, no agent/self approval, code/text/ref checks, opt-out at request/approve/send and START, daily and sends-before-reply caps, existing rate limits, expiry/renewal/conversion, both kill switches, list/revoke, audit and CRM outbox records (no body, no secrets, 0600), log redaction, and the `send-904-fc` CLI; the #1102 (a) daily-cap rule for revoked/expired re-requests; and, with approval off (default): immediate add, validation, STOP blocking add/send until START, the daily cap counting re-adds, TTL, no cap reset on re-add, existing rate limits, both kill switches, audit/CRM records, the approve route and SMS `YES <code>` being inert, and `send-904-fc add`.

Covered: token 401, validation, 72-hour allowlist, STOP until START, both rate limits, kill switch, missing gateway config, one mocked gateway call, log and #701 text redaction, 904 inbound forward plus allowlist recording, Bland HMAC including the compact-JSON signature, the SMSGate webhook: signature ok/bad/unsigned/stale, wrong path 404, payload parse and shape parity, allowlist, STOP/START, ignored events, duplicate ids, forward failure, and log redaction. The RCS notification route (`tests/test_rcs_webhook.py`) covers: app payload and lenient template parsing, number/title/name-map/unknown sender resolution, token 401, path 404, kill switch 503, package/summary filtering, STOP/START, the unknown sender not being allowlisted, dedupe both ways against SMSGate plus repeat notifications and window expiry, the SMSGate head-start wait, forward failure and retry, and log redaction. The Turo relay (`tests/test_turo_relay.py`, #1104, gateway/webhooks mocked and real network blocked) covers: the Turo branch on the same path/token, token 401 and path 404, other packages still ignored, Messages unchanged, `big_text` preference, the normalized shape, reservation/vehicle/guest parsing with nulls, dedupe (key+text, guest+text, prefix re-post, window expiry, persisted state), suppression (default, env, bad regex), each urgent class, priority, the negative list and env overrides, owner_alert (template, only the allowed number, any other refused, unset, 72 h skip for the owner only, `SMS_SEND_DISABLED`, STOP/START, dedupe, per-guest and hourly limits, relay-wide slot, gateway failure), non-urgent never texting, both kill switches, the Helm POST shape and auth (default Bearer, scheme passthrough, custom header, bad header, no key, timeout), JSONL-only when URLs are unset, the Alexandra `turo_urgent` push, webhook failure/exception isolation, inbox 0600/redaction/retention, and log/secret redaction.
