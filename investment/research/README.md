# Position & portfolio research (agentic fund manager)

Multi-agent research artifacts for the **agentic** book: holdings, strategy fit, **watchlist**, and **candidate discovery**.

**UI:** [Watchlist research dashboard](../financial-command/watchlist.html) (from FCC → Fund manager → Watchlist research).

## Native Grok workflows (how invocation works)

These are **first-class Grok Build workflows**: Rhai scripts under `.grok/workflows/*.rhai`. They use the host APIs `agent()`, `parallel()`, `phase()`, `complete()` — the same system as `/workflows` in the TUI.

| Workflow | Command | Purpose |
|----------|---------|---------|
| **position-deep-dive** | `/position-deep-dive symbol=BE` | Single-name deep dive → `investment/research/{SYM}_deep_dive.md` |
| **fund-manager-research** | `/fund-manager-research` | Portfolio + strategy + watchlist + **propose new candidates** → `fund_manager_research_latest.md` |

Optional args:

```text
/position-deep-dive symbol=BE theme=energy
/fund-manager-research focus=energy max_candidates=5
```

### Why a coding agent might say “no workflow tool”

- **Interactive TUI / fund-manager sessions:** slash commands `/position-deep-dive` and `/fund-manager-research` (or `/workflow <name> …`) run the native host workflow engine. Progress shows under `/workflows`.
- **Some agent tool surfaces** (e.g. a general coding session) only expose tools like `read_file`, `spawn_subagent`, shell — **not** the host `workflow` API. In that case the team should either:
  1. Run the slash command in a Grok TUI session in this repo, or  
  2. **Emulate** the same phases inline (frame → parallel research → critic → write report) — as done for the first BE deep-dive — and still write under `investment/research/`.

**Going forward, the fund manager process should prefer the native workflows** when `grok` / TUI is available; fall back to inline multi-agent only if the host lacks `workflow`.

### Automation hooks

Documented in `investment/fund_manager.json` → `research` / `watchlist` and in the daily prompt:

1. **Each allocation assessment / daily review:** read latest research + watchlist; include every **`ready`** name in the consider set (reject with reasons if not sized).  
2. **Owner adds a public watchlist name:** immediately queue `/position-deep-dive symbol=SYM` (or inline equivalent); on completion set status **`ready`** unless explicit **`pass`**. Do **not** leave owner names stuck on `monitor` without homework.  
3. **Owner adds a private-watchlist name:** immediately run the **same deep-dive process** (pre-IPO adapted) → `investment/research/private/{ID}_deep_dive.md`. Status stays **`private`** (never deploy). Do **not** leave private names without homework.  
4. **When need_llm / thematic scan / capital change:** run `/fund-manager-research` (or inline equivalent).  
5. **Refresh:** re-run deep-dive when `last_deep_dive` is older than `deep_dive_refresh_days` (default **90**), or on material news/earnings/drawdown/funding/IPO catalyst, or before first buy if stale.  
6. **Agent-proposed candidates:** may merge as `monitor` → auto-queue dive → `ready` (still **not** auto-buy). Private agent proposals start `private` + deep-dive.

**Owner policy (2026-08-04 / 2026-08-06):** Public watchlist = active interest for systemic deploys. Private watchlist = IPO/list monitor only, but **same deep research standard** as public. Strong theme bias at size time; core allowlist preferred for routine rebalance. Research ≠ order.

## Outputs

| Path | Role |
|------|------|
| `investment/research/{SYMBOL}_deep_dive.md` | Single-name **public** deep dive (verbose findings + conclusions). Includes **reject** dives that never entered `watchlist.json` (PAVE, GRID — 2026-08-17 thematic-significance gate). |
| `investment/research/private/{ID}_deep_dive.md` | **Private** deep dive — same process as public (pre-IPO adapted); required on owner add |
| `investment/research/private/{ID}_brief.md` | Optional short one-pager; does **not** replace the private deep dive |
| `investment/research/fund_manager_research_latest.md` | Latest portfolio/strategy/watchlist research pass |
| `investment/watchlist.json` | Public machine watchlist (monitor/ready/pass; not holdings) |
| `investment/private_watchlist.json` | Private / pre-IPO monitor list (not deployable) |

## Status vocabulary

| Status | Meaning |
|--------|---------|
| `monitor` | Awaiting first deep-dive (agent-proposed or mid-dive). **Not** the steady state for owner-added names. |
| `ready` | Deep-dive done; **must** be in Thesis/Risk consider set each deploy; proposal-eligible only (not an order) |
| `pass` | Researched; do **not** propose size for now (explicit negative verdict) |
| `held` | Already in agentic book |
| `promoted` | Moved toward core allowlist (rare; update `fund_manager.json`) |
| `private` | Pre-IPO / unlisted — **private lane only**; never in public deploy consider set until listing + promotion |

## Cadence

- **On owner add (public):** position-deep-dive immediately → `ready` (default).  
- **On owner add (private):** private deep-dive immediately → stay `private` (never deploy).  
- **Periodic refresh:** deep-dive age &gt; 90 days or catalyst-driven (public and private).  
- **Recurring:** fund-manager-research on a periodic / need_llm basis.  
- **Every deploy:** consider all `ready` **public** watchlist names + core allowlist; never auto-buy; private names context-only.
