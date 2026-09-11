---
title: "Install context backup + drift schedules"
tags: [ops, context, cron, deploy]
status: active
created: 2026-09-11
---

# Install context persistence (#580)

**Scripts:** `ops/context/persist.py`  
**Issue:** [#580](https://github.com/cvolkernick/personal-workspace/issues/580)

| Job | When |
|-----|------|
| backup | daily 07:00 America/New_York |
| drift + contradict (report-only) | daily 07:20 America/New_York |

Merge ≠ timer live. Git push only ([#569](https://github.com/cvolkernick/personal-workspace/issues/569)).

## Private remote (E1)

```bash
gh repo create cvolkernick/agent-context --private --description "Private agent home context. Do not make public."
```

Copy and edit config (chmod 600). **Never** point `remote` at this public repo.

```bash
mkdir -p ~/.config/personal-workspace ~/.cache/personal-workspace
cp ops/context/config.example.json ~/.config/personal-workspace/context-persist.json
chmod 600 ~/.config/personal-workspace/context-persist.json
# set remote to git@github.com:cvolkernick/agent-context.git
```

Dry-run, then one apply:

```bash
python3 ops/context/persist.py backup --config ~/.config/personal-workspace/context-persist.json
python3 ops/context/persist.py backup --apply --config ~/.config/personal-workspace/context-persist.json
```

## Mac (LaunchAgent)

```bash
mkdir -p ~/Library/Logs/personal-workspace ~/personal-workspace/ops/context/reports
cp deploy/macos/com.cvolkernick.context-backup.plist \
   deploy/macos/com.cvolkernick.context-drift.plist \
   ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.cvolkernick.context-backup.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.cvolkernick.context-drift.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.cvolkernick.context-backup.plist
launchctl load ~/Library/LaunchAgents/com.cvolkernick.context-drift.plist
```

Install the same backup unit on the **Hatch VM** (that is the home the audit named).

## Pi (systemd user)

```bash
mkdir -p ~/.config/systemd/user
cp deploy/units/context-backup.service deploy/units/context-backup.timer \
   deploy/units/context-drift.service deploy/units/context-drift.timer \
   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now context-backup.timer context-drift.timer
```
