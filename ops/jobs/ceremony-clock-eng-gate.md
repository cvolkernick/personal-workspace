---
name: ceremony-clock-eng-gate
unit: ceremony-clock-eng-gate.timer
plist: 
schedule: "15m"
host: prism-gateway
trigger: timer
notify: "#workflow on pull"
quiet_vs_interrupt: "silent if empty Ready"
source: live-only
---

# ceremony-clock-eng-gate

Eng-gate Ready pull → #workflow dispatch.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
