---
title: "Recurring job file mirrors"
tags: [ops, jobs, context]
status: active
created: 2026-09-11
---

# Job mirrors (#580 E3)

Each live cron/hook has `ops/jobs/<name>.md`. Adding or removing a job updates
the mirror in the **same change**. `persist.py drift` reports repo
`.timer`/`.plist` files with no mirror.

Do **not** copy OpenClaw/Hatch `jobs.json` — payloads have contained secrets.
This directory is the portable definition (schedule, purpose, notify, quiet vs interrupt).

| File | Source |
|------|--------|
| `harness-learn.md` | `repo` |
| `prune-stale-worktrees.md` | `repo` |
| `workspace-sync.md` | `repo` |
| `context-backup.md` | `repo` |
| `context-drift.md` | `repo` |
| `b2-puller.md` | `live-master` |
| `pi-heartbeat.md` | `live-master` |
| `fcc-tip-health.md` | `live-master` |
| `youtube-groom.md` | `live-master` |
| `auto-fleet-turo-writer.md` | `live-master` |
| `board-day-export.md` | `live-master` |
| `workflow-scheduler.md` | `live-only` |
| `ceremony-clock-eng-gate.md` | `live-only` |
| `ceremony-clock-daily-status.md` | `live-only` |
| `ceremony-clock-replenish.md` | `live-only` |
| `ceremony-clock-harvest.md` | `live-only` |
| `ceremony-clock-deep-groom.md` | `live-only` |
| `fund-manager-daily.md` | `live-mac` |
| `fund-manager-bp-poll.md` | `live-mac` |
| `platform-pipeline-digest.md` | `hatch-platform` |
| `platform-inbox-triage.md` | `hatch-platform` |
| `platform-price-watch.md` | `hatch-platform` |
| `platform-grok-limits-watch.md` | `hatch-platform` |
| `platform-landscaping-replies.md` | `hatch-platform` |
| `FEED_PROMPT.md` | hatch-platform |
