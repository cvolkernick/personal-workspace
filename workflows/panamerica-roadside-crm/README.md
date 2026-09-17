# Panamerica roadside CRM

Lightweight CRM + owner-outreach pipeline for cars Chris photographs
on the roadside. Intake is a Google Drive photo dump; the CRM file
store is the system of record.

Issue: [#807](https://github.com/cvolkernick/personal-workspace/issues/807)

## Canonical choices

| Item | Choice |
|------|--------|
| Intake | Google Drive folder **Panamerica - Roadside Leads** (`1QS6rqyApNDCrsJ90mp83rnbEthlzznxy`) |
| Store | JSON file (`FileStore`). Default `~/.local/share/panamerica-roadside-crm/store.json` |
| States | `needs-info \| new → sms_sent → call_attempted → responded → interested → converted \| declined \| dead` |
| Dedupe | Phone number. A number already in the CRM never gets a second first-touch |
| Copy | `outreach_copy.py` + `prompts/alexandra.roadside.v1.md`. **Not Chris-approved yet** — `--live` is blocked until `PANAMERICA_ROADSIDE_COPY_APPROVED=1` |
| Channel | Alexandra / Bland. Phase 1 SMS, Phase 2 voice 5–7 days later for non-responders only |

Secrets (`BLAND_AGENT_ID`, API keys, Drive tokens) live in env — never in this repo, issues, or logs.

Drive is **intake only**. Photos with no CRM lead, or CRM leads with no photos, are drift the weekly pass reconciles.

## Phone dump

From Chris's phone, open the Drive folder, make a subfolder
`YYYY-MM-DD-<road>` (example `2026-09-17-del-prado`), drop the car
photo plus the for-sale sign, stop. No form.

How-to doc in the folder: https://docs.google.com/document/d/1veS6NBRTyg0mCjAkJYz0CpsxL5ydsvx1CmYq0oRF9sY/edit

Folder: https://drive.google.com/drive/folders/1QS6rqyApNDCrsJ90mp83rnbEthlzznxy

Unreadable signs become `needs-info` and stay off outreach until a phone is added.

## Grok Bot harness

```bash
python3 workflows/panamerica-roadside-crm/run.py run \
  --dry-run \
  --fixture workflows/panamerica-roadside-crm/tests/fixtures/photo_sets.json \
  --simulate-replies \
  --simulate-interest \
  --store /tmp/panamerica-roadside-dry.json
```

| Arg / verb | Meaning |
|------------|---------|
| `--dry-run` | Default. Full pipeline, **no** Bland HTTP |
| `--live` | Real SMS/voice. Refused until copy approval |
| `weekly-pass` | Pull new photo sets, extract phone, skip duplicates |
| `sms-batch` | Phase 1 SMS for `new` leads (STOP always present) |
| `call-batch` | Phase 2 Bland voice for `sms_sent` non-responders aged 5–7 days |
| `ingest-reply` / `webhook` | STOP, decline, interest → CRM |
| `status` | Counts + `needs-info` queue |

## Dry-run vs live

Dry-run still executes intake, phone extract, dedupe, SMS/call rendering,
state transitions, callbacks, and records an outbox/alert log. It does
not call Bland or the interest webhook.

`--live` requires `PANAMERICA_ROADSIDE_COPY_APPROVED=1` (Chris sign-off
on SMS + call script). Compliance: only numbers posted on for-sale
signs, STOP honored immediately, one sequence per car, no re-contact
after decline.

## Tests

```bash
python3 -m unittest discover -s workflows/panamerica-roadside-crm/tests -v
```
