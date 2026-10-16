# x-analytics: weekly X metrics for @ChrisVolkernick

Runbook for `twitter_metrics.py` and its systemd user timer
(`deploy/units/x-weekly-analytics.{service,timer}`).

This replaces the OpenClaw gateway cron job `x-weekly-analytics`
(Mon 10:00 America/New_York). That job asked an LLM agent to run the script and
write a weekly report with insights; the gateway is disabled and the job had
failed 25 runs in a row (LLM credits exhausted). **The timer now runs the script
directly: no gateway, no LLM.**

## What it does

Every Monday 10:00 America/New_York (`Persistent=true`, so a missed run fires on
the next boot/login) the service runs the script once:

1. X API v2 (OAuth 1.0a, 3 GET requests): account public metrics, the 20 most
   recent tweets (public metrics incl. impressions), a sample of 100 followers
   (verified count).
2. Prints the report to stdout (journald) and writes it to the output dir.

### Output

| Where | What |
|-------|------|
| `journalctl --user -u x-weekly-analytics` | the full text report plus one `--- JSON OUTPUT ---` line |
| `~/x-analytics-output/YYYY-MM-DD.txt` | the same report |
| `~/x-analytics-output/YYYY-MM-DD.json` | machine-readable snapshot (`metrics`, `verified_sample_count`, `tweet_averages`, `errors` if any) |
| `~/x-analytics-output/latest.json` | copy of the newest snapshot |

The directory is mode 0700, files 0600, and it is **outside the repo** (not a
git path, not synced). Override with `--output-dir PATH` or
`X_ANALYTICS_OUTPUT_DIR`.

The report includes "change vs previous snapshot" (followers, following, tweets,
listed, avg impressions, verified sample) computed from the previous
`YYYY-MM-DD.json`. The old job's prompt asked the LLM to "compare to previous
week if we have data", but the old script stored nothing, so this is new and
purely arithmetic.

**Dropped:** the LLM "insights" step (best-performing content, suggested tactics,
verified-follower growth commentary, impression trends) and the Discord
`announce` delivery. Nothing delivers the report anywhere yet; it sits in the
journal and the output dir. Wiring a delivery target is an open question for
Chris.

### Exit codes (systemd marks the run failed on non-zero)

| Code | Meaning |
|------|---------|
| 0 | all three calls succeeded, output written |
| 1 | fatal: credentials missing, account lookup failed (401, 429, network, ...), or output not writable. No snapshot is written for an API failure. |
| 2 | partial: account metrics fetched, but tweets and/or followers failed (e.g. 403 on the followers endpoint for the API tier). Snapshot written with an `errors` list. |

On any API error only `HTTP <status> - <error title>` is printed. Response
bodies and exception messages are never printed or logged, and credential
values are scrubbed from everything the script emits (see the tests).

## Credentials

Read only from the environment or `~/.secrets/x-api.env` (`KEY=VALUE`, optional
`export`, optional quotes): `X_CONSUMER_KEY`, `X_CONSUMER_SECRET`,
`X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`. Environment values win over the
file. Keep the file `chmod 600`. The unit does not use `EnvironmentFile=`; the
script reads the file itself. **Never commit this file.**

## Dependencies

`/usr/bin/python3` with `requests` and `requests_oauthlib` (both present in
prism's system python; no venv needed). CI installs them from
`requirements-ci.txt`.

## Install (on prism, manual; nothing here auto-deploys)

`deploy/` is a manual prefix in `deploy/path_unit_map.json` and these units are
deliberately **not** in `install_remote.sh`'s `ALL_UNITS`. Like youtube-groom,
the script runs from `~/.local/lib/`, because prism's `work/treasury` checkout
does not contain `scripts/` or `deploy/` (they live on `master`).

```bash
# from a checkout of master (or this PR's branch) on a machine that can ssh to prism
scp scripts/x-analytics/twitter_metrics.py prism-agent@prism-gateway:/tmp/twitter_metrics.py
ssh prism-agent@prism-gateway '
  mkdir -p ~/.local/lib/x-analytics ~/.config/systemd/user
  install -m 0755 /tmp/twitter_metrics.py ~/.local/lib/x-analytics/twitter_metrics.py && rm /tmp/twitter_metrics.py'
scp deploy/units/x-weekly-analytics.service deploy/units/x-weekly-analytics.timer \
    prism-agent@prism-gateway:~/.config/systemd/user/
ssh prism-agent@prism-gateway '
  systemctl --user daemon-reload
  python3 ~/.local/lib/x-analytics/twitter_metrics.py --check-config   # no network, no values
  systemd-analyze calendar "Mon *-*-* 10:00:00 America/New_York"        # confirm next elapse
  systemctl --user enable --now x-weekly-analytics.timer
  systemctl --user list-timers x-weekly-analytics.timer'
```

## Test

```bash
# no network, no credentials needed
python3 -m pytest scripts/x-analytics/tests -q          # from repo root
python3 scripts/run_ci_tests.py --python-only           # full CI runner (auto-discovers the suite)
```

First live run (this calls the real X API with the real credentials; do it
deliberately, once):

```bash
systemctl --user start x-weekly-analytics.service
journalctl --user -u x-weekly-analytics -n 60 --no-pager
ls -l ~/x-analytics-output
```

## Roll back

```bash
systemctl --user disable --now x-weekly-analytics.timer
rm ~/.config/systemd/user/x-weekly-analytics.{service,timer}
rm -r ~/.local/lib/x-analytics
systemctl --user daemon-reload
# optional: rm -r ~/x-analytics-output
```

There is nothing to restore on the OpenClaw side: its gateway is disabled and
the cron job only ever ran the script via an LLM. The script's sha256 before
the port was `2c82e8fa...` (archive `openclaw-prism-20261014.tar.gz`).

## Differences from the OpenClaw script

See the PR description. In short: credentials load lazily (not at import),
request timeouts, status+title-only errors, non-zero exit on failure, output dir
and snapshots, previous-snapshot delta. The API calls, fields, ranking and the
report layout are unchanged.
