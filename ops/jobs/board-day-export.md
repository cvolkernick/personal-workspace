---
name: board-day-export
unit: board-day-export.timer
plist: 
schedule: "~15m"
host: prism-gateway
trigger: timer
notify: "none"
quiet_vs_interrupt: "quiet"
source: live-master
---

# board-day-export

Board day export.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
