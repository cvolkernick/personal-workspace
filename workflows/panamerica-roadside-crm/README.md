# Panamerica roadside CRM

Lightweight CRM + owner-outreach pipeline for cars Chris photographs
on the roadside. Intake is a Google Drive photo dump; the CRM file
store is the system of record.

Issues: [#807](https://github.com/cvolkernick/personal-workspace/issues/807), [#820](https://github.com/cvolkernick/personal-workspace/issues/820), [#854](https://github.com/cvolkernick/personal-workspace/issues/854), [#855](https://github.com/cvolkernick/personal-workspace/issues/855), [#860](https://github.com/cvolkernick/personal-workspace/issues/860), [#861](https://github.com/cvolkernick/personal-workspace/issues/861), [#862](https://github.com/cvolkernick/personal-workspace/issues/862), [#896](https://github.com/cvolkernick/personal-workspace/issues/896)

## Canonical choices

| Item | Choice |
|------|--------|
| Intake | Google Drive folder **Panamerica - Roadside Leads** (`1QS6rqyApNDCrsJ90mp83rnbEthlzznxy`) |
| Store | JSON file (`FileStore`). Default `~/.local/share/panamerica-roadside-crm/store.json` |
| States | `needs-info \| new → sms_sent → call_attempted → responded → interested → converted \| declined \| dead` |
| Dedupe | Phone number. A number already in the CRM never gets a second first-touch |
| Copy | `outreach_copy.py` + `prompts/alexandra.roadside.v1.md`. Chris-approved 2026-09-20 (#855). Location slot (#860): proximity → `near {place} in {city}`; road → `on {road}`. CRM keeps the full location string. `--live` still blocked until `PANAMERICA_ROADSIDE_COPY_APPROVED=1` (first-send human gate). SMS A/B (#896): variant A is that champion; variant B is an unapproved placeholder and is not assigned or sent until a Chris copy change sets `SMS_VARIANT_B_APPROVED` |
| Channel | Alexandra / Bland. Phase 1 SMS, Phase 2 voice 5–7 days later for non-responders only |
| OCR | Live `build_adapters` uses Google Cloud Vision (`VisionOcr`). `NullOcr` is tests-only. Failed/low-confidence reads return `UNKNOWN`, not empty text. Key: `GOOGLE_VISION_API_KEY` (env-only) |
| Phone extract | Prefer dashed/parenthesized NANP. GPS/decimal degree runs (`26.639011, -82.039046`) are rejected; coordinate-only text is `UNKNOWN` (#862) |

Secrets (`BLAND_AGENT_ID`, API keys, Drive tokens) live in env — never in this repo, issues, or logs.

Drive is **intake only**. Photos with no CRM lead, or CRM leads with no photos, are drift the daily pass reconciles.

## Phone dump

From Chris's phone, open the Drive folder and drop the car photo plus
the for-sale sign in the **folder root**. No subfolders, no naming.
Ingestion reads EXIF `DateTimeOriginal` + GPS, files each car into
`YYYY-MM-DD[-road]`, reverse-geocodes the location, and runs the same
day (daily pass, not weekly).

Missing EXIF is visible on the lead (`date_source: exif|upload|folder|manual`,
`location_source: exif|folder|manual|missing`). Upload date is the date
fallback; missing GPS leaves location blank and flags ops. JPEG and HEIC
are both accepted.

Existing `YYYY-MM-DD-<road>` subfolders still ingest (folder name is a
visible fallback, not EXIF).

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
| `daily-pass` | Organize root photos from EXIF, pull new sets, extract phone, skip duplicates |
| `weekly-pass` | Alias for `daily-pass` |
| `sms-batch` | Phase 1 SMS for `new` leads (no STOP footer on send; inbound STOP still honored). Assigns `sms_variant_id` once, then returns `variant_report` |
| `call-batch` | Phase 2 Bland voice for `sms_sent` non-responders aged 5–7 days |
| `ingest-reply` / `webhook` | STOP, decline, interest → CRM |
| `status` | Counts + `needs-info` queue |

## Dry-run vs live

Dry-run still executes intake, phone extract, dedupe, SMS/call rendering,
state transitions, callbacks, and records an outbox/alert log. It does
not call Bland or the interest webhook.

`--live` requires `PANAMERICA_ROADSIDE_COPY_APPROVED=1` (first-send
human gate; copy itself is already Chris-approved in #855). Compliance:
only numbers posted on for-sale signs, inbound STOP honored immediately,
one sequence per car, no re-contact after decline.

## SMS A/B (#896)

Variant **A** is the #855 champion. Variant **B** is a placeholder.
`SMS_VARIANT_B_APPROVED` stays `False` until Chris approves a real
challenger in the same change that replaces the placeholder. That is
not an env flag. Both arms still need the first-send gate above
before anything goes live.

`send_sms` assigns an empty `sms_variant_id` once, before the Bland
call, and never rewrites it. The arm is SHA-256 of the lead id: first
8 hex digits, even → A, odd → B (`assign_sms_variant`). While B is
unapproved every assignment is A and the report's `ab_mode` is
`champion_only`. A second lead with the same phone is skipped once
the first lead has an arm. One phone never gets both scripts.

Inbound STOP still declines and suppresses. The event and the lead
get `reply_quality`: `interested`, `not_now` (declined, no STOP),
`stop_angry` (STOP / opt-out), or `other`. `no_reply` is not written
onto the lead. The report derives it when `sms_sent` is still unanswered
72 hours after `sms_sent_at`.

`daily-pass`, `sms-batch`, `status`, and `run` include `variant_report`:
sends today by arm, cumulative sends / replies / interested / not_now /
stop_angry / no_reply, and `reply_rate_72h` (null until a send has
matured). `run.variant_report` is the post-SMS snapshot.

## Tests

```bash
python3 -m unittest discover -s workflows/panamerica-roadside-crm/tests -v
```
