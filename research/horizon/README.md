# Horizon

Global Macro & Geopolitical Intelligence System — world-state model + daily synthesis.

See [ARCHITECTURE.md](./ARCHITECTURE.md) for design.

## Run

```bash
# Offline (fixtures; CI-safe)
python3 research/horizon/run_horizon.py --offline

# Prefer live RSS + fixtures
python3 research/horizon/run_horizon.py

# Re-link strategy only
python3 research/horizon/run_horizon.py --link-only --offline
```

## Test

```bash
python3 -m unittest discover -s research/horizon/tests -v
```

## Outputs

- `data/world_state_latest.json` + `data/history/world_state_<id>.json`
- `data/briefs/brief_latest.{json,md}` + versioned copies

## Strategy sources

Read-only from workspace:

- `strategy/bets.md`
- `strategy/intent.json`
- `strategy/today.md`
- `investment/positions.md`
