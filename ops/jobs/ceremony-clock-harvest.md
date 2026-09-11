---
name: ceremony-clock-harvest
unit: ceremony-clock-harvest.timer
plist: 
schedule: "Mon 13:00 America/New_York"
host: prism-gateway
trigger: timer
notify: "channel"
quiet_vs_interrupt: "scheduled"
source: live-only
---

# ceremony-clock-harvest

Harvest ceremony.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
