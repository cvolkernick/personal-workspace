---
title: "Harness /learn routine"
tags: [ops, learn, harness, cron]
status: active
created: 2026-09-11
---

# Recurring harness /learn (#575)

Weekly **read-only** analysis of local Grok Build traces. Proposes create / fix / delete harness updates. **Applies nothing.**

Inspired by [aksheyd /learn](https://x.com/aksheyd/status/2097816493065154861). Interactive Grok Build `/learn` remains the human-driven map-reduce; this job is the scheduled, confirmation-gated cousin.

## Trace sources

| Reads | Never touches |
|-------|----------------|
| `~/.grok/sessions/<cwd>/<id>/summary.json` | Skill files in place |
| `…/chat_history.jsonl` (user turns + tool names) | `GROK_HOME/config.toml` values (tokens, env) |
| `~/.grok/skills/*/SKILL.md` and `bundled/skills/*/SKILL.md` | `GROK_HOME/learn/state.json` |
| MCP **names** in `config.toml` / `settings.json` | Memory / buzz mem |
| `slash-mru.json` command names | git |

Headless / subagent sessions are dropped. Secrets in prompts are redacted before the report is written.

## Report

Each run writes under `ops/learn/reports/<stamp>/` (gitignored):

- `report.md` — create / fix / delete tables with evidence counts + session ids
- `actions.json` — same items, every one `requires_confirmation: true`
- `summary.txt` — chat-ready one-liner
- `manifest.json` — coverage (seen / kept / dropped)

Deletion candidates use threshold **0 invocations in the scan window** (default 14 days) and are `ask` only — never auto-deleted.

Harness edits after confirmation go through **git PRs** ([#569](https://github.com/cvolkernick/personal-workspace/issues/569)). This CLI exits 2 on `--apply`.

## Schedule (weekly default, configurable)

| Host | Unit | When |
|------|------|------|
| Mac | `deploy/macos/com.cvolkernick.harness-learn.plist` | Monday 12:00 America/New_York |
| Pi | `deploy/units/harness-learn.timer` | `Mon *-*-* 16:00:00 UTC` |

Override days / grok-home / channel with `~/.config/personal-workspace/harness-learn.json` (see `config.example.json`). Cadence is the timer/`StartCalendarInterval`, not a hidden constant.

Traces live on the machine that sat the sessions (usually the Mac). The Pi timer is a no-op-with-empty-report if `GROK_HOME/sessions` is missing.

## Install

See `ops/INSTALL_HARNESS_LEARN.md`.

## Manual

```bash
python3 ops/learn/harness_learn.py --days 14 --out ops/learn/reports
# optional chat ping (still report-only):
python3 ops/learn/harness_learn.py --days 14 --post-buzz --channel db0e8f97-0c81-4976-b299-1c460b87134e
```
