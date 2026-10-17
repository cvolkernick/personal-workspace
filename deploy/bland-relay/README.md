# bland-relay

Stdlib HTTP relay on prism. Listens on `127.0.0.1:8799` (user unit `bland-relay.service`). Tailscale Funnel `:8443` is the existing public front. Secret paths stay unguessable. This tree is the source for `~/bin`. The unit keeps running the copy in `~/bin`, not this checkout.

`bland_relay.py` is the import surface (`import bland_relay`, and the `bland-relay.py` wrapper). It loads these parts from the same directory: `relay_config.py`, `relay_audit.py`, `relay_sms.py`, `relay_fc.py`, `relay_send.py`, `relay_rcs.py`, `relay_turo.py`, `relay_http.py`. `caller_context.py`, `meeting_check.py` and `turo_email.py` stay beside them. Each of those source files stays under ~40 KB.

## Routes

| Route | Auth | What it does |
|---|---|---|
| `POST /<RELAY_PATH>` | `X-Webhook-Signature` HMAC | Bland webhook. Forwards the raw body to Alexandra. |
| `POST /<FWD_PATH>` | `FWD_TOKEN` as `Authorization: Bearer` or `X-Relay-Token` | 904 inbound SMS from the phone forwarder. Forwards the normalized payload. Records the sender for the reply allowlist unless `test: true`. |
| `POST /<SMSGW_HOOK_PATH>` | Secret path, plus SMSGate `X-Signature`/`X-Timestamp` HMAC when `SMSGW_SIGNING_KEY` is set | SMSGate cloud `sms:received` webhook (#1054). Same inbound path as the forwarder: allowlist record, STOP/opt-out, same payload to Alexandra. Replaces the SMS Forwarder app. |
| `POST /<RCS_HOOK_PATH>` | Secret path, plus `RCS_HOOK_TOKEN` (required) as `Authorization: Bearer` or `X-Relay-Token` | Google Messages notifications from the 904 phone (#1056), so RCS chats arrive too. Same allowlist, STOP, and payload. Deduped against SMSGate. Kill switch `RCS_HOOK_DISABLED=1`. Turo app notifications on the same route go to the Turo relay (#1104, below). |
| `POST /<SEND_PATH>` | `SEND_TOKEN`, same headers | Alexandra sends one SMS. |
| `POST /<FC_PATH>` | `SEND_TOKEN` (add/request/list/revoke) or the approver token (approve, only with `FC_REQUIRE_APPROVAL=1`) | First-contact numbers (#1100). See below. |
| `GET /bland/caller-context?pn=` | `CALLER_CONTEXT_TOKEN` as `Authorization: Bearer` or `X-Caller-Context-Token` | Read-only caller history for Bland `dynamic_data` (#1096). See below. |
| `DELETE /bland/caller-context?pn=` | `CALLER_FORGET_TOKEN` (Grok seat only) | Forget one number in the local fallback index. |
| `POST /bland/meeting-check` | `MEETING_CHECK_TOKEN` as `Authorization: Bearer` or `X-Meeting-Check-Token` | Mid-call Bland tool (#1230). Answers `{"match": false}` or `{"match": true, "meeting_time": "..."}` for the meeting a caller states. See Meeting check below. |

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

The one number in `TURO_OWNER_ALERT_TO` (the operator cell, compared after NANP normalization) skips the 72-hour allowlist and first-contact checks (grant kind `operator`). Opt-out and rate limits still apply to it. Unset or invalid `TURO_OWNER_ALERT_TO` means no bypass.

Opt-out matches the whole inbound text, punctuation ignored. A later normal text does not clear it. `START`, `UNSTOP`, or `YES` does. Opt-out still blocks after 72 hours. The gateway is called once. A retry would risk a second SMS, so a failed attempt still uses a rate-limit slot.

Logs show the last 4 digits and the message length. They do not show the body, the secret paths, or the gateway password. State lives in `~/.local/state/bland-relay/sms_send_state.json` (mode 0600) and stores numbers and timestamps only.

## Gateway mode

**Cloud server.** The 904 line is a T-Mobile handset. It is not on the house LAN, so the Pi cannot depend on the phone's local HTTP port. Cloud mode keeps working when the phone is on cellular.

App: [SMS Gateway for Android](https://sms-gate.app) (`me.capcom.smsgateway`). API base, no trailing path:

`SMSGW_URL=https://api.sms-gate.app/3rdparty/v1`

The relay POSTs `{phoneNumbers, textMessage, withDeliveryReport: true}` to `{SMSGW_URL}/messages` with HTTP Basic auth (the app's cloud username and password). `withDeliveryReport` asks the gateway for `sms:delivered` (#1140). A `Pending` response is not a final delivery. Cloud OpenAPI 1.49.0 lists `POST /3rdparty/v1/messages` only. The device local server still uses `/message`. If `SMSGW_URL` already ends in `/message` or `/messages`, that full URL is the endpoint, so local mode is `SMSGW_URL=http://<phone>:8080/message` once the phone is reachable. Inbound now comes from the SMSGate `sms:received` webhook (see below). The forwarder route stays available.

## SMSGate inbound webhook (#1054)

The SMS Gateway app (`me.capcom.smsgateway`, cloud mode) posts `sms:received` events:

```json
{"deviceId": "...", "event": "sms:received", "id": "...", "webhookId": "...",
 "payload": {"messageId": "...", "message": "text", "sender": "+19045550199",
             "recipient": "+19043343975", "simNumber": 1, "receivedAt": "2026-10-03T23:00:00.000-04:00"}}
```

The route maps `payload.sender` to `from`, `payload.message` to `body` and `payload.receivedAt` to `received_at`, in the same record the forwarder route builds (`source` stays `t-mobile-904-forwarder`; `raw` holds the SMSGate envelope). It records the sender in `sms_send_state.json` inbound (72-hour allowlist), applies STOP/START exactly like the forwarder, and forwards to `ALEXANDRA_ALERT_URL`. A repeated event `id` that was already forwarded gets 200 `duplicate`. A forward failure returns 502, so the app retries with backoff, and raises the usual #701 alert.

`sms:sent`, `sms:delivered`, and `sms:failed` use the same path, signature, and event-id dedupe (#1140). Each is keyed by `payload.messageId`. A known id appends `send_state` to the first-contact audit (state, `partsCount`, `reason`) and a `crm_outbox.jsonl` line with the same `provider_msg_id` so a later drain can update `outcome` and `error_code`. An unknown id is logged and ignored. `sms:failed` forwards one `kind=send_failed` event (`gateway_id`, `to_tail`, `reason`, `placed_by`) and posts one #701 alert, on the existing 15-minute cap. The log line and the alert do not include the message body or the full number. A repeated failure for the same id does not alert again. Other events still get 200 `ignored`.

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
{"url": "<PRISM_PUBLIC_BASE>:8443/<SMSGW_HOOK_PATH>", "event": "sms:received", "device_id": "<optional>"}
```

Register the same URL and signing key for `sms:sent`, `sms:delivered`, and `sms:failed` (one POST per event, no new secret). This tree does not register them. Do not run these commands from a review or a deploy:

```
POST https://api.sms-gate.app/3rdparty/v1/webhooks
{"url": "<PRISM_PUBLIC_BASE>:8443/<SMSGW_HOOK_PATH>", "event": "sms:sent"}

POST https://api.sms-gate.app/3rdparty/v1/webhooks
{"url": "<PRISM_PUBLIC_BASE>:8443/<SMSGW_HOOK_PATH>", "event": "sms:delivered"}

POST https://api.sms-gate.app/3rdparty/v1/webhooks
{"url": "<PRISM_PUBLIC_BASE>:8443/<SMSGW_HOOK_PATH>", "event": "sms:failed"}
```

`GET /3rdparty/v1/webhooks` lists them, and `DELETE /3rdparty/v1/webhooks/<id>` removes one. The app also lists them under Settings → Webhooks → Registered webhooks. There is no delayed `GET /messages/{id}` in this tree, and a failed send is not resent.

## RCS inbound via Google Messages notifications (#1056)

SMSGate only sees SMS/MMS. Its docs say "RCS messages don't trigger SMS webhooks," and no event type covers RCS. To catch RCS chats, a notification-listener app on the 904 Pixel posts each Google Messages notification (`com.google.android.apps.messaging`) to `POST /<RCS_HOOK_PATH>`.

A Google Messages notification with no resolvable sender whose title or text matches `SEND_FAILURE_NOTICE_RE` (default `not sent`, case-insensitive) is not forwarded as an inbound text. It is logged as `send_failure_notice`. If a send is still `Pending` or `Processed`, or `Failed` without a recorded `send_failed` alert, that notice is the one fallback `send_failed`. It does not carry the notification text.

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

- Requests without the secret path get 404. `RCS_HOOK_TOKEN` is required: while it is unset or blank the whole route answers 503 (there is no path-only mode), and a missing or wrong token gets 401. `RCS_HOOK_DISABLED=1` returns 503 and is the kill switch for this route only. `SMS_SEND_DISABLED` still controls replies.
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
4. Add a webhook. URL: `<PRISM_PUBLIC_BASE>:8443/<RCS_HOOK_PATH>` (prism-gateway's tailnet HTTPS name, port 8443). Header: `Authorization` = `Bearer <RCS_HOOK_TOKEN>`. Tap **Test**. The relay answers 200 `ignored` because a test is not a Messages notification. Then tap **Save**.
5. Set forwarding mode to **Allowlist** and select only **Messages** (`com.google.android.apps.messaging`). Turn on "ignore group summaries" and "ignore ongoing" if the app offers them.
6. Battery: **Settings → Apps → Notification Relay Webhook → Battery → Unrestricted**.
7. Keep Google Messages notifications **on**, including for conversations. Don't mute the senders whose texts must reach Alexandra. A muted conversation posts no notification.
8. Keep RCS on. Don't change SMSGate.
9. For saved contacts, add their names to `RCS_NAME_MAP` on prism, exactly as Google Messages shows them. Otherwise they arrive as `unknown sender <name>` and can't be replied to.

## Turo notifications (#1104)

The same notification-relay app posts Turo app notifications (package `com.relayrides.android.relayrides`) to the RCS route on the same path and token. The RCS route sends them to the Turo relay instead of ignoring them. Google Messages handling is unchanged, and other packages still get `200 ignored: package`.

**Set `RCS_HOOK_TOKEN` before enabling Turo.** The Turo branch rides the RCS route, which refuses every post (503) while the token is unset or blank and answers 401 to a missing or wrong token. There is no unauthenticated path.

Pipeline (stdlib, no model):
1. **Normalize**, preferring `big_text` over `text`, to `guest` (title, with any ` • vehicle` suffix split off), `text`, `sub_text`, `category`, `posted_at` (from `postedAt`, UTC ISO), `key` and `received_at` (relay time, UTC ISO). Also parsed: `reservation_id` (a 7–9 digit trip number; a labeled "Trip/Reservation/Booking #" first, then a bare number in title, sub_text, text), `vehicle` and `plate` (24EWUH/25EWUH map to "Toyota Corolla <plate>", Rivian/R1S to "Rivian R1S", Corolla to "Toyota Corolla", plus common makes). Unknown fields are `null`.
2. **Dedupe** within `TURO_DEDUPE_WINDOW_S` (default 24 h) when any of these repeat: the same `key` + text, the same guest + text, or a shorter prefix of a text seen from the same guest within 10 min (truncated re-posts). This covers re-posts on update and the app's 3 retries. State is in `turo_state.json`, so it survives restarts.
3. **Inbox:** one JSON line per event in `~/.local/state/bland-relay/turo_inbox.jsonl` (`TURO_INBOX_PATH`), mode 0600. Phone numbers and emails in `text`/`sub_text` are replaced with `[phone]`/`[email]`. Lines older than `TURO_RETENTION_DAYS` (default 30) are pruned at startup and daily. Suppressed events are written with `suppressed: true`. Full text goes only to the Helm/Alexandra webhooks. The journal never has bodies, guest names, trip numbers or full numbers.
4. **Suppress** events matching `TURO_SUPPRESS_RE` (default `safe ?wheels`, case-insensitive; matched against title, sub_text, vehicle and text). These are Alex/SafeWheels vehicles, not our fleet (our fleet is the Rivian R1S, Corolla 24EWUH, Corolla 25EWUH, and Tesla Model 3 2022). Suppressed events are written to the inbox only: no SMS, no webhook, including urgent owner alerts. **Set the Alex vehicle names here**, e.g. `safe ?wheels|sentra|traverse|pathfinder|telluride`.

   `TURO_VEHICLE_ALLOW_RE` is optional (#1108). Unset, blank, or an invalid regex leaves this step unchanged. An invalid pattern logs a warning and does not filter. When it is set, an event whose parsed `vehicle` does not match (case-insensitive) is suppressed the same way. An event with no parsed vehicle passes this check, because Helm matches the guest against booked trips. `TURO_SUPPRESS_RE` still applies on top: a name that matches both is still suppressed. The match uses the parsed vehicle (`Rivian R1S`, `Toyota Corolla`, `Tesla Model 3`, `Kia Telluride`, …), not a raw `<Make> <Model> <Year>` string, so a trailing model year is not part of the haystack. Model-only names the parser does not recognize (Sentra, Traverse, Pathfinder, Santa Fe, Telluride alone) are not vehicle text for this filter; keep those on `TURO_SUPPRESS_RE`. Example: `(rivian|corolla|tesla)` keeps a Tesla Model 3 2022 lockout and its urgent owner alert, and suppresses a Kia Telluride only while the allowlist is set.
5. **Urgent filter**, in priority order: `safety`, `accident_damage`, `lockout`, `no_start`, `charging`. Defaults live in `TURO_URGENT_DEFAULTS` in code. Override a class with `TURO_URGENT_RE_<CLASS>` (e.g. `TURO_URGENT_RE_NO_START`). A sentence that matches the negative list `TURO_URGENT_NEG` (default: "before return", "already there", "pre-existing", "instructions", "no smoking", "thanks for the help", "no damage", …) is skipped. So "the scratch was already there" is not urgent, but "already there. I just got rear-ended" is. `matched_term` is logged in the inbox for tuning. An invalid override regex logs a warning and the default is used. Note: a systemd EnvironmentFile may eat backslashes, so check `journalctl` after changing a pattern.
6. **owner_alert** (urgent only): a 904 SMS `URGENT Turo [<class>]: <guest>: <first 140 chars>` to `TURO_OWNER_ALERT_TO`, which is the only recipient. No number is in code. The value must be strict E.164 (`+1NXXNXXXXXX`); unset or invalid disables owner alerts with a log warning that never shows the number. Logs show only the last 4 digits. The alert skips only the 72 h inbound window, and only for that exact env number. It still respects `SMS_SEND_DISABLED`, STOP/opt-out, `TURO_OWNER_ALERT_DISABLED`, an alert dedupe (same guest + text), `TURO_ALERT_GUEST_MAX` (1) per `TURO_ALERT_GUEST_WINDOW_S` (600 s), `TURO_ALERT_HOUR_MAX` (6/h overall), and the relay-wide `RECIPIENT_MAX`/`GLOBAL_MAX`. A gateway failure goes to `alert_failure` (journal + #701). **Non-urgent Turo traffic never texts anyone.**
7. **Handoff**, each in its own background task with a `TURO_WEBHOOK_TIMEOUT_S` (default 5 s) timeout and 2 tries. A failure is logged and sent to #701, and never blocks the owner alert or the 200 to the phone:
   - **Helm:** every non-suppressed event is POSTed as JSON to `HELM_TURO_WEBHOOK_URL`. Auth is `Authorization: Bearer <HELM_TURO_WEBHOOK_KEY>`, the same scheme as `ALEXANDRA_ALERT_AUTH` on the live Alexandra routine webhook. A key that already contains a space (e.g. `Bearer …`) is sent as is. `HELM_TURO_WEBHOOK_HEADER` (e.g. `X-Webhook-Key`) sends the bare key in that header instead.
   - **Alexandra:** urgent events only, through the existing `forward()` to `ALEXANDRA_ALERT_URL`/`ALEXANDRA_ALERT_AUTH`, with `"kind": "turo_urgent"`. Turn it off with `TURO_ALEX_PUSH_DISABLED=1`.
   - With no URL set, the event is only in the JSONL. Helm reads it in its daily pass.

Kill switch: `TURO_HOOK_DISABLED=1` answers Turo posts with `200 ignored: turo_disabled` (200, so the app doesn't retry), with no inbox write, SMS or webhook. Messages keep working. `RCS_HOOK_DISABLED=1` still stops the whole route.

Helm body (exactly these fields, `TURO_HELM_FIELDS`):

```json
{"kind": "turo_event", "schema": "panamerica.turo.event.v1", "event_id": "16 hex",
 "guest": "Sam", "reservation_id": "12345678", "vehicle": "Rivian R1S", "plate": null,
 "text": "full message (big_text preferred)", "received_at": "2026-10-09T20:00:02Z",
 "urgent": true, "urgent_class": "lockout"}
```

Alexandra body (urgent only, `TURO_ALEX_FIELDS`):

```json
{"kind": "turo_urgent", "guest": "Sam", "reservation_id": "12345678", "vehicle": "Rivian R1S",
 "text": "full message", "urgent_class": "lockout", "received_at": "2026-10-09T20:00:02Z"}
```

The local JSONL inbox row keeps the extra context (`sub_text`, `category`, `posted_at`, `key`, `matched_term`, `line`, `suppressed`).

Helm matches on `reservation_id` first, then `guest` + `vehicle`. `event_id` is stable for a key + text, so it can serve as an idempotency key.

Setup on prism (after deploy):

```bash
# RCS_HOOK_TOKEN must already be set (see above); without it the route refuses Turo posts.
printf %s "+1NXXNXXXXXX" | ~/bin/bland-relay-setkey TURO_OWNER_ALERT_TO --confirm-last4 XXXX  # optional last-4 guard; or TURO_OWNER_ALERT_CONFIRM_LAST4
printf %s "$HELM_URL" | ~/bin/bland-relay-setkey HELM_TURO_WEBHOOK_URL    # from Helm's "Turo inbound" routine
printf %s "$HELM_KEY" | ~/bin/bland-relay-setkey HELM_TURO_WEBHOOK_KEY
printf %s 'safe ?wheels|<alex vehicle names>' | ~/bin/bland-relay-setkey TURO_SUPPRESS_RE
# optional fleet allowlist. Leave it unset to keep current behavior. Do not set it during review.
# printf %s '(rivian|corolla|tesla)' | ~/bin/bland-relay-setkey TURO_VEHICLE_ALLOW_RE
# optional: TURO_HOOK_DISABLED TURO_OWNER_ALERT_DISABLED TURO_ALEX_PUSH_DISABLED HELM_TURO_WEBHOOK_HEADER
#   TURO_URGENT_RE_{LOCKOUT,NO_START,ACCIDENT_DAMAGE,CHARGING,SAFETY} TURO_URGENT_NEG TURO_DEDUPE_WINDOW_S
#   TURO_ALERT_GUEST_WINDOW_S TURO_ALERT_GUEST_MAX TURO_ALERT_HOUR_MAX TURO_INBOX_PATH TURO_RETENTION_DAYS TURO_WEBHOOK_TIMEOUT_S
```

Phone: in Notification Relay Webhook → Allowlist → select apps, also check **Turo** (keep Messages checked). See the #718 design comment §3 for the full checklist. The payload format is assumed until the first real notification. Check the app's Logs tab and tune the parser and regexes from `turo_inbox.jsonl`.

## Turo guest-message email via Gmail push (#1142)

This is a second real-time source for the same Turo pipeline. When the 904 phone or the Turo app is offline, guest messages still reach Helm in about a minute, from the Turo email copy in the panamerica Gmail. There is no polling and no model.

```
Gmail users.watch ──► Pub/Sub topic ──► subscription ◄══ StreamingPull (outbound gRPC) ══ gmail_turo_watch.py run (Pi)
                                                                                              │ history.list since historyId
                                                                                              │ messages.get → turo_email.py
                                                                                              ▼
                                         bland-relay loopback listener 127.0.0.1:$TURO_EMAIL_PORT /turo/email (bearer)
                                                                                              │ turo_email_event() → turo_event()
                                                                                              ▼
                                         turo_ingest(): dedupe · suppress · inbox · owner alert · Alexandra · Helm
```

- **Transport: streaming pull, not push.** The subscriber holds one long-lived StreamingPull stream, and Pub/Sub sends messages down it. There is no public endpoint, OIDC verification or new Funnel path. Its only credential is a service-account key that can subscribe to one subscription. A Funnel path with Pub/Sub push plus OIDC was rejected because it adds a public route.
- **Filter (`turo_email.py`, stdlib).** An email counts only when all of these hold:
  - The From address is on a turo.com domain.
  - The **topmost** `Authentication-Results` header (the first in Gmail's header order, which Gmail's MX prepends) starts with `mx.google.com;` and has `dkim=pass` with `header.i`/`header.d` on turo.com (or a subdomain) and `dmarc=pass header.from=turo.com`. Lower A-R headers are ignored because a sender can write them. A missing or non-Gmail top A-R is rejected. `TURO_EMAIL_REQUIRE_DKIM=0` / `TURO_EMAIL_REQUIRE_DMARC=0` relax this for debugging only.
  - The subject is `[(<owner>'s vehicle) - ]<Guest> has sent you a message about your <Vehicle>`.
  - `internalDate` is present (a missing date is rejected) and younger than `TURO_EMAIL_MAX_AGE_S` (12 h).

  It extracts guest, vehicle, reservation id and the message body; the body is the text between the header line and `Reply <url>`. Bookings, invoices, trip changes and non-Turo mail are ignored.
- **Same code path.** The relay builds the event with `turo_event()`, the normalizer it also uses for notifications. `src` is `gmail`, `key` is `gmail|<trip>`, `guest` is the first name, and `vehicle` and `reservation_id` come from the email. Then `turo_ingest()` runs, which is also the tail of `turo_handle()`. Suppress (`TURO_SUPPRESS_RE` also sees the subject, so a co-host owner prefix suppresses), the optional `TURO_VEHICLE_ALLOW_RE` (parsed vehicle only), urgent classes, owner alert, Alexandra and Helm are all shared, and `turo_state.json` keeps one writer.
- **Dedupe.**
  - The two Gmail copies collapse on the existing key + text and guest + text rules.
  - Across sources, `xseen` in `turo_state.json` matches guest first name, vehicle, trip and body text. The relay side reads these from `About <Vehicle> from <Guest>:\n<body>` and the trip from the notification key. A shorter body also matches when it is a prefix of the other and ends with `…`/`...` or is at least 60% of its length (see the body rule below).
  - Body rule: identical after normalization, or the shorter is a prefix of the longer **and** either ends with a truncation marker (`…` or `...`) or is at least 60% of its length (and at least 8 chars). Only the first 400 chars are stored, so an unmarked body longer than that must match exactly.
  - A cross-source match **consumes** the earlier record, so one phone notification can suppress at most one email (and vice versa).
  - Matches use a sliding `TURO_XDEDUPE_WINDOW_S` window (default `TURO_DEDUPE_WINDOW_S`, 24 h).
  - An xseen entry never matches its own source, so relay-only behavior is unchanged.
  - The later copy gets `duplicate`, and the log line `turo cross-source duplicate ... src=<later> first_src=<earlier> age_s=<n>` shows which source won.
- **Acks and poison messages.** A Pub/Sub message is acked once every new Gmail message in the batch is handled. Handled means handed to the relay with a 2xx (accepted, duplicate, suppressed or ignored), filtered out, or skipped. `historyId` only advances then; otherwise the Pub/Sub message is nacked and redelivered.
  - Attempts are counted per Gmail message id in `state.json`.
  - A relay **4xx** skips that message at once; retrying cannot help. **Except 401/403:** a token mismatch would hit every email, so it is retried and never skipped, with one #701 alert (`relay auth mismatch: check TURO_EMAIL_TOKEN`, at most once a day, re-armed after the next successful handoff). A missing `TURO_EMAIL_TOKEN` is treated the same way.
  - A relay **5xx, timeout, or reset/abort/`RemoteDisconnected` after the request was sent** (what a message that crashes the relay looks like), or a per-message Gmail fetch error, is retried and counted. At most one attempt per message is counted per `TURO_EMAIL_ATTEMPT_SPACING_S` (default 60 s; stored as `last_attempt_at`), so a burst of notifications during a short outage doesn't burn the budget. After `TURO_EMAIL_MAX_ATTEMPTS` (default 5) counted attempts the message is skipped.
  - **Relay down** (connection refused: relay stopped or restarting, `TURO_EMAIL_PORT` unset or wrong, bind failure) is counted separately, with the same spacing, against `TURO_EMAIL_MAX_DOWN_ATTEMPTS` (default 30, about 30 min). So a short restart never skips mail, but a message the relay can't take is eventually skipped with the usual skip alert, and `historyId` advances.
  - **Stall alerts.** The watcher persists `relay_down_since` (first refused handoff) and `gmail_stall_since` (Gmail 401/403/429 on `messages.get`, a failed `history.list`, a token-refresh failure, or Gmail unreachable). Once a stall lasts `TURO_EMAIL_DOWN_ALERT_S` (default 600 s) it posts one #701 alert a day: `Turo Gmail watcher: relay unreachable` or `Turo Gmail watcher: Gmail access failing`, with the cause. The next successful relay handoff, or the next complete Gmail pass, clears the stall and re-arms the alert. Gmail auth/quota errors are never counted toward a skip (they hit every message); they only alert.
  - Note: `TURO_EMAIL_MAX_AGE_S` (12 h) still applies when a message is finally handed off, so an outage longer than that drops older mail as `too_old` (journal only); those messages reached Helm via the phone path or not at all.
  - A skipped message is recorded under `skipped`, gets one ops alert through the relay's `alert_failure()` (journal ALERT + #701, 15 min dedupe), and stops pinning `historyId`.
  - `attempts`/`skipped` are pruned after 7 days.
  - The Pub/Sub dead-letter topic (below) is the backstop for anything else that keeps failing.
  - On start there is one catch-up from the stored historyId. If that historyId has expired (404), there is a one-time recovery by query (`newer_than:1d`).
- **Watch renewal.** `gmail-turo-watch-renew.timer` runs daily (watches expire after 7 days) and calls `gmail_turo_watch.py renew`, which is one `users.watch` call. By default it has no label filter, because co-host guest mail skips INBOX. `GMAIL_WATCH_LABEL_IDS` narrows it. If the stored watch had already **lapsed** (its expiration is in the past), renew then runs one catch-up sync from the stored historyId, with the same expired-historyId recovery. Renew and the run service share a file lock on `state.json`.
- **Kill switches.**
  - Relay side: `TURO_EMAIL_DISABLED=1`, or the existing `TURO_HOOK_DISABLED=1`, turns the email path off (the relay answers `ignored`).
  - Watcher side: `TURO_EMAIL_DISABLED=1` in `~/.config/gmail-turo-watch/env` makes `gmail_turo_watch.py run` idle. It opens no stream, reads no Gmail and acks nothing, so notifications stay queued in the subscription (up to its retention). Remove it and restart to resume; the catch-up then picks up from the stored historyId.
  - `TURO_EMAIL_SHADOW=1` logs the verdict (`new` / `duplicate` / `cross_duplicate`, suppressed, urgent) and writes nothing to relay state, the inbox or the webhooks. **The watcher still advances `historyId` and marks those Gmail messages processed**, so going live does not replay shadow-period mail. Messages that arrived during shadow reach Helm only via the phone path.
  - Leaving `TURO_EMAIL_PORT` unset turns the listener off.
- **Listener.** It binds 127.0.0.1 only and is never behind Serve or Funnel. It needs `TURO_EMAIL_TOKEN` and refuses requests that carry proxy headers (`Tailscale-Funnel-Request`, `X-Forwarded-For`).
- **Alerts.** Watcher alerts go through the relay's `alert_failure()` with their own wording (`Gmail message id`, `Attempts`, source `gmail-turo-watch.service`). They share `alert_state.json` and its 15 min cooldown with the relay, and both processes update that file under a `flock` (`alert_state.lock`).
- **Key file.** `run` refuses to start (exit 2, `refusing to start: service-account key mode is 0o644; run chmod 600 on it`) unless `GOOGLE_APPLICATION_CREDENTIALS` is a regular file with no group/other bits (0600 or 0400).
- **Known and accepted limits.**
  - Cross-source dedupe relies on the Turo app's `About <Vehicle> from <Guest>:` notification format. If Turo changes it, phone and email copies of a message both reach Helm (no loss, just doubles) until the regex is updated.
  - Stacked/summary notifications (several messages in one notification) don't cross-dedupe against the individual emails.

Files: `gmail_turo_watch.py`, `turo_email.py`, `gmail-turo-watch.service`, `gmail-turo-watch-renew.service`, `gmail-turo-watch-renew.timer`, `gmail-turo-watch.env.example`.

GCP (human, once, in the GCP project that owns the Gmail OAuth client; Gmail requires the topic there):

1. Enable the Pub/Sub API.
2. Create topic `gmail-turo`. Grant `gmail-api-push@system.gserviceaccount.com` **Publisher** on it.
3. Create topic `gmail-turo-dlq` and a pull subscription `gmail-turo-dlq-hold` on it (retention 7 d) so dead letters can be inspected.
4. Create the pull subscription `gmail-turo-pi` on `gmail-turo`: ack deadline 60 s, retention 1 d, retry policy 10 s min / 300 s max backoff, **dead-letter topic `gmail-turo-dlq`, max delivery attempts 10**.
5. Dead-lettering needs the Pub/Sub service agent `service-<PROJECT_NUMBER>@gcp-sa-pubsub.iam.gserviceaccount.com` to have **Publisher on `gmail-turo-dlq`** and **Subscriber on `gmail-turo-pi`**. The console offers to add both when you enable dead lettering.
6. Create service account `gmail-turo-sub` with **Subscriber on `gmail-turo-pi` only** (no project role). Create one JSON key and copy it to `~/.config/gmail-turo-watch/subscriber-sa.json` on prism, `chmod 600`.

```bash
# gcloud equivalent (PROJECT = the OAuth client's project id, PN = its project number)
gcloud services enable pubsub.googleapis.com --project PROJECT
gcloud pubsub topics create gmail-turo gmail-turo-dlq --project PROJECT
gcloud pubsub topics add-iam-policy-binding gmail-turo --project PROJECT \
  --member serviceAccount:gmail-api-push@system.gserviceaccount.com --role roles/pubsub.publisher
gcloud pubsub subscriptions create gmail-turo-dlq-hold --topic gmail-turo-dlq --message-retention-duration 7d --project PROJECT
gcloud pubsub subscriptions create gmail-turo-pi --topic gmail-turo --project PROJECT --ack-deadline 60 \
  --message-retention-duration 1d --min-retry-delay 10s --max-retry-delay 300s \
  --dead-letter-topic gmail-turo-dlq --max-delivery-attempts 10
gcloud pubsub topics add-iam-policy-binding gmail-turo-dlq --project PROJECT \
  --member serviceAccount:service-PN@gcp-sa-pubsub.iam.gserviceaccount.com --role roles/pubsub.publisher
gcloud pubsub subscriptions add-iam-policy-binding gmail-turo-pi --project PROJECT \
  --member serviceAccount:service-PN@gcp-sa-pubsub.iam.gserviceaccount.com --role roles/pubsub.subscriber
gcloud iam service-accounts create gmail-turo-sub --project PROJECT
gcloud pubsub subscriptions add-iam-policy-binding gmail-turo-pi --project PROJECT \
  --member serviceAccount:gmail-turo-sub@PROJECT.iam.gserviceaccount.com --role roles/pubsub.subscriber
gcloud iam service-accounts keys create subscriber-sa.json --iam-account gmail-turo-sub@PROJECT.iam.gserviceaccount.com
```

No OAuth re-consent is needed: the existing Gmail token already has `gmail.readonly`, which covers `users.watch`, history and message reads.

Setup on prism (after the GCP steps in #1142):

```bash
cp deploy/bland-relay/bland_relay.py deploy/bland-relay/relay_*.py \
  deploy/bland-relay/caller_context.py deploy/bland-relay/meeting_check.py deploy/bland-relay/turo_email.py \
  deploy/bland-relay/gmail_turo_watch.py ~/bin/
openssl rand -hex 32 | ~/bin/bland-relay-setkey TURO_EMAIL_TOKEN      # then copy the same value into the watcher env
printf %s 8797 | ~/bin/bland-relay-setkey TURO_EMAIL_PORT
printf %s 1 | ~/bin/bland-relay-setkey TURO_EMAIL_SHADOW              # first 48 h
systemctl --user restart bland-relay.service                          # log: "turo email listener on 127.0.0.1:8797"
python3 -m venv ~/.venvs/gmail-turo-watch && ~/.venvs/gmail-turo-watch/bin/pip install 'google-cloud-pubsub>=2.20,<3'
install -d -m 700 ~/.config/gmail-turo-watch   # env from gmail-turo-watch.env.example (0600) + subscriber SA key (chmod 600; run refuses anything looser)
cp deploy/bland-relay/gmail-turo-watch{.service,-renew.service,-renew.timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start gmail-turo-watch-renew.service && journalctl --user -u gmail-turo-watch-renew -n 5   # "watch ok expires=…"
systemctl --user enable --now gmail-turo-watch.service gmail-turo-watch-renew.timer
```

## First contact (#1100)

The send route only reaches numbers that texted 904 in the last 72 hours. To text a number that never texted 904 (a GVG lead, Turo renter or vendor), an agent first adds it to the first-contact allowlist. Sending is unchanged: once a number is active, use `send-904` (or `POST /<SEND_PATH>`) as usual.

**Approval is off by default** (`FC_REQUIRE_APPROVAL=0`, Chris's decision 2026-10-09): Alexandra and the other agents may text new numbers without a per-number yes from Chris. Every other guardrail below still applies. Set `FC_REQUIRE_APPROVAL=1` to restore the per-number approval gate from #1101 (rollback: `printf 1 | ~/bin/bland-relay-setkey FC_REQUIRE_APPROVAL`). Gated mode keeps the #1102 (a) daily-cap rule, binds chat approval to one seat (#1102 b), and ignores RCS notification titles for approvals (#1102 c).

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
   - **SMS (preferred):** Chris texts `YES 123456` to 904 from a number in `FC_APPROVER_NUMBERS`. The SMSGate (signed) or forwarder inbound route matches it. An RCS notification approves only when the sender is an explicit `number` field (`rcs-number`) or an `RCS_NAME_MAP` entry (`rcs-name_map`). A notification title never approves, even when the title is the approver's number. That path is `rcs-title` and is audited as `deny` (#1102 c).
   - **Chat (off unless configured):** only the Grok seat holds `FC_APPROVE_TOKEN`. No other seat or agent environment has it. The relay stores only `FC_APPROVE_TOKEN_SHA256`. Chat approval stays off while that hash is unset (SMS approvals only). When the hash is set, the relay logs a warning at startup. The token is bound to one human seat, `FC_CHAT_APPROVER`, which must be in `FC_APPROVERS` and must not be an agent seat. A token with no binding is 403 `chat_approval_unbound`. A request whose `approver` is any other name is 403 `approver_not_allowed` (#1102 b).
     ```bash
     printf '%s\n' "$FC_APPROVE_TOKEN" | send-904-fc approve --to "+19045550123" --code 123456 \
       --approver chris --text "yes 123456" --ref "<chat message id/link>" --token-stdin
     ```
   The approver must also not be the requester. The text must start with yes/approve/ok and contain the code. `approval_ref` is required. `SEND_TOKEN` can never approve, and an approver token equal to `SEND_TOKEN` is refused.
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

- `~/.local/state/bland-relay/first_contact_audit.jsonl` (0600, append-only; `FC_AUDIT_PATH` overrides): `add` (approval off, `approval_required: false`), `request`, `approve`, `deny`, `revoke`, `convert`, `opt_out`, `send`, `send_refused`, `send_state`, `send_failed`. Full E.164 numbers and the approval text, never message bodies or secrets. `send_state` records the gateway state, `partsCount`, and `reason` for a known `gateway_id` (#1140). `send_failed` means the failure alert was handed to Alexandra.
- `~/.local/state/bland-relay/crm_outbox.jsonl` (0600; `CRM_OUTBOX_PATH` overrides, `CRM_INGEST_MODE=off` disables): one record per send through the relay (first contact and replies), schema `panamerica.crm.interaction.v1` = the #1049 `interaction` columns (`channel`, `direction`, `provider`, `provider_msg_id`, `from_addr`, `to_addr`, `summary`, `outcome`, `error_code`, `occurred_at`, `logged_by`) plus `metadata` (`purpose`, `purpose_kind`, `placed_by`, `first_contact`, `approval_required`, `approval`; `approval.approved_via` is `auto` for no-approval adds) and `party_hint` (`phone`, `display_name`, `type`, `tags`, `source`) for #1097 auto-create. No `body`. crm-api is not deployed on prism yet (#1097 step 0); `CrmOutboxSink` is the adapter #1097 replaces with a `POST /v1/interactions` sink, draining this file idempotently on `provider_msg_id`.

### Setup on prism (after merge)

```bash
python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(48)), end="")' | ~/bin/bland-relay-setkey FC_PATH
# That is all with approval off (default). The rest is only for FC_REQUIRE_APPROVAL=1:
printf 1 | ~/bin/bland-relay-setkey FC_REQUIRE_APPROVAL
# Approver token: generate it in the Grok seat only. Do not copy it into any other
# seat or agent env, and do not write the token into this env file. Only the hash goes on prism.
printf %s "$FC_APPROVE_TOKEN" | sha256sum | cut -d' ' -f1 | tr -d '\n' | ~/bin/bland-relay-setkey FC_APPROVE_TOKEN_SHA256
printf %s 'chris' | ~/bin/bland-relay-setkey FC_CHAT_APPROVER   # the one seat that token may approve as
printf %s 'chris=+1NXXNXXXXXX' | ~/bin/bland-relay-setkey FC_APPROVER_NUMBERS   # Chris's own cell(s); SMS is the preferred approval path
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

`send-904` and `send-904-fc` post only to loopback. `SEND904_BASE` defaults to `http://127.0.0.1:8799` and is accepted only as `http://127.0.0.1:<port>` or `http://localhost:<port>`. Any other value exits non-zero before a request. The error does not echo the value or the token (#1102 d).

Phone setup, before any live send: install the app, turn Cloud Server on, tap Online, grant SMS, and set battery to Unrestricted. Keep **RCS on** in Google Messages. With RCS off, SMSGate outbound sends stall at Processed (#1023). RCS chats never fire `sms:received`, so they come in through the RCS notification route (see the #1056 section above).

## Caller context (#1096)

`GET /bland/caller-context?pn=<E.164>` is the Bland `dynamic_data` source. It does not call Bland, and it does not read or write the live inbound number. That config change is applied later, from a fresh GET of the current inbound config, by Grok.

**Active backend.** `CRM_API_BASE` plus `CRM_API_TOKEN` turns on the CRM read: `GET /v1/contacts/by-phone?pn=` with `Authorization: Bearer` and `X-CRM-Scope: caller-context`, timeout 250 ms. `CRM_API_BASE` is accepted only as `http://127.0.0.1:<port>` or `http://localhost:<port>` (same rule as `SEND904_BASE`). Any other value is not requested. The value and the token are not logged, and the local index is used. A 200 from an accepted base is rendered from that record. A 404 is an unknown caller (`backend: crm`) and does not consult the index, so leave `CRM_API_BASE` unset until #1097 actually serves that route. A timeout, connection error, or 5xx falls through to the local index. With the CRM env unset, the index is the active backend (`backend: index`). The index file is `CALLER_INDEX_PATH` or `$BLAND_RELAY_STATE_DIR/caller_index.sqlite` or `~/.local/state/bland-relay/caller_index.sqlite` (mode 0600).

#1097 owns production ingest (Bland webhooks, the live poller, SMSGate, Gmail, purpose logging). This tree's ingest helpers and `poll_once` load the same fixtures into the fallback index. `poll_once` issues GET only, stops the tick on HTTP 429, and records a 5xx without raising. The service does not start it. `BlandReadClient` has no POST method and refuses an empty base URL, so it cannot default to the Bland API.

The read token is not the Bland API key. A missing or wrong token is 401. More than `CALLER_CONTEXT_RPM` requests per minute from one IP (default 60) is 429. Any internal error returns 200 and the empty shape, so the greeting is not blocked. `caller_context` is plain English, at most 600 characters, last 3 interactions newest first, times in ET. Plates, street addresses, emails, and card numbers are stripped. A linked Turo booking adds one sentence and no trip details.

`DELETE` with `CALLER_FORGET_TOKEN` removes that number from the fallback index only. The read token cannot delete. Install the forget token only on the Grok seat. CRM erasure stays with #1097. Rows older than 180 days are purged on read.

Response:

```json
{"known": true, "caller_context": "Today 10:20 AM: we called you from this line; it went to voicemail (purpose: not recorded)", "last_contact_at": "2026-10-09 10:20 AM ET", "open_followups": [], "caller_type": "unknown", "backend": "index"}
```

Unknown number: `{"known": false, "caller_context": "No previous contact on file.", ...}`.

`dynamic_data` to add later (not in this PR). `response_data[].context` is the sentence Bland shows the model next to the variable:

```json
"dynamic_data": [
  {
    "url": "https://<funnel-host>/bland/caller-context",
    "method": "GET",
    "headers": {"Authorization": "Bearer <CALLER_CONTEXT_TOKEN>"},
    "query": {"pn": "{{phone_number}}"},
    "cache": true,
    "timeout": 1500,
    "response_data": [
      {
        "name": "caller_context",
        "data": "$.caller_context",
        "context": "Earlier contact with this caller, from our own records."
      }
    ]
  }
]
```

Prompt block to append immediately after the `### PANAMERICA KB vN ###` block. It must not repeat the live RETURNED CALLS or HONESTY paragraphs:

```text
CALLER HISTORY: {{caller_context}}. If it shows earlier contact, greet them as a returning caller and pick up the open item (for example, 'I see we tried to reach you this morning'). Never deny that we called. A matching number is not proof of identity: discuss booking specifics only after the caller gives a name and trip number that match. If there's no history, continue normally.
```

Rollback is `"dynamic_data": null` on the inbound update, preserving every other field.

Dry run, standing in for Bland `POST /v1/dynamic_data/test`. No live call. Loopback only:

```bash
curl -sS -H "Authorization: Bearer $CALLER_CONTEXT_TOKEN" \
  "http://127.0.0.1:8799/bland/caller-context?pn=%2B12075550100"
```

Tokens:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' | ~/bin/bland-relay-setkey CALLER_CONTEXT_TOKEN
python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' | ~/bin/bland-relay-setkey CALLER_FORGET_TOKEN
```

`bland-relay-setkey` restarts the unit. Do not run it from a review. Copy the whole set in Deploy, including `caller_context.py`, `meeting_check.py` and every `relay_*.py` part next to `bland_relay.py`.

Until #1231 or #1097 writes the index, a deployed `GET /bland/caller-context` returns `known: false` for every caller. That is the expected result of this slice, not a failed smoke. `smoke.py` is the public check. It stays on the operator machine and is not copied to `~/bin`.

Startup logs one line, with no token, phone number, or path: `caller-context: token=set|unset backend=crm|index index_rows=<n>`. `backend=crm` only when `CRM_API_BASE` is loopback and `CRM_API_TOKEN` is set. Leave `CRM_API_BASE` unset until #1097 serves the route, so the line should say `backend=index`.

## Meeting check (#1230)

`POST /bland/meeting-check` is a mid-call Bland tool. Alexandra needs one fact: does this caller have a meeting with Chris at the time they state. The caller is unknown until they speak, so this is a tool with the caller's stated name and time as input, not `dynamic_data`. `meeting_check.py` is a standalone stdlib module imported in `relay_config.py` next to `caller_context`. The route is one early branch in `H.do_POST`. Nothing here calls Bland, reads the Bland API key, or writes to a calendar.

**Status: built and tested against a fake calendar only.** No calendar credential exists for this relay. With the `MEETING_GCAL_*` keys unset there is no Google client, no snapshot thread and no network call, and every check answers `{"match": false}`. Nothing is wired into Bland until Chris approves the read-only calendar consent (gate G2) and the Bland config change (G4).

Request (Bland tool body, at most 2 KB; a larger or bad body gets `{"match": false}`):

```json
{"call_id": "{{call_id}}", "phone_number": "{{phone_number}}",
 "caller_name": "{{input.caller_name}}", "meeting_date": "{{input.meeting_date}}",
 "meeting_time": "{{input.meeting_time}}", "meeting_tz": "{{input.meeting_tz}}"}
```

`meeting_date` is `YYYY-MM-DD`. `meeting_time` is `HH:MM AM/PM`, `3 PM`, or 24 h. `meeting_tz` is ET, CT, MT or PT (also Eastern, Central, Mountain, Pacific, with or without "Standard"). A blank or unfilled tz means ET. Any other tz text is a no-match.

Response, always 200 after auth, and exactly one of these key sets (a final guard enforces it):

```json
{"match": true, "meeting_time": "Monday, October 19 at 3:00 PM ET"}
{"match": false}
```

| Case | Status |
|---|---|
| More than 60 requests a minute from one IP (separate bucket from caller-context) | 429 |
| Missing or wrong token, or `MEETING_CHECK_TOKEN` unset | 401 |
| Kill switch, bad or oversized body, missing field, calendar failure or timeout, `invalid_grant`, anti-guess cap, any exception | 200 `{"match": false}` |

Matching (all in memory, nothing returned but the two keys above):

- Candidate events start within 15 minutes of the stated instant. Cancelled events, all-day events, events the owner declined, and events with no guest other than the owner are skipped.
- A participant name is the `displayName` plus the local part of each non-owner attendee, organizer and creator email, split on `. _ + -` and digits (`first.last`, `flast`). The last name must equal the participant's last name (one edit allowed when both are 6+ letters). The first name must be close (up to two edits, or the same rough sound, when 4+ letters; the same initial when the email is initial plus last name).
- An event whose title contains the stated last name as a whole word also matches. Owner name words and common filler words ("meeting", "call", ...) never count.
- The title alone, the time alone, the phone number alone, a first name alone and a last name alone never match. The phone number is not a factor at all.
- `meeting_time` is the matched event's start converted to ET from its `dateTime` offset. `timeZone` on the event is never used.
- An event with no other guest cannot match, even when the title names the caller. Add a guest to any QA event.

Anti-guess caps return `{"match": false}` without a calendar read: 3 checks per `call_id`, 6 per phone number per hour, 40 per hour overall. A denied check is not counted again. Response shape and path are the same for a miss, a cap and an error.

Calendar read (optional, off until G2): `GcalClient` refreshes an access token at `https://oauth2.googleapis.com/token` (cached until 60 s before expiry) and calls `events.list` with `singleEvents=true`, `orderBy=startTime`, `maxResults=250` and this fixed mask, which never asks for `description`, `location`, conference data or links:

`nextPageToken,items(id,status,summary,start,end,creator/email,organizer/email,attendees(email,displayName,self,responseStatus))`

A snapshot thread loads now-1 h to now+45 d every `MEETING_SYNC_S` seconds and keeps only derived tuples in memory (start, name tokens, title words). The request path reads the snapshot and makes no network call while it is under 600 s old and non-empty. Otherwise it makes one live fetch with a 500 ms timeout, then answers no-match on failure. `invalid_grant` or a 401/403 logs `meeting-check: calendar auth failing` and posts one #701 alert a day through the existing alert sink. The check keeps answering `{"match": false}`.

Attempt record for #1231: `meeting_attempts.sqlite` in the relay state dir (file 0600, dir 0700). One row per check with `call_id`, `ts`, the stated name clipped to 60 characters, the stated start (ISO, UTC) and `result`. Rows older than 48 h are purged on each write. The logger never reads it.

Logging: `meeting-check: call_id=<id> result=true|false ms=<n>`. No name, number, stated time, email or token. Errors log the exception class. Startup logs `meeting-check: token=set|unset calendar=set|unset disabled=0|1`.

Env keys (names only; values go in `~/.config/bland-relay/env` through `bland-relay-setkey`, never in git or chat):

| Key | Purpose |
|---|---|
| `MEETING_CHECK_TOKEN` | Route token. Separate from `CALLER_CONTEXT_TOKEN`. Unset means every request is 401. |
| `MEETING_CHECK_DISABLED` | `1` answers `{"match": false}` and skips the calendar and the attempt record. Kill switch. |
| `MEETING_GCAL_CLIENT_ID`, `MEETING_GCAL_CLIENT_SECRET`, `MEETING_GCAL_REFRESH_TOKEN`, `MEETING_GCAL_CALENDAR_ID` | Read-only calendar credential. All four must be set or the calendar stays off. Not created by this change. |
| `MEETING_SELF_EMAILS` | Optional. Comma-separated owner addresses, so the owner is never read as a guest. The calendar id counts when it is an address. |
| `MEETING_SYNC_S` | Optional snapshot interval, default 120, clamped to 30 to 3600. |

`MEETING_CHECK_TOKEN`, the four `MEETING_GCAL_*` keys and `MEETING_SELF_EMAILS` are in `SECRET_ENV_KEYS`, so `_scrub()` redacts them.

Future wiring (after G2, not part of this change; each `setkey` restarts the unit):

1. Chris grants consent once, on the Panamerica account, in a Google Cloud OAuth client whose publishing status is **In production**, not "Testing" (a Testing refresh token expires after 7 days). The only scope is `https://www.googleapis.com/auth/calendar.events.readonly`, and the consent screen must show only "View events on all your calendars". Never request a write scope.
2. Mint one refresh token for that scope. Set the four `MEETING_GCAL_*` keys on prism with `bland-relay-setkey`. The relay env on prism is the only place they live.
3. `python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' | ~/bin/bland-relay-setkey MEETING_CHECK_TOKEN`, then check the journal for `meeting-check: token=set calendar=set disabled=0`.
4. Run the live checks from #1230 (Grok seat). Applying the Bland tool and prompt block is a separate Bland config change that needs Chris's approval (G4). It is not part of this repo change.

Rotate `MEETING_CHECK_TOKEN`: mint a new value with the same `setkey` command, then update the Bland tool header to match in the same change window. The old value stops working at the restart. Rotate the calendar credential by revoking the app's access on the Panamerica account, minting a new refresh token, and setting `MEETING_GCAL_REFRESH_TOKEN` again.

Rollback: `printf 1 | ~/bin/bland-relay-setkey MEETING_CHECK_DISABLED` (endpoint answers `{"match": false}`). Bland side: restore the saved backup or POST `"tools": null` preserving every other field. Relay side: restore `~/bin/*.bak-pre1230` and delete `~/bin/meeting_check.py` (the Deploy rollback does both). Google side: revoke the app's access on the Panamerica account and delete the `MEETING_GCAL_*` keys.

## Deploy

Not done by this PR. Do not copy these files onto prism, do not restart the unit, and do not run `install_remote.sh`, from a review. Ops runs this checklist after merge, off-peak, with no call in progress. `bland-relay.py` is the unit entry (`chmod 755`). `bland_relay.py` loads every `relay_*.py` part from the same directory (`chmod 644`), and that load imports `caller_context.py`, `meeting_check.py` and `turo_email.py`. A partial copy crash-loops the unit.

`smoke.py` is a local read-only tool. It is not copied.

Deploy files: `bland-relay.py`, `bland_relay.py`, `relay_config.py`, `relay_audit.py`, `relay_sms.py`, `relay_fc.py`, `relay_send.py`, `relay_rcs.py`, `relay_turo.py`, `relay_http.py`, `caller_context.py`, `meeting_check.py`, `turo_email.py`, `bland-relay-setkey`, `send-904`, `send-904-fc`.

1. Back up the files already on prism. A name that is not on prism yet (`meeting_check.py` on the first #1230 deploy) is skipped. `bak-pre1229` was the #1229 stamp; this slice uses `bak-pre1230`.

```bash
stamp=bak-pre1230
for f in bland-relay.py bland_relay.py relay_config.py relay_audit.py relay_sms.py \
  relay_fc.py relay_send.py relay_rcs.py relay_turo.py relay_http.py \
  caller_context.py meeting_check.py turo_email.py bland-relay-setkey send-904 send-904-fc; do
  if [ -f ~/bin/$f ]; then cp ~/bin/$f ~/bin/$f.$stamp; fi
done
```

2. Copy that set together. Do not copy `smoke.py`.

```bash
cp deploy/bland-relay/bland-relay.py \
  deploy/bland-relay/bland_relay.py \
  deploy/bland-relay/relay_config.py \
  deploy/bland-relay/relay_audit.py \
  deploy/bland-relay/relay_sms.py \
  deploy/bland-relay/relay_fc.py \
  deploy/bland-relay/relay_send.py \
  deploy/bland-relay/relay_rcs.py \
  deploy/bland-relay/relay_turo.py \
  deploy/bland-relay/relay_http.py \
  deploy/bland-relay/caller_context.py \
  deploy/bland-relay/meeting_check.py \
  deploy/bland-relay/turo_email.py \
  ~/bin/
cp deploy/bland-relay/bland-relay-setkey deploy/bland-relay/send-904 deploy/bland-relay/send-904-fc ~/bin/
chmod 755 ~/bin/bland-relay.py ~/bin/bland-relay-setkey ~/bin/send-904 ~/bin/send-904-fc
chmod 644 ~/bin/bland_relay.py ~/bin/relay_config.py ~/bin/relay_audit.py ~/bin/relay_sms.py \
  ~/bin/relay_fc.py ~/bin/relay_send.py ~/bin/relay_rcs.py ~/bin/relay_turo.py ~/bin/relay_http.py \
  ~/bin/caller_context.py ~/bin/meeting_check.py ~/bin/turo_email.py
```

The unit file already matches `~/.config/systemd/user/bland-relay.service`. Its header lists the same Python files. `ExecStart` stays `%h/bin/bland-relay.py`.

3. Mint the caller-context tokens if they are unset. `bland-relay-setkey` restarts the unit, so do this off-peak. The commands do not print the token.

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' | ~/bin/bland-relay-setkey CALLER_CONTEXT_TOKEN
python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' | ~/bin/bland-relay-setkey CALLER_FORGET_TOKEN
```

If both tokens were already set, restart once after the copy:

```bash
systemctl --user restart bland-relay.service
```

4. Confirm the unit and the startup line. `active` is required. The line has no token, phone number, or path.

```bash
systemctl --user is-active bland-relay.service
journalctl --user -u bland-relay.service -n 80 --no-pager
```

5. Funnel, read-only. Confirm `/bland/*` is proxied to `127.0.0.1:8799`. If the status shows one forward of `:8443` to that port, every path is already proxied. If Funnel maps by path and `/bland/` is absent, add that path. Do not change the Bland number. Paste `tailscale funnel status` into the issue with the host name redacted.

```bash
tailscale funnel status
```

6. From a checkout, not from `~/bin`, run the smoke harness against the public Funnel URL. It waits about a minute before the 61st-request burst so the earlier samples are outside the 60/minute window. Funnel may present every public caller as `127.0.0.1`, so that burst fills the limit for the relay. Run it with no call in progress, and wait a minute before running it again. Attach the JSON report to the issue. It has status codes and p50/p95/max milliseconds, and it has no token and no caller text. `known: false` on `pn=%2B10000000000` passes. End-to-end p95 over the Funnel must be under 1000 ms. The server-side 300 ms figure is the local #1096 test; this harness does not measure inside the process.

```bash
RELAY_PUBLIC_BASE=https://<funnel-host> \
  python3 deploy/bland-relay/smoke.py --check caller-context --n 50
```

`CALLER_CONTEXT_TOKEN` is read from the environment. Do not put it in the command line.

7. Do not apply `dynamic_data` here. That stays a separate Bland config change after Chris's go. No Bland API call and no live call.

Rollback restores the `$stamp` copies over `~/bin`, then removes any `~/bin/relay_*.py` or `~/bin/meeting_check.py` that has no `$stamp` backup, so a restored monolith is the file the unit loads. Restart the unit. Older backups remain `~/bin/*.bak-pre1104`, `~/bin/*.bak-1103`, `~/bin/*.bak-pre1100`, `~/bin/*.bak-pre1056`, `~/bin/*.bak-pre1054`, and `~/bin/bland-relay.py.bak-fwd904`. The kill switch is unchanged. The Bland route and the 904 inbound route stay on the same paths and tokens. The Bland number is untouched.

```bash
stamp=bak-pre1230
for f in bland-relay.py bland_relay.py relay_config.py relay_audit.py relay_sms.py \
  relay_fc.py relay_send.py relay_rcs.py relay_turo.py relay_http.py \
  caller_context.py meeting_check.py turo_email.py bland-relay-setkey send-904 send-904-fc; do
  if [ -f ~/bin/$f.$stamp ]; then cp ~/bin/$f.$stamp ~/bin/$f; fi
done
for f in ~/bin/relay_*.py ~/bin/meeting_check.py; do
  base=$(basename "$f")
  if [ ! -f ~/bin/$base.$stamp ]; then rm -f "$f"; fi
done
systemctl --user restart bland-relay.service
```

## Tests

From this directory, with pytest on the path:

```bash
python3 -m pytest tests -q
```

`tests/test_module_split.py` (#1107) checks that each production source file is at most 40 KB, that the `relay_*.py` parts load into the `bland_relay` namespace, and that the Deploy file list matches the `bland-relay.service` header (#1229). `smoke.py` is not in that list.

`tests/test_smoke.py` (#1229, fixture server, no network) covers the read-only harness: a pass case, a 401 case, a slow server over the end-to-end p95 limit, malformed JSON, a response body that must not appear in the report, and the 61st request returning 429 after the window wait. It also checks the startup line has no token, phone number, or path.

`tests/test_send_state.py` (#1140, gateway mocked, webhooks not registered on a device) covers signed and unsigned `sms:sent` / `sms:delivered` / `sms:failed` fixtures, a bad signature, a duplicate event id, an unknown `messageId`, one `send_failed` plus one #701 alert and the 15-minute cap, log redaction of the full number and the body, and a no-sender "not sent" notice that is not forwarded as inbound and is used only when no `sms:failed` was recorded.

`tests/test_meeting_check.py` (#1230, fake calendar client, fixtures in `tests/fixtures/meeting_check`, real network blocked) covers the exact response key set on every response, 401 and 429, every fail-closed path returning 200 `{"match": false}`, the match and no-match matrix (booking-page email, initial plus last, title-only, `timeZone` ignored, Central to ET, speech-to-text spellings, cancelled, all-day, declined, owner-only, wrong time/date/name, name or time or number alone, spoofed number), the privacy sweep over 25 fixture events, the anti-guess caps with zero calendar reads, fresh-snapshot zero-network and stale-snapshot one live call at 500 ms, the `fields` mask and OAuth refresh through a fake opener, `SECRET_ENV_KEYS` scrubbing, the 0600 attempt record and its 48 h purge, and p95 under 50 ms over 1000 requests on 2000 cached events.

`tests/test_caller_context.py` (#1096, no Bland or CRM network) covers index ingest for an inbound call webhook, a polled voicemail, a concurrency rejection, Bland SMS, and SMSGate; provider-id dedupe; `(239) 555-0101` == `+12395550101`; endpoint 401, unknown, last-3 ET order, sensitive-pattern stripping, and internal-error empty 200; a fixture CRM record (purpose, follow-up, booking sentence) and CRM timeout/5xx falling back to the index inside 250 ms; a non-loopback `CRM_API_BASE` using the index with no request and without echoing the token; both 2026-10-09 callback replays; a GET-only poller that stops on 429 and survives 5xx; 180-day purge; Grok-seat forget; token scrubbing; and p95 under 300 ms on 10k local rows.

`tests/test_first_contact.py` (#1100, SMSGate mocked, no network) covers, with `FC_REQUIRE_APPROVAL=1`: approval required, chat and SMS approval, no agent/self approval, code/text/ref checks, opt-out at request/approve/send and START, daily and sends-before-reply caps, existing rate limits, expiry/renewal/conversion, both kill switches, list/revoke, audit and CRM outbox records (no body, no secrets, 0600), log redaction, and the `send-904-fc` CLI; the #1102 (a) daily-cap rule for revoked/expired re-requests at request and at chat and SMS approve; #1102 (b) chat approval bound to `FC_CHAT_APPROVER` (unbound token and a different approver name are 403, and enabling the hash logs a warning); #1102 (c) an RCS title that is the approver's number does not approve (`deny`) while an `RCS_NAME_MAP` name does; #1102 (d) a non-local `SEND904_BASE` is refused by both CLIs before any request and the token is not printed; and, with approval off (default): immediate add, validation, STOP blocking add/send until START, the daily cap counting re-adds, TTL, no cap reset on re-add, existing rate limits, both kill switches, audit/CRM records, the approve route and SMS `YES <code>` being inert, and `send-904-fc add`.

Covered: token 401, validation, 72-hour allowlist, STOP until START, both rate limits, kill switch, missing gateway config, one mocked gateway call, log and #701 text redaction, 904 inbound forward plus allowlist recording, Bland HMAC including the compact-JSON signature, the SMSGate webhook: signature ok/bad/unsigned/stale, wrong path 404, payload parse and shape parity, allowlist, STOP/START, ignored events, duplicate ids, forward failure, and log redaction. The RCS notification route (`tests/test_rcs_webhook.py`) covers: app payload and lenient template parsing, number/title/name-map/unknown sender resolution, token 401, unset/blank token 503 (no path-only mode), path 404, kill switch 503, package/summary filtering, STOP/START, the unknown sender not being allowlisted, dedupe both ways against SMSGate plus repeat notifications and window expiry, the SMSGate head-start wait, forward failure and retry, and log redaction. The Turo relay (`tests/test_turo_relay.py`, #1104, gateway/webhooks mocked and real network blocked) covers: the Turo branch on the same path/token, token 401, unset/blank `RCS_HOOK_TOKEN` refusing the whole route, path 404, other packages still ignored, Messages unchanged, `big_text` preference, the normalized shape, reservation/vehicle/guest parsing with nulls, dedupe (key+text, guest+text, prefix re-post, window expiry, persisted state), suppression (default, env, bad regex), each urgent class, priority, the negative list and env overrides, owner_alert (template, `TURO_OWNER_ALERT_TO` as the only recipient, invalid E.164 disabled and never logged, no number in code, unset, 72 h skip for the owner only, `SMS_SEND_DISABLED`, STOP/START, dedupe, per-guest and hourly limits, relay-wide slot, gateway failure), non-urgent never texting, both kill switches, the trimmed Helm field set and auth (default Bearer, scheme passthrough, custom header, bad header, no key, timeout), JSONL-only when URLs are unset, the trimmed Alexandra `turo_urgent` push, webhook failure/exception isolation, inbox 0600/redaction/retention, and log/secret redaction. The Gmail push path (`tests/test_gmail_turo_watch.py`, #1142, Gmail history/messages from `tests/fixtures/gmail_turo`, webhooks stubbed, network blocked) covers: the parser (both copies, multi-line, HTML-only, non-Turo, booking mail, spoofed DKIM, too old, SPAM/SENT, lookalike domain), end-to-end history sync, the two Gmail copies giving one Helm POST, relay-first and email-first cross-source dedupe, truncated notifications, the guest/vehicle/trip guard and window, unchanged relay-only behavior, co-host suppression, the urgent owner alert + Alexandra, no second owner alert, kill switches, shadow mode, nack on relay or Gmail failure without advancing historyId, history 404 recovery, ack rules, watch renew, the loopback-only listener (token, path, proxy headers), the unit templates (the only timer is the daily renew), and the #1143 review fixes: topmost-A-R-only auth (forged lower header, missing A-R, non-Gmail top, DMARC fail rejected; real pass accepted), missing internalDate, 4xx skip and 5xx/fetch retry-then-skip with one alert and bounded state, cross-source consumption, the truncation/60% prefix rule, the watcher-side disable switch, catch-up after a lapsed watch, the key-file mode check, and shadow mode advancing historyId.
