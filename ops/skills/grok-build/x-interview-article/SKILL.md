---
name: x-interview-article
description: >
  Conduct a multi-turn interview of Chris (ChrisV.btc) on a given topic, then
  write a long-form article as Grok the interviewer/author using his answers.
  Use for X/Twitter articles, interview pieces, "Grok interviews Chris" drafts,
  topic-to-article sessions, full article pipeline, or when the user runs
  /x-interview-article. Upstream: workflow x-topic-scout or x-article-pipeline
  (discover/approve topic), then x-interview-prep or pipeline mode with topic.
---

# X Interview → Article

Turn a topic into a publishable long-form piece for Chris’s X account by
**interviewing him**, then **writing as Grok** (the author who conducted the interview).

Subject: **Chris** (display names may include ChrisV.btc / ChrisV.btc⚡).
Author voice: **Grok** — interviewer and writer, not ghostwriter of Chris’s first person.

## When this skill is active

Load this playbook end-to-end. Do not skip the interview and invent a bio-style essay.
Do not write as Chris in the first person unless he explicitly asks for that variant.

## Defaults (user-chosen)

| Knob | Default |
|------|---------|
| Byline | Neutral — e.g. title + “Interview with ChrisV.btc” or “A conversation with ChrisV.btc”; no forced “Grok interviews…” branding unless it fits |
| Length | Fit the piece: typically ~800–1,800 words; shorter for tight takes, longer if the material earns it |
| Off-limits | None unless Chris marks something off-record in the interview |
| Platform | X long-form (markdown); ready to paste/polish |

## Phase 0 — Topic

1. If Chris already gave a topic/theme, lock it and confirm in one line.
2. If not, propose 2–3 sharp options (or ask one clarifying question) and lock a topic in ≤2 turns.
3. Optional: if a prep brief exists from workflow `x-interview-prep` (scratch/report or prior message), use it to sharpen questions — do **not** treat research as Chris’s views.

Output a one-line working frame:
`Topic: … | Angle: … | Why it matters now: …`

## Phase 1 — Interview (multi-turn)

Goal: enough raw material for a real piece — **thesis + 3–5 concrete beats** with stakes, story, or contrarian edge.

### How to ask

- **Rounds, not a questionnaire.** 1–3 questions per message; go deeper on strong answers.
- Prefer: stakes, personal stake, concrete examples, what most people get wrong, tradeoffs, what changed his mind, what he’d bet on.
- Follow the interesting thread; drop dead ends.
- If he says something quotable, note it and probe once more for the sharpest version.
- If he marks **off-record**, exclude it from the article entirely.

### When to stop interviewing

Stop when you can honestly check off:

- [ ] Clear point of view (not just “topic overview”)
- [ ] 3–5 concrete beats (stories, numbers, examples, decisions)
- [ ] Tension or disagreement with a common take (even mild)
- [ ] Something only Chris would say (voice, experience, judgment)

Then say you’re moving to draft (one short line) and write the article in the **same turn or the next**.

Do **not** wait for a fixed question count. Do **not** keep interviewing after the checklist is met unless he wants more depth.

## Phase 2 — Article draft

### Stance

- You are **Grok**, the author who interviewed Chris.
- Frame as conversation-based: what he said, how he thinks, where he pushes back.
- Use **short quoted or closely paraphrased lines** grounded in the interview. Never invent biography, credentials, or events he didn’t provide.
- You may add light connective tissue, structure, and context. Flag any factual claims that need a source if they didn’t come from him.

### Structure (adapt as needed)

1. **Hook** — concrete or contrarian; not “In a recent conversation…”
2. **Setup** — why this topic matters now (brief)
3. **Through-line** — Chris’s core claim, in your narration
4. **Beats** — 3–5 sections; each earns its place with his substance
5. **Pushback / nuance** — where he’s careful or disagrees with himself/others
6. **Closer** — one lasting line or implication; no corporate summary

### Voice

- Sharp, plain, opinionated where earned — not corporate PR, not hype-bro.
- Short paragraphs; scannable for X.
- Neutral byline block at top, e.g.:

```markdown
# <Title>

*A conversation with ChrisV.btc*
```

Or omit byline if the opening already establishes the interview frame. Riff later if he wants a house style.

### Length

Match the material. Prefer tight over padded. If thin on interview signal, ask 1–2 more questions instead of fluff.

## Phase 3 — Delivery & revision

1. Deliver the full markdown article.
2. Optionally add a short **Editor notes** section under a horizontal rule: open questions, risks, alternate titles (3 max) — only if useful.
3. Offer one revision pass (tighter, longer, sharper hook, different title). Don’t nag for more passes.

## Full production pipeline (skill + workflows)

Grok workflows **cannot nest** other workflows or run the live interview. The agent
(or Chris) glues the stages. Platform limit: approval cannot inject a new topic
into a resumed run’s args — approve in chat, then re-invoke with `args.topic`.

```
1) DISCOVER   workflow x-topic-scout
              OR x-article-pipeline  (no args.topic)
              → ranked shortlist + recommended topic

2) APPROVE    Chris: approve recommended | pick rank N | reject
              (in Buzz/chat — not an automatic workflow edge)

3) PREP       workflow x-interview-prep  args.topic=<approved>
              OR x-article-pipeline      args.topic=<approved>
              → interview prep brief

4) INTERVIEW + ARTICLE   this skill /x-interview-article
              → multi-turn interview → draft as Grok
```

### When Chris says “full pipeline” / “find a topic and write an article”

1. Run **`x-article-pipeline`** or **`x-topic-scout`** (discover mode).
2. Post the shortlist + recommended topic; **stop and wait** for explicit approve/reject.
3. On approve: run **`x-article-pipeline`** or **`x-interview-prep`** with `args.topic`.
4. Load prep brief into Phase 0/1 of this skill; conduct interview; write article.
5. On reject: re-run scout with `args.exclude` or `args.seed` as he directs.

### Standalone prep

- Workflow **`x-interview-prep`** with `args.topic` when the topic is already chosen.
- Interview still rules the article; prep never substitutes for Chris’s words.

## Anti-patterns

- Essay that ignores the interview
- Fake quotes or inflated resume
- Wall of 15 questions in one message
- Ghostwriting as Chris (“I believe…”) unless he asks
- SEO sludge, listicle empty calories, “In conclusion”
- Publishing or posting to X unless he explicitly asks

## Quick start (agent)

On trigger with a topic already given:

1. Confirm topic frame (one line).
2. Start interview round 1 (2 strong questions).
3. Iterate until checklist met.
4. Draft article as Grok.
5. Offer one revision.
