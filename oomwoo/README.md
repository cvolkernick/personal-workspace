# OOMWOO project status

House copy of the makerspet/oomwoo status dashboard (Pi `:8798`).

**Canonical source (community PRs):** [cvolkernick/oomwoo-status](https://github.com/cvolkernick/oomwoo-status)  
**Public HTTPS:** https://oomwoo.vercel.app/

This tree is a consumer for the house Pi unit. Do not send community contributions here.

## Open

```bash
python3 oomwoo/server.py
# or
bash oomwoo/start.command
```

http://localhost:8798/

API: `/api/health` · `/api/status` · `/api/status?refresh=1`

Optional `GITHUB_TOKEN` / `GH_TOKEN` raises the GitHub rate limit. Unauthenticated works; Pi responses cache for 3 minutes.

Public HTTPS (share this): **https://oomwoo.vercel.app/**

Vercel edge-caches `/api/status` ~15 min. Pi `:8798` remains the house surface.

## What it shows

- Hub vitals (stars, forks, last push, last human commit)
- Module board grouped by README status (done / in progress / ready)
- Open-source deliverable checkboxes
- Related makerspet repos (`oomwoo-one`, CAD, PCB, firmware, …)
- Open issues and PRs

v0 target on upstream: 3D-printed chassis, Gazebo sim, basic cleaning/mapping, Pi CM4/CM5. Build instructions: Fall 2026.

## Tests

```bash
python3 -m unittest oomwoo.tests.test_parse oomwoo.tests.test_status oomwoo.tests.test_server oomwoo.tests.test_vercel
```

Pi unit (after merge + `install_remote.sh --only oomwoo`): port **8798**.
