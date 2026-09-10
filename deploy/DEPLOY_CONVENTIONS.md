# Deploy Conventions — personal-workspace monorepo

> Source of truth for code: **GitHub** (`cvolkernick/personal-workspace`).
> FitDash production: **Vercel from `master`**. FCC / treasury live: **`work/treasury`** on Pi (never pull `master` or `work/holistic` onto that checkout). See `deploy/product_branch_map.py`.

## Project map

| Vercel project | Repo dir (root directory) | Ignored Build Step |
|---|---|---|
| `fitdash` | `resistance-dashboard` | `git diff --quiet HEAD^ HEAD -- ./resistance-dashboard` |
| `mikrafts` | repo root (output: `mikrafts`) | skips unless `mikrafts/` changed (and not on `work/treasury`) |

Both projects deploy from this **one monorepo**. Every push fans out to both Vercel projects.

## Rules

### 1. Deploy via git push only
- Push to the linked repo and let Vercel's Git integration build. **Never run `vercel deploy` from a CLI**, especially not from a dirty worktree or the wrong directory — that is how the `fitdash` ERROR ("Root Directory does not exist") happened.
- If you must trigger manually, use the Vercel dashboard redeploy button, not the CLI.

### 2. Keep changes scoped to your project's directory
- If you only touch `mikrafts/`, the `fitdash` build skips itself via its Ignored Build Step, and vice versa. Cross-directory changes build everything — avoid unless intentional.

### 3. `CANCELED` from the Ignored Build Step is normal
- A deployment canceled with *"the Ignored Build Step command returned exit code 0"* means Vercel correctly skipped a build for an unrelated change. **Do not "fix" this. Do not redeploy.** It is not a failure.

### 4. Batch your pushes
- The Vercel plan allows limited concurrent builds. Rapid-fire pushes queue up and supersede each other, which looks like failures. Commit in batches, not one push per keystroke.

### 5. Preview deploys are fine, production is `master`
- Feature branches get preview URLs automatically. Only `master` promotes to production. Never force-push `master`.

### 6. If a deploy ERRORS, diagnose before retrying
- Read the build log first. Common causes: wrong root directory setting, missing files in the upload, broken ignore command. Retrying the same broken deploy changes nothing.

## Triage cheat sheet

| Vercel state | Meaning | Action |
|---|---|---|
| `READY` | Built and live | None |
| `CANCELED` (Ignored Build Step) | Unrelated change, skipped by design | None — do not redeploy |
| `CANCELED` (superseded) | A newer push replaced it | None — the newer build is the real one |
| `ERROR` | Build failed | Read logs, fix cause, then push a fix |
| Queued long time | Concurrent build limit | Wait; batch future pushes |

## Ownership
- Changes under `resistance-dashboard/` → fitdash owners
- Changes under `mikrafts/` → mikrafts owners
- Changes at repo root or shared dirs → coordinate; expect both projects to attempt builds

*Last verified: 2026-09-09. If project settings change in the Vercel dashboard, update this file in the same PR.*
