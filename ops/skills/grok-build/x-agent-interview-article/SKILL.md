---
name: x-agent-interview-article
description: >
  Agent-to-agent X interview article: Grok interviews another channel agent
  (default Hotseat), then writes a long-form piece as the interviewer/author.
  Use when Chris wants two agents interviewing each other, Hotseat as guest,
  agent interview article, synthetic interview for X, or /x-agent-interview-article.
  Same pipeline as x-interview-article except the interviewee is an agent, not Chris.
---

# X Agent Interview → Article

Same production arc as `x-interview-article` (topic → multi-round interview →
article as Grok), but the **guest is another agent**, not Chris.

| Role | Who |
|------|-----|
| **Producer** | Chris (approves topic, can pause/redirect, final article owner) |
| **Interviewer + author** | **Grok** |
| **Interviewee** | **Hotseat** by default (or another agent Chris names) |

## Defaults

| Knob | Default |
|------|---------|
| Interviewer | Grok |
| Interviewee | Hotseat (`7cc8904182b6123a23cdae04c3fc8006c32afbe5a2a019496d4425ec44a0d168`) |
| Byline | Neutral — e.g. *A conversation with Hotseat* / *Interview: Grok × Hotseat* |
| Length | Fit the piece (~800–1,800 soft band) |
| Platform | X long-form markdown for Chris’s account |
| Human interview | Off — do **not** interview Chris unless he overrides |

Upstream topic discovery still works: `x-topic-scout` / `x-article-pipeline` /
`x-interview-prep`. Prep briefs feed questions; they never invent the guest’s views.

## Channel mechanics (required)

- Post interview rounds in the **same channel/thread** Chris started.
- Every question message to the guest **must** `@mention` them with exact display name
  and pass `--mention <guest-pubkey>` so they are notified.
- When the article (or a blocker) is ready, `@mention` Chris (callback) with the deliverable.
- Do **not** @mention Chris on every intermediate question — only for gate decisions,
  blockers, or finished draft.
- Wait for the guest’s real reply before the next round. Do not invent Hotseat’s answers.
- If the guest is silent after a reasonable wait or Chris pings, one polite re-ping; then report blocker to Chris.

## Phase 0 — Topic

1. Use Chris’s approved topic, or run the same discover/approve flow as `x-interview-article`.
2. Confirm in one line: `Topic: … | Guest: Hotseat | Interviewer: Grok`
3. Optional: load `x-interview-prep` brief for angles — label them as research hooks, not Hotseat’s opinions.

## Phase 1 — Interview (agent guest)

Goal: **thesis + 3–5 concrete beats** with stakes, examples, tension.

### How Grok asks

- **1–3 questions per round**, not a questionnaire dump.
- Address Hotseat by name; make the ask clear and answerable from their persona/role.
- Prefer: concrete practice, failure modes, tradeoffs, contrarian takes, what they’d bet on.
- Follow interesting answers; drop dead ends.
- If Hotseat marks **off-record**, exclude from the article.
- If Hotseat deflects or lacks lived detail, probe once for a concrete scenario; if still thin, note the gap for the article (honest framing > invented depth).

### Guest posture (what we expect from Hotseat)

Hotseat should answer **in character as themselves** (their agent persona / hot-seat role),
not as Chris and not as a generic LLM essay. Prefer opinions + examples over safe neutrality.

### When to stop

Checklist:

- [ ] Clear point of view from the guest
- [ ] 3–5 concrete beats
- [ ] Some tension / pushback / nuance
- [ ] Enough quotable lines for a real piece

Then announce move-to-draft (one line) and write.

Cap roughly **4–6 rounds** unless Chris asks for more depth. Don’t infinite-loop agents.

## Phase 2 — Article draft

### Stance

- Author: **Grok**, who interviewed **Hotseat**.
- Frame as agent-to-agent conversation grounded in the thread transcript.
- Quote / closely paraphrase **only** what Hotseat actually posted.
- Never invent Hotseat biography, credentials, or “Chris said via Hotseat.”
- Light connective tissue and context OK; flag unverified external claims.

### Structure

1. Hook  
2. Setup (why now)  
3. Through-line (guest’s core claim)  
4. Beats (3–5)  
5. Pushback / nuance  
6. Closer  

### Byline example

```markdown
# <Title>

*A conversation with Hotseat — interviewed by Grok*
```

### Voice

Sharp, plain, opinionated where earned. Scannable for X. Not corporate PR.

## Phase 3 — Delivery

1. Post full markdown article in-thread; **@mention Chris**.
2. Optional short editor notes (alternate titles, thin spots).
3. One revision pass if Chris wants it.
4. Do not publish to X unless Chris explicitly asks.

## Relation to human skill

| Skill | Interviewee |
|-------|-------------|
| `x-interview-article` | Chris |
| `x-agent-interview-article` | Hotseat (or named agent) |

Same scout/prep workflows. Different interview target and mention rules.

## Anti-patterns

- Interviewing Chris when this skill is active
- Fabricating guest answers or a fake transcript
- Writing the article before enough real guest signal
- 15-question dumps; model-rank bait; SEO sludge
- Forgetting to notify Hotseat (`@Hotseat` + `--mention`)

## Quick start (this thread’s pattern)

Topic already approved (e.g. harness / multi-agent OS wars):

1. Confirm guest = Hotseat, interviewer = Grok.
2. Round 1: @Hotseat with 2 strong questions.
3. Iterate on Hotseat’s replies until checklist met.
4. Draft article as Grok; @Chris with the piece.
