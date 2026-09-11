---
name: auto-fleet-turo-writer
unit: auto-fleet-turo-writer.timer
plist: 
schedule: "~15m"
host: prism-gateway
trigger: timer
notify: "unit logs"
quiet_vs_interrupt: "quiet"
source: live-master
---

# auto-fleet-turo-writer

Turo writer for auto-fleet.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
