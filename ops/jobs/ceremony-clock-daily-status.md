---
name: ceremony-clock-daily-status
unit: ceremony-clock-daily-status.timer
plist: 
schedule: "09:00 America/New_York"
host: prism-gateway
trigger: timer
notify: "channel"
quiet_vs_interrupt: "scheduled"
source: live-only
---

# ceremony-clock-daily-status

Daily status ceremony.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
