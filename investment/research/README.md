# Position & portfolio research (agentic fund manager)

Multi-agent research artifacts for the **agentic** book: holdings, strategy fit, **watchlist**, and **candidate discovery**.

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

Documented in `investment/fund_manager.json` → `research` and in the daily prompt:

1. **Each daily review:** read latest research + watchlist (always).  
2. **When need_llm / thematic scan / capital change:** run `/fund-manager-research` (or inline equivalent).  
3. **Before first buy of non-core / watchlist name:** `/position-deep-dive symbol=SYM` required if `deep_dive_required_before_buy`.  
4. **Candidate adds:** research may **propose** watchlist symbols; merge into `watchlist.json` with status `monitor` only after Thesis/Risk agree (still **not** auto-buy).

## Outputs

| Path | Role |
|------|------|
| `investment/research/{SYMBOL}_deep_dive.md` | Single-name deep dive (verbose findings + conclusions) |
| `investment/research/fund_manager_research_latest.md` | Latest portfolio/strategy/watchlist research pass |
| `investment/watchlist.json` | Machine watchlist (monitor/ready/pass; not holdings) |

## Status vocabulary

| Status | Meaning |
|--------|---------|
| `monitor` | On watchlist; no size yet |
| `ready` | Deep-dive done; eligible for Thesis/Risk **proposal** only |
| `pass` | Researched; do not buy for now |
| `held` | Already in agentic book |
| `promoted` | Moved toward core allowlist (rare; update `fund_manager.json`) |

## Cadence

- **Recurring:** fund-manager-research on a periodic / need_llm basis.  
- **On demand:** position-deep-dive for any name under consideration (owner-added or agent-discovered).  
- Prefer before first buy when `deep_dive_required_before_buy` is true.
