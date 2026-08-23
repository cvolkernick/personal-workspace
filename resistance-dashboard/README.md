# FitDash

Resistance training &amp; nutrition dashboard (working title: FitDash).

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

Pi / LAN (bind all interfaces):

```bash
python3 server.py --host 0.0.0.0 --port 8787 --no-browser --local
```

### Deploy to Raspberry Pi

When on the home LAN (see `deploy/README.md`):

```bash
bash resistance-dashboard/deploy/install_remote.sh prism-agent@192.168.100.98
# → http://192.168.100.98:8787/
```

Off-LAN: Tailscale on Pi + client (do **not** public port-forward while single-user).

### Vercel ignore-build (Hobby FitDash project)

The Vercel project **FitDash** is Git-linked to this monorepo with
`rootDirectory=resistance-dashboard`. `commandForIgnoringBuildStep` is unset
in the dashboard; this repo owns the skip via `vercel.json`:

```
python3 scripts/vercel_ignore.py || exit 1
```

Vercel: exit **0** → deployment **Canceled** (skip). Exit **1** → build.
Git/shallow-clone errors also exit 1 (build), never 128.

**Paths that count as FitDash** (see `vercel-ignore-paths.txt`):

- `resistance-dashboard/` only

`fitness/` is **not** a Vercel trigger (Pi `path_unit_map.json` still maps it
for the systemd unit). Orchestra, FCC, Auto Fleet, treasury, and other apps
must skip.

Prove skip **without** talking to Vercel:

```bash
cd resistance-dashboard
python3 -m unittest tests.test_vercel_ignore -v
python3 scripts/vercel_ignore.py --changed orchestra/server.py; echo $?   # 0
python3 scripts/vercel_ignore.py --changed resistance-dashboard/server.py; echo $?  # 1
```

Prove on Vercel: push a SHA whose diff vs `VERCEL_GIT_PREVIOUS_SHA` (else
`HEAD^`) has no `resistance-dashboard/` path. FitDash should be **Canceled**,
not `PYTHON_ENTRYPOINT_NOT_FOUND`. A commit that *does* touch
`resistance-dashboard/` still builds.

### Vercel preview (not prod)

Project: `fitdash` (https://vercel.com/cvolkernick/fitdash). Root Directory =
`resistance-dashboard`. `vercel.json` + `.vercelignore` make Vercel serve
`static/` and `GET /api/healthz` (`role=vercel-preview`). They do **not** run
`server.py` (no WSGI `app`). Pi stays on `deploy/install_remote.sh` and the
systemd unit. OAuth/SQLite/dashboard APIs stay unproven on Vercel until secrets
and a preview URL exist.

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
- `GET /api/dashboard?refresh=1` — force 90-day Google Health pull (Refresh data)
- `GET /api/warm` — incremental 14-day Health + Hidrate cache warm (loopback / service token; no page load)
- `GET /api/agent/today` — read-only Today brief for agents (workout, hydration wake pace, bottle, wake window). Same loopback / `FITDASH_SERVICE_TOKEN` gate as `/api/sleep_battery`. Cookie-less without token is 401. Does not invent ml / loads / sessions.
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
python3 -m unittest tests.test_vercel_ignore -v
python3 -m unittest tests.test_vercel_preview -v
python3 scripts/verify_github.py
python3 scripts/verify_google.py
python3 scripts/verify_launch.py
```

## Layout

```
resistance-dashboard/
  server.py                 # real entry (Pi / Mac)
  api/healthz.py            # Vercel preview liveness only
  vercel.json               # ignoreCommand + static output; not a WSGI app
  vercel-ignore-paths.txt   # prefixes that count as FitDash for Vercel
  .vercelignore             # keeps server.py out of the Vercel build
  rt_dashboard/             # parse, analytics, recovery, GitHub, Google Fit
  static/                   # mobile-first UI + Chart.js
  tests/                    # pure logic + wire-format fixtures
  scripts/                  # verification helpers + vercel_ignore.py
  REVIEW.md                 # post-build improvement suggestions
```

## Workout log format

Compatible with existing PPL markdown:

```markdown
## May 26, 2026 - Session Complete
- DB Flat Press: 50 lbs x 1 x 12, 45 lbs x 1 x 12
- Tricep Pushdowns: 47.5 lbs x 3 x 12 (PR!)
```
