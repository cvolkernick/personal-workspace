---
name: ceremony-clock-deep-groom
unit: ceremony-clock-deep-groom.timer
plist: 
schedule: "Wed 12:00 America/New_York"
host: prism-gateway
trigger: timer
notify: "channel"
quiet_vs_interrupt: "scheduled"
source: live-only
---

# ceremony-clock-deep-groom

Deep groom ceremony.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
