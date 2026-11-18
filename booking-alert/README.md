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
