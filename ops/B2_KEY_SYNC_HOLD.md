# HOLD — sealed key-sync (not implemented)

Status: **HOLD**. Design note only. Do not implement. Do not copy venue keys
in this slice.

## Why keys stay on prism

`prism-gateway` (`app-books`) is the only box that runs live books: FCC `:8000`,
venue adapters, YNAB, IoT secrets, scheduler env. Keys are a **kill-switch**:
if the app Pi is seized or dies, rotate and re-issue — the finley pull must
not contain them. Putting keys on finley in v1 (B2 + puller on **one** box)
would recreate a single point of failure, just moved.

The puller snapshots **books** (treasury JSON as of last run), not credentials.
`FCC_TREASURY_JSON` must never be loaded onto Vercel. Raw treasury must never
land on a Mac home or a Vercel dest.

## What a later unlock would copy

After a **third** Pi exists (or an operator-held sealed store), a later unlock
might copy **only**:

- Sealed blobs (age/sops), not plaintext API keys
- From prism → the sealed store (not onto finley `~/b2-pulls`, not onto `~/B2`)
- Unwrap only on a new `app-books` host, after hostname/role check
- Operator-present unlock (not a timer)

That path is not built.

## What it must never do

- Rsync `secrets.json`, `*.env`, `ynab/token`, Coinbase/Robinhood key files
- Write `FCC_TREASURY_JSON` or any raw treasury into a Vercel project / env
- Write pull dest under `/Users/…` (Mac)
- Push the knowledge graph or keys onto prism “for convenience”
- Commit keys to git
- Let the puller restore itself from the book snapshot (that is an off-site
  bucket, store not chosen — out of scope; when added it must **not** get
  its own clock — the only pull timer is hourly :20 `America/New_York`)

Until unlock, treat missing keys as **re-issue**, not restore.
