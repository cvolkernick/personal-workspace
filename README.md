# personal-workspace

Personal tracking, data, and planning workspace for cvolkernick.

## How to Open the Command Center (Recommended)

**Easiest:** Double-click **`open-command-center.command`** right here in this folder.

It will start a local server and automatically open the beautiful visual dashboard in your browser.

### Manual alternative
```bash
python3 -m http.server 8000
```
Then open: http://localhost:8000/dashboard/index.html

> The dashboard gives you a much nicer visual overview than reading the raw Markdown files or GitHub.

All real content lives in clean, git-tracked Markdown + data + images. The dashboard is just the friendly visual layer on top.

## Contents
- **strategy/** — High-conviction bets (Energy, Bitcoin, AI/Autonomy/Robotics), dynamic domain weightings, today's/this week's actionable micro plan (the bridge from macro to daily focus).
- **initiatives/** — Structured projects with next_action, linked_bets, status, etc. (the fuel for the daily plan).
- **fitness/** — PPL workout logs (push/pull/legs), charts, nutrition targets, Fitbit data (execution engine that supports energy for the bets).
- **investment/** — Portfolio positions, allocation, strategy & thesis (direct support for the Bitcoin + thematic bets).
- **iot/** — Wiz smart bulb control (entryway lights) + home systems.
- **dashboard/** — The visual command center (the synthesis layer that turns the above into focus and action).

## Editing Workflow
1. Edit the `.md` files (or CSVs/JSON) in your editor of choice.
2. Refresh the dashboard in the browser.
3. Use Grok in this TUI to help log sessions, update snapshots, add initiatives, or improve the dashboard HTML itself.

Everything stays versioned and simple.

See `dashboard/README.md` for launch details and philosophy.

*Last updated as part of building the visual command center.*