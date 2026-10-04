# bland-relay

Stdlib HTTP relay on prism. Listens on `127.0.0.1:8799` (user unit `bland-relay.service`). Tailscale Funnel `:8443` is the existing public front. Secret paths stay unguessable. This tree is the source for `~/bin`. The unit keeps running the copy in `~/bin`, not this checkout.

## Routes

| Route | Auth | What it does |
|---|---|---|
| `POST /<RELAY_PATH>` | `X-Webhook-Signature` HMAC | Bland webhook. Forwards the raw body to Alexandra. |
| `POST /<FWD_PATH>` | `FWD_TOKEN` as `Authorization: Bearer` or `X-Relay-Token` | 904 inbound SMS from the phone forwarder. Forwards the normalized payload. Records the sender for the reply allowlist unless `test: true`. |
| `POST /<SMSGW_HOOK_PATH>` | Secret path, plus SMSGate `X-Signature`/`X-Timestamp` HMAC when `SMSGW_SIGNING_KEY` is set | SMSGate cloud `sms:received` webhook (#1054). Same inbound path as the forwarder: allowlist record, STOP/opt-out, same payload to Alexandra. Replaces the SMS Forwarder app. |
| `POST /<RCS_HOOK_PATH>` | Secret path, plus `RCS_HOOK_TOKEN` as `Authorization: Bearer` or `X-Relay-Token` when set | Google Messages notifications from the 904 phone (#1056), so RCS chats arrive too. Same allowlist, STOP, and payload. Deduped against SMSGate. Kill switch `RCS_HOOK_DISABLED=1`. |
| `POST /<SEND_PATH>` | `SEND_TOKEN`, same headers | Alexandra sends one SMS. |

Send body:

```json
{"to": "+19045550199", "message": "text", "in_reply_to": "optional"}
```

`to` is strict E.164 US NANP (`+1` then NXX NXX XXXX). `message` is required, stripped, and at most 640 characters. `in_reply_to` is accepted and not sent to the gateway.

Success is `{"ok": true, "id": "...", "state": "Pending"}`. The id and state come from the gateway.

| Result | Status |
|---|---|
| Kill switch `SMS_SEND_DISABLED=1` | 503 `send disabled` |
| Send token or gateway settings missing | 503 `relay not configured` |
| Bad or missing token | 401 |
| Bad `to` or `message` | 400 |
| Number has not texted 904 in the last 72 hours | 403 `not_allowlisted` |
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
```

`SEND_PATH` and `SEND_TOKEN` are long random strings. Alexandra gets the Funnel URL plus the token in her own secret store.

Phone setup, before any live send: install the app, turn Cloud Server on, tap Online, grant SMS, and set battery to Unrestricted. Keep **RCS on** in Google Messages. With RCS off, SMSGate outbound sends stall at Processed (#1023). RCS chats never fire `sms:received`, so they come in through the RCS notification route (see the #1056 section above).

## Deploy

Not done by the PR. After merge, on prism:

```bash
cp deploy/bland-relay/bland_relay.py deploy/bland-relay/bland-relay.py ~/bin/
cp deploy/bland-relay/bland-relay-setkey ~/bin/
chmod 755 ~/bin/bland-relay.py ~/bin/bland-relay-setkey
# unit file already matches ~/.config/systemd/user/bland-relay.service
systemctl --user restart bland-relay.service
```

Rollback is `~/bin/*.bak-pre1056` (latest), `~/bin/*.bak-pre1054`, `~/bin/bland-relay.py.bak-fwd904`, or the kill switch. The Bland route and the 904 inbound route stay on the same paths and tokens.

## Tests

From this directory, with pytest on the path:

```bash
python3 -m pytest tests -q
```

Covered: token 401, validation, 72-hour allowlist, STOP until START, both rate limits, kill switch, missing gateway config, one mocked gateway call, log and #701 text redaction, 904 inbound forward plus allowlist recording, Bland HMAC including the compact-JSON signature, the SMSGate webhook: signature ok/bad/unsigned/stale, wrong path 404, payload parse and shape parity, allowlist, STOP/START, ignored events, duplicate ids, forward failure, and log redaction. The RCS notification route (`tests/test_rcs_webhook.py`) covers: app payload and lenient template parsing, number/title/name-map/unknown sender resolution, token 401, path 404, kill switch 503, package/summary filtering, STOP/START, the unknown sender not being allowlisted, dedupe both ways against SMSGate plus repeat notifications and window expiry, the SMSGate head-start wait, forward failure and retry, and log redaction.
