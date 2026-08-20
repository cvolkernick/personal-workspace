# Restore prism from last pull + git

**Role:** rebuild `prism-gateway` (`app-books`) after disk loss / replacement.  
**Source of books:** last successful pull on `finley-gateway` (`~/b2-pulls/prism`).  
**Source of code:** git (`origin/master`).  
**Keys:** not in the snapshot — re-issue (kill-switch).

The puller **cannot restore itself**. Off-site bucket for finley is out of
scope (store not chosen). If finley is gone too, this runbook cannot run.

## What you get

| Restored | Not restored |
|----------|----------------|
| treasury / book snapshots (as of last pull) | venue keys (Coinbase, Robinhood, YNAB token, `.env`) |
| `financial-command/treasury_latest.json` | `treasury/config.json` venue wiring |
| youtube-groom `state.json`, `never_readd`, `groom.log` | live FCC/FitDash/Orchestra processes (reinstall units) |
| systemd **unit files** (user) | linger / `GITHUB_TOKEN` / scheduler env |
| nest-published copies | knowledge graph (stays on finley; prism queries `:8792`) |

Books are **as of last snapshot**, not live.

## Steps (operator on LAN)

1. **Stand up the box** — hostname `prism-gateway`, user `prism-agent`, Tailscale
   `100.67.114.2`. Do not SSH from the cloud VM.

2. **Git** — clone and hard-reset to the last known good `master`:

   ```bash
   git clone https://github.com/cvolkernick/personal-workspace.git ~/personal-workspace
   cd ~/personal-workspace
   git fetch origin master
   git checkout -B master origin/master
   ```

3. **Copy last pull** from finley (or a USB copy of `~/b2-pulls/prism`):

   ```bash
   # dry-run first
   python3 deploy/b2-puller/restore_prism.py \
     --from /path/to/b2-pulls/prism \
     --to /home/prism-agent \
     --dry-run

   python3 deploy/b2-puller/restore_prism.py \
     --from /path/to/b2-pulls/prism \
     --to /home/prism-agent
   ```

   The script writes **only** allowlisted paths and **refuses** key-shaped names.

4. **Reinstall app-books units** (FCC, FitDash, Orchestra, Auto Fleet):

   ```bash
   bash deploy/install_remote.sh prism-agent@prism-gateway
   # or on-box: copy deploy/units/* to ~/.config/systemd/user and enable
   systemctl --user daemon-reload
   loginctl enable-linger prism-agent
   ```

5. **Re-issue keys** (kill-switch — assume the old prism is burned):

   - Rotate Coinbase / Robinhood / YNAB / Grok / GitHub tokens
   - Recreate `~/.config/ynab/token`, `iot/secrets.json`, `workflow-scheduler.env`
   - Do **not** load `FCC_TREASURY_JSON` onto Vercel
   - Do **not** copy raw treasury onto a Mac

6. **Point prism at B2** — query `http://finley-gateway:8792/` (or
   `http://100.124.165.50:8792/`). Do not rsync `~/B2` onto prism.

7. **youtube-groom** — unit files came back from the pull; start the existing
   groom unit if it was installed. State/log/never_readd are as of last pull.

## Finley is gone

Stop. This snapshot does not include a puller self-backup. Choose an off-site
bucket later (`ops/B2_KEY_SYNC_HOLD.md` is unrelated — keys still stay off the
pull). Until a store exists, finley loss means graph + last books are gone.
