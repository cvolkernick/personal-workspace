# Daily Brief newspaper (FCC, #1091)

Morning (~8 AM ET) and evening (~8 PM ET) briefs, published by Grok as a
personal newspaper with a dated archive, served by FCC on prism.

**Stable URL (tailnet only):** https://prism-gateway.tailb1085a.ts.net/brief
(newest edition). Permalink: `https://prism-gateway.tailb1085a.ts.net/brief/YYYY-MM-DD/am|pm`.
Archive: `/brief/archive`.

## Publish (Grok routines, over SSH)

```bash
# edition JSON on stdin (preferred: no temp file on the Pi)
ssh prism-gateway 'python3 ~/personal-workspace/financial-command/brief.py publish -' < edition.json

# or a file already on prism, via the `fcc` wrapper (spec name: `fcc brief publish`)
bash ~/personal-workspace/financial-command/fcc brief publish /path/to/edition.json

# dry-run validation, no write
ssh prism-gateway 'python3 ~/personal-workspace/financial-command/brief.py validate -' < edition.json
```

Output on success (exit 0):
`{"ok": true, "path": ".../2026-10-09-am.json", "url": "/brief/2026-10-09/am", ...}`.
Put `https://prism-gateway.tailb1085a.ts.net` + `url` in the chat brief.
Schema errors exit 2 with `{"ok": false, "problems": [...]}` on stderr and write nothing.

- Idempotent: the same `date` + `edition` overwrites (atomic replace).
- `date` / `edition` may be omitted: they are derived from `published_at`
  (or now) in **America/New_York** (am before 15:00 ET, pm after). See #1042.
- `published_at` may be omitted: defaults to now (ET).
- Rows with `status: "Done"` are dropped from `todos[]` at publish.

Store: `~/.local/share/fcc/brief-editions/YYYY-MM-DD-{am|pm}.json`
(override with `FCC_BRIEF_DIR`).

## Edition schema

```jsonc
{
  "date": "2026-10-09",            // Eastern date, YYYY-MM-DD
  "edition": "am",                 // "am" | "pm"
  "vol": 1, "no": 2,               // int or string (optional)
  "published_at": "2026-10-09T08:02:00-04:00",
  "theme": "Ship the paper",       // day theme (optional)
  "quote": {"text": "...", "author": "..."},   // or a plain string
  "weather": {"summary": "Partly cloudy", "high": 88, "low": 74, "icon": "⛅", "rain": "40% after 2 PM"}, // or a string
  "front_page": {"headline": "...", "subhead": "...", "body": "para 1\n\npara 2", "image": "https://..."},
  "sections": [
    {"id": "day-ahead", "title": "The Day Ahead", "items": ["9:00 AM ...", {"text": "...", "link": "https://..."}]}
  ],
  "todos": [ /* see below; [] renders "Nothing open" */ ]
}
```

Section ids are lowercase slugs. Suggested ids: am `day-ahead`, `business`,
`fleet`, `shipped`, `drafted-replies`; pm `tomorrow`, `loose-ends`,
`roadside`, `ynab` (Mon). The To-Do block is inserted after `day-ahead` /
`tomorrow` (or where a section with id `todo` sits). Links must be http(s).

### `todos[]` (Notion Todo List snapshot)

Source: Notion **Todo List**, data source
`collection://2f6cba2a-d31b-80b1-a138-000b1721d50d`. At publish time, query
every row whose `Status` is not `Done` and map:

| field        | type              | from Notion                                   |
| ------------ | ----------------- | --------------------------------------------- |
| `title`      | string (required) | `Task name`                                   |
| `status`     | `"Not started"` \| `"In progress"` | `Status`                     |
| `due`        | `YYYY-MM-DD`, ISO datetime, or null | `Due date` start           |
| `owner`      | string or null    | `Owner: …` line in the page body (else Assignee name) |
| `priority`   | string or null    | `Priority: …` line in the page body           |
| `notion_url` | https URL or null | page URL                                      |

Rendering buckets (Eastern dates, relative to the **edition's** date so
archive pages show what was open then): Overdue (< date, red) / Due today /
Due this week (next 7 days) / Later / No date. The Reminders card shows the
overdue + due-today count and top 3.

## Degrades without Grok / LLM

FCC only renders stored JSON: no LLM, no network, no credentials. If the
publisher is down (e.g. Grok CLI sign-in on prism expired), `/brief` keeps
showing the last edition with a "newer edition not published yet" banner once
it is older than 14 h.

## Live Notion refresh

Not shipped: there is no Notion integration token on prism, and #1091 says not
to add one. The To-Do section is snapshot-only and says so in the footer.
