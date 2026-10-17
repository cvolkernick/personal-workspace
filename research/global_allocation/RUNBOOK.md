# Global allocation refresh

Price snapshots for gold, bitcoin, and listed equities, plus the monthly rebuild. No orders, no accounts, no personal portfolio data.

## Which runner is live

The Actions workflow is not installed. Copying `ops/github-workflows/global-allocation.yml` into `.github/workflows/` needs a token with `workflow` scope. This change does not do that copy.

The live runner is the box fallback below. It is not scheduled by this change. The user timers are a second fallback and ship disabled. Do not enable them from the remote installer. They are not in its unit list.

Nothing in this package runs on FCC, Vercel, or Funnel.

## Box fallback

Copy the script once:

```sh
mkdir -p "$HOME/.local/lib/global-allocation"
cp research/global_allocation/refresh_box.sh "$HOME/.local/lib/global-allocation/refresh.sh"
chmod 755 "$HOME/.local/lib/global-allocation/refresh.sh"
```

The script reads the checkout at `$HOME/personal-workspace`, or `$GLOBAL_ALLOCATION_ROOT` when that is set. An Alpha Vantage key is optional. Put it in the routine's environment as `ALPHAVANTAGE_API_KEY`. Do not write the key into the script, the unit, or the snapshot.

A Grok routine on the box runs:

```sh
"$HOME/.local/lib/global-allocation/refresh.sh" weekly
```

Monthly:

```sh
"$HOME/.local/lib/global-allocation/refresh.sh" monthly
```

Weekly cadence: Friday 22:15 UTC. Monthly cadence: 12:00 UTC on the first. The routine commits only new snapshot files plus the index append, with message `chore(global-allocation): <kind> snapshot <date> [skip ci]`. If the default-branch push is refused, push a side branch and open a pull request. Do not force-push.

## Disabled user timers

`deploy/units/global-allocation-weekly.service` and `.timer`, and the monthly pair, match those times (`Persistent=true`). They call the copied script. They are not enabled.

To arm them later, copy the four unit files into the user unit directory and enable the timers. To stop them: `systemctl --user disable` on each timer. Snapshots already committed stay.

## Run by hand

From the checkout, with the package root on `PYTHONPATH` (a normal checkout already is):

```sh
python3 -m research.global_allocation.refresh --kind weekly
python3 -m research.global_allocation.validate_snapshots
```

`--date YYYY-MM-DD` overrides the America/New_York run date. `--dry-run` prints sources and writes nothing. A second run for the same date and kind prints `exists` and does not change the file or the index. A correction is `--revise "reason"`, which writes a new `*_rN.json` and leaves the original bytes alone.

No key, or a failed equity response: the run still exits 0. Listed equities keep the source value and as_of, `value_kind` is `carried_forward`, `price_stale` is true, and a caveat line names the failure. Twelve Data is not called.

## Live check still open

This file does not record a live weekly snapshot. The check runs on the box routine, or on `workflow_dispatch` with `kind=weekly` after the workflow is installed. It does not run on a laptop. Confirm:

1. A new `<date>_weekly.json` is committed and `validate_snapshots` exits 0.
2. `git diff` on older snapshot files and older index lines is add-only.
3. Carried classes still show the source as_of, and the caveat line is present.
4. After the next Friday 22:15 UTC, a weekly file exists whose run was the schedule, not a manual dispatch. If that slot has not passed, add the evidence in a follow-up comment. The slice closes after that comment.

Record which price sources returned values and which carried forward. Do not record key values.
