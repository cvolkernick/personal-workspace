# Braiins snapshot producer — prism/Pi SoT (#689)

**Source of truth host: `prism` (Pi, `prism-agent@192.168.100.98`).**
Mac launchd `com.personalworkspace.braiins-refresh` is retired. Do not run two
producers against `treasury/snapshots/braiins_latest.json`.

Token: `~/.config/braiins/token` for `prism-agent` (mode 600). **Never**
`treasury/config.json`, git, chat, or GitHub. Env `BRAIINS_POOL_TOKEN` is an
alternate, still host-local.

The token is read-only pool data (profile / workers / payouts / rewards). It
cannot move funds.

## Eng-gate sequence

| Step | Where | Gate | Stop if |
|------|-------|------|---------|
| **1** | Pi | Token already at `~/.config/braiins/token` mode 600, `prism-agent` | Token written to repo / `config.json` / chat |
| **2** | Pi | `python3 -m treasury.braiins_sync` writes `braiins_latest.json` with `ok: true` **and** a `payouts` list | `payouts` missing; `ok: false` |
| **3** | Pi | `braiins-refresh.timer` enabled, interval **4h** (under FCC 6h stale) | Timer missing; Mac still producing |
| **4** | Mac | `launchctl bootout` `com.personalworkspace.braiins-refresh`; omit `braiins_latest.json` from Mac→Pi push | Dual-writer still loaded |

## Install (Pi)

```bash
cd /home/prism-agent/personal-workspace
sudo cp treasury/deploy/braiins-refresh.service treasury/deploy/braiins-refresh.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now braiins-refresh.timer
sudo systemctl start braiins-refresh.service
systemctl list-timers | grep braiins
```

Smoke (no token in stdout):

```bash
python3 - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("treasury/snapshots/braiins_latest.json").read_text())
pay = d.get("payouts")
print("ok", d.get("ok"))
print("as_of", d.get("as_of"))
print("payouts_is_list", isinstance(pay, list), "len", len(pay) if isinstance(pay, list) else None)
print("payouts_error", d.get("payouts_error"))
PY
```

## Mac disarm (after step 2)

```bash
launchctl bootout gui/$(id -u)/com.personalworkspace.braiins-refresh 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.personalworkspace.braiins-refresh.plist
```

Do not recopy `treasury/deploy/com.personalworkspace.braiins-refresh.plist`.
That file is a retired stub.

## Push / dual-write

Mac `pi_sync.push_files` and `DEFAULT_PUSH_FILES` omit `braiins_latest.json`.
Emergency override only: `TREASURY_BRAIINS_PUSH=1`.
