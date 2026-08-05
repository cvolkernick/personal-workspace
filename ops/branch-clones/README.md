# Branch clone reports (multi-machine matrix)

The Workflow Management **Repo → Matrix** view always includes:

1. **origin** — remote branches from `git fetch` / `refs/remotes/origin`
2. **this machine** — live local heads + worktree checkouts

It does **not** magically discover every network machine. Extra columns come from
peer clone reports (cached JSON), optionally refreshed over SSH.

## Preferred: SSH hosts config (auto-refresh)

```text
ops/branch-clones/hosts.json
```

```json
{
  "hosts": [
    {
      "machine": "prism",
      "label": "Pi",
      "ssh": "prism-agent@192.168.100.98",
      "path": "/home/prism-agent/personal-workspace",
      "timeout_sec": 8
    }
  ]
}
```

On each matrix build the dashboard SSHes (BatchMode, short timeout), inventories
`refs/heads` on that clone, and writes `ops/branch-clones/<machine>.json`.
If SSH fails, the last good cache is still shown.

```bash
# Manual refresh only:
python3 -c "from git_workflow import refresh_ssh_clone_reports; print(refresh_ssh_clone_reports())"
```

Requires passwordless SSH (key already authorized on the peer).

## Manual: drop a JSON report

```text
ops/branch-clones/<machine-id>.json
```

## Schema

```json
{
  "machine": "pi",
  "label": "Pi",
  "hostname": "prism",
  "updated_at": "2026-08-05T12:00:00Z",
  "branches": [
    { "name": "master", "sha": "abc1234", "current": true },
    { "name": "work/iot", "sha": "def5678" }
  ]
}
```

| Field | Required | Notes |
|-------|----------|--------|
| `machine` | yes (or filename stem) | Stable column id; not `origin` / `local` |
| `label` | no | Column header (defaults to hostname or machine) |
| `hostname` | no | Shown as subtitle |
| `updated_at` | no | ISO-8601 when the report was generated |
| `branches` | yes | List of local heads on that clone |

## Generate on a peer host

```bash
# On the peer clone of personal-workspace:
python3 - <<'PY'
import json, socket, subprocess
from datetime import datetime, timezone
from pathlib import Path

repo = Path(".").resolve()  # or absolute path to clone
def run(*a):
    return subprocess.check_output(["git", "-C", str(repo), *a], text=True).strip()

host = socket.gethostname().split(".")[0]
current = run("branch", "--show-current")
lines = run(
    "for-each-ref",
    "--format=%(refname:short)|%(objectname:short)",
    "refs/heads",
).splitlines()
branches = []
for line in lines:
    if "|" not in line:
        continue
    name, sha = line.split("|", 1)
    branches.append({"name": name, "sha": sha, "current": name == current})

payload = {
    "machine": host.lower().replace(" ", "-"),
    "label": host,
    "hostname": host,
    "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "branches": branches,
}
print(json.dumps(payload, indent=2))
PY
```

Copy the output to this directory on the machine that runs the Workflow dashboard
(or commit/sync it if you want the matrix shared in git).

Refresh the dashboard — a new column appears for that machine.
