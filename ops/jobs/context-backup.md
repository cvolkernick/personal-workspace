---
name: context-backup
unit: context-backup.timer
plist: com.cvolkernick.context-backup.plist
schedule: "daily 07:00 America/New_York"
host: mac (Hatch VM too)
trigger: timer
notify: "failure → Buzz if post_buzz; always FAILURE.txt"
quiet_vs_interrupt: "quiet on success; interrupt on failure"
source: repo
---

# context-backup

Nightly allowlisted home-context commit to private agent-context repo (#580 E1).

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
