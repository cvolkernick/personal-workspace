---
name: youtube-groom
unit: youtube-groom.timer
plist: 
schedule: "hourly"
host: prism-gateway
trigger: timer
notify: "ops/YOUTUBE_GROOM_HEALTH.md"
quiet_vs_interrupt: "quiet if ok"
source: live-master
---

# youtube-groom

YouTube groom queue.

**Rule:** change the live job and this file in the same commit (or the same Hatch change + git follow-up).
