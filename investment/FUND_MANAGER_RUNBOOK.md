# Agentic fund manager — operating model

**Status:** `live_autopilot` · **Cadence:** ~**1× per day** (mid-session preferred) · **Not** day-trading.

## Why rationale is logged

You want to **see how the team thinks**, spot mistakes, give feedback, and optionally tweet interesting decisions. Every decision should leave a trail:

| Artifact | Path | Use |
|----------|------|-----|
| Structured log | `treasury/snapshots/fund_manager_decisions.jsonl` | Machine-readable audit |
| Human journal | `investment/fund_manager_journal.md` | Readable history |
| FCC panel | Brokerage → **Decision log / rationale** | Ongoing monitor |

Each entry: summary, weights before, team votes, why now / why not alternatives, actions + order ids.

## Team (debate → single executor)

| Role | Orders? | Job |
|------|---------|-----|
| **Scout** | No | Book snapshot, weights, light market context |
| **Thesis** | No | 40/60 + allowlist / themes |
| **Risk** | No | Trade or not; size; capital bounds |
| **Critic** | No | Challenge weak ideas; can block / cut size |
| **Executor** | **Yes** | Only role that places/cancels on Robinhood MCP |

**Quorum:** Risk OK + Thesis OK; Critic can force hold or size-down. Scout is advisory.

## Cadence (your preference)

- **Once daily** active review is enough  
- Prefer **mid-session** (e.g. ~12:30 America/New_York)  
- **Avoid** first/last ~30 minutes of the session (open/close noise)  
- **Not** momentum / swing / day-trading; no high-frequency mandate  
- Missed day → next scheduled review (no catch-up spam)

Automation target: **Pi or always-on timer once/day in market hours** — not “when FCC loads.”

## What it optimizes

Modernized **40% BTC & digital credit** / **60% stocks** on the **agentic account only**.  
Risk budget = deposits into agentic. No per-trade human approval; you monitor via the log.

## Daily review loop (when automated or agent-run)

```text
1. Scout: MCP portfolio + positions + weights (fund_manager.py)
2. Thesis / Risk / Critic: debate → structured votes
3. If quorum fails → HOLD + log rationale
4. If quorum passes → Executor places/cancels + log full rationale
5. Refresh FCC snapshot; owner reviews decision panel
```

## Kill / feedback

- Soft pause: `"live": false` in `investment/fund_manager.json`  
- Hard stop: withdraw agentic capital / disconnect MCP  
- Feedback: note wrong calls in journal, or tell the agent next session (“don’t do X”)

## Bootstrap history

See decision log + journal for MSTR then MARA/TSLA/SPCX deploy after investor-profile gate cleared.
