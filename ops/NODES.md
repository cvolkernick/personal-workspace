---
title: "Nodes: prism-gateway and finley-gateway"
tags: [ops, pi, nodes, tailscale, backup]
status: active
created: 2026-10-14
---

# Nodes

Verified 2026-10-14 ~03:30 ET. Tailscale MagicDNS short names are used
throughout; tailnet IPs and domains are left out on purpose.

| | prism-gateway | finley-gateway |
|---|---|---|
| Role | **prod**: all dashboards, schedulers, bots, public Funnel | **backup / secondary**: hourly pull of prism state, B2 graph, openclaw |
| Hardware | Raspberry Pi 4 Model B Rev 1.4, 4 cores, 3.7 GiB RAM, 29 G SD (72% used) | Raspberry Pi 4 Model B Rev 1.5, 4 cores, 3.7 GiB RAM, 58 G SD (18% used) |
| Temp / throttle | ~52–60 °C, `throttled=0x0` | ~44 °C, `throttled=0x0` |
| Service user | `prism-agent` (systemd `--user`) | `finley-agent` (systemd `--user`) |
| Checkout | `~/personal-workspace` on `work/treasury` (workspace-sync); master-only apps run from `~/personal-workspace-worktrees/workflow-master` | `~/personal-workspace` on `master` (workspace-sync) |
| Journal | persistent, `/etc/systemd/journald.conf.d/60-persistent.conf` (500M cap, 30 days) | same |
| Heartbeat | `pi-heartbeat.timer` (60 s) → orchestra `:8790/api/heartbeat` | `pi-heartbeat.timer` (60 s) + `pi-heartbeat-serve` → `finley-gateway:8797/api/heartbeat` |

## prism-gateway

### Units → ports

| Unit | Port | Notes |
|---|---|---|
| orchestra-dashboard | 8790 | serves `/api/heartbeat` |
| workflow-dashboard | 8787 | tailscale serve `:10001` |
| horizon-dashboard | 8795 (127.0.0.1) | tailscale serve `:10000`; Nodes card `/api/nodes` |
| resistance-dashboard (FitDash) | 8000 (127.0.0.1) | tailscale serve `:443` |
| financial-command | 8799 (127.0.0.1) | tailscale **Funnel** `:8443` (public) |
| gethelpfrom | 8802 (127.0.0.1) | tailscale serve `:10002`, noindex |
| b2-ux | 8792 | #1115 drop-in → master worktree |
| holistic-dashboard | 8770 | #1115 drop-in → master worktree |
| oomwoo-dashboard | 8798 | #1115 drop-in → master worktree |
| openclaw-gateway | 18789/18791/18792 (loopback) | |
| bland-relay, auto-fleet, panamerica-auto, life-compass-dashboard, google-tasks-mcp, plaid-bank-mcp | various (8765, 8780, 8793, 8796, 8797, 18800/18801) | |
| iot-dashboard | — | **inactive** (yellow in the watchlist) |

Timers: pi-heartbeat, workspace-sync, workflow-scheduler, fcc-tip-health,
board-day-export, solana-refresh, youtube-groom, grok-auth-keepalive,
prune-stale-worktrees, auto-fleet-turo-writer, ceremony-clock-* (eng-gate,
daily-status, deep-groom, replenish, harvest).

### #1115 drop-ins (reversible)

`~/.config/systemd/user/<unit>.d/1115-master-path.conf` for `pi-heartbeat`,
`oomwoo-dashboard`, `holistic-dashboard`, `b2-ux`. They repoint
WorkingDirectory/ExecStart at the `workflow-master` worktree, because those
entrypoints don't exist on `work/treasury`. b2-ux takes
`B2_VAULT_PATH=%h/personal-workspace/brain2` instead of `--vault`. The three
dashboards also get `StartLimitIntervalSec=600` / `StartLimitBurst=5` so a
regression can't crash-loop thousands of times again. Before the fix each had
about 9,400 restarts.
Rollback: delete the drop-in, then `systemctl --user daemon-reload && systemctl --user restart <unit>`.

`pi-heartbeat.service.d/1175-vitals-staged.conf` runs the #1175 build from
`~/.local/share/pi-heartbeat-1175` with `HEARTBEAT_ENV_FILE=/dev/null`, which
means no #701 posting yet. Delete it once #1175 is merged.

### Data folders

- `~/personal-workspace/treasury/snapshots/`, `~/personal-workspace/financial-command/treasury_latest.json`
- `~/personal-workspace/brain2` (B2 vault)
- `~/.local/share/youtube-groom/`, `~/.config/systemd/user/` (unit definitions)
- FitDash and GetHelpFrom app dirs under `~/apps/`, with dated `.bak-*` copies from each deploy
- `~/personal-workspace-worktrees/workflow-master/orchestra/data/heartbeat/` (heartbeat + alert state)

## finley-gateway

### Units → ports

| Unit | Port | Notes |
|---|---|---|
| b2 | 8792 (0.0.0.0) | `B2_GRAPH_PATH=~/B2`, which is currently **empty** (4 K) |
| openclaw-gateway | 18789/18791/18792 (loopback) | |
| pi-heartbeat-serve | 8797 (tailnet IP only) | read-only `/api/heartbeat` |

Timers: `workspace-sync.timer`, `b2-puller.timer` (hourly at :20),
`pi-heartbeat.timer` (60 s).

### Backup pull (b2-puller): **succeeding**

- `deploy/b2-puller/pull_from_prism.py` → `~/b2-pulls/prism/` (34 M).
- Last run 2026-10-14 03:20 ET: `Result=success`, exit 0. `MANIFEST.json`
  has `ok: true` and nothing skipped or refused. Next run 04:20 ET.
- Pulled: `treasury/snapshots/`, `financial-command/treasury_latest.json`,
  `.local/share/youtube-groom/`, `.config/systemd/user/`.
- Gaps:
  - The unit description mentions books, but **no book paths are pulled**.
  - The B2 vault (`brain2`) is not pulled either, so finley's `b2` graph is empty.
  - Run history before 2026-10-14 was lost because the journal was volatile until today.
- It is now watched continuously: the finley heartbeat watchlist marks
  `b2-puller` red if `MANIFEST.json` is older than 7800 s or `ok` is not true.

### Data folders

- `~/b2-pulls/prism/` (backup copy of prism state, see above)
- `~/B2` (b2 graph, empty)
- `~/personal-workspace/orchestra/data/heartbeat/`
