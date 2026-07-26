# Agents & Tooling

**Hub:** [[00 Home - B2 Hub]] · **Bets:** [[Strategy & Bets]] (AI / Autonomy / Robotics)

## Purpose

Personal leverage through agents, dashboards, and small automations. B2 itself is part of this domain: a durable knowledge layer for every session and project.

## Core stack

| Tool | Role |
|------|------|
| **Grok / Grok Build** | Agent CLI; sessions under `~/.grok/sessions` |
| **B2 vault + UX** | Global KB; browse/search/Ask Grok on vault notes |
| **Orchestra** | Top-level multi-domain dashboard (port 8790) |
| **Subordinate UIs** | FCC, projects, holistic, IoT, resistance |
| **personal-workspace** | Git monorepo for all of the above |

## Auth pattern (Ask Grok)

Live model calls prefer:

1. `XAI_API_KEY` env (console.x.ai), or
2. SuperGrok session in `~/.grok/auth.json` (`grok login`)

B2 Ask Grok and resistance-dashboard Ask share this pattern. If credentials are missing, B2 falls back to an **offline grounded** answer from retrieved vault text only.

## Initiative themes

- Improve daily planner / Today's Focus synthesis (command center)
- Ship small automations that remove repeat friction (notes → structured MD, reviews, etc.)
- Keep scope tiny: hours, not multi-day rewrites

## Related

- [[Workflow & Projects]] — session index, worktrees, protect & push
- [[HOWTO - Using B2]] — Ask Grok over the vault
- [[Personal Workspace Map]] — ports and paths
