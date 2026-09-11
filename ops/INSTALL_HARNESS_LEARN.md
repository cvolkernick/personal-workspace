---
title: "Install harness /learn schedule (Mac + Pi)"
tags: [ops, learn, cron, deploy]
status: active
created: 2026-09-11
---

# Install harness-learn schedule

**Script:** `ops/learn/harness_learn.py`  
**Cadence:** weekly **Monday 16:00 UTC** (Mac LaunchAgent 12:00 local while EDT · Pi systemd user timer)  
**Issue:** [#575](https://github.com/cvolkernick/personal-workspace/issues/575)

Read-only. Writes `ops/learn/reports/<stamp>/`. Never edits skills or config.

Optional config (copy and edit):

```bash
mkdir -p ~/.config/personal-workspace
cp ops/learn/config.example.json ~/.config/personal-workspace/harness-learn.json
chmod 600 ~/.config/personal-workspace/harness-learn.json
```

## Mac (LaunchAgent) — preferred (traces live here)

```bash
mkdir -p ~/Library/Logs/personal-workspace ~/personal-workspace/ops/learn/reports
cp deploy/macos/com.cvolkernick.harness-learn.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.cvolkernick.harness-learn.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.cvolkernick.harness-learn.plist
launchctl list | grep harness-learn
```

Manual: `python3 ~/personal-workspace/ops/learn/harness_learn.py --days 14`

## Pi (systemd user timer)

Empty report if this host has no `~/.grok/sessions`. Still valid as a visible cron.

```bash
mkdir -p ~/.config/systemd/user ~/personal-workspace/ops/learn/reports
cp deploy/units/harness-learn.service deploy/units/harness-learn.timer \
  ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now harness-learn.timer
systemctl --user list-timers | grep harness-learn
```

## Deploy

Git push only ([#569](https://github.com/cvolkernick/personal-workspace/issues/569)). Do not `vercel deploy`. Do not copy skills onto prod. Install the units after merge; do not treat a merge as “the timer is live.”
