# Agentic fund manager — operating model

**Status:** `live_autopilot`  
**Schedule:** ~**1 automated review per trading day** (mid-session preferred)  
**Trade cap:** **none** — a review may hold or place many orders  
**Owner role:** **optional observer** — feedback **after** each pass when useful (not blocking)

## Design intent

| You want | How we handle it |
|----------|------------------|
| See agent thinking / debate | Decision log on **FCC** + JSONL + journal — full fleet rationale |
| Same process at any NAV | **Size-invariant:** research/rotate every deploy; only ticket size scales |
| Deploy where themes say | Not “top up held only” by default |
| Step in when needed | Optional **after-pass** feedback; `live:false` / withdraw capital to kill |
| Automated as possible | Scheduled runner (Pi/cron) + manual kickoffs use the **same** process |
| Not day-trading | Prefer mid-session; avoid open/close noise |

## Uniform process (every capital deploy / full review)

```text
Scout → Research/rotate → Thesis → Risk → Critic → Executor (if quorum) → Log brief
```

1. **Scout** — book, cash, held vs allowlist coverage, watchlist/research  
2. **Research/rotate** — required; consider held **and** unheld theme names; reject with reasons  
3. **Thesis** — best allocation *now* for 60/40 + themes  
4. **Risk** — size/concentration (small NAV = small tickets, not skipped process)  
5. **Critic** — block held-only inertia / weak cases  
6. **Executor** — agentic MCP only; log alternatives + votes  

**Forbidden shortcut:** deploy idle cash only into existing positions without documenting alternatives considered.

Workflows (preferred when available):

```text
/fund-manager-research
/position-deep-dive symbol=TICKER
```

## Owner feedback loop

| Timing | Expectation |
|--------|-------------|
| During pass | None — do not block |
| After pass | Optional; owner may request adjustments |
| Next pass | Apply prior feedback unless superseded |

## Where to watch

**Assistant-readable sink (no human courier, #736):** GitHub issue **#701** comments.

`append_decision` posts the full what+why on every logged run (deploy / rebalance / hold / error), including the first HOLD of the day. Same-day identical HOLDs stay quiet only after `_github.posted` is true; a token/network miss retries the #701 comment in place (no extra JSONL row). Marker: `<!-- fund-manager-decision schema=1 -->` plus a JSON block (`kind`, `summary`, `rationale`, `team_votes`, `actions`, `weights_before` / `weights_after`, `nav_usd`, `buying_power_usd`). `FM_DECISION_GITHUB=0` / `--no-github` keeps local JSONL only.

FCC → **Brokerage** → **Decision log / rationale** (Pi-local JSONL; producer is Pi only, #729)

Also:
- GitHub [#701](https://github.com/cvolkernick/personal-workspace/issues/701)
- `treasury/snapshots/fund_manager_decisions.jsonl` (Pi-local JSONL; also committed when dirty)
- `investment/fund_manager_journal.md` (origin `work/treasury` via GitHub API after every Pi run — #737 / #742; never `git commit` on the FCC live clone. workspace-sync pulls it down.)
- `investment/research/fund_manager_research_latest.md` (when research pass wrote one)

## Team

| Role | Orders? | Job |
|------|---------|-----|
| Scout | No | Book + coverage gaps |
| Thesis | No | Themes + rotate/allocate |
| Risk | No | Size / trade-or-not |
| Critic | No | Challenge / block |
| **Executor** | **Yes** | Place/cancel + full log |

Quorum: Risk + Thesis OK; Critic can force hold or size-down.

## Bias-weighting loop (#768)

New-money consider-share (not orders, not 60/40 sleeves). Same committee roles debate the ~8:49 ET digest's proposed deltas.

| Guardrail (Chairman 2026-09-15) | Auto-apply | Else |
|---------------------------------|------------|------|
| Single-symbol \|Δ\| | ≤ 2 pts | stage |
| Total \|Δ\| | ≤ 5 pts | stage |
| Pins (`consider_share.json`) | never | stage |
| 60/40 sleeve targets / band | never | stage |
| Thin / uncited signal | skip | — |

```bash
python3 -m treasury.bias_weight_loop --digest investment/digests/YYYY-MM-DD.md --apply
python3 -m treasury.bias_weight_loop --override SATA --delta 1 --why "Chairman SATA-above-STRC" --apply
```

SoT: `investment/consider_share.json` (pins + `loop_adjustments`). Staged queue: `investment/bias_weight_staged.json`. Minutes: `investment/fund_manager_journal.md`. Naka audits; Chairman signs staged moves and may override anytime.

## Unattended automation

```text
rh_refresh (~3h) → snapshot only

bp_poll (~15m, market hours, Pi):
  rh_refresh → rules review
    ├─ HOLD (cash=0 and BP=0, in band) → first of day → JSONL + #701 (no LLM)
    └─ any cash>0 or BP>0 → full research_rotate + team → Executor → JSONL + #701

daily (~12:30 ET weekdays, Pi): same team path as before
```

**Trigger rule:** free capital = **cash > 0 or BP > 0** (no %NAV floor).

```bash
python3 -m treasury.fund_manager --rules-review --notify
./treasury/fund_manager_daily.sh
./treasury/fund_manager_bp_poll.sh          # or FM_BP_POLL_FORCE=1 outside hours
```

**Producer = Pi only (#729).** See `treasury/deploy/FUND_MANAGER_PRODUCER.md`.
Mac launchd `com.personalworkspace.fund-manager-*` is retired (stale checkout
kept POSTing ntfy.sh after #704). Do not reload it.

## Kill switches

- `"live": false` in `investment/fund_manager.json`  
- Withdraw agentic capital  
- Disable timer / disconnect MCP  

## Watchlist & research

| Artifact | Role |
|----------|------|
| [`watchlist.json`](./watchlist.json) | Owner active-interest **public** candidates — not auto-buy |
| [`private_watchlist.json`](./private_watchlist.json) | Pre-IPO / private companies — **IPO/list monitor only**; not deployable |
| [`research/`](./research/) | Public deep-dives + portfolio research |
| [`research/private/`](./research/private/) | **Private deep-dives** (same process as public) + optional short briefs |
| `.grok/workflows/position-deep-dive.rhai` | Single-name deep dive (**deep-research** pattern: Plan → Research claims → Verify → Report + Critic) |
| `.grok/workflows/fund-manager-research.rhai` | Book + themes + candidates |

**Owner policy (2026-08-04):** Public watchlist entry = active interest for systemic deploys.  
1. **On owner add** → auto-queue deep-dive (no stuck `monitor`).  
2. **After dive** → status **`ready`** (default) unless explicit **`pass`**.  
3. **Each allocation assessment** → every `ready` name in the consider set; reject with reasons if not sized.  
4. **Refresh** deep-dives on ~90-day age / earnings / material news / drawdown.  
5. **Still never auto-buy**; strong theme bias; core allowlist preferred when RV favors it.

**Private lane (2026-08-06):** Separate from public watchlist — **same deep-dive process** as public stocks (owner).  
1. **Not** in deploy consider set; **no** private-market / secondary authority; **no** auto-buy.  
2. On owner add → **immediate private deep-dive** (`research/private/{ID}_deep_dive.md`) + thesis_fit/rank; optional short brief. Do not leave without homework.  
3. Refresh private deep-dives on ~90-day age / funding / material news / owner request.  
4. On listing / IPO → promote to `watchlist.json` → **fresh public** deep-dive → `ready` only after that dive (private dive is not a substitute).  
5. ~30-day catalyst monitor (S-1, funding, material news) in addition to deep-dive refresh.

## Strategy reminder

Modernized **60/40** (**60% stocks / 40% BTC & digital credit**) on **agentic account only**.  
Cash risk budget = deposits (cash account today). Full allowlist is the menu each deploy; held names are not privileged without a thesis case.

**Owner prefs:**
- **Digital credit (2026-08-04):** **Small bias within the ~40% stack** toward **STRC / SATA** — BTC-fundamental, high-yield, frequent-dividend positions (not cash). Yields ~2× typical USDC/USDG cash → prefer a real STRC/SATA seat over cash-like residual when deploying into the complex. Not MSTR-only by habit; not 40% all-credit. Skip only with a strong, logged Risk/Critic rebuttal (liquidity/structure/ticket-size/thesis).
- **STRK (2026-08-30):** Strategy Strike 8% convertible preferred is **watchlist/consider-set**, not preferred_core. Name it each deploy; reject unless residual-after-floors (or BP ≥ min_trade) plus a logged RV case versus held STRC/SATA. Same issuer as MSTR+STRC — not a hedge. Not Jack Mallers Strike; not Starknet; not JR-strcUSX.
- **Energy / nuclear (2026-08-31):** Owner-add **CCJ + BWXT** (Chronometer/Smith option A). BE stays the equipment seat with a **fuel-cost overlay**: Bloom burns NG/biogas; HH $8–10 is customer TCO, not a BE tailwind; turbine-slot scarcity can cut the other way. Name BE/CCJ/BWXT each deploy; reject size unless residual-after-floors (or BP ≥ min_trade). Not a basket. Not core. EXE not named — pass. Lock: nest `RESEARCH/CCJ_BWXT_CONSIDER_SET_LOCK_2026_08_31.md`.
- **Miners (2026-07-27):** Multi-miner diversification is intentional. Do not reject names for “miner overlap.” Diversify across MARA, IREN, CLSK, RIOT, WULF, etc.
