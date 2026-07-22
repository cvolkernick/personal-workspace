# Orchestra — top-level command center

Unifies **strategy**, **workflow/projects**, **finance/treasury**, **fitness/health**, **time-allocation**, and **IoT/home** into one orchestration interface.

Surfaces:

- **Today's Focus** (human plan) — prominent cards from `strategy/today.md`, with links to bets + initiatives
- **Recommended next actions** (automated) — merge of hygiene, high/medium synergies, and priorities
- Multi-domain status (file-based; optional live port probe)
- Cross-domain **synergies** (high preferred; medium fallback when none are high)
- Supporting streams: attention, raw priorities, day bridge
- **Source freshness** ages for treasury snapshot and ops backlog
- Deep-links / launch commands for every subordinate dashboard

## Today's Focus (daily planner MVP)

Source of truth stays lightweight Markdown (portable to any editor / Obsidian later):

| File | Role |
|------|------|
| `strategy/today.md` | 2–5 open checklist items for the next 24–48h |
| `strategy/bets.md` | Slow-moving north-star bets + domain weightings |
| `initiatives/*.md` | Structured initiatives (YAML frontmatter + next action) |
| `initiatives/_template.md` | Copy this to add a new initiative |

The UI hero section renders open items as cards (title / why / linked bets & initiatives). Use **Copy path**, **View source MD**, or open `/api/strategy/today.md`.

**Edit flow:** update `strategy/today.md` → Refresh the dashboard. Optional: ask Grok to rewrite today from initiative status.

## Launch (recommended)

From the monorepo root:

```bash
python3 launch.py
# or
python3 orchestra/server.py --port 8790
```

Double-click **`open-command-center.command`** (opens Orchestra).

- UI: http://127.0.0.1:8790/
- Full payload: http://127.0.0.1:8790/api/orchestra
- Today's Focus: http://127.0.0.1:8790/api/today
- Raw today MD: http://127.0.0.1:8790/api/strategy/today.md
- Raw bets MD: http://127.0.0.1:8790/api/strategy/bets.md
- Recommended actions: http://127.0.0.1:8790/api/recommendations
- Attention + freshness: http://127.0.0.1:8790/api/attention
- Health: http://127.0.0.1:8790/api/health

Add `?probe=1` to probe child server ports for live badges.

See `REVIEW.md` for architecture review and prioritized roadmap.

## Subordinate dashboards

| Domain | Port | Launch |
|--------|------|--------|
| Finance / financial-command | 8000 | `python3 financial-command/server.py` |
| Workflow / projects-dashboard | 8765 | `python3 projects-dashboard/server.py` |
| Time / holistic | 8770 | `python3 holistic/server.py` |
| IoT / home lights | 8780 | `python3 iot/server.py` |
| Fitness / resistance-dashboard | 8787 | `python3 resistance-dashboard/server.py` |
| **Orchestra (this)** | **8790** | `python3 orchestra/server.py` |

## Tests

```bash
python3 -m unittest discover -s orchestra/tests -v
```

Pure collectors/synergies/priorities/today-focus run against fixtures without child servers.

### Manual verify (Today's Focus)

1. `python3 orchestra/server.py --port 8790 --no-browser`
2. Open http://127.0.0.1:8790/ — hero **Today's Focus** shows open cards from `strategy/today.md`.
3. `curl -s http://127.0.0.1:8790/api/today | python3 -m json.tool | head -40` — structured open_items.
4. `curl -s http://127.0.0.1:8790/api/strategy/today.md | head` — raw markdown.
5. Edit an open checkbox in `strategy/today.md`, click **Refresh**, confirm the card updates.
