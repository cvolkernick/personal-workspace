---
name: harness-learn
unit: harness-learn.timer
plist: com.cvolkernick.harness-learn.plist
schedule: "Mon 16:00 UTC (Mac 12:00 America/New_York)"
host: mac+pi
trigger: timer
notify: "opt-in post_buzz"
quiet_vs_interrupt: "quiet (report only)"
source: repo
---

# harness-learn

Weekly read-only Grok Build /learn report (#575). Never applies.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
