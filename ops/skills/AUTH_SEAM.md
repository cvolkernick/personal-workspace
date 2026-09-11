# Skill auth seams (#580 E7)

Path for versioned skills: `ops/skills/`.

## Hatch custom skills (audit)

Live copies live on the Hatch agent VM under `~/workspace/skills/`. They were
**not reachable from this Mac** on 2026-09-11. Import with:

```bash
python3 ops/skills/import_skills.py --from /path/to/workspace/skills --to ops/skills/hatch
```

| Skill | Portable as-is? | Platform provides | Foreign stack must reimplement |
|-------|-----------------|-------------------|--------------------------------|
| `x-post-reader` | **Yes** (public X API, no auth) | nothing | nothing |
| `github` | No | `dynamic_credentials.py`, `hsurr:*` surrogates, `hatch_gws_cli` | GitHub auth (`gh` / PAT / GitHub App) and any hatch-only helpers |
| `vercel` | No | same hatch helpers | Vercel token / git-only deploy (#569) |
| `ynab` | No | same hatch helpers | YNAB personal access token **outside git** |

Until import, placeholders sit in `ops/skills/hatch/<name>/README.md`.

## Grok Build user skills

Copied from `~/.grok/skills/` (SKILL.md + scripts, no credential files) into
`ops/skills/grok-build/`. Auth stays in env / MCP credential stores, which the
backup exclude-list refuses.
