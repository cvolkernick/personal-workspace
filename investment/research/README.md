# Position research (agentic fund manager)

Deep-dive reports for **watchlist** and other names the fund-manager team is considering.

## How to run

```text
/position-deep-dive symbol=BE
# or
/workflow position-deep-dive symbol=BE
```

Optional args: `theme`, `context` (free text), `force=true` to re-run even if a recent report exists.

## Output

- Markdown report: `investment/research/{SYMBOL}_deep_dive.md`
- Manager should update `investment/watchlist.json` entry fields (`last_deep_dive`, `status`) after a material conclusion.

## Status vocabulary (reports + watchlist)

| Status | Meaning |
|--------|---------|
| `monitor` | On watchlist; no size yet |
| `ready` | Deep-dive done; eligible for Thesis/Risk size proposal |
| `pass` | Researched; do not buy for now |
| `held` | Already in agentic book |
| `promoted` | Moved to core allowlist / sleeve symbols |

## Cadence

Use on a **recurring** basis whenever Scout/Thesis floats a non-core name, or on owner watchlist adds. Prefer before first buy when `deep_dive_required_before_buy` is true.
