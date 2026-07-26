# B2 — Brain 2 (global knowledge base)

**Stable vault paths (open either in Obsidian — same folder):**

```
/Users/cvolkernick/B2
/Users/cvolkernick/personal-workspace/brain2
```

`~/B2` is a symlink to `personal-workspace/brain2` (git-tracked). The vault is **not** named `B2/` inside the monorepo because macOS case-insensitive volumes would collide with the `b2-ux/` UX package.

## What this is

B2 is a global, Obsidian-compatible Markdown knowledge base for use across all sessions, projects, and dashboards. Notes are plain files; Obsidian and the B2 web UX share the same source of truth.

## Open in Obsidian

1. Obsidian → **Open folder as vault**
2. Select `~/B2` (or `personal-workspace/brain2`)
3. Start from [[00 Home - B2 Hub]]

## Web UX (browse / search / Ask Grok)

```bash
cd ~/personal-workspace/b2-ux
./start.sh
# → http://localhost:8792/
```

## Layout

| Path | Purpose |
|------|---------|
| `00 Home - B2 Hub.md` | Entry hub + wikilink graph |
| `HOWTO - Using B2.md` | How to capture, link, and query |
| `domains/` | Domain seed notes (strategy, finance, fitness, …) |
| `map/` | Workspace map and navigation |
| `.obsidian/` | Minimal Obsidian vault config |

## Env overrides

| Variable | Default | Meaning |
|----------|---------|---------|
| `B2_VAULT_PATH` | `~/B2` → `brain2/` | Vault root for the UX |
| `B2_PORT` | `8792` | Local server port |
| `XAI_API_KEY` / `~/.grok/auth.json` | — | Live Ask Grok (offline grounded fallback if missing) |

## Principles

- One vault, many consumers (Obsidian, B2 UX, agents).
- Seed lightly, link densely; grow notes in place rather than bulk-importing operational JSON.
- Do not store secrets, tokens, or raw API dumps here.
