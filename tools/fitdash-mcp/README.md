# FitDash MCP server

Slice 2 of #1281. A thin stdio MCP server over the FitDash agent API
(`/api/agent/*`, slice 1 / #1282). The section tools are one `GET`; the JSON
comes back unchanged, including `generated_at` and `as_of`.

Coaching targets (#1370) are the one settings write. The same agent token
is used. A settings override wins; otherwise the phase calendar applies.

| Tool | Endpoint |
|------|----------|
| `fitdash_targets` | `GET /api/agent/targets` |
| `fitdash_inventory` | `GET /api/agent/inventory` |
| `fitdash_logs(from_date?, to_date?)` | `GET /api/agent/logs?from=YYYY-MM-DD&to=YYYY-MM-DD` (default last 7 days, max 92) |
| `fitdash_plan` | `GET /api/agent/plan` |
| `fitdash_training` | `GET /api/agent/training` |
| `fitdash_recovery` | `GET /api/agent/recovery` |
| `get_coaching_targets(as_of?)` | `GET /api/agent/coaching_targets` |
| `get_coach_card(as_of?)` | `GET /api/agent/coach_card` (includes `phase_report`) |
| `get_phase(as_of?)` | `GET /api/agent/phase` |
| `set_coaching_target(target, value)` | `POST /api/agent/coaching_targets` |

`set_coaching_target` accepts `steps` (1000–30000), `kcal` (1200–6000), and
`hydration_ml` (500–8000). `value: null` clears that override.

`get_phase` returns structured fields from the body-fat median rule already
used by the coach card: `phase` (`cut` or `bulk`), `median_7d`, `hits`,
`hits_needed`, `counted`, `counted_needed`, `median_14d`, `thin`, `paused`,
`flip_applied`, `flip_date`, `flip_trigger`, and `targets`
(`calories`, `protein_g`, `carbs_g`, `fat_g`, `phase`). `get_coach_card`
includes that same object as `phase_report`.

## Install

```bash
python3 -m venv ~/.venvs/fitdash-mcp
~/.venvs/fitdash-mcp/bin/pip install -r tools/fitdash-mcp/requirements.txt
```

## Configuration (env)

| Var | Default | Notes |
|-----|---------|-------|
| `FITDASH_AGENT_TOKEN` | — | Bearer token. Takes precedence over the file. |
| `FITDASH_AGENT_TOKEN_FILE` | `/workspace/.fitdash_agent_token` | Keep it mode 600. Never commit it. |
| `FITDASH_API_BASE` | `https://prism-gateway.tailb1085a.ts.net:10001` | |
| `FITDASH_PROXY` | *(direct)* | e.g. `socks5h://127.0.0.1:1055` from a box without tailnet routing (hostname resolved by the proxy, like `curl --socks5-hostname`). |
| `FITDASH_TIMEOUT` | `20` | seconds |

Errors are clear and never echo the token: 401 → "agent token missing, wrong
or rotated"; connection failure → "FitDash API unreachable at <url> … check
FITDASH_API_BASE / FITDASH_PROXY".

## Register it

Generic MCP client config (Cursor `~/.cursor/mcp.json`, Claude Desktop, etc.):

```json
{
  "mcpServers": {
    "fitdash": {
      "command": "/home/you/.venvs/fitdash-mcp/bin/python",
      "args": ["/path/to/personal-workspace/tools/fitdash-mcp/fitdash_mcp.py"],
      "env": {
        "FITDASH_AGENT_TOKEN_FILE": "/path/to/.fitdash_agent_token",
        "FITDASH_PROXY": "socks5h://127.0.0.1:1055"
      }
    }
  }
}
```

Claude Code CLI equivalent:

```bash
claude mcp add fitdash \
  -e FITDASH_AGENT_TOKEN_FILE=/path/to/.fitdash_agent_token \
  -e FITDASH_PROXY=socks5h://127.0.0.1:1055 \
  -- /home/you/.venvs/fitdash-mcp/bin/python /path/to/personal-workspace/tools/fitdash-mcp/fitdash_mcp.py
```

Drop `FITDASH_PROXY` on machines that are on the tailnet directly.

## Tests

```bash
python3 -m pytest -q tools/fitdash-mcp/tests
```

HTTP is mocked with `httpx.MockTransport`; tests cover pass-through JSON,
401 handling, unreachable host, token redaction, logs range validation,
coaching-target bounds, and tool registration.
