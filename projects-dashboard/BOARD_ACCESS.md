# Buzz Board access for agents

**Decision (issue #21):** **Stay on GitHub.** Do **not** introduce Notion as the chat task surface.

| Layer | System |
|-------|--------|
| Source of truth | GitHub issues on `cvolkernick/personal-workspace` |
| Board UI / Status | [Buzz Board](https://github.com/users/cvolkernick/projects/1) (ProjectV2) |
| Agent access path | **GraphQL + REST with `GITHUB_TOKEN`** via `buzz_board_cli.py` / `sprint_board.py` |
| Avoid | GitHub Projects **MCP** tools when they return ACL / “resource not accessible by integration” |

## Auth

Set one of:

- `GITHUB_TOKEN`
- `GH_TOKEN`
- `BUZZ_BOARD_GITHUB_TOKEN`

Required scopes (classic): **`repo`** + **`project`**.  
Fine-grained: repository issues read/write + access to user project “Buzz Board”.

Optional env:

| Var | Default |
|-----|---------|
| `BUZZ_BOARD_OWNER` | `cvolkernick` |
| `BUZZ_BOARD_PROJECT_NUMBER` | `1` |
| `BUZZ_BOARD_REPO` | `cvolkernick/personal-workspace` |

**Never** log or commit the token.

## CLI (from monorepo root)

```bash
# Reachability
python3 projects-dashboard/buzz_board_cli.py auth

# List board (JSON)
python3 projects-dashboard/buzz_board_cli.py list
python3 projects-dashboard/buzz_board_cli.py list --status Ready --format table

# Read issue detail (+ board Status when known)
python3 projects-dashboard/buzz_board_cli.py show 21

# Create issue
python3 projects-dashboard/buzz_board_cli.py create --title "…" --body "…"

# Move Status (needs project item node id from list)
python3 projects-dashboard/buzz_board_cli.py set-status --item-id PVTI_… --status "In Progress"
```

Library: `from sprint_board import sprint_payload, set_item_status`.

## Fallback if Projects GraphQL fails

1. Confirm token has `project` scope and can open the board in browser as that user.  
2. Issues-only: `GET /repos/cvolkernick/personal-workspace/issues` (no Status column).  
3. Do **not** switch to Notion without a new decision.

## Related

- Nest guide: `GUIDES/BUZZ_BOARD_AGENT_ACCESS.md` (Buzz workspace)  
- Sprint tab uses the same adapter: `sprint_board.py`  
- Process: `GUIDES/CADENCE_SCRUM_CEREMONIES.md`
