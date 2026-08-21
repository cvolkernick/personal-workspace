# Hosting — own public Vercel project

MiKrafts is a **public** static site. It is not on the Pi intranet and is
not a FitDash / Orchestra / FCC / Fleet surface.

## This agent could not publish

Vercel MCP in this environment is `needsAuth`, and no `VERCEL_TOKEN` / CLI
session is available. Grok creates and links the project after merge.

## What Grok should create

Separate Vercel project (not FitDash):

| Setting | Value |
|---------|--------|
| Project name | `mikrafts` |
| Root Directory | `mikrafts` |
| Framework | Other / None (`vercel.json` already sets `"framework": null`) |
| Environment variables | **None** — no SMTP, Gmail, or `GOOGLE_*` |
| Production branch | `master` after this PR merges |

FitDash already skips non-`resistance-dashboard/` commits via
`resistance-dashboard/vercel-ignore-paths.txt`. Do not change that file
except to keep that lock.

Public URL will be whatever Vercel assigns (for example
`https://mikrafts.vercel.app`). Custom domain is out of scope.
