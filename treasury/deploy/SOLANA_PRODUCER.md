# Solana snapshot producer — prism/Pi SoT

**Source of truth host: `prism` (Pi, `prism-agent@prism-gateway`).**
Prism writes `treasury/snapshots/solana_latest.json` from the live clone
(`~/personal-workspace`, `work/treasury`) with
`python3 -m treasury.solana_sync`. Public RPC only (SOL + USDC whitelist,
JR-strcUSX retired 2026-10-10). No keys, no secrets.

Same model as RH (#518), Braiins (#689) and Coinbase price (#695): the Pi
owns the file and the Mac does not push it. No dual-writer.

## Units

| Unit | Kind | Cadence |
|------|------|---------|
| `solana-refresh.service` | systemd **user** unit (prism-agent, linger on) | oneshot |
| `solana-refresh.timer` | systemd **user** timer | `OnBootSec=5min`, every **15min** (`Persistent=true`) |

The Mac job ran hourly (`StartInterval` 3600). 15min is well under the
FCC 6h stale window and costs 3 RPC calls + 1 price call per run.

## Install (Pi) — only after the PR is merged

```bash
cd ~/personal-workspace
mkdir -p ~/.config/systemd/user
cp treasury/deploy/solana-refresh.service treasury/deploy/solana-refresh.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now solana-refresh.timer
systemctl --user start solana-refresh.service
systemctl --user list-timers | grep solana-refresh
journalctl --user -u solana-refresh.service -n 20 --no-pager
```

Smoke:

```bash
python3 - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("treasury/snapshots/solana_latest.json").read_text())
print("source", d.get("source"), "as_of", d.get("as_of"))
print([h.get("symbol") for h in d.get("holdings") or []])  # ['SOL', 'USDC']
print("strcusx" in json.dumps(d).lower())                  # False
PY
```

Uninstall:

```bash
systemctl --user disable --now solana-refresh.timer
rm -f ~/.config/systemd/user/solana-refresh.{service,timer}
systemctl --user daemon-reload
```

## Push / dual-write

Mac `pi_sync.push_files` and `DEFAULT_PUSH_FILES` omit `solana_latest.json`,
and `_strip_rh_push` drops it even if a local config still lists it.
Emergency override only: `TREASURY_SOLANA_PUSH=1`.

There is no receive-side guard on prism: Mac → Pi is a plain `scp` into
`treasury/snapshots/`. A Mac checkout that has not pulled this change still
pushes `solana_latest.json` hourly until it pulls. Pull the Mac worktree
(`~/personal-workspace-worktrees/treasury`) after merge.

Mac launchd `com.personalworkspace.cb-solana-refresh` stays up: it still
produces liquid Coinbase balances (Coinbase CLI), re-merges the Mac
treasury JSON, and pushes the remaining non-Pi-SoT files. It still runs
`solana_sync` for the Mac's local FCC copy, but no longer pushes it.
