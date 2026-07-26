# B2 web UX — Brain 2 knowledge base

Local visual interface for the global **B2** Obsidian vault: browse notes, search, and **Ask Grok**.

## Paths

| What | Path |
|------|------|
| **Vault** (open in Obsidian) | `~/B2` |
| **This package** | `~/personal-workspace/b2-ux-ux` |
| **Default URL** | http://127.0.0.1:8792/ |

> Vault is at `~/B2` (not under this package) so it stays global and does not
> collide with the `b2` package name on case-insensitive macOS volumes.

## Launch

```bash
./start.sh
# or
python3 server.py --port 8792
```

Env:

- `B2_VAULT_PATH` — override vault root
- `B2_PORT` — default `8792`
- `XAI_API_KEY` or `~/.grok/auth.json` — live Ask Grok; offline grounded fallback otherwise

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | service + note count + auth |
| GET | `/api/notes` | list notes |
| GET | `/api/note?path=` | note body |
| GET | `/api/search?q=` | search |
| POST | `/api/ask` | Ask Grok `{ "question": "..." }` |
| GET | `/api/auth` | credential status |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Layout

```
b2/
  b2_kb/          # pure vault + ask library
  static/         # HTML/CSS/JS UI
  server.py       # entry point
  start.sh
  tests/
```

Vault Markdown is the single source of truth — no dual database.
