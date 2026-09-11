# TOOLS.md — local quirks

Canonical copy. Restore to `~/TOOLS.md`.

## Hosts

| Role | Host | User | Notes |
|------|------|------|-------|
| Dev | Chris’s Mac | `cvolkernick` | Grok Build traces live here (`~/.grok/sessions`) |
| Prod apps | `prism-gateway` | `prism-agent` | dashboards, OpenClaw workspace (stale/large; do not backup secrets) |
| B2 puller | `finley-gateway` | `finley-agent` | hourly :20 ET pull **from** prism. Pulse lock: no extra B2 clock |

This Mac can SSH to prism/finley. Hatch/cloud agent VM is a **different**
home (`~/MEMORY.md`, `~/workspace/skills/`) — install `persist.py` there too.

## Git / worktrees

- Clone: `~/personal-workspace`
- Worktrees: `~/personal-workspace-worktrees/<area>/`
- `gh` identity: `cvolkernick` (repo + project scopes)

## Agent runtimes on this Mac

- **Grok Build / Buzz Forge:** `~/.grok`, Buzz CLI (`BUZZ_*`)
- **OpenClaw:** on prism/finley under `~/.openclaw/workspace` — not the Hatch VM
- Python 3.9 is the Xcode default (`/usr/bin/python3`). Use it for ops scripts
  unless a unit specifies otherwise.

## Buzz

- Home channel for platform work: `#workflow` `db0e8f97-0c81-4976-b299-1c460b87134e`
- Multiline `buzz messages send`: real newlines on stdin (`--content -`)

## Quirks

- `personal-workspace` is **public**. Private context → `cvolkernick/agent-context`.
- OpenClaw `cron/jobs.json` on prism has historically held **secrets in prompt
  payloads**. Never copy `jobs.json` into git; use `ops/jobs/*.md` mirrors.
