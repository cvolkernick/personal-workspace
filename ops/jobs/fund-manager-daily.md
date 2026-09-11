---
name: fund-manager-daily
unit: 
plist: com.personalworkspace.fund-manager-daily.plist
schedule: "daily mid-session ET"
host: mac
trigger: LaunchAgent
notify: "FCC / journal"
quiet_vs_interrupt: "scheduled"
source: live-mac
---

# fund-manager-daily

Agentic RH fund manager daily review.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
