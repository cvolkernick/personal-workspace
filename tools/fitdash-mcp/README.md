# FitDash MCP server (read-only)

Slice 2 of #1281. A thin stdio MCP server over the FitDash agent API
(`/api/agent/*`, slice 1 / #1282). Every tool is one `GET`; the JSON comes back
unchanged, including `generated_at` and `as_of`. Nothing writes.

| Tool | Endpoint |
|------|----------|
| `fitdash_targets` | `GET /api/agent/targets` |
| `fitdash_inventory` | `GET /api/agent/inventory` |
| `fitdash_logs(from_date?, to_date?)` | `GET /api/agent/logs?from=YYYY-MM-DD&to=YYYY-MM-DD` (default last 7 days, max 92) |
| `fitdash_plan` | `GET /api/agent/plan` |
| `fitdash_training` | `GET /api/agent/training` |
| `fitdash_recovery` | `GET /api/agent/recovery` |

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
401 handling, unreachable host, token redaction, logs range validation and
tool registration.
