# Agent context bootstrap

**Canonical file for this repo.** Everything that stores durable agent context
(facts, preferences, SOPs, skills, recurring jobs) is listed here so a fresh
machine can reconstruct it. Do not copy this file’s facts into a second
document — link here.

**Issue:** [#580](https://github.com/cvolkernick/personal-workspace/issues/580)  
**Audit date:** 2026-09-10 (ops). Files named in that issue on `master`
(`ops/context-persistence-audit-2026-09-10.md`, this path) were **not** in
git as of 2026-09-11 — this file is the committed bootstrap.

## Canonical locations

One canonical location per fact or SOP. Everything else **links**.

| Kind | Canonical | Copies / indexes |
|------|-----------|------------------|
| User identity (name, tz, address-form) | `ops/context/USER.md` | restore → `~/USER.md` / OpenClaw `workspace/USER.md` |
| Agent conventions | `ops/context/AGENTS_CONVENTIONS.md` + repo `Agents.md` | restore → `~/AGENTS.md` conventions section |
| Local tool quirks | `ops/context/TOOLS.md` | restore → `~/TOOLS.md` |
| Connector inventory (no values) | **this file §6** | nowhere else |
| Recurring jobs | `ops/jobs/<name>.md` | live systemd / LaunchAgent / Hatch DB |
| Feed prompt | `ops/jobs/FEED_PROMPT.md` | Hatch/platform prompt |
| Custom skills | `ops/skills/` | `~/.grok/skills/`, Hatch `~/workspace/skills/` |
| Deploy SOP | GitHub #569 + `ops/SDLC_MERGE_DEPLOY.md` (on `master`) | do not duplicate |
| Home memory | private backup remote (not this public repo) | `~/MEMORY.md`, `~/memory/` |

`cvolkernick/personal-workspace` is **public**. Personal memory, daily logs, and
home standing files do **not** belong on any branch of this repo (including an
orphan `context-backup` branch). See E1.

## E1 — backup target (team recommendation)

**Pick: B. Separate private repo `cvolkernick/agent-context`.**

| Option | Verdict |
|--------|---------|
| A. Orphan branch on this repo | **Reject.** This repo is public. Git history is forever. Personal memory in the same remote as product code. |
| B. Private repo | **Choose.** Independent ACL, `git clone` restore, no new crypto tooling. Exclude-list still required and tested. |
| C. Encrypted B2 | Defer. Existing B2 is the finley←prism **book puller** with an explicit pulse lock (“no off-site bucket / no extra clock”). Don’t overload it for agent home. Better later if we need purge/rotation. |

Nightly job: `ops/context/persist.py backup --apply`. Secrets refused on backup
**and** restore. Failure notifies (does not stay silent). Merge ≠ timer live.

## 1. Standing files (portable)

Versioned here; restored onto a fresh home:

- `ops/context/USER.md`
- `ops/context/AGENTS_CONVENTIONS.md`
- `ops/context/TOOLS.md`

Home copies (`~/USER.md`, OpenClaw `~/.openclaw/workspace/USER.md`, Hatch
`~/USER.md`) are **restored from** these, not the other way around.

## 2. Memory

- Long-term: `MEMORY.md` (home / OpenClaw workspace) — **private remote only**
- Daily: `memory/YYYY-MM-DD.md`
- Bank: `memory/bank/`
- Contradictions: `ops/context/persist.py contradict` (standalone, not folded
  into #575 `/learn` — `/learn` reads Grok traces, not MEMORY.md)

## 3. Skills

| Tree | Path in this repo | Auth |
|------|-------------------|------|
| Grok Build user skills | `ops/skills/grok-build/` | per `ops/skills/AUTH_SEAM.md` |
| Hatch custom (`github`, `vercel`, `x-post-reader`, `ynab`) | `ops/skills/hatch/` (import when the Hatch home is reachable) | hatch platform helpers — **rewrite on a foreign stack** except `x-post-reader` |

## 4. Recurring jobs

File mirrors: `ops/jobs/`. Adding or removing a live job updates the mirror in
the same change. Drift: `persist.py drift`.

## 5. Grok Build (this Mac)

| Reads | Never backup |
|-------|----------------|
| `~/.grok/skills/` (user) | `mcp_credentials.json`, `auth.json`, `config.toml` values |
| `~/.grok/memory/` | `sessions/` (traces — huge; #575 `/learn` reads them in place) |
| `~/.grok/workflows/` | `bundled/` |

## 6. Connector inventory (no values)

Verified 2026-09-11 from this Mac’s Grok `config.toml` **table names** plus the
Buzz session MCP roster. **No tokens, no client secrets, no wallet dumps.**

| Service | Purpose | Auth method | Skill / pointer |
|---------|---------|-------------|-----------------|
| GitHub | issues, PRs, repo | `gh` as `cvolkernick`; Buzz `github` MCP | Hatch `github` skill needs auth rewrite |
| Vercel | FitDash / mikrafts deploys | Buzz `vercel` MCP; **deploy via git only** (#569) | Hatch `vercel` skill needs auth rewrite |
| Gmail | mail | Buzz `gmail` MCP OAuth | — |
| Google Calendar | calendar | Buzz `google_calendar` MCP OAuth | — |
| Google Drive | files | Buzz `google_drive` MCP OAuth | — |
| Calendly | scheduling | Buzz `calendly` MCP | — |
| Coinbase | brokerage | Coinbase CLI env / MCP (CDP key **not in git**) | `ops/skills/grok-build/coinbase/` |
| Robinhood | brokerage + agentic fund | MCP OAuth (`mcp_credentials.json` — excluded) | `ops/skills/grok-build/robinhood-agentic/` |
| YNAB | budget | token **not in git**; Hatch skill needs rewrite | Hatch `ynab` |
| Tasks / gtasks | tasks | Buzz `tasks` + Grok `gtasks` MCP | — |
| Blender | local 3D | Grok `blender` MCP stdio | — |
| Buzz / Nostr | collaboration | `BUZZ_*` env on the agent runtime | Buzz CLI |
| DoorDash | orders | `dd-cli` | `ops/skills/grok-build/dd-cli-usage/` |
| X (Twitter) | public read | public API — portable | Hatch `x-post-reader` (no auth) |
| Secure Vault | secrets | platform-bound; **re-auth; never backup** | — |
| Wallet | keys | platform-bound; **re-auth; never backup** | — |

Re-auth on a new stack is expected for every row except public X read.

## 7. Known SoT drift (E8)

| Pair | Cadence | Notes |
|------|---------|-------|
| Dirty worktree files older than 24h | daily | E2 going-forward |
| `origin/master` vs `origin/work/treasury` `financial-command/` file list | daily | PWA assets (manifest/sw/icons) present on **both** as of 2026-09-11. Remaining drift is **test files** (master has `test_grok_login.py`, `test_morpho_ltv_barometer.py`, `test_planned_actual.py`, `test_ynab_refresh.py`; treasury has `test_spectrum_nav_restore.py`). Tracked — not silently ignored. Do not merge treasury from the workflow branch. |
| `ops/jobs/` vs `deploy/units/*.timer` + `deploy/macos/*.plist` | daily | |
| MEMORY.md vs `memory/` facts | weekly | `persist.py contradict` |
| Deployed Vercel SHA vs git HEAD | weekly / optional | git-push deploys (#569); no CLI `vercel deploy` |

## 8. Restore a fresh VM

See `ops/RESTORE_AGENT_CONTEXT.md`. Source of home context is the **private**
repo, not this public tree. This public tree supplies the tool, exclude-list,
job mirrors, and skill SKILL.md surfaces.
