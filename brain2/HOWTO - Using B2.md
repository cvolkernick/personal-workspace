# HOWTO — Using B2

B2 is an Obsidian vault plus a small local web app. Both read the same files under:

```
/Users/cvolkernick/B2
# same folder (git-tracked):
/Users/cvolkernick/personal-workspace/brain2
```

## Open the vault

**Obsidian:** File → Open folder as vault → select `~/B2` (or `brain2`).

**Web UX:**

```bash
cd ~/personal-workspace/b2-ux && ./start.sh
```

Browse notes, full-text search, and **Ask Grok** at http://localhost:8792/

## Capture knowledge

| Do | Don't |
|----|-------|
| Write durable decisions, maps, how-tos | Dump secrets, API keys, OAuth tokens |
| Link with `[[Note Title]]` | Rely only on folder nesting |
| Keep operational JSON in personal-workspace tools | Bulk-import session transcripts |
| Seed domain notes, then grow them | Leave empty "TODO only" stubs |

### Wikilinks

```markdown
See [[Strategy & Bets]] and the [[00 Home - B2 Hub]].
```

Titles match note filenames without `.md`. Nested notes still use the **title** (e.g. `[[Finance & Investment]]` for `domains/Finance & Investment.md`).

### Suggested structure

- Hub + HOWTO at vault root (always navigable).
- `domains/` — one note per major life/work area.
- `map/` — cross-cutting maps (workspace layout, system relationships).
- Optional later: `daily/`, `projects/`, `people/` — only when needed.

## Search and Ask Grok

1. **Search** (UX or Obsidian): keyword over note titles and bodies; results show path/title.
2. **Ask Grok** (UX): question → retrieve top matching notes → answer grounded in those notes.
   - Live xAI when `XAI_API_KEY` or `~/.grok/auth.json` is available.
   - Otherwise offline grounded path: answer built only from retrieved snippets (no invented vault facts).
   - If nothing matches, the reply says the vault lacks relevant material.

### Good questions

- "What are my high-conviction thematic bets?"
- "Where does fitness data live and which dashboard shows it?"
- "What is the dual-venue treasury strategy?"

### Weak questions

- Anything requiring live balances, tokens, or session state (ask the relevant dashboard instead).

## Related systems

- [[Personal Workspace Map]] — Orchestra and subordinate UIs
- [[Agents & Tooling]] — Grok, dashboards, automation initiatives
- Source planning files (outside vault): `personal-workspace/strategy/`, `initiatives/`

## Maintenance

- Weekly: skim hub + domain notes; fold wins and next actions.
- After a big decision: update [[Strategy & Bets]] or the right domain note the same day.
- Prefer editing in Obsidian or the web UX; both write/read Markdown on disk.

Back to [[00 Home - B2 Hub]].
