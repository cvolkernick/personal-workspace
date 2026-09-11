---
name: pi-heartbeat
unit: pi-heartbeat.timer
plist: 
schedule: "~1m"
host: prism-gateway
trigger: timer
notify: "none"
quiet_vs_interrupt: "quiet"
source: live-master
---

# pi-heartbeat

Pi heartbeat.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
