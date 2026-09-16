# Deploy conventions — standard operating procedure for all bot teams

> Migrated from issue #569 (closed 2026-09-16). Effective 2026-09-09.

All bot teams deploying from `personal-workspace` must follow this. No exceptions, no improvising.

## Why

`personal-workspace` is a monorepo. Every push fans out to **both** Vercel projects below. Recent deploy noise (mass canceled builds, one failed fitdash deploy) was caused by deploy hygiene, not Vercel limits.

## Project map

| Vercel project | Deploys from | Root directory |
|---|---|---|
| `fitdash` | `resistance-dashboard/` | `resistance-dashboard` |
| `mikrafts` | repo root | (output: `mikrafts/`) |

## The rules

1. **Deploy via `git push` only.** Never run `vercel deploy` from a CLI — especially not from a dirty worktree or the wrong directory. That is exactly how the fitdash ERROR ("Root Directory does not exist") happened.
2. **Keep changes scoped to your project's directory.** Touch only `resistance-dashboard/` for fitdash work, only `mikrafts/` for mikrafts work. Cross-directory changes trigger both builds.
3. **`CANCELED` from the Ignored Build Step is NOT a failure.** It means Vercel correctly skipped a build for an unrelated change. Do not redeploy. Do not "fix" it.
4. **Batch your pushes.** The Vercel plan has limited concurrent builds. Rapid-fire pushes queue up and supersede each other, which looks like failures.
5. **Production is `master`.** Feature branches get preview URLs automatically. Never force-push `master`.
6. **If a deploy shows ERROR, read the build log first.** Diagnose the cause (wrong root directory, missing files, broken ignore command) before pushing anything. Retrying the same broken deploy changes nothing.

## Triage

- `READY` → live, nothing to do.
- `CANCELED` (Ignored Build Step) → by design, nothing to do.
- `CANCELED` (superseded) → a newer push replaced it, nothing to do.
- `ERROR` → read logs, fix the cause, push a fix.
- Stuck queued → concurrent build limit, wait it out.
