---
name: workflow-scheduler
unit: workflow-scheduler.timer
plist: 
schedule: "~15m"
host: prism-gateway
trigger: timer
notify: "none"
quiet_vs_interrupt: "quiet"
source: live-only
---

# workflow-scheduler

Workflow dashboard scheduler.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
