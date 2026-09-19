# Fund developments digests

Daily capital-allocation briefings for Naka (AI CFO). One file per day: `YYYY-MM-DD.md`.

**Convention (Chairman decision 2026-09-16):** digests live here, not in GitHub issues. Issues are actionable tasks with specs and acceptance criteria — not a data store or report archive.

**Flow:**
1. The daily sweep (Great Value Grok's morning cron) writes today's file: developments per holding/watchlist name, Boardy Brief items relevant to the fund, and allocation relevance (up / down / neutral). The sweep should also emit **concrete proposed deltas** (e.g. SATA +1) once it has material, cited developments — not ticker noise.
2. The **assessment committee** (`python3 -m treasury.bias_weight_loop --digest <file> --apply`) is the decision engine (#768). Small residual-mix moves auto-apply to `investment/consider_share.json` `loop_adjustments`. Pin changes, 60/40 sleeve-target changes, and oversized batches **stage** in `investment/bias_weight_staged.json` for the Chairman. Quiet days log one line.
3. Minutes + decision record append to `investment/fund_manager_journal.md` (committee minutes: scout / thesis / risk / critic). Naka reviews/audits; he is not the decider. Historical `## Naka: actions taken` sections remain as archive.

Chairman override: `python3 -m treasury.bias_weight_loop --override SATA --delta 1 --why "…" --apply` — applies immediately and is logged as an override, not a loop decision.

**History:** digests before 2026-09-14 were filed as GitHub issues (`#727` and earlier); their content remains in those closed issues. The three open at migration time (#727, #754, #783) were moved here verbatim.
