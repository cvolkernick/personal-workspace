# Workflow scheduler on Raspberry Pi (24/7)

The Mac sleeps; the Pi should not. Move **schedule authority** (groom pre-step + tick) to a Raspberry Pi so approved backlog work is prepared on a reliable cadence.

## Architecture

| Layer | Role |
|--------|------|
| **Pi (systemd timer)** | Every 15m: `run_scheduler.py tick` — **groom → auto-queue → launch** |
| **Job store** | `ops/backlog/jobs.json` + reports (git-synced or shared clone) |
| **Grok Build** | Optional on Pi. If missing, jobs become **`pending_terminal`** |
| **Mac frontend** | Dashboard reviews `pr_ready`; **Launch pending** claims Terminal jobs |

`tick()` is the full autonomous cycle (no Mac dashboard required):

1. Groom ranks / schedule slots  
2. Auto-queue ready + `now`/`this_week` items (`auto_start=true`)  
3. Launch up to `max_per_tick` (agent / spawn / pending_terminal)  
4. Write jobs + reports; unit post-steps commit/push `ops/backlog`

### Execution modes (`ops/backlog/scheduler.json`)

| Mode | Behavior |
|------|----------|
| **auto** (default) | Spawn Grok when Terminal+CLI available; otherwise queue `pending_terminal` |
| **queue** | Never spawn; always prepare seeds for Mac claim |
| **spawn** | Always try to start Grok (needs CLI; on Pi needs Grok installed) |
| **agent** | Unattended branch → headless Grok → push → open PR (`pr_ready`) |

Recommended for Pi with Grok + `GITHUB_TOKEN`: `"backend": "raspi"`, `"execution_mode": "agent"`.  
Without Grok: `"execution_mode": "auto"` (or `"queue"`) so jobs wait for Mac Terminal.

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

## Option B — Full agent on Pi (branch → work → push → PR)

When the Pi has **Grok Build** + **GITHUB_TOKEN**, ticks run the unattended agent pipeline:

1. `git pull` master  
2. Create `work/auto/<slug>-<id>`  
3. Headless `grok --single --always-approve` against the backlog prompt  
4. Commit + push  
5. Open a GitHub PR  
6. Job status becomes **`pr_ready`** — shown on the Mac dashboard for review  

```bash
# Grok CLI (linux aarch64):
curl -fsSL https://x.ai/cli/install.sh | bash

# Preferred: device login ON THE PI (Pi owns refresh_token → auto-refresh works headless)
ssh prism-agent@PI
grok login --device-auth
# open the printed URL on any phone/laptop, enter the code, wait for "Signed in"

# Backup while Mac is awake (copies Mac session every 20 min):
bash projects-dashboard/install_mac_auth_sync.sh prism-agent@PI
# one-shot: bash projects-dashboard/sync_pi_grok_auth.sh prism-agent@PI

# Optional pay-as-you-go key (no OIDC; set in env file below):
#   export XAI_API_KEY=xai-...   # from https://console.x.ai

# Verify:
ssh prism-agent@PI 'export PATH=$HOME/.grok/bin:$PATH; grok --single "pong" --max-turns 1 --always-approve'

# GitHub token (chmod 600; never commit):
mkdir -p ~/.config
cat > ~/.config/workflow-scheduler.env <<'EOF'
GITHUB_TOKEN=github_pat_...   # fine-grained: Contents R/W + Pull requests R/W on this repo
PATH=$HOME/.grok/bin:/usr/bin:/bin
HOME=/home/prism-agent
# Optional alternative to OIDC auth.json:
# XAI_API_KEY=xai-...
EOF
chmod 600 ~/.config/workflow-scheduler.env
```

**Grok auth on Pi (why it broke before):** browser OIDC access tokens expire in hours. A one-time `scp` of Mac `auth.json` goes stale; headless ticks then fail with “Not signed in” and produce empty/scaffold PRs. Fix: **device-auth on the Pi** (native refresh) + optional Mac LaunchAgent sync.

**PR quality gate:** a job only becomes `pr_ready` when headless Grok **succeeds** and commits **implementation files** (not `ops/backlog/seeds/*` alone). Auth failure or seed-only diffs → `pending_terminal` / failed, no fake PR.


**Token permissions required for full autonomy**

| Permission | Why |
|------------|-----|
| **Contents: Read and write** | `git push` job branches |
| **Pull requests: Read and write** | Open PRs via API |
| **Metadata: Read** | Always required on fine-grained PATs |

Without Pull requests:write, the Pi can still implement + push; PR open will fail with 403 until the token is upgraded.

Without Grok, the agent step falls back to **`pending_terminal`**, which the Mac dashboard **auto-claims** on load (opens Terminal).

## Option C — Install Grok only for interactive spawn

1. Install Grok CLI on the Pi under `~/.grok/bin/grok`.
2. Set `"execution_mode": "spawn"` and optionally `"prefer_headless_spawn": true`.
3. Interactive `/goal` is still better on a Mac Terminal — prefer B or A for review UX.

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
