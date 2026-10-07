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
