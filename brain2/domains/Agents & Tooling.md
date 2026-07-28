# Agents & Tooling

**Hub:** [[00 Home - B2 Hub]] · **Identity:** [[Ikigai & Identity]] · **Bets:** [[Strategy & Bets]]

## Purpose of B2 for agents

B2 is the durable context foundation and aggregated **tribal knowledge** across domains. It is **not** a live data store.

## How agents should use this vault

- Always ground recommendations in the north-star ([[Ikigai & Identity]]) and the current primary bets ([[Strategy & Bets]]).
- Surface connections, synergies, complementary overlaps, and patterns across domains and prior efforts.
- Transfer lessons, approaches, and strategies from earlier projects to new ones.
- Help avoid repeating past mistakes.
- Treat capital, time, and energy as scarce; prefer capital-efficient and automatable paths.
- Never invent holdings, balances, or live operational numbers.
- Prefer durable policies, theses, and pointers over ephemeral session data.
- No secrets (keys, passwords, account numbers) in notes or prompts that write to B2.

## Kaizen Instruction for Agents

- Prefer the next smallest high-leverage improvement over large speculative leaps.
- Actively look for **muda** (waste) in AI systems, capital deployment processes, and energy use.
- When proposing changes to owner-acquisition AI, ASIC workflows, or agent tooling, frame them as **PDCA** experiments.
- Help keep B2 itself under continuous improvement.
- Full stance: [[Kaizen & Continuous Improvement]].

## Continuous improvement

B2 itself is a living system. Updates to these notes are part of the continuous improvement loop — same meta-theme as [[Strategy & Bets]], [[Kaizen & Continuous Improvement]], and [[Workflow & Projects]].

## Core stack

| Tool | Role |
|------|------|
| **Grok / Grok Build** | Agent CLI; sessions under `~/.grok/sessions` |
| **B2 vault + UX** | Global KB; browse/search/graph/Ask Grok |
| **Orchestra** | Top-level multi-domain dashboard (~8790) |
| **Subordinate UIs** | FCC, projects, holistic, IoT, resistance |
| **personal-workspace** | Git monorepo for execution code |

## Auth pattern (Ask Grok)

1. `XAI_API_KEY` env, or  
2. SuperGrok session in `~/.grok/auth.json` (`grok login`)  

Offline grounded fallback uses retrieved vault text only when live creds are missing.

## Initiative themes (tooling)

- Daily planner / Today's Focus synthesis
- Small automations that remove repeat friction
- AI-assisted owner acquisition for the vehicle fleet
- Keep scope shippable in hours when possible

## Related

- [[Kaizen & Continuous Improvement]]
- [[Ikigai & Identity]]
- [[Strategy & Bets]]
- [[Workflow & Projects]]
- [[Vehicle Rental Management Fleet]] — systems leverage on managed-owner channel
- [[HOWTO - Using B2]]
- [[Personal Workspace Map]]

---

*Updated 2026-07-26 from B2 seed interview.*
