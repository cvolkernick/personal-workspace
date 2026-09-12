# Coinbase BTC/USD price producer — prism/Pi SoT (#695)

**Source of truth host: `prism` (Pi, `prism-agent@192.168.100.98`).**
Mac launchd `com.personalworkspace.cb-solana-refresh` still produces **liquid
balances** on the Mac (Coinbase CLI). It must **not** push
`treasury/snapshots/coinbase_latest.json` — Pi owns that file for the mining
price. No dual-writer.

Endpoint: `https://api.coinbase.com/v2/prices/BTC-USD/spot` (public, no auth,
no CLI, no node, no secrets).

Out of scope: liquid USDC/BTC *balance* still needs the Mac CLI (or a later
issue). This producer only patches `btc_usd_price` + `as_of` and preserves
existing keys.

## Eng-gate sequence

| Step | Where | Gate | Stop if |
|------|-------|------|---------|
| **1** | Pi | `python3 -m treasury.coinbase_price_sync` writes `btc_usd_price` and moves `as_of` under 6h | Fetch fails; `as_of` unchanged |
| **2** | Pi | `coinbase-price-refresh.timer` enabled, interval **2h** (under FCC 6h stale) | Timer missing |
| **3** | Mac | Omit `coinbase_latest.json` from Mac→Pi `push_files` (`_strip_rh_push`) | Dual-writer still pushing the file |

Mac CB/Solana launchd stays up for Solana + local Mac balances. Do not boot it
out for this issue.

## Install (Pi)

```bash
cd /home/prism-agent/personal-workspace
sudo cp treasury/deploy/coinbase-price-refresh.service treasury/deploy/coinbase-price-refresh.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now coinbase-price-refresh.timer
sudo systemctl start coinbase-price-refresh.service
systemctl list-timers | grep coinbase-price
```

Smoke:

```bash
python3 - <<'PY'
import json
from pathlib import Path
d = json.loads(Path("treasury/snapshots/coinbase_latest.json").read_text())
print("source", d.get("source"))
print("as_of", d.get("as_of"))
print("btc_usd_price", d.get("btc_usd_price"))
print("liquid_usdc", d.get("liquid_usdc"))
print("liquid_btc", d.get("liquid_btc"))
PY
```

## Push / dual-write

Mac `pi_sync.push_files` and `DEFAULT_PUSH_FILES` omit `coinbase_latest.json`.
Emergency override only: `TREASURY_COINBASE_PUSH=1`.
