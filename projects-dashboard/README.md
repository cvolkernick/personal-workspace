# Graceful Exit — personal-workspace

Pre-reset / reboot readiness for Grok Build work in this monorepo.

**Goal:** before system updates or reboots, confirm you can stop without:

- losing **session context** (Grok history under `~/.grok/sessions`)
- losing **uncommitted or unpushed** code that would break builds after reset

## What it shows

1. **Verdict** — ready / caution / blocked  
2. **Checklist** — uncommitted files, unpushed commits, sessions on disk, live agents, stashes, local servers  
3. **Graceful exit order** — ordered steps to shut down safely  
4. **Resume kit** — `grok --resume <session-id>` per workspace-linked session (copy buttons)  
5. **Per-project areas** — dirty files + exit-ready flag + related sessions  

Session PIDs die on reboot; **history does not** — resume after reboot.

## Launch

```bash
python3 projects-dashboard/server.py
# or double-click start.command
```

http://127.0.0.1:8765/

```bash
curl -sS 'http://127.0.0.1:8765/api/projects?only_touched=1' | python3 -m json.tool
python3 -m unittest discover -s projects-dashboard/tests -v
```
