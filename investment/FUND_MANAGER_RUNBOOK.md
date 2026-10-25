# Agentic fund manager — operating model

**Status:** `live_autopilot`  
**Live trading paused by Chris 2026-10-25** (`"live": false`; buys paused until he resumes; Nakatoshi gives the go). Timers stay disabled. To go live: set live:true via a PR, then enable timers.  
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
  rh_refresh → rules review → should-buy (no network)
    ├─ settled cash < $5, before 10:00 ET, or a buy run already happened this ET day → no buy run
    └─ settled cash >= $5 (deposit settled) → ONE full research_rotate + team → gate → planner → adapter → JSONL + #701

daily (~12:30 ET weekdays, Pi): same team path; may buy if settled cash >= $5 and no buy run yet today
```

**Trigger rule (Nakatoshi 2026-10-24):** never buy just because cash > $0. A buy run needs **settled cash >= $5**
(`execution.broker_adapter.min_investable_usd`) and happens **at most once per ET day**, a guard shared by the daily run and bp-poll
(`python3 -m treasury.fund_manager_broker --should-buy`; ledger `days`). It keys off settled cash, not calendar dates: deposits
($75 on the 1st and 15th) can take ~6 days to settle.

```bash
python3 -m treasury.fund_manager --rules-review --notify
./treasury/fund_manager_daily.sh
./treasury/fund_manager_bp_poll.sh          # or FM_BP_POLL_FORCE=1 outside hours
```

**Producer = Pi only (#729).** See `treasury/deploy/FUND_MANAGER_PRODUCER.md`.
Mac launchd `com.personalworkspace.fund-manager-*` is retired (stale checkout
kept POSTing ntfy.sh after #704). Do not reload it.

## Kill switches

- `FM_BROKER_DISABLE=1` on the fund-manager units (adapter places nothing)  
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
- **STRK (2026-08-30):** Strategy Strike 8% convertible preferred is **watchlist/consider-set**, not preferred_core. Name it each deploy; reject unless residual-after-floors (or BP ≥ min_trade) plus a logged RV case versus held STRC/SATA. Same issuer as MSTR+STRC — not a hedge. Not Jack Mallers Strike; not Starknet.
- **Energy / nuclear (2026-08-31):** Owner-add **CCJ + BWXT** (Chronometer/Smith option A). BE stays the equipment seat with a **fuel-cost overlay**: Bloom burns NG/biogas; HH $8–10 is customer TCO, not a BE tailwind; turbine-slot scarcity can cut the other way. Name BE/CCJ/BWXT each deploy; reject size unless residual-after-floors (or BP ≥ min_trade). Not a basket. Not core. EXE not named — pass. Lock: nest `RESEARCH/CCJ_BWXT_CONSIDER_SET_LOCK_2026_08_31.md`.
- **Miners (2026-07-27):** Multi-miner diversification is intentional. Do not reject names for “miner overlap.” Diversify across MARA, IREN, CLSK, RIOT, WULF, etc.


## No-sell-to-rebalance order gate (#1093)

**Standing order (Chairman 2026-09-22/23):** the stocks book is held (TSLA/SPCX 50/50). The ~40% BTC + digital-credit
target is restored **only with new cash, using buys**. We never sell to rebalance.

Enforced in code, not just prompts:

| Layer | Where | What |
|-------|-------|------|
| Decision | `treasury/fund_manager.py` `rules_based_review` | Drift with no deployable cash (`min(cash, BP) < min_trade`) is `hold` / `drift_no_cash`, `need_llm=False`. Never `kind=rebalance`. `weights_after_expected` = what cash can buy. |
| Decision log | `append_decision` -> `guard_decision_entry` | Any sell-type/unclassifiable action row is rewritten `blocked_no_sell_guard`, record gets `blocked_sell` + marker, and a `blocked_sell` ops alert is posted to #701. A sell that already shows as executed is flagged `SELL EXECUTED PAST GUARD`. |
| Order path | `treasury/fund_manager_order_gate.py` `execute_intents` | The only submit path. Rejects sell / sell_short / short / option / crypto sell, rebalance-worded sells (never overridable), sells with no explicit non-rebalance purpose, unknown/conflicting sides, unknown amounts, unknown cash, blocked symbols, and buys beyond `min(cash, BP)`. Policy unreadable or `guardrails.no_sell_to_rebalance != true` => places nothing (not even buys). `live:false` => nothing submitted. |
| LLM run | `fund_manager_daily.sh`, `fund_manager_bp_poll.sh` | `grok --yolo` runs with `--deny MCPTool(robinhood-trading__place_*)` and friends (`grok_deny_args()`); deny rules still apply under `--yolo`. The LLM writes buy intents to `treasury/snapshots/fund_manager_intents.json`; the gate validates and submits. `cancel_*` stays allowed. No deny list => grok is not run. |
| CI | `treasury/tests/test_fund_manager_order_gate.py` | Fails if a `grok --yolo` call lacks the deny list, or order-building code appears outside the gate. |

**Sell override** (the only way a sell can ever pass): `investment/fund_manager_sell_override.json`, **committed by Chris**:

```json
{"overrides": [{"id": "2026-10-16-mara-risk-exit", "approver": "Chris", "symbols": ["MARA"],
  "max_qty": 1.0, "max_notional_usd": 50.0, "reason": "why",
  "approved_at": "2026-10-16T14:00:00Z", "expires_at": "2026-10-17T01:00:00Z"}],
 "total_max_notional_usd": 50.0}
```

* **Authenticated by git.** The gate honors the file only if it is tracked, clean in the working tree, byte-identical to the blob at
  git HEAD, and the last commit that touched it was **authored by Chris** (`cvolkern@gmail.com`; committer Chris or GitHub web).
  Untracked, staged-only, edited-after-commit, wrong-author, or not-a-git-repo => rejected (fail closed). Optional extra:
  `FM_OVERRIDE_TRUSTED_REF=origin/work/treasury` additionally requires the commit to be already pushed to that ref.
* **Spend is cumulative.** Each override `id` (required, unique; never reuse one) has `max_qty` / `max_notional_usd` that apply across
  ALL runs; spend is persisted in `treasury/snapshots/fund_manager_override_spend.json` (flock + atomic replace, charged before any
  submit; a dry run never charges; a corrupt ledger blocks every override sell). A **total** notional ceiling across all overrides in any
  rolling 24h window applies: min(`DEFAULT_TOTAL_CAP_USD`=100 in code, `total_max_notional_usd`, `guardrails.sell_override_total_cap_usd`).
  Config and the file can only lower it. A bare single-override object (with `id`) is also accepted.
* `expires_at` must be timezone-aware, in the future and <= 24h out. Covers non-rebalance purposes only (`risk_exit`, `stop_loss`,
  `tax`, `withdrawal`, `chairman_manual`); shorts, options, crypto sells and anything worded as a rebalance are never overridable.
* `rh_snapshot_sync.py` (the read-only snapshot refresh) also runs `grok --yolo` with the same deny list from `grok_deny_args()`.

**Buy-only, by design.** The fund manager never sells. **Any sell is a manual action Chris takes himself** in the Robinhood app
(the override file above only lets a sell pass the gate; the broker adapter rejects every non-buy, so nothing automated places it).

**Broker adapter:** approved orders go to the callable named by `FM_BROKER_ADAPTER=module:callable` (takes a `GatedOrder`). With none
configured, the gate logs `not_submitted_no_broker_adapter` and places nothing. The shipped adapter is
`FM_BROKER_ADAPTER=treasury.fund_manager_broker:submit` (config `execution.broker_adapter` + `sleeves` + `targets` in
`investment/fund_manager.json`). Policy (Nakatoshi 2026-10-24, Grok-approved): Agentic is ~60% stocks (TSLA/SPCX 50/50) and 40%
BTC + digital credit; hold, never sell; new cash fills the BTC + digital-credit sleeve first until it reaches 40%.

1. **Run planner** (`plan_intents`, exposed as `submit.plan`; the gate CLI runs it before gating; deterministic, no network):
   * Calendar: `market_calendar` (years 2026-2027, NYSE holidays + 1 PM early closes; source https://www.nyse.com/markets/hours-calendars,
     checked 2026-10-24, matches Nakatoshi's list). A date outside `market_calendar.years`, or a missing/malformed calendar, fails
     closed: `not_submitted_calendar_unknown` (plan, submit and `--should-buy`). **Add the next year's NYSE dates before Jan 1, 2028.**
   * Window: ET weekday, not a calendar holiday, `earliest_place_et` (10:00) <= now < `latest_place_et` (15:50; 10 min before an
     early close). Else `not_submitted_before_window` / `not_submitted_outside_market_hours`. Nothing is queued.
   * One buy run per ET day (shared with bp-poll/daily; same-day fills placed outside the adapter count): `skipped_already_bought_today`.
   * Allowlist: BTC sleeve STRC, SATA, STRK + floating MSTR, ASST, BITA, IBIT, MARA, RIOT, CLSK, IREN, WULF; stocks TSLA, SPCX.
     IBIT is the native-BTC stand-in (no spot BTC in this account) and the default for the floating 35%. MSTR has low preference
     through `low_preference.MSTR.until` (2026-11-30): prompt-level, noted in the plan when chosen.
   * Run size is deterministic, never the AI's call: run total = investable = min(settled cash, `max_run_notional_usd` $160, daily
     cap left), and ALL of it is invested (AI-proposed totals are ignored and noted). Under `min_investable_usd` ($5):
     `skipped_below_min_investable`. If the AI step errors, times out (`FM_GROK_TIMEOUT_S`, default 1800s), is missing, or proposes
     nothing valid, the run still happens with the floating 35% in IBIT (`floating_source` = `IBIT_fallback: ...`). Settled cash = an explicit settled/withdrawable field if the snapshot or
     broker has one, else min(cash, buying power, unleveraged buying power). Snapshot older than 6h: `not_submitted_book_unknown`.
   * Valuing the 40% test: snapshot market value / last price; for held symbols without one, live quotes via the RH
     `get_equity_quotes` tool (same `grok_mcp` client, read-only call, every `place_*` tool denied). Only if quotes fail: cost basis,
     logged as `valuation: cost_basis_fallback: ...` in the plan notes, run summary and the #701 entry.
   * Sleeve gate: BTC + digital-credit share of invested value under 40% => the
     whole run goes to the BTC sleeve; TSLA/SPCX legs are dropped as `not_submitted_sleeve_gate` and their dollars redirected. At or
     over 40%: BTC gets what keeps it at 40% after the run, stocks get the rest, split to move TSLA/SPCX toward 50/50.
   * BTC sleeve: STRC 25 / SATA 25 / STRK 15 of the BTC amount, always exactly (AI amounts for these are ignored; one off by more than
     max($1, 5% of the run total) is logged as `weights_repaired` in `repairs`). The LLM only splits the floating 35%, pro rata to
     its floating proposals (none valid => all IBIT).
     Legs over $75 spill into IBIT; legs under $1 fold into IBIT. The plan is recorded in the ledger (`runs`) with a `run_id`.
2. **submit** (per gate-approved leg): buy-only hard reject (any non-`GatedOrder`, sell/short/option/crypto, non-`buy` side); kill
   switches (`FM_BROKER_DISABLE=1`, `live`, `enabled`); the order must be a leg of today's recorded plan (`not_submitted_unplanned`);
   window; day guard; allowlist / $1 min / $75 per order / $150 per ET day; idempotency key (ET date + account + symbol + side + cents)
   not already placing/placed; broker: account match, live buying power and settled cash cover it, **any live buy created today ET that
   this run did not place => claim the day as `external` and `skipped_already_bought_today`**, open buy in the symbol or a matching recent
   buy => `skipped_duplicate_broker_order`, tradable + fractional, `review_equity_order` ok (a rejection is final for the run, not
   retried), then `place_equity_order` (market, dollar-based, DAY/`gfd`, regular hours, `extended_hours=false`). The ledger row is
   `placing` and the day is claimed before the place call; a timeout/crash is `place_error_outcome_unknown` and never retried.
3. **#701:** when anything was placed (or the planner dropped/repaired legs), the gate posts one decision entry via `append_decision`
   (kind `deploy` / `deploy_not_placed`): per-order symbol, $, qty, fill price (when the broker reports a fill), order id, status, plus a
   one-line run summary (incl. any cost-basis valuation or IBIT fallback) and the plan (`repairs`, `notes`, `btc_share_before`,
   `valuation`, `floating_source`).

**Terminal statuses:** `placed`, `rejected_non_buy_side`, `rejected_not_gated`, `not_submitted_live_false`,
`not_submitted_adapter_disabled`, `not_submitted_adapter_config`, `not_submitted_no_broker_client`, `not_submitted_review_failed`,
`not_submitted_broker_rejected`, `not_submitted_ledger_unavailable`, `not_submitted_unplanned`, `not_submitted_before_window`,
`not_submitted_outside_market_hours`, `not_submitted_calendar_unknown`, `not_submitted_sleeve_gate`, `not_submitted_book_unknown`, `skipped_below_min_investable`,
`skipped_already_bought_today`, `skipped_duplicate_idempotency`, `skipped_duplicate_broker_order`, `place_error_outcome_unknown`.
Plan statuses: `planned`, `weights_repaired`. Each order result carries `broker_reason` (+ `idempotency_key`, `order_id`).

**Client:** `FM_BROKER_CLIENT=grok_mcp` drives the producer host's existing `grok` + `robinhood-trading` MCP OAuth (no new
credentials), one headless call per step with a per-call allow/deny list (place call: only `place_equity_order` allowed). Unset =>
`not_submitted_no_broker_client`. `FM_BROKER_CLIENT=pkg.module:factory` injects another client (tests use mocks).

**Mon 2026-10-26 manual buy (Nakatoshi, ~10:15 ET, $7.62 into STRC/SATA/STRK/IBIT, outside the adapter).** If the adapter runs that
day it will not duplicate it: the first broker check sees those same-day fills, claims the day as `external` and returns
`skipped_already_bought_today` (later runs that day skip at plan time). Independently, settled cash after the manual buy is about
$7.62 - $7.62 = ~$0.00, under the $5 minimum, so both bp-poll (`--should-buy`) and the planner skip (`skipped_below_min_investable`)
until the next deposit settles (the 11/1 $75 deposit may take ~6 days to settle).

**Ship steps (prism; after the GitHub restore and the merge to `work/treasury`; timers stay off until step 5):**
1. Drop-in on both `fund-manager.service` and `fund-manager-bp-poll.service`
   (`/etc/systemd/system/<unit>.service.d/20-broker-adapter.conf`):
   `[Service]` + `Environment=FM_BROKER_ADAPTER=treasury.fund_manager_broker:submit`, `Environment=FM_BROKER_CLIENT=grok_mcp`,
   `Environment=FM_BROKER_GROK_BIN=/home/prism-agent/.grok/bin/grok` (optional: `FM_BROKER_GROK_TIMEOUT_S`, kill switch `FM_BROKER_DISABLE=1`).
2. Offline checks: `python3 -m treasury.fund_manager_broker` (config) and `python3 -m treasury.fund_manager_broker --should-buy`.
3. With approval: read + review-only smoke of `GrokMcpClient` (`get_account`, `get_recent_orders`, `get_tradability`,
   `get_quotes`, `review_order`; no place). If anything looks off, stop and report.
4. If the Mon 10/26 manual buy happened, confirm the ledger/broker view: `--should-buy` should report under $5 settled cash.
5. `sudo systemctl daemon-reload && sudo systemctl enable --now fund-manager.timer fund-manager-bp-poll.timer && systemctl list-timers | grep fund-manager`
