---
title: "Improve command center daily action planner (Today's Focus)"
status: active
linked_bets: ["AI/Autonomy/Robotics"]
priority_impact: high
next_action: "Add a prominent 'Today's Focus' section to dashboard/index.html that nicely renders strategy/today.md (and makes it easy to edit)"
energy: medium
target_date: "2026-06-10 or ongoing"
domain_weighting_context: "Agents / AI tooling (currently high because this directly advances the AI/Autonomy/Robotics leverage bet and reduces synthesis friction across all domains)"
---

## Description
Evolve the command center from a good visual reader into the place where macro strategy (the bets + dynamic domains) becomes a trustworthy, low-friction daily action plan. The key is a synthesized "Today's Focus" view that surfaces the highest-leverage next actions without the user having to manually re-synthesize scattered notes every time.

This initiative itself is meta: building the tool that helps execute on the other bets.

## Current Next Action
Add / enhance the rendering of `strategy/today.md` in the dashboard HTML (prominent section, nice cards for the top items, easy visual link back to the source bets and initiatives). Make "Add new initiative" guidance point to creating a real structured MD like this one.

## Progress / Wins
- [x] Requirements gathered via Socratic process (user confirmed direction and that Today's Focus list is the single most valuable first slice).
- [ ] First implementation of the Today's Focus rendering + supporting MDs (this file + bets.md + today.md skeleton).
- [ ] User starts using the new flow for at least one real day of planning.

## Notes / Ideas
- Keep the source of truth as lightweight MD (frontmatter + body) so it's editable in any editor and portable to Obsidian later if desired.
- The dashboard HTML can fetch and render it live (using the existing marked.js pattern) when served over HTTP.
- Over time this can become more automated (Grok proposes updates to today.md based on initiative status changes).

See the parent requirements doc in `strategy/command-center-requirements.md` for the full context and other related initiatives.