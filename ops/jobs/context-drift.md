---
name: context-drift
unit: context-drift.timer
plist: com.cvolkernick.context-drift.plist
schedule: "daily 07:20 America/New_York; contradict weekly Mon 16:30 UTC"
host: mac+pi
trigger: timer
notify: "report file; optional Buzz"
quiet_vs_interrupt: "silent if clean"
source: repo
---

# context-drift

SoT drift (#580 E8) + dirty>24h (E2). Clean runs silent.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
