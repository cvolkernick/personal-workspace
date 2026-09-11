---
name: superwhisper
description: Toggle the Superwhisper voice integration for this workspace on or off
---

The voice loop itself is automatic — the `Stop` hook opens Superwhisper when a
turn ends, and `PreToolUse` gates tool permissions. There is nothing to call for
that.

## Toggle integration (workspace flag)

Determine on / off / toggle from the user. Run:

```bash
h=$(echo -n "$PWD" | md5 -q 2>/dev/null || echo -n "$PWD" | md5sum | cut -d' ' -f1); f="/tmp/superwhisper-agent/disabled-$h"; mkdir -p /tmp/superwhisper-agent; case "$STATE" in on) rm -f "$f"; echo "Superwhisper: ON" ;; off) touch "$f"; echo "Superwhisper: OFF" ;; *) [ -f "$f" ] && { rm -f "$f"; echo "Superwhisper: ON"; } || { touch "$f"; echo "Superwhisper: OFF"; } ;; esac
```

Replace `$STATE` with `on`, `off`, or empty for toggle. Report the one-line output.

## Last message / pending voice reply

```bash
HOOK="${SUPERWHISPER_GROK_HOOK:-/Applications/superwhisper.app/Contents/Resources/agent-hook}"
"$HOOK" grok last-message --session "${GROK_SESSION_ID}"
"$HOOK" grok take-pending --session "${GROK_SESSION_ID}"
```

`take-pending` prints the pending reply and consumes it; exit 1 when there is none.
