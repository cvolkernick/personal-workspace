# Daily Brief newspaper (#1091) - moved

The Daily Brief is no longer rendered by FCC. It is a sibling page on the
Horizon host at `/daily-brief` (code and docs: `research/daily_brief/`,
see `research/daily_brief/README.md`).

What FCC still has:

- `/brief`, `/brief/*`, `/api/brief/*` answer **302** to `BRIEF_BASE_URL`
  (default `/horizon/daily-brief`, i.e.
  https://prism-gateway.tailb1085a.ts.net/horizon/daily-brief) with the same
  subpath and query. The logic is in `brief_redirect.py`; `server.py` is
  unchanged and reaches it through its existing `brief_route` -> `brief.route`.
- `financial-command/brief.py` is a thin shim, so the publish command is unchanged:

  ```bash
  ssh prism-gateway 'python3 ~/personal-workspace/financial-command/brief.py publish -' < edition.json
  ```

- `brief-samples/` stays as the shim's test fixtures.

Rollback: revert the PR that moved it; the FCC route comes back.
