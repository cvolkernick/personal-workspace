# B2 / knowledge graph on finley + prism puller (v1)

Repo-side first slice. Forge installs on **finley-gateway** later. This cloud
seat cannot reach the LAN — do not SSH from here.

## Roles (not hostnames)

| Hostname | Role tag | User | LAN | Tailscale |
|----------|----------|------|-----|-----------|
| `prism-gateway` | `app-books` | `prism-agent` | 192.168.100.98 | 100.67.114.2 |
| `finley-gateway` | `b2-puller` | `finley-agent` | 192.168.100.216 | 100.124.165.50 |

v1: B2 / knowledge graph primary on finley **:8792** **and** this puller on the
**same box**. Pull **FROM** `prism-gateway`. Write only what was pulled.
No replica, no off-site bucket, no machine rename. Role tags only:
`app-books` (prism), `b2-puller` (finley). Prism **queries** `:8792`.

Inventory: `deploy/b2-puller/hosts.json`.

## What lands on finley

| Unit | Port / cadence | Purpose |
|------|----------------|---------|
| `b2.service` | **:8792** | B2 / knowledge graph dashboard (`b2-ux/server.py`) |
| `b2-puller.timer` | **hourly :20 `America/New_York`**, all 7 days incl. overnight | One job PULLS books + youtube-groom + published + units **from** prism |
| `workspace-sync.timer` | 5m | git pull of this repo (not a pull clock; no FCC / FitDash / Orchestra units) |

**PULSE LOCK:** one systemd timer on finley (`b2-puller.timer` → `b2-puller.service`).
`OnCalendar=*-*-* *:20:00 America/New_York`. One job pulls book snapshots +
youtube-groom state + published copies + units. Do **not** add a prism
self-backup, venue keys, or a units-only timer. No off-site bucket in v1
(and it must not get its own clock later).

Graph root: `~/B2` (`B2_GRAPH_PATH`). Pull dest: `~/b2-pulls/prism`.
App Pi query: `http://finley-gateway:8792/api/health` (or Tailscale `100.124.165.50`).

## Install (Forge / operator on LAN)

```bash
# From a machine that can SSH to finley (not this cloud VM):
bash deploy/b2-puller/install_finley.sh finley-agent@192.168.100.216
# or MagicDNS:
bash deploy/b2-puller/install_finley.sh finley-agent@finley-gateway

# Already on finley:
bash deploy/b2-puller/install_finley.sh --local
```

Need passwordless SSH **from finley → prism**:

```bash
# on finley-agent
ssh prism-agent@prism-gateway 'echo ok'
# fallback: prism-agent@100.67.114.2
```

Override remote: `B2_PULL_REMOTE=prism-agent@100.67.114.2`.

Do **not** run `deploy/install_remote.sh` against finley — that installer is
app-books (FCC, FitDash, Orchestra, Auto Fleet).

## Pull list (source of truth: `deploy/b2-puller/paths.py`)

Pulled from prism `$HOME` into `~/b2-pulls/prism` on finley. Missing sources skip.
Write **only** what was pulled.

| Relative path | Why |
|---------------|-----|
| `personal-workspace/treasury/snapshots/` | book snapshots |
| `personal-workspace/financial-command/treasury_latest.json` | published book copy |
| `youtube-groom/state.json` | groom state |
| `youtube-groom/never_readd` | never-readd list |
| `youtube-groom/groom.log` | groom log |
| `.config/systemd/user/` | systemd user unit files |
| `.buzz/published/` | nest-published copies |
| `nest-published/` | nest-published copies (alt path) |

## Live-block / refuse

Never on the pull list, never written:

- Venue keys: `secrets.json`, `*.env`, `workflow-scheduler.env`, `ynab/token`,
  `*api_key*`, `*credential*`, `*.pem`, `id_rsa*`, Coinbase/Robinhood key files
- `FCC_TREASURY_JSON` or any dest that would load raw treasury onto **Vercel**
- Any dest under a **Mac** home (`/Users/…`)
- Full `~/B2` push onto prism

`treasury/config.json` is **not** pulled (account numbers + venue wiring).
Books restore from snapshots; keys are a kill-switch re-issue.

## Tests

```bash
python3 -m unittest deploy.tests.test_b2_puller deploy.tests.test_map_changed_paths -v
# or:
python3 -m unittest discover -s deploy/tests -v
```

## Related

- Restore prism: [`B2_RESTORE_PRISM.md`](B2_RESTORE_PRISM.md)
- Sealed key-sync (HOLD only): [`B2_KEY_SYNC_HOLD.md`](B2_KEY_SYNC_HOLD.md)
- Existing B2 helpers: `b2-ux/` (Meet recordings ingest). Same graph, not a second one.
