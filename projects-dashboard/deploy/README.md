# Workflow scheduler on Raspberry Pi (24/7)

The Mac sleeps; the Pi should not. Move **schedule authority** (groom pre-step + tick) to a Raspberry Pi so approved backlog work is prepared on a reliable cadence.

## Architecture

| Layer | Role |
|--------|------|
| **Pi (systemd timer)** | Every 15m: `run_scheduler.py tick` — auto-queue + initiate seeds |
| **Job store** | `ops/backlog/jobs.json` + reports (git-synced or shared clone) |
| **Grok Build** | Optional on Pi. If missing, jobs become **`pending_terminal`** |
| **Mac frontend** | Dashboard **Launch pending** claims those jobs → opens Terminal + Grok |

### Execution modes (`ops/backlog/scheduler.json`)

| Mode | Behavior |
|------|----------|
| **auto** (default) | Spawn Grok when Terminal+CLI available; otherwise queue `pending_terminal` |
| **queue** | Never spawn; always prepare seeds for Mac claim |
| **spawn** | Always try to start Grok (needs CLI; on Pi needs Grok installed) |

Recommended for Pi without Grok: `"backend": "raspi"`, `"execution_mode": "auto"` (or `"queue"`).

## Option A — Pi schedules, Mac runs Grok (recommended first)

1. Clone/sync `personal-workspace` on the Pi (git pull on a timer or after protect&push).
2. Deploy the systemd timer:

```bash
# From Mac monorepo root (SSH key access required):
bash projects-dashboard/deploy/install_remote.sh pi@YOUR_PI_IP
```

3. On the Pi, set config (or ship via git):

```json
{
  "enabled": true,
  "backend": "raspi",
  "execution_mode": "auto",
  "cron_expression": "*/15 * * * *"
}
```

4. On the Mac dashboard: **Launch pending on this Mac** when jobs show `pending_terminal`.

Keep local Mac cron **disabled** (or uninstall) so only the Pi ticks.

## Option B — Install Grok Build on the Pi

If Grok Build supports your Pi architecture (check current install docs for Linux aarch64):

1. Install Grok CLI on the Pi under `~/.grok/bin/grok`.
2. Set `"execution_mode": "spawn"` and optionally `"prefer_headless_spawn": true`.
3. Note: interactive `/goal` without a real TTY is limited — prefer Option A for full Terminal UX.

## Deploy script

```bash
bash projects-dashboard/deploy/install_remote.sh pi@192.168.x.x
bash projects-dashboard/deploy/install_remote.sh pi@host --dashboard   # also serve UI :8765
bash projects-dashboard/deploy/install_remote.sh pi@host --dir /home/pi/personal-workspace
```

Installs:

- `workflow-scheduler.service` + `.timer` (OnCalendar every 15 minutes)
- Optional `workflow-dashboard.service` on `0.0.0.0:8765`

### Logs

```bash
ssh pi@HOST 'journalctl -u workflow-scheduler -f'
ssh pi@HOST 'systemctl status workflow-scheduler.timer'
```

## Sync note

Pi and Mac must share the same `ops/backlog/` truth. Simplest path: both git remotes, pull on Pi before tick (timer unit runs `git pull --rebase` best-effort). For stronger consistency later: shared NFS/git server.
