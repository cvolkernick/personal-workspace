---
name: platform-inbox-triage
unit: 
plist: 
schedule: "Hatch/platform (not exported)"
host: hatch-vm
trigger: hatch cron
notify: "platform"
quiet_vs_interrupt: "unknown until exported"
source: hatch-platform
---

# platform-inbox-triage

Inbox triage standing watch.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
