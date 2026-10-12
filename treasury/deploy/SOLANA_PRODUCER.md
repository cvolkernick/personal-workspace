# Solana snapshot producer — prism/Pi SoT

**Source of truth host: `prism` (Pi).**
Prism writes `treasury/snapshots/solana_latest.json` from the live clone
(`~/personal-workspace`, `work/treasury`) with
`python3 -m treasury.solana_sync`. Public RPC only (SOL + USDC whitelist,
JR-strcUSX retired 2026-10-10). No keys, no secrets.

Same model as RH (#518), Braiins (#689) and Coinbase price (#695): the Pi
owns the file and the Mac does not push it. No dual-writer.

## Units

| Unit | Kind | Cadence |
|------|------|---------|
| `solana-refresh.service` | systemd **user** unit (prism-agent, linger on) | oneshot, `TimeoutStartSec=5min` |
| `solana-refresh.timer` | systemd **user** timer | `OnBootSec=5min`, every **15min** (`Persistent=true`) |

Each run:

1. `python3 -m treasury.solana_sync --prism-copy` writes
   `solana_latest.json` **and** the prism-only `solana_latest.prism.json`
   (`producer_host: prism`, live reads only; an RPC failure never re-stamps it).
2. `ExecStartPost=python3 -m treasury.run_treasury --offline --skip-coinbase`
   re-merges `treasury/snapshots/treasury_latest.json` and
   `financial-command/treasury_latest.json` from snapshot files, so FCC
   serves this refresh. Offline: no Coinbase CLI, no YNAB/Plaid/Sheet/RPC
   calls, no push, no orders. It also rewrites `fund_manager_latest.json`
   from the RH snapshot, as the braiins / coinbase-price / rh refresh scripts
   already do.

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

## Zero-flicker guard (works with old Mac code)

Mac → Pi is a plain `scp` into `treasury/snapshots/`, and a Mac checkout that
has not pulled still pushes `solana_latest.json`, `treasury_latest.json` and
`financial-command/treasury_latest.json` (with its own Solana block) hourly.
That push can only overwrite those names. It never touches
`solana_latest.prism.json`, so prism keeps its own copy whatever the Mac runs.

Readers on prism prefer that copy (`treasury.solana_sync.prism_copy_wins`):

- `overlay_solana_snapshot` (FCC `/api/treasury` read path and every
  `run_treasury` write) replaces a present `snapshot.solana` block with the
  prism copy when the prism copy is newer, or when it is still fresh (≤6h)
  and the block is not prism-produced. A newer prism-produced block is kept.
- `fetch_solana(prefer_live=False)` and the Interest Spectrum read the prism
  copy before a pushed `solana_latest.json`.
- If the prism producer is dead past 6h, a newer pushed copy is used again.

Pull the Mac worktree (`~/personal-workspace-worktrees/treasury`) after merge
anyway, so it stops pushing the stale files at all.

Mac launchd `com.personalworkspace.cb-solana-refresh` stays up: it still
produces liquid Coinbase balances (Coinbase CLI), re-merges the Mac
treasury JSON, and pushes the remaining non-Pi-SoT files. It still runs
`solana_sync` for the Mac's local FCC copy, but no longer pushes it.
