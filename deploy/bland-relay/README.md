# bland-relay

Stdlib HTTP relay on prism. Listens on `127.0.0.1:8799` (user unit `bland-relay.service`). Tailscale Funnel `:8443` is the existing public front. Secret paths stay unguessable. This tree is the source for `~/bin`. The unit keeps running the copy in `~/bin`, not this checkout.

## Routes

| Route | Auth | What it does |
|---|---|---|
| `POST /<RELAY_PATH>` | `X-Webhook-Signature` HMAC | Bland webhook. Forwards the raw body to Alexandra. |
| `POST /<FWD_PATH>` | `FWD_TOKEN` as `Authorization: Bearer` or `X-Relay-Token` | 904 inbound SMS from the phone forwarder. Forwards the normalized payload. Records the sender for the reply allowlist unless `test: true`. |
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

The relay POSTs `{phoneNumbers, textMessage}` to `{SMSGW_URL}/messages` with HTTP Basic auth (the app's cloud username and password). Cloud OpenAPI 1.49.0 lists `POST /3rdparty/v1/messages` only. The device local server still uses `/message`. If `SMSGW_URL` already ends in `/message` or `/messages`, that full URL is the endpoint, so local mode is `SMSGW_URL=http://<phone>:8080/message` once the phone is reachable. Do not switch inbound off the forwarder app in this slice.

## Secrets

Values go in `~/.config/bland-relay/env` only, through `bland-relay-setkey` (stdin, not argv). Not in git, chat, or logs.

```bash
printf %s "$VALUE" | ~/bin/bland-relay-setkey SMSGW_URL
printf %s "$VALUE" | ~/bin/bland-relay-setkey SMSGW_USER
printf %s "$VALUE" | ~/bin/bland-relay-setkey SMSGW_PASS
printf %s "$VALUE" | ~/bin/bland-relay-setkey SEND_PATH
printf %s "$VALUE" | ~/bin/bland-relay-setkey SEND_TOKEN
printf %s 1 | ~/bin/bland-relay-setkey SMS_SEND_DISABLED   # kill switch
printf %s 0 | ~/bin/bland-relay-setkey SMS_SEND_DISABLED
```

`SEND_PATH` and `SEND_TOKEN` are long random strings. Alexandra gets the Funnel URL plus the token in her own secret store.

Phone setup, before any live send: install the app, turn Cloud Server on, tap Online, grant SMS, set battery to Unrestricted, leave RCS off in Google Messages. RCS chats never reach the forwarder.

## Deploy

Not done by the PR. After merge, on prism:

```bash
cp deploy/bland-relay/bland_relay.py deploy/bland-relay/bland-relay.py ~/bin/
cp deploy/bland-relay/bland-relay-setkey ~/bin/
chmod 755 ~/bin/bland-relay.py ~/bin/bland-relay-setkey
# unit file already matches ~/.config/systemd/user/bland-relay.service
systemctl --user restart bland-relay.service
```

Rollback is `~/bin/bland-relay.py.bak-fwd904`, or the kill switch. The Bland route and the 904 inbound route stay on the same paths and tokens.

## Tests

From this directory, with pytest on the path:

```bash
python3 -m pytest tests -q
```

Covered: token 401, validation, 72-hour allowlist, STOP until START, both rate limits, kill switch, missing gateway config, one mocked gateway call, log and #701 text redaction, 904 inbound forward plus allowlist recording, Bland HMAC including the compact-JSON signature.
