# FitDash restore runbook

This is the bare-metal path. It assumes the secrets bundle and the age private key are already stored somewhere that is not the Pi (password manager, plus a second offline copy). This repository does not contain those secrets.

The backup archive is gzip compressed, then encrypted with age. The file name looks like `fitdash-YYYYMMDD-HHMM.tar.gz.age`.

## 0. Confirm the secrets bundle and the age private key

You need all of these before the rest of the steps are worth doing:

- `master.key` (and `master.vercel.key` if you have it)
- the FitDash `env` file (Turso URL, Turso token, and the other names the app already uses)
- the Google OAuth client JSON, if Google Health sign-in has to work again
- the age **private** key that matches the public key in the recipients file

If the master key is missing, the workout rows will restore but stay unreadable. Stop and get the key.

## 1. Flash the Pi and install the basics

Flash Raspberry Pi OS, install Tailscale, and sign in so the machine is on the tailnet. Create the `prism-agent` user. Then:

```bash
sudo apt-get update
sudo apt-get install -y python3 age
```

`age` is required. The backup job refuses to run without it, and it will not leave an unencrypted archive behind.

## 2. Put the code on the Pi

```bash
git clone https://github.com/cvolkernick/personal-workspace.git ~/personal-workspace
```

The normal update path after that is workspace-sync. For a first install of the dashboard unit, from a machine that can SSH to the Pi:

```bash
bash resistance-dashboard/deploy/install_remote.sh prism-agent@<pi>
```

That creates `~/personal-workspace` and the `resistance-dashboard` user service. Stamp the served commit (the same SHA you just deployed):

```bash
git -C ~/personal-workspace rev-parse HEAD | ssh prism-agent@<pi> 'mkdir -p ~/apps/fitdash && cat > ~/apps/fitdash/.served-sha'
```

## 3. Put the secrets on the Pi

On the Pi, as `prism-agent`:

```bash
mkdir -p ~/.config/resistance-dashboard
chmod 700 ~/.config/resistance-dashboard
# copy master.key, env, and the OAuth client JSON into that directory
chmod 600 ~/.config/resistance-dashboard/master.key \
          ~/.config/resistance-dashboard/env \
          ~/.config/resistance-dashboard/*oauth*client*.json
```

Do not copy those files into the git checkout.

## 4. Download and decrypt the latest backup

On a machine with the GitHub token that can read `cvolkernick/fitdash-backups`, download the newest `fitdash-*.tar.gz.age` asset and the `manifest.json` asset. Copy the `.age` file to the Pi.

Decrypt and unpack (this is gzip, not zstd):

```bash
age -d -i ~/age-identity.txt -o /tmp/fitdash-backup.tar.gz fitdash-YYYYMMDD-HHMM.tar.gz.age
mkdir -p /tmp/fitdash-restore
tar -xzf /tmp/fitdash-backup.tar.gz -C /tmp/fitdash-restore
rm -f /tmp/fitdash-backup.tar.gz
```

Or let the restore script do the decrypt. It uses a private temp directory and deletes it when it finishes.

## 5. Restore

Dry-run first. It prints a per-table row-count diff and does not write:

```bash
cd ~/personal-workspace/resistance-dashboard
PYTHONPATH=. python3 ops/backup/fitdash_restore.py \
  --archive /tmp/fitdash-YYYYMMDD-HHMM.tar.gz.age \
  --identity ~/age-identity.txt \
  --sqlite-only \
  --target /tmp/fitdash-scratch
```

`--sqlite-only` is the safe default. It rebuilds the Turso tables in a scratch SQLite file under `--target`. It does not touch the live Turso database and it does not touch `~/.config/resistance-dashboard`.

When the diff looks right, write the scratch copy:

```bash
PYTHONPATH=. python3 ops/backup/fitdash_restore.py \
  --archive /tmp/fitdash-YYYYMMDD-HHMM.tar.gz.age \
  --identity ~/age-identity.txt \
  --sqlite-only \
  --target /tmp/fitdash-scratch \
  --apply \
  --sample-decrypt 5 \
  --master-key ~/.config/resistance-dashboard/master.key
```

To load the rows into Turso:

- `--turso-new` if this is a brand-new empty Turso database (step 7).
- `--turso-existing` if the Turso database is still the old one and you intend to upsert the archived rows into it.

Both of those write to whatever `TURSO_DATABASE_URL` is in the environment. Tables that are not in the archive are left alone.

Copying files onto the live Pi paths (`~/.config/resistance-dashboard`, the local SQLite files, coach state, systemd units, `.served-sha`) requires both flags:

```bash
PYTHONPATH=. python3 ops/backup/fitdash_restore.py \
  --archive /tmp/fitdash-YYYYMMDD-HHMM.tar.gz.age \
  --identity ~/age-identity.txt \
  --sqlite-only \
  --target /tmp/fitdash-scratch \
  --target-live --yes \
  --apply
```

Stop the dashboard before `--target-live` so it is not writing the same SQLite files:

```bash
systemctl --user stop resistance-dashboard.service
```

## 6. Check that the restore matches the manifest

The script already does this at the end of `--apply`: row counts and per-table hashes must match `manifest.json`. `--sample-decrypt 5` opens five sealed `workout_sessions.exercises_json` values with the master key and prints only `ok` / `failed` counts. It does not print the workout text.

Then start the app and look at it:

```bash
systemctl --user start resistance-dashboard.service
curl -fsS http://127.0.0.1:8787/api/healthz
```

Sign in and open Today. The latest session date should match the newest row you expect from the manifest.

## 7. If the Turso account itself is gone

Create a new Turso database. Put the new URL and token in `~/.config/resistance-dashboard/env` (mode 600). Run step 5 again with `--turso-new --apply`. The saved `CREATE TABLE` statements rebuild the tables. Nothing outside the archive is dropped.

## 8. Turn the timers back on

```bash
mkdir -p ~/.config/systemd/user
cp ~/personal-workspace/resistance-dashboard/ops/backup/systemd/fitdash-backup.service \
   ~/personal-workspace/resistance-dashboard/ops/backup/systemd/fitdash-backup.timer \
   ~/personal-workspace/resistance-dashboard/ops/backup/systemd/fitdash-backup-retry.timer \
   ~/personal-workspace/resistance-dashboard/ops/backup/systemd/fitdash-backup-failed.service \
   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now fitdash-backup.timer
loginctl enable-linger "$USER"
```

Enable the timer only. The retry timer is started by the backup service when a workout write defers the run. Do not enable the failure unit; `OnFailure=` starts it.

Re-link Google Health if the refresh token cannot be decrypted with the master key you restored.

The coach timer is optional. Enable it only if you still want that job.

## Run a backup by hand

```bash
# public key file. Not the private key.
cp ops/backup/recipients.txt.example ~/.config/resistance-dashboard/backup-recipients.txt
# edit that file and put the real age1... public key on its own line

cp ops/backup/backup.env.example ~/.config/resistance-dashboard/backup.env
chmod 600 ~/.config/resistance-dashboard/backup.env
# set FITDASH_BACKUP_GH_TOKEN in that file

cd ~/personal-workspace/resistance-dashboard
set -a
. ~/.config/resistance-dashboard/backup.env
set +a
PYTHONPATH=. python3 ops/backup/fitdash_backup.py \
  --recipients ~/.config/resistance-dashboard/backup-recipients.txt \
  --dry-run --out /tmp/fitdash-backup-out
```

`--dry-run` builds and encrypts the archive and does not upload it. Leave `--dry-run` off to upload to `cvolkernick/fitdash-backups`. The tag is `bk-YYYYMMDD`, or `bk-YYYYMMDD-HHMM` if that tag already exists.

If a workout was written in the last 30 minutes the command exits 75 and writes `~/.local/state/fitdash-backup/last.json` with `"status": "deferred"`. The timer retries 30 minutes later. That is not a failure.

A non-empty code-drift list is a warning. The backup still succeeds. Pi-only and modified files are packed inside the archive as `pi-local-code.tgz`. The Pi often has no git. Set `FITDASH_BACKUP_CODE_HASHES` to a `sha256sum` listing of the tree at `.served-sha` (paths relative to `~/apps/fitdash`) when you want that check.

## Dry-run the retention prune

After at least one upload:

```bash
PYTHONPATH=.:ops/backup python3 ops/backup/fitdash_prune.py
```

That prints what it would delete and deletes nothing. It keeps 14 daily snapshots, 8 Sunday snapshots, 12 snapshots from the 1st of the month, one snapshot per year, the newest 3 releases, and any release marked `last-verified-good` (in the release text, or as a tag in `~/.local/state/fitdash-backup/last-verified-good`).

When the plan looks right, set `FITDASH_BACKUP_PRUNE_APPLY=1` in `backup.env`. The nightly job then deletes after a successful upload. Until that variable is set, the nightly job only prints the plan.

## What a failed backup does

`fitdash-backup-failed.service` appends the error to `~/.local/state/fitdash-backup/last.json` and to the journal:

```bash
journalctl --user -u fitdash-backup.service -u fitdash-backup-failed.service -n 80
```

There is no Pi-side sender that posts to Grok. `deploy/on_merge.sh` can run `buzz messages send` into the workflow channel, but that is part of the deploy script, it is skipped when the `buzz` CLI is missing, and a failed send is non-fatal. The backup failure unit does not call it. A dead-man check that lists the private repo's latest release is what notices a stale backup.

## What is not in the archive

`env`, `env.bak`, `master.key`, `master.vercel.key`, OAuth client JSON, `grok_sessions`, token files, and `cache/`. Sealed column values are copied as ciphertext. The backup never decrypts them.
