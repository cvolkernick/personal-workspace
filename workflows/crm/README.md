# General CRM (P1)

SQLite system of record for roadside leads, marketplace leads, and vendors.
`workflows/panamerica-roadside-crm/` stays the outreach pipeline. It keeps
writing `FileStore` until `PANAMERICA_ROADSIDE_BACKEND=crm`.

| Item | Value |
|------|--------|
| Database | `~/.local/share/panamerica-crm/crm.db` (WAL, mode 600) |
| API | `python3 -m workflows.crm.api` on `127.0.0.1:8791` |
| Client | `python3 -m workflows.crm.cli` |
| Import | `python3 -m workflows.crm.importer` |
| Service unit | `workflows/crm/deploy/crm-api.service` (ops installs it; this PR does not) |

Only the API process opens `crm.db`. Seat tokens are hashed in `credential`.
`write:*` does not include `delete`. Alexandra's token cannot create a vendor
or delete a party. Audit rows are append-only; `POST`/`PATCH`/`DELETE /v1/audit`
return 405.

`outreach_eligible` is the lead view SMS and call batches read: `type=lead`,
not archived, funnel status `new` or `sms_sent`, primary phone not suppressed.
A landline tag (`error_code=30006`) stays in the view so voice still works, and
the roadside SMS selector skips it. Non-lead types are never selected.

The roadside adapter exports JSON back to the old store path for 30 days after
the database is created. `PANAMERICA_ROADSIDE_BACKEND=file` reads that JSON (or
the `.bak-p1` backup) and does not open `crm.db`.

```bash
python3 -m unittest discover -s workflows/crm/tests -v
python3 -m unittest discover -s workflows/panamerica-roadside-crm/tests -v
```

No live SMS. `PANAMERICA_ROADSIDE_COPY_APPROVED` stays unset.
Importer `--dry-run` uses a throwaway database. `--backup` copies the roadside
JSON to `store.json.bak-p1` once and never writes that file again.

## Contact lookup by phone (#1097 slice 1)

`GET /v1/contacts/by-phone?pn=<phone>&limit=<N>` returns one contact for the
caller-context endpoint in bland-relay (#1096). Any US form of the number is
normalized to E.164 (`(239) 555-0101` == `+12395550101`). A number that does
not normalize is 400. An unknown or archived number is 404 `{"error": "not found"}`.
`limit` is the number of interactions (default 5, max 20). The request does no
outbound network.

```json
{"party": {"id": "…", "type": "customer", "status": "new", "display_name": "Sam Rivera", "tags": ["turo-guest"]},
 "type": "customer", "tags": ["turo-guest"],
 "identities": [{"kind": "phone"}, {"kind": "turo_guest_id"}],
 "turo_booking": true,
 "open_followups": ["Chris to call back about storage"],
 "followups": [{"id": "…", "title": "…", "due_at": "2026-10-10T09:00:00-04:00", "due_text": "Sat Oct 10 9:00 AM ET", "assignee_seat": "grok"}],
 "interactions": [{"id": "…", "channel": "call", "direction": "out", "outcome": "voicemail", "summary": "…", "occurred_at": "2026-10-09T10:20:00-04:00", "occurred_text": "Fri Oct 9 10:20 AM ET"}],
 "tz": "America/New_York"}
```

- Interactions are newest first. Times are ET with the offset (DST aware).
  Follow-ups are open `task` rows, earliest due first.
- `turo_booking` is true when the party has the `turo-guest` or `turo` tag, a
  `turo_guest_id` identity, or `ext_json.turo_booking`. bland-relay's
  `view_from_crm` turns that into "Has a Turo booking on file; confirm name and
  trip number before discussing it." No trip details are returned.
- Summaries, titles and the display name go through `pii.scrub` (card, SSN,
  street address, access/lockbox codes, plates, emails, phone numbers). Bodies,
  transcripts and vehicles are never returned. Identities are limited to phone,
  email and turo_guest_id.
- Scope `read:caller-context`. The `caller-context` seat holds only that scope:
  it can call this lookup and nothing else (403), and it sees identity kinds
  without values. `read:*` seats (grok, buzz, chris) also see identity values.
  `alexandra` (read:party only) gets 403.

## Deploy (standalone, #1097)

Not done by the PR. Ops runs this on prism-gateway after merge. The service runs
from a path-scoped copy in `~/apps/crm-api`; it never reads `~/personal-workspace`.
`deploy/crm-api.service` (P1) stays for reference and is not installed.

```bash
# from a checkout of master, on prism
install -d -m 700 ~/apps/crm-api ~/.local/share/panamerica-crm
rsync -a --delete --exclude tests --exclude __pycache__ workflows/crm/ ~/apps/crm-api/workflows/crm/
printf '' > ~/apps/crm-api/workflows/__init__.py
cp workflows/crm/deploy/crm-api-apps.service ~/.config/systemd/user/crm-api.service
systemctl --user daemon-reload && systemctl --user enable --now crm-api.service
systemctl --user is-active crm-api.service
stat -c '%a %n' ~/.local/share/panamerica-crm ~/.local/share/panamerica-crm/crm.db   # 700 / 600
curl -s http://127.0.0.1:8791/health                                                    # {"ok": true}
```

Issue the caller-context token into the DB and hand it to bland-relay without
printing it (the API must be the only writer, so stop it briefly):

```bash
systemctl --user stop crm-api.service
cd ~/apps/crm-api && python3 -c 'import sys; from pathlib import Path; from workflows.crm.core import CrmDB
db = CrmDB(Path.home()/".local/share/panamerica-crm/crm.db"); sys.stdout.write(db.issue_token("caller-context")["token"])' \
  | ~/bin/bland-relay-setkey CRM_API_TOKEN
systemctl --user start crm-api.service
printf %s http://127.0.0.1:8791 | ~/bin/bland-relay-setkey CRM_API_BASE   # only once #1097 ingest is live
```

Leave `CRM_API_BASE` unset on the relay until ingest (slice 2) has data: a CRM
404 is final for caller-context and does not fall back to the local index.
Rotate: issue a new `caller-context` token the same way, then revoke the old row
(`UPDATE credential SET revoked = 1 WHERE id = ?`). Rollback: `systemctl --user
disable --now crm-api.service`, unset `CRM_API_BASE`/`CRM_API_TOKEN` on the
relay. The DB stays.

Env names only: `CRM_API_BASE`, `CRM_API_TOKEN` (bland-relay side). The CRM side
has no env secrets.
