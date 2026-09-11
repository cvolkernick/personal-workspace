---
name: fund-manager-bp-poll
unit: 
plist: com.personalworkspace.fund-manager-bp-poll.plist
schedule: "~15m"
host: mac
trigger: LaunchAgent
notify: "none"
quiet_vs_interrupt: "quiet"
source: live-mac
---

# fund-manager-bp-poll

Buying-power poll for agentic fund manager.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
