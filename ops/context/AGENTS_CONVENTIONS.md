# Agent conventions (portable)

Seed for `~/AGENTS.md` conventions. Repo git rules stay in `Agents.md` at the
personal-workspace root.

## Repo-first handoffs

- Durable work lives in git on a `feature/*` or `fix/*` branch, then a PR.
  Chat is not the archive.
- `cvolkernick/personal-workspace` work branches: `work/<area>` per TLD
  (`Agents.md`). Workflow/ops → `work/projects-dashboard`.
- Do not commit on `master` for product work. Do not force-push shared history.

## Deploy via git

- SOP: GitHub #569. **git push only.** Never `vercel deploy` from a dirty
  tree. Production is `master`. FitDash root = `resistance-dashboard/`.
- Mac = dev. Pi (prism-gateway) = prod. Localhost is not “shipped.”
- Path-scoped Pi deploys: `bash deploy/install_remote.sh prism-agent@<host> --only <units>`.
- Merge ≠ timer live. Install LaunchAgent / systemd after merge.

## X-link reading

- Prefer the public X API / `x-post-reader` (no auth) for tweet content.
- Do not paste API keys or tokens into job payloads, MEMORY.md, or channel.

## Secrets

- Never commit `.env`, `mcp_credentials.json`, `auth.json`, `.ssh` private
  keys, wallet data, or Secure Vault contents.
- Agent home backup uses `ops/context/exclude.py` (tested).

## Memory discipline

- Reconcile facts in place. Never duplicate a `## Facts` row with a new
  contradictory value — `persist.py contradict`.
- One canonical location per fact (`CONTEXT.md`).
