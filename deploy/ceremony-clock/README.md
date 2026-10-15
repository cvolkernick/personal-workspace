# ceremony-clock / pi-dispatch

Source of truth for the **eng-gate pickup dispatcher** (`ceremony_eng_gate_dispatch.py`,
"pi-dispatch") that runs on the workflow Pi after the 15-minute `eng-gate-sweep` kick.
Before #1195 it lived only under `~/.local/lib/ceremony-clock/` on the Pi.
`ceremony_clock.py` and `ceremony_consume.py` (the signer and the snapshot poster) are
unchanged and stay Pi-local; they carry relay workflow ids and are not in this repo.

## What a pass does

1. **Release sweep.** A `status:in-progress` claim with no branch, PR, or holder comment
   for 24h goes back to `status:ready` with a `[pi-dispatch] released ...` comment that
   names the previous holder. Grok eng-gate bookkeeping comments (`Status check`,
   `Deadline check`, `Re-staffed (Grok eng-gate ...`) are not holder activity, except that
   one saying ``label stays `status:in-progress` `` (or `pi-dispatch:keep`) renews the
   24h clock. An open PR that mentions `#N` keeps the claim live. Any API error while
   checking activity means *no release*.
2. **Pick.** One `status:ready` issue, flipped to `status:in-progress`, kick posted to
   #workflow. Per-owner WIP is still 1, but only *live* claims count, so a dead claim
   cannot block the queue.

## Pick rules

| Rule | Behaviour |
|------|-----------|
| `**Worker:**` line | Authoritative. `Buzz/GrokBuild`, in-chat, direct handoff, human, or an unknown name: **not staffed** here. `Forge`, `Frankenfit`, `Nakatoshi`: routed to that bot. No Worker line: legacy label/title routing. |
| Body says `Do not let pi-dispatch staff this one` | Never staffed. |
| Re-staff cooldown | No claim within 24h of a comment containing `Re-staffed (Grok eng-gate`, or of a pi-dispatch release. |
| Repeat releases | After 2 releases with no work the issue needs human re-triage. |
| Consumer health | If the owner's newest #workflow message in the last 24h is the harness error (`I couldn't process the last request ...`), the owner is not staffed. Fails open if the relay cannot be read. |
| Claim order | Flip label, send the kick, then post `[pi-dispatch] started ...`. A rejected kick rolls the label back and posts nothing. |

Skipped: `process`, `human-only`, `idea`, `parked`, `capital-parked`, dangerous-path
titles, and the numbered holds in `SKIP_ISSUES`.

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

Flags: `--stale-hours 24`, `--cooldown-hours 24`, `--no-health-check`,
`--only N[,N]` (limit the sweep and pick to those issues, for proofs).
Env: `CEREMONY_DISPATCH_ROSTER`, `CEREMONY_STATE_DIR`.

## Tests

```bash
cd deploy/ceremony-clock && python3 -m pytest tests -q
```
