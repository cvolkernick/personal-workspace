# ceremony-clock / pi-dispatch

Source of truth for the **eng-gate pickup dispatcher** (`ceremony_eng_gate_dispatch.py`,
"pi-dispatch") that runs on the workflow Pi after the 15-minute `eng-gate-sweep` kick.
Before #1195 it lived only under `~/.local/lib/ceremony-clock/` on the Pi.
`ceremony_clock.py` and `ceremony_consume.py` (the signer and the snapshot poster) are
unchanged and stay Pi-local; they carry relay workflow ids and are not in this repo.

## What a pass does

0. **Preflight (before any label is touched).** The local state file must be readable, the
   roster valid (non-empty channel id, at least one staffable agent) and the kick sender
   importable (`ceremony_clock.send_channel_message`). If not, the run stops with exit code 3
   and a clear error (journal + stdout JSON); nothing is released, flipped or sent. A
   corrupt state file is **never** treated as empty (that could re-enable releases and
   re-claims): see "State files" below.
1. **Release sweep.** A `status:in-progress` claim **that pi-dispatch made itself** with no
   branch, PR, or holder comment for 24h goes back to `status:ready` (new label added first,
   then the old one removed) with a `[pi-dispatch] released ...` comment that names the
   previous holder. The 24h clock starts at the **latest** of the `[pi-dispatch] started`
   marker, the last release comment and the latest `labeled status:in-progress` event, so
   re-labelling an issue restarts it. Branches, comments and label events are read with full
   pagination (the repo has 500+ branches); a pagination or API error means *no release*.
   Grok eng-gate bookkeeping comments (`Status check`, `Deadline check`,
   `Re-staffed (Grok eng-gate ...`) are not holder activity, except that one saying
   ``label stays `status:in-progress` `` (or `pi-dispatch:keep`) renews the 24h clock. A PR
   that *targets* `#N` (`Fixes/Closes/Resolves/Refs/Part of #N`, a trailing `(#N)` in its title, or a
   title starting with `#N`; open, or updated after the claim) keeps the claim live. A PR that only
   mentions `#N` in prose does not. Any API error while checking activity means *no
   release*.
2. **Pick.** One `status:ready` issue, flipped to `status:in-progress`, kick posted to
   #workflow. Per-owner WIP is still 1, but only *live claims pi-dispatch made itself*
   count, so a dead claim, or a Buzz/GrokBuild, Grok or human claim, cannot block the queue.

### Which in-progress claims are pi-dispatch's own

A claim is pi-dispatch's only if the newest thing on the issue is its exact marker comment
(`[pi-dispatch] started — staffed to <Forge|Frankenfit|Nakatoshi> in #workflow. WIP=1. Do not self-merge.`,
nothing else in the comment, owner a staffable roster agent and never Buzz/GrokBuild), or the
local claim record pi-dispatch writes right after an accepted kick (covers a failed `started`
comment, and a failed rollback). The marker must not be followed by a release comment or by a
later `labeled status:in-progress` event (more than 10 minutes after it), which means someone
else re-labelled the issue. Everything else is **foreign**: it is listed as `foreign_claims`,
never counted as WIP for anyone and never released. The GitHub login is deliberately not the
identity: the dispatcher's token was `cvolkernick` until 10/14 and `GrokBot-BTC` after, and
Grok and humans comment under those same logins. If you want a login check anyway, set
`claim_authors` in the roster and only markers by those logins count.

## Pick rules

| Rule | Behaviour |
|------|-----------|
| Worker line | Authoritative. Recognised (case-insensitive; bold, colon, dash, `->`, `=`, bullet, heading and table variants; fullwidth colon and zero-width characters are normalised): `Worker`, `Workers`, `Assigned worker`, `Assigned to`, `Dispatch`/`Dispatched to`, `Staffed to/by` (and `Owner`/`Assignee`, which only count when their value is Buzz/GrokBuild or a staffable agent, so `Owner: Chris` is ignored). A list under a bare `Workers:` is read. Fenced code blocks (an unclosed fence runs to the end), blockquote lines and HTML comments are **ignored**; if the only Worker line is inside one of those the issue is **not staffed**. **Conflicting** Worker lines (different agents, or an agent and Buzz/GrokBuild) are unknown: not staffed. Repeated identical lines are fine. `Forge`, `Frankenfit`, `Nakatoshi` route to that bot. Everything else is **not staffed** by pi-dispatch: `Buzz`/`GrokBuild` in any spelling (`Grok Build`, `grok-build`, ...), in-chat, direct handoff, human, an unknown name, an empty or malformed value, two staffable names in one value. |
| Empty body | An empty or whitespace-only body is not staffed. A non-empty body with no Worker line keeps legacy label/title routing. |
| Buzz/GrokBuild | **Never staffed by pi-dispatch.** There is no flag and no roster entry for it; a roster that lists `GrokBuild`/`Buzz` (any spelling) is ignored, and a roster whose only staffable agents are those is rejected. |
| Body says `Do not let pi-dispatch staff this one` | Never staffed. |
| Re-staff cooldown | No claim within 24h of a comment containing `Re-staffed (Grok eng-gate`, or of a pi-dispatch release. |
| Repeat releases | After 2 releases with no work the issue needs human re-triage. |
| Consumer health | If the owner's newest #workflow message in the last 24h is the harness error (`I couldn't process the last request ...`), the owner is not staffed. Fails open if the relay cannot be read. |
| Claim order | 0. the sender and roster are built and checked (no label touched yet), 1. add `status:in-progress`, 2. remove `status:ready` (add first, so a failed call never leaves the issue without a status label; if only the remove fails the add is undone and nothing is sent), 3. send the kick to #workflow, 4. post `[pi-dispatch] started ...`. If the `started` comment itself fails the claim stands (the kick is out); the local claim record keeps it recognisable as pi-dispatch's and the `labeled` event starts the 24h clock. |
| Failed or rejected kick | The labels are swapped back (add first again), nothing is posted, and the issue gets a local **kick back-off of 1h** (`--kick-cooldown-hours`, stored in the state file) so the sweep moves on instead of flipping and rolling back the same top issue every 15 minutes. A failed label swap backs off the same way. The run exits 2. |
| Failed release comment | The labels are already swapped, so the issue gets a local 24h cooldown (state file) and is not re-staffed every sweep. If that cooldown cannot be saved the run reports it in `errors` and on stderr, exits 3, and does not re-pick the issue in the same sweep. |
| Half-failed swap | An issue that ends up with both `status:ready` and `status:in-progress` and a pi-dispatch claim is treated as a claim, never picked, and loses the leftover `status:ready`. A foreign claim's labels are left alone. |

Skipped: `process`, `human-only`, `idea`, `parked`, `capital-parked`, dangerous-path
titles, and the numbered holds in `SKIP_ISSUES`.

## State files

`release-cooldowns.json` (release cooldowns, kick back-offs, claim records) and
`last-dispatch.json` live in `CEREMONY_STATE_DIR` and are written **atomically**: temp file in
the same directory, flush + fsync, `os.replace`, then an fsync of the directory. A crash at any
point leaves the old file or the new one, never a truncated mix (a leftover
`.release-cooldowns.json.*.tmp` after a hard kill is harmless).

A **missing** `release-cooldowns.json` is an empty store. A file that is unreadable, not valid
JSON or has the wrong shape raises `StateFileError`: the run exits 3 before touching any
label (dry-run too), prints the path and reason to stderr and as JSON, and keeps a copy of the
bad content next to it as `release-cooldowns.json.corrupt-<hash>`. The original is left in
place on purpose, so the unit keeps failing visibly until a human repairs or removes it;
silently starting over could re-claim an issue Grok just re-staffed or drop a release
cooldown. A cooldown or claim record that cannot be *saved* is also reported (`errors`,
stderr, exit 3), never ignored.

Exit codes: `0` ok or nothing to do, `2` kick not accepted, `3` loud failure (corrupt state,
bad roster, no sender, failed state write), `4` unexpected error.

## Important: the dispatcher does not start sessions

A `[pi-dispatch] started` comment means *the kick was accepted by the relay*. The
agent session is started by the Buzz agent harness for the staffed bot (Forge,
Frankenfit, ...), which runs off the Pi. If that harness has lost its Grok login it
answers the kick with `I couldn't process the last request ... Authentication required`
and nothing starts. The health gate above and the 24h release exist so that failure
costs one owner for a day, not the whole queue. Re-login the harness to fix the cause.

## Install / update on the Pi

Back up first, then copy the script and add the roster (identities are not committed):

```bash
cd ~/.local/lib/ceremony-clock
cp -p ceremony_eng_gate_dispatch.py ceremony_eng_gate_dispatch.py.bak-$(date +%Y%m%d-%H%M)
cp <repo>/deploy/ceremony-clock/ceremony_eng_gate_dispatch.py .
mkdir -p ~/.config/ceremony-clock
cp <repo>/deploy/ceremony-clock/dispatch-roster.example.json ~/.config/ceremony-clock/dispatch-roster.json
chmod 600 ~/.config/ceremony-clock/dispatch-roster.json   # then fill in channel id + pubkeys
```

No service restart: `ceremony-clock@eng-gate-sweep.service` is a oneshot and picks the
new file up on the next timer tick. Unit copies are in `units/`.

Dry run (no mutation, prints what would be released and picked):

```bash
python3 ceremony_eng_gate_dispatch.py eng-gate-sweep --dry-run
```

Flags: `--stale-hours 24`, `--cooldown-hours 24`, `--kick-cooldown-hours 1`, `--no-health-check`,
`--only N[,N]` (limit the sweep and pick to those issues, for proofs).
Env: `CEREMONY_DISPATCH_ROSTER`, `CEREMONY_STATE_DIR` (lock, last-dispatch, cooldowns/claim records).
Roster keys: `workflow_channel_id`, `agents`, `staffable`, optional `claim_authors`.

## Tests

```bash
cd deploy/ceremony-clock && python3 -m pytest tests -q
```
