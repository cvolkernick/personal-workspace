# scripts/

| Tool | Purpose |
|------|---------|
| `run_ci_tests.py` | Unified Python + JS CI runner (#584). Canonical: `ops/github-workflows/test.yml` |
| `check_workaround_comments.py` | Fails on unexplained `TODO`/`FIXME`/`HACK`/`XXX`/`workaround` comments in live code unless they cite an issue `#N` (#1184). Runs in the CI suite via `scripts/tests`. |
| `workaround_comments.py` | Fails if a youtube-groom sidecar imports the writer module `youtube_groom` (AST, including `importlib.import_module`). Not a second marker scan. |
| `buzz-board` | Buzz Board (GitHub Project #1) CLI — `list`, `get`, `set-status N Done`, … |
| `eng_gate_post_merge.py` | After eng-gate merge: mark board Done / residual / sweep (#58) |
| `forge_daily_brief.py` | Daily outcomes brief from live GitHub labels and merged PRs (#983). Pi lines stay `last known <date>`. |
| `youtube_groom.py` | AI Curated house-cap policy (not the Pi writer; do not copy over `~/.local/lib/youtube-groom/youtube_groom.py`) |
| `youtube_groom_health.py` | Tick health from `groom.log` — #workflow alert to Grok + `health.json`. Copy **this** file to Pi alongside the writer, never over it. |
| `youtube_groom_tick_report.py` | Per-tick listed/add/skip/quota + 24h rollup (#838, not #759). Copy **alongside** the writer, never over it. |
| `x-analytics/twitter_metrics.py` | Weekly X metrics for @ChrisVolkernick (replaces the OpenClaw `x-weekly-analytics` cron; no LLM). Runbook: `scripts/x-analytics/README.md`; units: `deploy/units/x-weekly-analytics.*`. Copy to `~/.local/lib/x-analytics/` on prism. |

```bash
./scripts/buzz-board whoami
./scripts/buzz-board set-status 58 Done
python3 scripts/eng_gate_post_merge.py --pr 47
python3 scripts/eng_gate_post_merge.py --sweep
python3 scripts/youtube_groom_health.py --dry-run --json
python3 scripts/youtube_groom_tick_report.py --dry-run --json
python3 -m unittest discover -s scripts/tests -v
```

Runbook: `ops/ENG_GATE_BOARD_DONE.md`. Auth: `GITHUB_TOKEN` with `repo` + `project`.
