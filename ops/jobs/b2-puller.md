---
name: b2-puller
unit: b2-puller.timer
plist: 
schedule: "hourly :20 America/New_York"
host: finley-gateway
trigger: timer
notify: "unit logs"
quiet_vs_interrupt: "quiet"
source: live-master
---

# b2-puller

Pull books/units from prism. Pulse lock: no extra B2 clock.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
