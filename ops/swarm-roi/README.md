# Swarm ROI ledger (#773)

Home-team baseline for parallel-agent vs single-agent/human work: spend, rework, review burden, outcome.

**This directory is the storage location.** Canonical sources:

| Metric | Source | Drift |
|--------|--------|--------|
| PR metadata (reviews, comments, open→merge latency, linked issues) | GitHub API | GitHub wins if git disagrees on merge *state* |
| Rework (follow-up commits / reverts on the same paths within 7 days) | this repo's git history | git wins for what is in the tree |
| Spend (tokens) | Grok Build / SuperGrok usage payload if present | **currently unavailable** — no per-issue API in this repo; never estimated |
| Outcome | linked `Fixes`/`Closes` issue `state` + `state_reason` | GitHub |

## Layout

```
ops/swarm-roi/
  config.json              # known agent names, window, labels
  records/pr-<N>.json      # one record per merged PR (after this ships)
  weekly/YYYY-MM-DD.json   # Monday-start rollup
```

## Collector

```bash
python3 projects-dashboard/swarm_roi.py record --pr 12
python3 projects-dashboard/swarm_roi.py backfill-rework
python3 projects-dashboard/swarm_roi.py rollup
```

GitHub Action: `ops/github-workflows/swarm-roi.yml` (install copy `.github/workflows/swarm-roi.yml`).

- On merged PR: write `records/pr-N.json` and commit with `[skip ci]` (ledger-only).
- Weekly (Monday): close elapsed rework windows + write the rollup.

If the merge commit to `master` is blocked, the Action still comments the JSON on the PR. The weekly job backfills missing records from GitHub + git.

## Parallel vs single vs human

Classified **only** with evidence:

- labels `parallel-agent` / `swarm` → parallel
- label `single-agent` → single_agent
- label `human-only` → human
- 2+ known-agent `Co-authored-by` trailers → parallel
- exactly one such trailer → single_agent
- otherwise **unavailable** (operator git identity is used for agent commits; calling those "human" would be a silent inference)

No new required author checklist. Labels/trailers are optional and improve classification.

## Non-goals (from the issue)

Does not judge people. Weekly cadence is enough. Does not change how Grok Build parallelizes.
