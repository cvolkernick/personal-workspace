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
- Items with `checked: true` are dropped from `mac_tasks.items` at publish.

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
  "todos": [ /* see below; [] renders "Nothing open" */ ],
  "mac_tasks": {                   // optional, see below; absent renders "Nothing waiting at the Mac"
    "source_url": "https://app.notion.com/p/3efcba2ad31b814aa4ece8389594cdc5",
    "fetched_at": "2026-10-09T08:01:00-04:00",
    "page_found": true,
    "items": [{"text": "Set up SuperTake"}]
  }
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

### Building `todos[]` from Notion (Grok, at publish time)

Verified against the live data source on 2026-10-09 (Notion connector,
read-only SQL):

```sql
SELECT "Task name" AS title, "Status" AS status,
       "date:Due date:start" AS due, url AS notion_url
FROM "collection://2f6cba2a-d31b-80b1-a138-000b1721d50d"
WHERE "Status" != 'Done'
ORDER BY due
```

Then read each page body for `Owner: …` / `Priority: …` lines (fetch the
page) and set `owner` / `priority`, else `null` (owner falls back to the
`Assignee` name). If the Notion query fails, publish with the previous
edition's `todos[]` or `[]`; never block the paper on Notion.

### `mac_tasks` (Notion "Mac tasks" checklist snapshot)

Every am and pm edition has a **Mac tasks** section (directly after To-Do)
listing the **unchecked** to-do checkboxes in the body of the Notion page
"Mac tasks: do these when I'm at the MacBook"
(<https://app.notion.com/p/3efcba2ad31b814aa4ece8389594cdc5>, an item in the
Todo List database). These are steps that need Chris at the computer. One line
per unchecked box; the count also appears in the Reminders card at the top
("N Mac tasks", links to the section).

Snapshot-only, like `todos[]`: prism has no Notion token, so the publisher
fetches the page and passes the block in with the edition JSON.

| field        | type                         | meaning                                                 |
| ------------ | ---------------------------- | ------------------------------------------------------- |
| `source_url` | https URL or null            | the Notion page (linked from the section header)        |
| `fetched_at` | ISO-8601 timestamp or null   | when the page was read (shown in the footer, ET)        |
| `page_found` | bool (default `true`)        | `false` = page missing / fetch failed                   |
| `items`      | list of `{"text": string}`   | unchecked boxes, page order; bare strings are accepted and normalized to `{text}`; `{"checked": true}` items are dropped at publish |

Validation: `source_url` must be https, `fetched_at` must parse, `page_found`
must be a bool, every item needs non-empty `text`. Text is HTML-escaped on render.

**Empty state.** "Nothing waiting at the Mac" is shown when:
- `page_found` is `false` (any `items` are ignored), or
- `items` is empty (every box checked, or no boxes), or
- **the block is absent / null** (editions published before this field, or a
  publisher that skipped it). Choice: render the same empty state rather than
  omit the section, so the section and the Reminders count ("0 Mac tasks") sit
  in a stable place in every edition; the footer adds "Mac tasks: no snapshot
  in this edition." so an absent block is distinguishable from a clear list.
  The section carries `data-mac-state="items|clear|missing|absent"`.

#### Building `mac_tasks` from Notion (Grok, at publish time)

Verified on 2026-10-09 with the Notion connector's `notion-fetch` (read-only).
The page body comes back as enhanced Markdown inside `<content>…</content>`;
each to-do block is one line:

```text
- [ ] Set up SuperTake          <- unchecked: include
- [x] Something already done    <- checked: skip
	- [ ] nested sub-step        <- nested to-dos are tab-indented; include as their own line
```

Prose paragraphs, headings and plain bullets are ignored. On 2026-10-09 the
page had an intro paragraph plus flat (non-nested) unchecked to-dos only.

Helper (pure, no network): pipe the fetched page text into

```bash
python3 financial-command/brief.py mac-tasks - < page.md   # -> {"source_url", "fetched_at", "items", "page_found"}
python3 financial-command/brief.py mac-tasks --missing     # page not found -> page_found: false, items: []
```

`--fetched-at` / `--source-url` override the defaults (`fetched_at` defaults
to now ET). Same logic in Python: `brief.mac_tasks_from_markdown(md, fetched_at=...)`.
Put the output into the edition JSON as `mac_tasks`. If the fetch fails,
publish with `page_found: false` (or omit the block); never block the paper on
Notion. Fixture: `brief-samples/notion-mac-tasks-page.md` (structure mirrors the
live page; text is sample copy, plus a checked and a nested box for coverage).

## Degrades without Grok / LLM

FCC only renders stored JSON: no LLM, no network, no credentials. If the
publisher is down (e.g. Grok CLI sign-in on prism expired), `/brief` keeps
showing the last edition with a "newer edition not published yet" banner once
it is older than 14 h.

## Live Notion refresh

Not shipped: there is no Notion integration token on prism, and #1091 says not
to add one. The To-Do and Mac tasks sections are snapshot-only and say so in the
footer.

## Deploy

No new service, port, or timer. `server.py` routes `/brief*` and
`/api/brief/*` to `brief.py` (loaded lazily; a render error returns a 500
page for the brief only, never the rest of FCC).

1. Merge into `work/treasury`.
2. `workspace-sync.timer` (every 5 min) pulls and restarts
   `financial-command.service` on change. Manual equivalent:
   `systemctl --user restart financial-command.service` on prism.
3. Smoke: `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/brief`
   on prism should print `200` (empty-store page until the first publish).
4. The edition store dir is created on first publish.

The morning/evening routines that call `publish` stay on Grok's side
(Grok's scheduler, ~8 AM / ~8 PM ET); nothing is installed on prism for them.
