# Goal seed: Improve command center daily action planner (Today's Focus)

- **Backlog id:** `0cb116db-cfee-485e-b841-629a8593e61d`
- **Priority:** medium
- **Status:** in_progress
- **Area:** `orchestra`
- **Created:** 2026-07-17T06:14:42.157807+00:00
- **Initiated:** 2026-07-19T21:10:43.827530+00:00

## Problem / intent

The command center (Orchestra) already aggregates domains, synergies, and a merged priority/recommendation stream. What is still missing as a first-class daily surface is a **trustworthy, low-friction "Today's Focus"** view: open checklist items from `strategy/today.md` rendered as prominent cards with enough context to act, plus clear links back to bets and initiatives so the user does not re-synthesize scattered notes each morning.

This initiative is meta: the tool that helps execute on the other bets.

**Path clarification:** Backlog notes historically said `dashboard/index.html`. The live command center is **`orchestra/index.html` + `orchestra/server.py`**. MVP ships there.

## Users

- **Primary:** solo operator of personal-workspace who already maintains (or will maintain) lightweight MD under `strategy/` and `initiatives/`.
- **Secondary:** Grok/agents that update `strategy/today.md` or propose next actions; they need a stable payload shape (`today_focus` / open items) to reason over.

## Success criteria

- [x] Spec written (this file refined): problem, users, success criteria, non-goals, MVP, file layout, risks, area=`orchestra`.
- [ ] MVP implemented and runnable: prominent **Today's Focus** section in Orchestra UI with card-style open items from `strategy/today.md`.
- [ ] Happy-path verification: unit tests drive shipped collectors/payload; open checklist lines appear in API/payload fields the UI uses; server `/api/orchestra` consistent with workspace `today.md`.
- [ ] Source/edit guidance: visible paths to `strategy/today.md`, `strategy/bets.md`, `initiatives/`; "Add new initiative" points at structured initiative MD (frontmatter fields like title, status, linked_bets, next_action).
- [ ] Changes committed on `work/orchestra` and pushed via `git_workflow.py sync`.

## Non-goals

- Automated Grok rewrites of `strategy/today.md` from initiative status changes (future iteration).
- Full multi-user polish, auth, or remote hosting.
- Obsidian plugins or editor integration beyond portable MD on disk.
- Replacing the broader recommendations / coordinated action plan streams — Today's Focus is the primary *daily* slice, not a replacement for synthesis.
- Proving the human used the flow for a real calendar day (habit, not code).
- Live full-file `marked.js` dump of entire `today.md` (optional polish if cards + links already satisfy MVP).

## MVP scope

Smallest shippable slice:

1. **Collector:** Parse open checklist lines from `strategy/today.md` into structured focus items (title, optional why/context from italics or parentheticals, raw line, source path). Keep string list `today_open` for existing consumers.
2. **Payload:** Top-level `today_focus` object: `items` (structured cards), `count`, paths (`today_path`, `bets_path`, `initiatives_dir`), and short `add_initiative_guidance` for the UI. `counts.today_items` remains consistent.
3. **UI:** Prominent **Today's Focus** section near the top of `orchestra/index.html` (above or immediately after the single-next hero / before demoted streams): card grid for open items; source/edit copy; links to bets + initiatives; "Add new initiative" guidance naming the structured MD pattern.
4. **Tests:** Fixture with open checklist lines → assert those titles appear in `today_focus.items` / strategy signals via real `build_orchestra_payload` / `collect_strategy`.
5. **Strategy MDs:** Keep/refresh skeleton `strategy/today.md` + `strategy/bets.md` as source of truth (already present).

## File layout

| Path | Role |
|------|------|
| `strategy/today.md` | Source of truth for today's open checklist |
| `strategy/bets.md` | Macro bets (link target) |
| `initiatives/*.md` | Structured initiative MDs (template for "add new") |
| `orchestra/collectors.py` | Parse open items + structured today focus fields |
| `orchestra/payload.py` | Expose `today_focus` on orchestra payload |
| `orchestra/index.html` | Today's Focus section + cards + guidance |
| `orchestra/server.py` | Existing `/api/orchestra` (no new write API for MVP) |
| `orchestra/tests/test_orchestra.py` | Happy-path tests |
| `ops/backlog/seeds/improve-command-center-daily-action-planner-toda-0cb116db.md` | This seed/spec |

## Risks

- **Branch vs TLD:** UI/server belong on `work/orchestra`; `ops/` and `strategy/` are shared workspace paths — prefer orchestra worktree; seed progress updates may land with the orchestra commit or a follow-up on workflow branch.
- **Breaking consumers:** Keep `signals.today_open` as `list[str]` for synergies/priorities; add structured data alongside, not as a replacement of the string list.
- **Empty today.md:** UI must show empty state + guidance to edit `strategy/today.md`, not a blank hole.
- **Stale main checkout:** Main tree may be on another work branch; implement in `~/personal-workspace-worktrees/orchestra`.

## Progress / Wins

- [x] Requirements gathered via Socratic process (Today's Focus is the highest-value first slice).
- [x] Design/spec refined in this seed (area = orchestra).
- [ ] First implementation of Today's Focus rendering + structured payload fields.
- [ ] User starts using the new flow for at least one real day of planning (post-MVP / human).

## Next iteration steps (after MVP)

1. Optional full MD body preview of `today.md` via marked.js when served over HTTP.
2. Agent-assisted refresh: propose edits to `today.md` from initiative `next_action` + status changes.
3. Click-to-complete / write-back open checklist items (out of MVP).
4. Deeper initiative card links when today item text matches an initiative title/id.

## Notes / Ideas

- Keep MD as source of truth (frontmatter + body) for editor/Obsidian portability.
- Dashboard fetches payload live; cards are presentational over payload fields (no ranking logic duplicated in the browser).
- Parent requirements: `strategy/command-center-requirements.md`.

## Grok `/goal` objective

```
Backlog project: Improve command center daily action planner (Today's Focus)
… (see backlog item 0cb116db; implement MVP on work/orchestra)
```

## How to start

```bash
bash ops/backlog/seeds/improve-command-center-daily-action-planner-toda-0cb116db.launch.sh
# or: cd ~/personal-workspace-worktrees/orchestra && work on orchestra/
```
