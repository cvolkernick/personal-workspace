---
title: "GetHelpFrom.ai v1 product spec"
tags: [gethelpfrom, spec, funnel]
status: active
created: 2026-09-11
issue: 621
---

# GetHelpFrom.ai — v1 product spec

**Brand / domain:** GetHelpFrom.ai (canonical).  
**Issue:** [#621](https://github.com/cvolkernick/personal-workspace/issues/621)  
**This issue does not build the site.** It is the spec an engineer can estimate and implement from.

Kernel: visitor taps through a guided funnel → we infer AI-solvable pain → they confirm and rank a short list → we get a digest → we time-box a demo → close if it helps.

People will not name their problem. The product *feels it out* (v0.2). Free text is an escape hatch, never the front door.

## Canonical sources (no second SoT)

| Thing | File | Who reads it |
|-------|------|----------------|
| This spec | `products/gethelpfrom.ai/SPEC.md` | Humans |
| Questions, buckets, sub-areas, context taps | `questions.json` | Frontend |
| Scenario tap-cards + `pain`/`solution` map | `scenarios.json` | Frontend + scorer |
| Weights, taxonomy, solution catalog | `scoring.json` | Scorer |
| Lead payload | `digest.schema.json` | Ingest |
| Scorer | `score.py` | Server + tests |

`version` on each JSON is bumped on any edit. Deployed app embeds those three versions. **Git files win.** If `/api/canon` versions ≠ git SHA of those files, the build fails (CI) and prod serves a banner to ops (not to visitors). Nightly: `python3 products/gethelpfrom.ai/tests/test_canonical.py`.

Do not copy questions into CMS, Notion, or prompt text.

## Funnel (5 stages, ~3 minutes of taps)

| Step | Purpose | UI | Primary CTA | Skip / exit | Mobile |
|------|---------|-----|-------------|-------------|--------|
| Landing | Promise: "tap what's true, we'll suggest what to build" | Headline + 1 example card | Start | Footer links only | Thumb-reach CTA |
| Q0 Track | Personal / business / both | 3 large buttons | Continue | None (required) | Full-width buttons |
| Stage 1 buckets | Pick ≤3 heavy areas | Chip grid | Continue | None (need ≥1) | 2-col chips |
| Thin context | Hours, tools, trust, urgency | One question per screen | Continue | Skip allowed except hours | Likert as 5 buttons |
| Stage 2 sub-areas | For each bucket, 4–6 stings | Cards, multi-tap | Continue | Skip a bucket | Cards, 1 col |
| Stage 3 scenarios | 4 cards per sub-area: That's me / Sort of / Not me | Statement + 3 buttons | Continue | Skip card (counts as skip) | Sticky 3-button bar |
| Analyzing | 1–2s deterministic score | Progress copy, no fake "AI thinking" >2s | — | — | Same |
| Stage 4 shortlist | 3–5 problem → fix → why | Cards + Not relevant | These look right | Escape hatch: "none of these — tell us" | Cards |
| Stage 5 rank | Drag + must-have / nice-to-have | Rank list, max 8 after "show more" | Send my list | Must rank ≥1 | Up/down if drag is bad |
| Contact | After value | Name, email, optional phone, consent | Send | Save-and-resume via email | Native keyboard |
| Confirm | What happens next | "We'll read this and reply in 2 business days" | Done | — | — |

**Both tracks:** personal first, then business, one merged shortlist (tag each card Personal/Business).

**Abandon:** autosave session id in cookie + localStorage. After email capture, mail a resume link. Mid-survey abandon: no email, no chase. Do not gate Stage 4 behind email.

**Dispute:** Stage 4 "not relevant" removes that solution this session and writes `rejected[]` for the learning loop.

**Show more:** Stage 4 default 5. Once per session, expand to 8. Do not dump the catalog.

### Stage UX copy (build from)

- Stage 1: "Which areas feel heaviest?"
- Stage 3: "Does this sound like you?"
- Stage 4: "Here's what we think fits — do these sound right?"
- Stage 5: "Pick what you want. Rank by how useful it sounds. That's what we build first."
- Consent: "We'll send your list and may follow up about building the top picks. No newsletter."

## Question set

v0.1 (14+16 questions, free-text primary) is **superseded**. Remaining structured asks are `questions.json`:

1. Q0 track  
2. Stage 1 buckets (6 personal, 6 business)  
3. Thin context (role, hours, tools, trust, urgency, budget/worth)  
4. Stage 2 sub-areas (4 per bucket)  
5. Stage 3 is scenarios, not questions  

Branching = which Stage 2/3 packs load (chosen buckets × tapped subs). Self-employed on personal context: optional chip "also look at the business side?"

Anti-gaming: `questions.json.anti_gaming`.

Human tables: [`QUESTIONS.md`](QUESTIONS.md) (generated from `questions.json`). Frontend reads JSON only.

## Scenario library (core deliverable)

**Craft bar:** a stranger taps "That's me" without thinking. Not a category name. Present tense. One breath.

Pass: "I send an invoice and then forget to chase it for 3 weeks."  
Fail: "Invoicing inefficiency."

**Count:** 4 scenarios per sub-area (A/B 3 vs 5 later). Tap fatigue > coverage if we show 6+.

**Sources for v1 library**

| Source | What it is | Status |
|--------|------------|--------|
| Divergent persona round | 6 independent lenses (household+elder, inbox/calendar, health/learning, SMB sales/support, ops/money, marketing/crew) | This PR, `.scratch/persona-*.md` compiled into `scenarios.json` |
| Monorepo lived work | Auto-fleet / home services, FCC bills, FitDash habits, IoT wind-down | Informed solution catalog |
| Hallway tests | 5 humans per sub-area, not agents | **Required before funnel goes live** — not claimed done |

If all writers look like desk workers, the library is incomplete. Trade crew, shift workers, elders' kids, and gig drivers are in the seed set on purpose.

**Validation before live:** each sub-area needs ≥5 hallway taps. Retire or rewrite any scenario with That's-me rate < 15% *and* Not-me rate > 70% after n≥5. Log outcomes; do not silently down-weight only.

**"Not me" is training data.** Weekly review: rewrite/retire; bump `scenarios.json` version. Never silent weight-only changes for content failures.

**Where new scenarios come from after v1:** demo-call notes, "none of these fit" text (cluster monthly), support tickets. Human editor. Not an LLM writing production copy unsupervised.

## Scoring (simple)

Happy path: **no LLM**. `score.py` + `scenarios.json` map.

```
value        = Σ tap_score(scenario→solution) × hours_mult × urgency_mult
feasibility  = trust_mult × tool_hit
confidence   = answered_scenarios / shown_scenarios
score        = 0.5·value + 0.3·feasibility·10 + 0.2·confidence·10
```

Tap scores: That's me +2, Sort of +1, Not me −1, skip 0.  
Drop solutions with value taps < 1. Take top 5.

**Why line (required):** 1–2 sentences citing their taps: *We suggested this because that's you: "…"*.

**Cold start** (<3 scenario answers): show 3 generic high-feasibility cards (inbox_triage, followup_nudge, bill_pay_agent or speed_to_lead by track) + escape hatch. Digest flags `thin_session`.

**LLM boundary:** only Stage 4 escape-hatch text (and "Other"). One call, SpaceXAI `grok-4.5` (`XAI_API_KEY`, `https://api.x.ai/v1`), max 400 tokens, timeout 8s, $0.03/session cap. If it fails, keep the deterministic shortlist and store the raw text for humans. Never block the funnel on the model.

**Learning loop:** `rejected[]` + conversion (demo booked / closed) go to `ops/gethelpfrom/outcomes.jsonl` (git-ignored live file; sampled into repo quarterly). Weight changes are a versioned PR to `scoring.json`. No silent prod edits.

### Worked personas

Taps are in `tests/test_scoring.py`. Expected top solution is the assert.

1. **Maya** — dual income, kids, dad's EOBs. That's-me on late fees, EOB pile, school forms. Expect `eob_explainer` or `bill_pay_agent` in top 3.  
2. **Luis** — HVAC owner, Facebook lead sat 6 hours on a job, quotes unchased. Expect `speed_to_lead` or `quote_followup` in top 3.  
3. **Priya** — W-2, Slack+Gmail, "I'll reply later." Expect `inbox_triage` or `followup_nudge` in top 3.

## Internal digest

JSON must validate `digest.schema.json`. Also render `summary_md` for 60-second skim.

Delivery v1 (proposal):

| Channel | What |
|---------|------|
| Email | `summary_md` to ops alias (config, not hardcoded) |
| Repo drop | `ops/gethelpfrom/leads/{date}-{id}.json` on a **private** ops store, not `personal-workspace` master if PII |
| Alert | ntfy on new lead, 1/hour max |
| CRM | later; do not block MVP |

Dedupe: same email within 14 days → append session, don't new-lead spam.

**HITL:** every digest is read by a human before we spend build hours. No auto-accept.

Demo-prep (`demo_prep` in schema):

- `build_before_call`: stub against *their* tools if named; else screenshots of a cousin flow  
- `show_live`: one happy-path click  
- `ask_on_call`: data access, who approves sends, what must never automate (from trust Likert + optional later notes)

Example payload: `examples/digest-maya.json`.

## Demo → close

Motion: digest → **3–5 day** scoped prototype of **rank 1 must-have only** → live demo → written proposal → close.

**Site may promise:** "We'll show you a working slice of your #1 pick."  
**Site must not promise:** "We'll automate everything," "guaranteed ROI," "set and forget."

Pricing to test in MVP (pick in copy tests, not in the scorer):

| Option | Shape | Use |
|--------|-------|-----|
| A | Build fee for #1 + monthly retainer | Default |
| B | $X discovery credited to first build | If they hesitate on A |
| C | Outcome fee on a metric we instrument (e.g. lead reply <5 min) | Only if we can measure; not v1 default |

## Tech (MVP)

- **App:** Next.js App Router, Vercel (same family as FitDash/mikrafts).  
- **State:** session cookie + server rows (Turso or Postgres). Answers are JSONB.  
- **Score:** `score.py` ported to TS, or call Python in a tiny worker. Must match fixtures.  
- **LLM:** SpaceXAI only, server-side, escape hatch.  
- **Analytics:** funnel step events, per-question abandon, suggestion accept / not-relevant. No PII in analytics.  
- **Privacy:** store answers 90 days; delete on email request within 7 days; do not train foundation models on answers.  
- **Latency:** Stage 4 < 300ms without LLM. Escape hatch < 8s or skip.  
- **Cost guardrail:** LLM off unless hatch used; cap above.

Data model: `sessions`, `answers`, `recommendations`, `rankings`, `leads`, `outcomes`.

## Open questions (with a pick)

| Q | Pick |
|---|------|
| Exact domain | **GetHelpFrom.ai**. WHOIS `whois.nic.ai` 2026-09-11: **Domain not found** → treat as unregistered. Chris registers (human). Fallback only if the registrar disagrees: gethelpfrom.com if free. |
| One session vs two funnels | **One session**, sequential if Both. |
| Gate results on email? | **No.** Results then contact. |
| Auto-accept digests? | **No.** HITL every demo build. |
| Scenarios per sub-area | **4.** A/B 3 vs 5 after 50 sessions. |
| Stage 4 width | **5, expand once to 8.** |

## Out of scope

Building the site. Final legal copy. Final prices. Hallway tests (tracked as a follow-up issue before launch).

## Engineer build order (after this spec is approved)

1. Register domain (Chris).  
2. JSON-driven funnel shell (no LLM).  
3. Scorer + fixtures green.  
4. Digest email.  
5. Escape hatch last.
