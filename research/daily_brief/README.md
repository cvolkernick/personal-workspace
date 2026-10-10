# Daily Brief (#1091)

Morning (~8 AM ET) and evening (~8 PM ET) editions, published by Grok as a
personal newspaper with a dated archive. Moved out of FCC: the page is now a
sibling route on the **Horizon host** (`research/horizon/server.py`, :8795) at
`/daily-brief`. It is not Horizon model output and never reads Horizon's
world-state or model brief artifacts.

| | |
|---|---|
| Latest (tailnet) | https://prism-gateway.tailb1085a.ts.net/horizon/daily-brief |
| Permalink | `https://prism-gateway.tailb1085a.ts.net/horizon/daily-brief/YYYY-MM-DD/am\|pm` |
| Archive | `https://prism-gateway.tailb1085a.ts.net/horizon/daily-brief/archive` |
| On the Horizon host (LAN debug) | `http://<prism>:8795/daily-brief` |
| JSON | `/daily-brief/api/latest` (money-scrubbed), `/daily-brief/api/editions` |
| Old FCC URLs | `/brief`, `/brief/*`, `/api/brief/*` answer **302** to `BRIEF_BASE_URL` + same subpath + query |

The tailnet URL goes through FCC's existing `/horizon/*` lens (a plain reverse
proxy onto :8795, no FCC chrome), so no new Tailscale mapping is needed.

## Layout

```
research/daily_brief/
  store.py     edition JSON store, schema, publish, To-Do bucketing, Mac tasks builder
  redact.py    keeps FCC marks, balances and capital so-whats off the Horizon host
  render.py    /daily-brief pages + routes (Horizon base palette and type)
  cli.py       publish CLI (also: python3 -m research.daily_brief ...)
  fixtures/    sample editions + Notion Mac tasks page
  tests/       unittest; run: python3 -m unittest discover -s research/daily_brief/tests -t .
financial-command/brief.py   thin shim: same CLI, same store (existing invocations keep working)
financial-command/server.py  /brief* -> 302 to BRIEF_BASE_URL
research/horizon/server.py   small, lazy, error-isolated hook for /daily-brief* (GET + HEAD)
```

Nothing under `research/horizon` that the model or publish path owns is
touched (run_horizon.py, world_state/brief artifacts, fixtures, version_id
files, OFFLINE_PUBLISH.md), and neither is `investment/book_channel_map.json`.

## Rules on the Horizon host

- **No FCC money.** No FCC marks, balances or capital so-whats render here.
  Every edition goes through `redact.scrub_edition` before rendering and
  before `/daily-brief/api/latest`. Withheld: whole money sections (id/title
  like business, money, treasury, fund, markets, portfolio, ynab-style
  budget/bills/payments), and any line, todo, Mac task, quote, theme or
  front-page paragraph with an amount (`$900`, `0.05 BTC`), an account or
  ticker word (USDC, Coinbase, balance, NAV, P&L, runway, ...) or a link into
  FCC. A money headline is replaced by "Front page held for the private
  edition". The footer says how many lines were held back. The match is
  deliberately broad: a false positive only hides a line. The stored edition
  JSON is not modified.
- **No model widgets.** No regime, weather or model widgets. The newspaper's
  local weather box is not rendered here (the field stays in the schema and
  the store). The footer says "Not Horizon model output."
- **No FCC chrome.** No FCC nav, tab bar, manifest or back-link. Links are
  root-absolute under `/daily-brief` so they survive the lens prefixing;
  in-page anchors carry the page path because the lens injects a `<base>`.
- Horizon is on hold; this adds no Horizon data and does not trigger the
  Horizon pipeline.

## Publish (Grok routines, over SSH) - command unchanged

```bash
ssh prism-gateway 'python3 ~/personal-workspace/financial-command/brief.py publish -' < edition.json
# same thing, new home:
ssh prism-gateway 'cd ~/personal-workspace && python3 -m research.daily_brief publish -' < edition.json
# file already on prism, via the wrapper:
bash ~/personal-workspace/financial-command/fcc brief publish /path/to/edition.json
# dry run:
ssh prism-gateway 'python3 ~/personal-workspace/financial-command/brief.py validate -' < edition.json
```

Success (exit 0):
`{"ok": true, "path": ".../2026-10-09-am.json", "url": "/daily-brief/2026-10-09/am", "public_url": "https://prism-gateway.tailb1085a.ts.net/horizon/daily-brief/2026-10-09/am", ...}`.
Put `public_url` in the chat brief (`DAILY_BRIEF_PUBLIC_BASE` overrides the
prefix). Schema errors exit 2 with `{"ok": false, "problems": [...]}` on
stderr and write nothing.

- Idempotent: the same `date` + `edition` overwrites (atomic replace).
- `date` / `edition` may be omitted: derived from `published_at` (or now) in
  America/New_York (am before 15:00 ET, pm after). See #1042.
- Rows with `status: "Done"` and Mac task items with `checked: true` are dropped at publish.

Store (unchanged, no migration): `~/.local/share/fcc/brief-editions/YYYY-MM-DD-{am|pm}.json`.
Override with `DAILY_BRIEF_DIR` (the legacy `FCC_BRIEF_DIR` still works).

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
`roadside`, `ynab` (Mon). Money sections (`business`, `ynab`, ...) stay in the stored edition but are withheld from the Horizon-hosted page (see "Rules on the Horizon host"). The To-Do block is inserted after `day-ahead` /
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
Todo List database). These are steps that need someone at the computer. One line
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
python3 -m research.daily_brief mac-tasks - < page.md   # -> {"source_url", "fetched_at", "items", "page_found"}
python3 -m research.daily_brief mac-tasks --missing     # page not found -> page_found: false, items: []
```

`--fetched-at` / `--source-url` override the defaults (`fetched_at` defaults
to now ET). Same logic in Python: `research.daily_brief.store.mac_tasks_from_markdown(md, fetched_at=...)`.
Put the output into the edition JSON as `mac_tasks`. If the fetch fails,
publish with `page_found: false` (or omit the block); never block the paper on
Notion. Fixture: `fixtures/notion-mac-tasks-page.md` (structure mirrors the
live page; text is sample copy, plus a checked and a nested box for coverage).

## Degrades without Grok

The page only renders stored JSON: no LLM, no network, no credentials. If the
publisher is down, `/daily-brief` keeps showing the last edition with a
"newer edition not published yet" banner once it is older than 14 h.

## Deploy (after merge; prism smoke test owned by Forge/Grok)

1. Merge into `work/treasury`. `workspace-sync.timer` pulls within ~5 min and
   restarts `financial-command.service`, so the `/brief` 302 goes live on its own.
2. `workspace-sync` does **not** restart `horizon-dashboard.service`, so restart
   it once to load the `/daily-brief` hook. Until then `/brief` redirects to a
   404. Caveat: the unit's `ExecStart` has `--bootstrap`, and every start with
   that flag re-runs the offline pipeline and writes a new world-state/brief
   version into `research/horizon/data/`. Horizon is on hold, so agree with
   Meridian first. Either restart as-is, or drop `--bootstrap` first (a
   `brief_latest.json` already exists, so the server will not bootstrap):

   ```bash
   mkdir -p ~/.config/systemd/user/horizon-dashboard.service.d
   printf '[Service]\nExecStart=\nExecStart=/usr/bin/python3 %%h/personal-workspace/research/horizon/server.py --host 0.0.0.0 --port 8795 --no-browser\n' \
     > ~/.config/systemd/user/horizon-dashboard.service.d/no-bootstrap.conf
   systemctl --user daemon-reload
   systemctl --user restart horizon-dashboard.service
   ```
3. Smoke on prism:

   ```bash
   ls research/horizon/data/briefs | wc -l                                       # note before step 2; must not change if --bootstrap was dropped
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8795/daily-brief      # 200
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/horizon/daily-brief/archive   # 200
   curl -sI http://127.0.0.1:8000/brief/2026-10-09/am | grep -i '^location'        # /horizon/daily-brief/2026-10-09/am
   curl -s http://127.0.0.1:8000/horizon/daily-brief | grep -cE 'USDC|Coinbase|\$[0-9]'   # 0
   ```

No new systemd unit and no new port: the page runs inside the existing
Horizon process.

### Optional: own Tailscale path (not required, not run)

To serve the page without going through the FCC process, mount the path on
the existing tailnet-only hostname (never on :8443, which is Funnel/public):

```bash
tailscale serve --bg --set-path /daily-brief http://127.0.0.1:8795/daily-brief
tailscale serve status    # expect: /daily-brief proxy http://127.0.0.1:8795/daily-brief
```

Then point FCC's redirect at it: add `Environment=BRIEF_BASE_URL=/daily-brief`
to a `financial-command.service` drop-in and restart FCC; set
`DAILY_BRIEF_PUBLIC_BASE=https://prism-gateway.tailb1085a.ts.net` for the CLI's
`public_url`. Undo with `tailscale serve --set-path /daily-brief off`.

## Rollback

Revert the PR. FCC's in-process `/brief` renderer, `brief.py` and the old
tests come back; the Horizon hook goes away. The edition store is shared and
untouched, so every edition published in between still renders under
`/brief`. After the revert merges: `financial-command.service` restarts via
workspace-sync; restart `horizon-dashboard.service` (same `--bootstrap`
caveat) and remove the optional Tailscale path / env drop-ins if they were added.
