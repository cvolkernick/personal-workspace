# scripts/

| Tool | Purpose |
|------|---------|
| `buzz-board` | Buzz Board (GitHub Project #1) CLI — `list`, `get`, `set-status N Done`, … |
| `eng_gate_post_merge.py` | After eng-gate merge: mark board Done / residual / sweep (#58) |

```bash
./scripts/buzz-board whoami
./scripts/buzz-board set-status 58 Done
python3 scripts/eng_gate_post_merge.py --pr 47
python3 scripts/eng_gate_post_merge.py --sweep
python3 -m unittest discover -s scripts/tests -v
```

Runbook: `ops/ENG_GATE_BOARD_DONE.md`. Auth: `GITHUB_TOKEN` with `repo` + `project`.
