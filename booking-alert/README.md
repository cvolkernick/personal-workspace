# booking-alert

Classifies Turo mail from `panamerica.cars@gmail.com` and wakes Helm's
event-driven Turo inbound routine with `kind=booking_change`
(`schema` `panamerica.booking.event.v1`, `change_type` `new`, `modified`,
`cancelled`, or `unknown`).

Guest messages, reviews, payouts, and invoices are not bookings. Mail for
Alex's co-host vehicles and SafeWheels is excluded. A guest named Alex on
our fleet is still a booking. The same Gmail message id, and the two copies
of one reservation, wake Helm once.

`classify`, `build_payload`, and `webhook_wake` are the emit path. The Gmail
watch (`deploy/bland-relay/gmail_turo_watch.py`) hands a prepared payload to
the relay loopback. The relay calls `webhook_wake`, which POSTs to
`HELM_TURO_WEBHOOK_URL` with `Authorization` from `HELM_TURO_WEBHOOK_KEY`
when that key is set. Those values live in the relay environment
(`~/.config/bland-relay/env` on prism-gateway). This tree does not contain
them.

Unverified rows in `patterns.json` do not match. The shipped Turo booking
rows are verified.

```bash
python3 -m pytest booking-alert/tests -q
```

## Deploy

Not done by this PR. On prism, after merge, from a checkout of this tree.

`gmail-turo-watch.service` runs `~/bin/gmail_turo_watch.py`. `_load_booking_alert` in that file, and the same helper in `relay_turo.py`, imports this package from `~/apps/booking-alert` (`Path.home() / "apps" / "booking-alert"`). Copy the `booking-alert/` directory there so `booking_alert.py` and `patterns.json` sit side by side.

```bash
install -d ~/apps
rm -rf ~/apps/booking-alert
cp -a booking-alert ~/apps/booking-alert
systemctl --user restart gmail-turo-watch.service bland-relay.service
```

`bland-relay.service` restarts because `relay_turo.py` changed. That file is the copy the existing `~/bin` deploy puts next to `bland-relay.py`.

Env names only: `HELM_TURO_WEBHOOK_URL`, `TURO_EMAIL_TOKEN`.

The first booking wake logs no `failed status=err`. This exits 1 when that string is absent:

```bash
journalctl --user -u bland-relay.service -u gmail-turo-watch.service -n 200 --no-pager | grep -F 'failed status=err'
```
