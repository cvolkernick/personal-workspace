---
name: workspace-sync
unit: workspace-sync.timer
plist: 
schedule: "every 5m (prism/finley)"
host: prism+finley
trigger: timer
notify: "none"
quiet_vs_interrupt: "quiet"
source: repo
---

# workspace-sync

git pull of personal-workspace on the Pi.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
