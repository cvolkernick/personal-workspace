---
name: ceremony-clock-replenish
unit: ceremony-clock-replenish.timer
plist: 
schedule: "12:00 America/New_York"
host: prism-gateway
trigger: timer
notify: "channel"
quiet_vs_interrupt: "scheduled"
source: live-only
---

# ceremony-clock-replenish

Replenish ceremony.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
