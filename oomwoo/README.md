# OOMWOO project status

Local tracker for [makerspet/oomwoo](https://github.com/makerspet/oomwoo) — the open-source robot vacuum you build yourself.

This is a **read-only MVP**: it parses the hub README (contribution modules + open-source deliverables) and overlays live GitHub pulse (stars, related repos, issues, PRs, commits). It does not write to GitHub.

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
