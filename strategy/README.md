# Strategy — bets, daily focus, and requirements

Lightweight Markdown is the source of truth for macro strategy and the daily action plan. Orchestra (the command center) reads these files and renders them — no database.

| File | Role |
|------|------|
| `bets.md` | High-conviction thematic bets + dynamic domain weightings (slow-moving) |
| `today.md` | **Today's Focus** checklist — highest-leverage 2–5 open actions |
| `command-center-requirements.md` | Product requirements / related initiatives |

Related:

| Path | Role |
|------|------|
| `initiatives/*.md` | Structured initiatives (YAML frontmatter + `next_action`) |
| `initiatives/_TEMPLATE.md` | Copy this to add a new initiative |

## Edit Today's Focus

1. Open `strategy/today.md`.
2. Keep **2–5** open checklist lines under **Top Priorities Right Now**:
   ```markdown
   - [ ] **Short title** (optional detail). *Why this moves the bet: …*
   ```
3. Check boxes when done (`[x]`).
4. Refresh Orchestra — cards update from disk.

## View in the dashboard

```bash
# from monorepo root
python3 orchestra/server.py --port 8790 --no-browser
# open http://127.0.0.1:8790/  → Today's Focus section at top
curl -s http://127.0.0.1:8790/api/today | python3 -m json.tool | head
```

Or: double-click `open-command-center.command` / `python3 launch.py`.

## Add an initiative

```bash
cp initiatives/_TEMPLATE.md initiatives/my-new-initiative.md
# edit frontmatter: title, status, linked_bets, next_action
# optionally add a matching - [ ] line in strategy/today.md
```
