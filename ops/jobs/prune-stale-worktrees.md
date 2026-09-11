---
name: prune-stale-worktrees
unit: prune-stale-worktrees.timer
plist: com.cvolkernick.prune-stale-worktrees.plist
schedule: "daily (see unit)"
host: mac+pi
trigger: timer
notify: "none"
quiet_vs_interrupt: "quiet"
source: repo
---

# prune-stale-worktrees

Prune stale git worktrees.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
