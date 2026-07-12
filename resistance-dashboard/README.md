# Resistance Training Dashboard

Mobile-friendly dashboard that:

- **Pulls lift history** from the GitHub repo `cvolkernick/personal-workspace` (`fitness/workouts/{push,pull,legs}.md`)
- **Logs new workouts** by appending to those markdown files (GitHub Contents API when `GITHUB_TOKEN` is set; local workspace otherwise)
- **Charts** weekly volume and per-exercise strength (best working load + Epley e1RM)
- **Fetches weight & sleep** from Google Fit REST (`dataset:aggregate` / sessions) when OAuth env vars are set
- **Suggests recovery status** from sleep, recent training volume, and weight trend

## Quick start

```bash
cd resistance-dashboard
python3 server.py          # http://127.0.0.1:8787/
```

Or: `PORT=8787 python3 server.py`

## Configuration (env)

| Variable | Purpose |
|----------|---------|
| `GITHUB_TOKEN` | PAT with `contents:write` for remote log append |
| `GITHUB_OWNER` | default `cvolkernick` |
| `GITHUB_REPO` | default `personal-workspace` |
| `GITHUB_BRANCH` | default `master` |
| `GITHUB_PREFER_LOCAL` | `1` to read/write local workspace only |
| `LOCAL_WORKSPACE_DIR` | path to repo root (auto-detected as parent of this app) |
| `GOOGLE_CLIENT_ID` | OAuth client id |
| `GOOGLE_CLIENT_SECRET` | OAuth client secret |
| `GOOGLE_REFRESH_TOKEN` | refresh token with Fit body + sleep read scopes |
| `GOOGLE_ACCESS_TOKEN` | optional short-lived token (skips refresh) |
| `PORT` | server port (default `8787`) |

### Google Fit OAuth scopes

```
https://www.googleapis.com/auth/fitness.body.read
https://www.googleapis.com/auth/fitness.sleep.read
```

Without Google credentials the UI still loads lift charts and recovery from training volume, and surfaces a clear health auth error (does not invent metrics).

## API

- `GET /api/healthz` — liveness
- `GET /api/dashboard` — sessions, analytics, health, recovery
- `POST /api/workouts` — log a session  
  ```json
  {
    "session_type": "push",
    "date": "2026-07-10",
    "notes": "optional",
    "exercises": [
      {"name": "DB Flat Press", "weight_lbs": 50, "sets": 3, "reps": 10}
    ]
  }
  ```

## Tests

```bash
python3 -m unittest tests.test_analytics -v
python3 scripts/verify_github.py
python3 scripts/verify_google.py
python3 scripts/verify_launch.py
```

## Layout

```
resistance-dashboard/
  server.py                 # real entry
  rt_dashboard/             # parse, analytics, recovery, GitHub, Google Fit
  static/                   # mobile-first UI + Chart.js
  tests/                    # pure logic + wire-format fixtures
  scripts/                  # verification helpers
  REVIEW.md                 # post-build improvement suggestions
```

## Workout log format

Compatible with existing PPL markdown:

```markdown
## May 26, 2026 - Session Complete
- DB Flat Press: 50 lbs x 1 x 12, 45 lbs x 1 x 12
- Tricep Pushdowns: 47.5 lbs x 3 x 12 (PR!)
```
