---
title: "Restore agent home context from the private backup"
tags: [ops, context, restore]
status: active
created: 2026-09-11
---

# Restore agent context (fresh VM)

**Source of home context:** private repo `cvolkernick/agent-context` (E1).  
**Source of tools / job mirrors / SKILL.md:** this public repo.  
**Never** restore `.env`, `mcp_credentials.json`, `.ssh` keys, wallet, or vault
files — `persist.py restore` refuses them even if they snuck into the remote.

## End-to-end (verified in tests)

`ops/context/tests/test_backup.py` builds a fixture home with MEMORY.md +
secrets, `--apply` backup to a temp remote, restore to a new dir, and asserts
MEMORY.md matches and secrets are absent.

## Operator steps

1. Clone this public repo (tools).
2. Clone the private backup (data):

   ```bash
   git clone git@github.com:cvolkernick/agent-context.git ~/.cache/personal-workspace/agent-context
   ```

3. Dry-run, then apply onto the new home:

   ```bash
   python3 ops/context/persist.py restore \
     --source ~/.cache/personal-workspace/agent-context \
     --to "$HOME"
   python3 ops/context/persist.py restore --apply \
     --source ~/.cache/personal-workspace/agent-context \
     --to "$HOME"
   ```

4. Copy standing files if the backup used the `ops/context/` copies:

   ```bash
   cp ops/context/USER.md ~/USER.md
   # merge AGENTS_CONVENTIONS.md into ~/AGENTS.md conventions
   cp ops/context/TOOLS.md ~/TOOLS.md
   ```

5. Re-auth connectors from `CONTEXT.md` §6. Vault and wallet cannot be
   restored — re-issue.

6. Reinstall timers (`ops/INSTALL_CONTEXT_PERSISTENCE.md`). Re-create Hatch
   platform jobs from `ops/jobs/platform-*.md` (definitions only; live DB is
   gone until re-entered).
