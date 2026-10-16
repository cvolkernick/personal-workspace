# Daily Brief newspaper (#1091) - moved

The Daily Brief is no longer rendered by FCC. It is a sibling page on the
Horizon host at `/daily-brief` (code and docs: `research/daily_brief/`,
see `research/daily_brief/README.md`).

What FCC still has:

- No Daily Brief button in the header and no Brief tab on the mobile tab bar (#1139). `nav-horizon.js` wires the Horizon link only. Putting the newspaper back on this chrome is a new spec, not a leftover to uncomment.
- `/brief`, `/brief/*`, `/api/brief/*` answer **302** to `BRIEF_BASE_URL`
  (default `/horizon/daily-brief`). The tailnet URL is that path on
  `PRISM_PUBLIC_BASE` once the env is a valid https origin. The logic is in
  `brief_redirect.py`; `server.py` reaches it through its existing
  `brief_route` -> `brief.route`.
- `financial-command/brief.py` is a thin shim, so the publish command is unchanged:

  ```bash
  ssh prism-gateway 'python3 ~/personal-workspace/financial-command/brief.py publish -' < edition.json
  ```

- `brief-samples/` stays as the shim's test fixtures.

Rollback: revert the PR that moved it; the FCC route comes back.
