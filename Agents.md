# Agent Instructions (personal-workspace)

## Git / remote sync (standing rule)
- After any and all durable project changes, commit and push to `origin/master` without waiting to be asked.
- Never commit secrets (`.env`, OAuth tokens, `~/.config/**`).
- Rebase/pull if remote moved, then push; confirm `HEAD` == `origin/master`.
