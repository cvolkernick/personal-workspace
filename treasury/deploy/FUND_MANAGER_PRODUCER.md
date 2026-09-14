# Fund-manager producer — prism/Pi SoT (#729)

**Source of truth host: `prism` (Pi, `prism-agent@192.168.100.98`).**
Mac launchd `com.personalworkspace.fund-manager-daily` and
`com.personalworkspace.fund-manager-bp-poll` are retired. Do not run two
producers against the agentic book — dual team/LLM reviews can double-spend
free capital, and a stale Mac checkout still POSTs `ntfy.sh` (#704).

This producer **may place Robinhood agentic orders** (`live:true` + grok
`--yolo`). Snapshot producers (RH / Braiins / CB price) do not.

Alerts: GitHub standing issue [#701](https://github.com/cvolkernick/personal-workspace/issues/701)
only (`FCC_HOST_TAG=prism`). ntfy is retired (#704).

Journal (#737): after every run, `fund_manager_journal_sync` commits
`investment/fund_manager_journal.md` (and the JSONL when dirty) on
`work/treasury` with message `journal: fund-manager <kind> <YYYY-MM-DD HH:MM>`,
then `git pull --rebase --autostash` and push. Never force-push. Git
failure comments #701 and does **not** fail the review. Mac/manual runs
no-op unless `FM_JOURNAL_SYNC=1` (`FCC_HOST_TAG=prism` is the producer
gate). Requires Pi `git config user.email`. Network git (pull/push/fetch)
loads `~/.config/workflow-scheduler.env` via `load_scheduler_env` and uses
the same `x-access-token` insteadOf as `deploy/workspace_sync.sh` — do not
wait for a systemd `EnvironmentFile=` copy. Token is redacted on #701.

## Eng-gate sequence

| Step | Where | Gate | Stop if |
|------|-------|------|---------|
| **1** | Pi | Checkout at/after `dae37dc` (#704). `fund_manager.py` has no `ntfy.sh` / `push_ntfy` | Stale checkout still POSTs ntfy |
| **2** | Pi | Units use `User=prism-agent` and `/home/prism-agent/personal-workspace` (not `/home/pi/`) | ExecStart path 404 |
| **3** | Pi | `fund-manager.timer` (weekdays 12:30 ET) and `fund-manager-bp-poll.timer` (~15m) enabled | Timer missing; Mac still producing |
| **4** | Mac | `launchctl bootout` + `disable` both fund-manager agents; plists removed | Dual-writer still loaded |

## Install (Pi)

```bash
cd /home/prism-agent/personal-workspace
sudo cp treasury/deploy/fund-manager.service treasury/deploy/fund-manager.timer /etc/systemd/system/
sudo cp treasury/deploy/fund-manager-bp-poll.service treasury/deploy/fund-manager-bp-poll.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fund-manager.timer
sudo systemctl enable --now fund-manager-bp-poll.timer
systemctl list-timers | grep fund-manager
```

Do **not** `systemctl start fund-manager-bp-poll.service` while a Mac grok
review is still in flight — that is a dual executor.

Smoke (no orders; rules path only):

```bash
FM_SKIP_WEEKENDS=0 python3 -m treasury.fund_manager --rules-review --notify
# notify.github.posted or skipped; never ntfy.sh
grep -n ntfy.sh treasury/fund_manager.py && echo FAIL || echo ok-no-ntfy
```

## Mac disarm (after step 2–3)

```bash
UID_N=$(id -u)
launchctl disable gui/$UID_N/com.personalworkspace.fund-manager-bp-poll 2>/dev/null || true
launchctl disable gui/$UID_N/com.personalworkspace.fund-manager-daily 2>/dev/null || true
# Wait until any in-flight grok review exits, then:
launchctl bootout gui/$UID_N/com.personalworkspace.fund-manager-bp-poll 2>/dev/null || true
launchctl bootout gui/$UID_N/com.personalworkspace.fund-manager-daily 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.personalworkspace.fund-manager-bp-poll.plist
rm -f ~/Library/LaunchAgents/com.personalworkspace.fund-manager-daily.plist
```

Do not recopy `treasury/deploy/com.personalworkspace.fund-manager-*.plist`.
Those files are retired stubs.

## Why not "just git pull the Mac worktree"

The Mac launchd cwd is `~/personal-workspace-worktrees/treasury` (`work/treasury`).
That tree is a dirty live producer (snapshots + local config). Fast-forwarding
it while bp-poll is running mixes files under a live grok `--yolo` review.
Retire the jobs instead; Pi already has #704.

## Phone topic

`cvolk-grok-7f3k9x` is unused after Mac disarm. Unsubscribe in the ntfy app.
