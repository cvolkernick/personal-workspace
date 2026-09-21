# Marketplace lead queue (#876)

Ops CRM for Facebook Marketplace vehicle leads. The JSON file is the
source of truth. The 7:45 morning brief Roadside section and the FCC
lead-queue page both render from it.

There was no in-repo marketplace CRM when this landed (no `listing_id`
store on `master` or on prism-gateway). Roadside sign outreach stays in
`workflows/panamerica-roadside-crm/` and is not this queue.

## Status

| Status | Meaning |
|---|---|
| `surface_to_chairman` | No phone. Open on the queue and in the Roadside section. |
| `sms_queued` | Phone present, or ops marked the lead SMS-eligible. Off the queue. This package does not send SMS. |
| `contacted` | Chairman marked contacted. Off the queue. |
| `disqualified` | Chairman marked disqualified. Off the queue. |
| `converted` | Chairman converted the lead to an owner. Off the queue. |

Dedup key is `listing_id` plus normalized phone. A listing id already in
the CRM is never inserted again and its status is not reset, so a lead
that already left the queue is not re-queued.

Listing links use `https://www.facebook.com/marketplace/item/<id>/`.
Slugs are ignored. Missing photo or seller renders as unavailable.

## Commands

```bash
export MARKETPLACE_LEAD_STORE=~/.local/share/marketplace-leads/store.json
printf '%s' '{"listing_id":"100200300400","year":"2021","make":"Toyota","model":"Corolla","price":"$8500","mileage":"92000","location":"Cape Coral, FL","listing_date":"2026-09-20","reason_flagged":"no phone on listing"}' \
  | python3 -m workflows.marketplace_leads.run ingest
python3 -m workflows.marketplace_leads.run brief
python3 -m workflows.marketplace_leads.run action <lead-id> contacted
```

`brief` prints the Roadside section. No 7:45 emitter exists on
`origin/master` or in the Pi user timers, so this output is not delivered
by a morning brief. Brief delivery stays open until an emitter calls
`morning_brief_from_store`.

FCC serves `/lead-queue.html` (nav **Leads**). Actions are status writes
only. Chairman messaging stays a manual open of the listing link.
