# Naka portable proof (#792 AC4)

## Buzz runtime

| Field | Value |
|-------|--------|
| harness | `buzz` |
| when | 2026-09-16T21:55:00Z |
| dump | `~/Library/Application Support/xyz.block.buzz.app/agents/managed-agents.json` (mtime 2026-09-16T02:15 local) |
| startup pull | `origin/master` `8b456c29` — `docs/identity/` did not exist; empty pull |
| `pulled_sha` | `8b456c29a78e32b4d1838f1258d40f14881124ce` |
| write-back | this PR `feat/identity-sync-792` · MEMORY entry `2026-09-16T21:55:00Z · harness=buzz` |

## Bot runtime

Not run from this Mac Buzz session. Grok.btc must pull `docs/identity/nakatoshi/` after merge (or from this PR) and append one MEMORY block with `harness=bot` + ISO timestamp. Until that lands, AC4 is Buzz-only.
