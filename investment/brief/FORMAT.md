# Naka's daily fund notes: file format

Naka writes one file per day, **`investment/brief/YYYY-MM-DD.md`** (ET date), by **6:17 AM ET, seven days a week**,
commits it to **`work/treasury`**, and appends the same *actions taken* text to `investment/naka-actions.md`.
The Daily Brief renders it as the Fund watch section (`research/daily_brief`). Direction stays advisory: the file
authorizes nothing and the renderer never turns it into a trade signal.

The Brief never writes, edits or backfills this file, and it never shows another day's file as today's.
Do not put balances, share quantities or account ids in it (the repo is public): weights are percentages only.

## Rules

1. UTF-8 Markdown, at most 64 KB. Text is treated as untrusted: HTML is shown as plain text, only `http(s)` links are linked.
2. **Front matter** first: a `---` line, `key: value` lines, a `---` line.
   - `date`: required, `YYYY-MM-DD`, must equal the file name's date.
   - `filed_at`: required, ISO-8601 with offset, e.g. `2026-10-16T06:12:00-04:00`.
   - `author`: optional.
3. Four `## ` sections, **these exact headings, in this order**:
   1. `## Developments`
   2. `## Relevance`
   3. `## Naka: direction (advisory)`
   4. `## Naka: actions taken`
4. `## Developments`: one bullet per item, this exact shape (em dash `—` or ` - ` after the ticker):
   `- **TICKER** — one-line summary of the last ~48h. Source: https://full-url`
   - One bullet per source; repeat the ticker for a second item.
   - A ticker with nothing new: `- **BE** — No developments in the last 48h. Source: none`
   - Cover every ticker you want shown. Tickers outside `investment/consider_share.json` are allowed and are listed as "Other tickers".
5. `## Relevance`: one line per ticker: `- **TICKER**: pinned` or `- **TICKER**: watch only`.
   Only SPCX and TSLA (the `pins` in `consider_share.json`) read `pinned`. Every other ticker reads `watch only`.
   The renderer takes pinned vs watch only from `consider_share.json`; if a line disagrees it flags it and the JSON wins.
6. `## Naka: direction (advisory)`: free text in your own words. Paragraphs and `- ` bullets only (no tables, no raw HTML).
7. `## Naka: actions taken`: what you changed, or the single word `none`. Same text goes into `investment/naka-actions.md`
   under `## Naka: actions taken — YYYY-MM-DD` (append-only, one entry per day).

## What the renderer does

| Situation | Result |
|---|---|
| File for the edition's ET date is missing | "Naka's fund notes not yet filed for YYYY-MM-DD." No content. The newest earlier file is named but never shown. |
| Front matter `date` differs from the file name / edition date | Treated as not filed for today. The content is not shown. The mismatch is stated. |
| Section missing | Renders what it finds and flags `Missing required section(s): ...`. |
| Unknown extra `## ` section | Shown collapsed under its heading as "extra section". |
| Bullet that does not match the shape | Not rendered as a development. Listed under "Lines that did not match the format". |
| Sections out of order, no `filed_at`, bad timestamp | Rendered; a "Format notes" line says so. |
| No front matter and no recognizable section | "A file exists ... not in the agreed format." No content. |

Shown with the content: the file date, `filed_at`, the git commit time (when the checkout has history) and the
file's modification time on prism. Check a file before committing:
`python3 -m research.daily_brief fund-check investment/brief/YYYY-MM-DD.md` (exit 0 = clean).

## Complete example

This exact text is `research/daily_brief/fixtures/fund-notes-example.md`; a test parses it and fails if this block drifts.

````markdown
---
date: 2026-10-16
filed_at: 2026-10-16T06:12:00-04:00
author: Naka
---

## Developments

- **SPCX** — SAMPLE: one-line summary of what happened in the last ~48h. Source: https://example.com/sample/spcx-1
- **TSLA** — SAMPLE: a second ticker, one line, one link. Source: https://example.com/sample/tsla-1
- **TSLA** — SAMPLE: the same ticker again; one bullet per source. Source: https://example.com/sample/tsla-2
- **MSTR** — SAMPLE: tickers outside consider_share.json are allowed. Source: https://example.com/sample/mstr-1
- **BE** — No developments in the last 48h. Source: none

## Relevance

- **SPCX**: pinned
- **TSLA**: pinned
- **MSTR**: watch only
- **BE**: watch only

## Naka: direction (advisory)

SAMPLE: free text in Naka's own words. Plain paragraphs and "- " bullets only.

- SAMPLE bullet one.
- SAMPLE bullet two.

## Naka: actions taken

none
````
