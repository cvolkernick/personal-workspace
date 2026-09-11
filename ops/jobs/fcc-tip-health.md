---
name: fcc-tip-health
unit: fcc-tip-health.timer
plist: 
schedule: "~15m"
host: prism-gateway
trigger: timer
notify: "alert on fail"
quiet_vs_interrupt: "quiet if ok"
source: live-master
---

# fcc-tip-health

FCC git tip / branch-attachment assert.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
