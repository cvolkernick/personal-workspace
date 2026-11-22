# Read-only quote env for run_treasury writers

`solana-refresh` (and the other offline `run_treasury` writers) were rewriting
`treasury/snapshots/fund_manager_latest.json` with `price missing` whenever
quotes came back empty. The code now carries the last market marks forward
(`price_stale`, `price_as_of`) and will not replace a priced snapshot with an
unpriced one. Cost basis is not a price.

Those writers still need the same three read-only variables the bp-poll
drop-in already sets, so an open market can refresh quotes through the
existing `get_quotes` path. The values live in one file:

`treasury/deploy/fm-broker-quote.env`

```
FM_BROKER_ADAPTER=treasury.fund_manager_broker:submit
FM_BROKER_CLIENT=grok_mcp
FM_BROKER_GROK_BIN=/home/prism-agent/.grok/bin/grok
```

Copied from `investment/FUND_MANAGER_RUNBOOK.md` (the
`20-broker-adapter.conf` note). They are not secrets. Nothing in this change
calls the order gate, adds an order path, or changes what bp-poll is allowed
to buy. Shell writers source `treasury/deploy/broker_quote_env.sh`, which
fills the three variables only when none of them is already set.

`run_treasury` also reads the env file itself when the variables are unset,
so a pull is enough for quotes to be attempted. Carry-forward still covers a
closed market and a missing client. Install the drop-ins below so the
process environment matches bp-poll. Do not restart bp-poll and do not change
its drop-in.

## Install on prism (after this merges; Nakatoshi runs these)

User units (`solana-refresh`, `financial-command`):

```bash
cd ~/personal-workspace
mkdir -p ~/.config/systemd/user/solana-refresh.service.d \
  ~/.config/systemd/user/financial-command.service.d
cp treasury/deploy/drop-ins/user/solana-refresh.service.d/20-broker-quote.conf \
  ~/.config/systemd/user/solana-refresh.service.d/20-broker-quote.conf
cp treasury/deploy/drop-ins/user/financial-command.service.d/20-broker-quote.conf \
  ~/.config/systemd/user/financial-command.service.d/20-broker-quote.conf
systemctl --user daemon-reload
systemctl --user restart solana-refresh.service
systemctl --user restart financial-command.service
```

System units (`braiins-refresh`, `coinbase-price-refresh`, `rh-refresh`):

```bash
cd ~/personal-workspace
sudo mkdir -p /etc/systemd/system/braiins-refresh.service.d \
  /etc/systemd/system/coinbase-price-refresh.service.d \
  /etc/systemd/system/rh-refresh.service.d
sudo cp treasury/deploy/drop-ins/system/braiins-refresh.service.d/20-broker-quote.conf \
  /etc/systemd/system/braiins-refresh.service.d/20-broker-quote.conf
sudo cp treasury/deploy/drop-ins/system/coinbase-price-refresh.service.d/20-broker-quote.conf \
  /etc/systemd/system/coinbase-price-refresh.service.d/20-broker-quote.conf
sudo cp treasury/deploy/drop-ins/system/rh-refresh.service.d/20-broker-quote.conf \
  /etc/systemd/system/rh-refresh.service.d/20-broker-quote.conf
sudo systemctl daemon-reload
```

`rh_refresh.sh`, `braiins_refresh.sh`, `coinbase_price_refresh.sh`, and
`cb_solana_refresh.sh` source the helper, so the next timer run picks the
env up even before a unit is recopied. The repo unit files also reference
`fm-broker-quote.env` for a fresh install. `fund-manager-bp-poll` already
has the three variables; leave that drop-in and the buy path alone.

Check, without placing an order:

```bash
systemctl --user show solana-refresh.service -p EnvironmentFiles -p Environment
systemctl show braiins-refresh.service -p EnvironmentFiles -p Environment
```
