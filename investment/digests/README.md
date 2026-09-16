# Fund developments digests

Daily capital-allocation briefings for Naka (AI CFO). One file per day: `YYYY-MM-DD.md`.

**Convention (Chairman decision 2026-09-16):** digests live here, not in GitHub issues. Issues are actionable tasks with specs and acceptance criteria — not a data store or report archive.

**Flow:**
1. The daily sweep (Great Value Grok's morning cron) writes today's file: developments per holding/watchlist name, Boardy Brief items relevant to the fund, and allocation relevance (up / down / neutral).
2. Naka reads the file, adjusts capital allocation weightings in his environment, then appends his report under `## Naka: actions taken` in the same file — weighting changes made, or no change and why. That section is the durable record.
3. The next day's sweep reads the previous file's action report and surfaces it back.

**History:** digests before 2026-09-14 were filed as GitHub issues (`#727` and earlier); their content remains in those closed issues. The three open at migration time (#727, #754, #783) were moved here verbatim.
