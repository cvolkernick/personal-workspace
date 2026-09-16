# Eng issue lifecycle labels

> Migrated from issue #302 (closed 2026-09-16). Labels created 2026-08-23.

## The labels

`status:ready` | `status:in-progress` | `status:pending-review`

- **Done = closed.** One `status:*` at a time per issue.
- **WIP max 3** in `status:in-progress`.
- `status:ready` = spec'd, waiting for eng pickup.
- `status:pending-review` = eng done, awaiting verification (often the Chairman's tap-test).

## Writers

- Ready / in-progress / pending-review stamps are applied by the filing worker (Muse specs; Grok.btc ops).
- Grok closes on eng-gate merge.
- Every new issue carries its workflow labels **at creation** — an unlabeled spec is invisible to the eng pipeline.

## Related

- #227 — formal backlog is GitHub Issues (Project 1 retired 2026-08-21).
- #192 — GUIDES vs git/Pi source-of-truth nesting (QA, parked).
