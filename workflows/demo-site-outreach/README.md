# Demo-site outreach (Alexandra)

Harness-invokable workflow: source local businesses with no website, email as
**Alexandra**, parse discovery, generate a one-page demo, SMS the link via
Bland, hand qualified interest to the Chairman.

Issue: [#798](https://github.com/cvolkernick/personal-workspace/issues/798)

## Canonical choices

| Item | Choice |
|------|--------|
| Store | JSON file (`FileStore`). Default `~/.local/share/demo-site-outreach/store.json` |
| States | `sourced → contacted → discovering → building → demo_sent → qualifying → handed_off \| dead \| suppressed` |
| Template | `templates/v1/` pinned per lead. **Not Chairman-approved yet** — `--live` is blocked until `DEMO_SITE_OUTREACH_TEMPLATE_APPROVED=1` |
| Demo retention | 30 days, then `teardown-expired` |
| Sender | Alexandra (research framing; no brand name) |
| Geo | `--geo` (default `US`); `--category` optional / empty |

Secrets (`BLAND_AGENT_ID`, API keys) live in env — never in this repo, issues, or logs.

## Grok Bot harness

```bash
python3 workflows/demo-site-outreach/run.py run \
  --geo "US" \
  --category "" \
  --batch-size 10 \
  --dry-run \
  --fixture workflows/demo-site-outreach/tests/fixtures/places_listings.json \
  --simulate-replies \
  --simulate-interest \
  --store /tmp/demo-site-outreach-dry.json
```

| Arg | Meaning |
|-----|---------|
| `--geo` | Places location string (`US` or `City, ST`) |
| `--category` | Optional filter; empty = no category filter |
| `--batch-size` | Max new leads per `source` / `run` |
| `--dry-run` | Default. Full pipeline, **no** email/SMS/Vercel/alert HTTP |
| `--live` | Real sends. Refused until template approval + CAN-SPAM address |

Other verbs: `source`, `outreach`, `ingest-reply`, `build`, `follow-up`,
`webhook`, `serve-webhook`, `status`, `teardown-expired`.

Inbound Bland SMS: POST JSON to `serve-webhook` with `X-Webhook-Secret`, or
pipe a payload into `webhook`. Metadata `lead_id` or `from` phone maps the lead.

## Dry-run vs live

Dry-run still executes sourcing, personalization, reply parsing, site
generation, state transitions, and records an outbox/alert log. It does not
call SMTP, Bland, Vercel, or the Chairman alert webhook.

Places may be live (read) when `GOOGLE_PLACES_API_KEY` is set and `--fixture`
is omitted. `--fixture` is the harness-safe path.

## Tests

```bash
python3 -m unittest discover -s workflows/demo-site-outreach/tests -v
```
