# Identity sync protocol (portable layer)

Spec: GitHub #792. Pilot: Nakatoshi. Frankenfit / Pulse / Helm / Forge stay gated until Naka AC4 passes on **both** runtimes.

This is a **sync protocol**, not a single source of truth that replaces either shell. Agent identity lives inside each harness. Nest git holds the **portable layer only**.

## Portable files (sync)

Under `docs/identity/`:

| Path | What |
|------|------|
| `ROSTER.md` | slug ↔ Bot seat ↔ Buzz Desktop name ↔ `mirrored\|bot-only\|buzz-only\|exec` |
| `<slug>/PROFILE.md` | persona, role, do/don't |
| `<slug>/INSTRUCTIONS.md` | standing instructions / locks |
| `<slug>/MEMORY.md` | dated role facts (append-oriented) |
| `_template/` | copy these when seeding a new slug |

**Never sync:** harness tools, permissions, connectors, session/chat transcripts, Bot `update_state` server memory as a whole, Buzz Desktop private state, credentials, live balances.

## Startup pull

1. Before role work, `git pull` (or GitHub MCP read) of `docs/identity/<slug>/`.
2. Load PROFILE + INSTRUCTIONS + MEMORY into the **local shell’s** working context.
3. Record `pulled_sha` (commit or content hash) for the write-back check.

## Write-back

1. After material role facts change (not after every chat turn), append/update MEMORY. Touch PROFILE/INSTRUCTIONS only when a lock or role definition changes.
2. Prefer a short-lived branch → PR. One PR per session write-back for the Naka pilot.
3. Tag each MEMORY entry with ISO timestamp + harness id (`bot` or `buzz`).

## Conflict handling (v1)

- **Default: last-write-wins** on the file as a whole, ordered by the ISO timestamp in the newest MEMORY entry (not wall-clock of the push alone).
- **No lock file in v1.** Soft rule: never dual-write the same slug within 60s. If push/PR conflicts: rebase/pull, keep both append blocks if both are dated facts, prefer later timestamp on contradictory PROFILE/INSTRUCTION lines.
- PROFILE / INSTRUCTIONS lines marked `Chris YYYY-MM-DD`: never overwritten by episode MEMORY; only human/Grok unlock edits those lines.
- Concurrent MEMORY edits: merge by concatenating non-duplicate dated blocks; drop exact duplicate lines.

## Outage / meters

- Bot weekly exhausted → Buzz still pulls last portable SHA and works in its shell.
- Mac down → Bot still pulls via GitHub MCP.
- Meters stay split: ops = Bot weekly; heavy eng = GrokBuild.

## Secrets

Portable files must not contain live credentials or token literals. CI: `scripts/tests/test_identity_portable_secrets.py`.
