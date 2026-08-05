---
title: "Install stale worktree prune (Mac + Pi)"
tags: [ops, worktrees, cron, deploy]
status: active
created: 2026-08-05
---

# Install prune-stale-worktrees schedule

**Script:** `ops/prune_stale_worktrees.sh`  
**Engine:** `projects-dashboard/worktrees.py prune-stale|repair-areas`  
**Cadence:** daily **04:30** local (Mac LaunchAgent · Pi systemd user timer)

## What it does

1. `git fetch origin --prune`
2. Remove **clean** stale feature/temp worktrees (remote gone or fully on `origin/master`)
3. Repair **area** worktrees that sit on wrong/dead branches (`work/treasury`, etc.)
4. Never removes the main monorepo; never removes dirty worktrees

## Mac (LaunchAgent)

```bash
mkdir -p ~/Library/Logs/personal-workspace
# After script is on the main checkout path:
cp deploy/macos/com.cvolkernick.prune-stale-worktrees.plist \
  ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.cvolkernick.prune-stale-worktrees.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.cvolkernick.prune-stale-worktrees.plist
launchctl list | grep prune-stale
```

Manual run: `bash ~/personal-workspace/ops/prune_stale_worktrees.sh`

## Pi (systemd user timer)

```bash
mkdir -p ~/.config/systemd/user ~/personal-workspace/ops/logs
cp deploy/units/prune-stale-worktrees.service deploy/units/prune-stale-worktrees.timer \
  ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now prune-stale-worktrees.timer
systemctl --user list-timers | grep prune
```

Requires the monorepo at `~/personal-workspace` with the script present (pull/deploy master).

## Dry-run

```bash
bash ops/prune_stale_worktrees.sh --dry-run
```
