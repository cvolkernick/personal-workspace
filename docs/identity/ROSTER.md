# Identity roster (portable layer)

Source of truth for **names and pairing only**. This file is not an agent.

**Buzz dump (Mac):** `~/Library/Application Support/xyz.block.buzz.app/agents/managed-agents.json`  
**Dumped:** 2026-09-16T21:52Z · file mtime 2026-09-16T02:15 local · 32 records  
**Rule:** do not invent Buzz Desktop names. Unnamed duplicate rows (null `display_name`) are omitted, not guessed.

Unique non-null `display_name` from that dump: Assay, Byline, Cadence, Fizzbuzz, Forge, Frankenfit, Grok, Honey, Launch, Meridian, Nakatoshi, Pollen, Pulse.

Bot seats below are from GitHub #792 (2026-09-16 inventory). GVG is a separate CEO bot, not a Squad clone.

| slug | Bot seat | Buzz Desktop | kind |
|------|----------|--------------|------|
| assay | Assay | Assay | mirrored |
| byline | Byline | Byline | mirrored |
| cadence | Cadence | Cadence | mirrored |
| forge | Forge | Forge | mirrored |
| frankenfit | Frankenfit | Frankenfit | mirrored |
| grok | Grok.btc | Grok | exec |
| helm | Helm | — | bot-only |
| herald | Herald | — | bot-only |
| launch | Launch | Launch | mirrored |
| lens | Lens | — | bot-only |
| meridian | Meridian | Meridian | mirrored |
| nakatoshi | Nakatoshi | Nakatoshi | mirrored |
| nourish | Nourish | — | bot-only |
| pulse | Pulse | Pulse | mirrored |
| quarry | Quarry | — | bot-only |
| restore | Restore | — | bot-only |
| scrip | Scrip | — | bot-only |
| fizzbuzz | — | Fizzbuzz | buzz-only |
| honey | — | Honey | buzz-only |
| pollen | — | Pollen | buzz-only |

**Pilot:** `nakatoshi` only. Do not seed other slugs until Nakatoshi AC4 passes on both runtimes.

**Notes**
- Honey is still in the Mac dump as an active builtin; nest persona spec treats Byline as the content specialist. Roster reports the dump, not a retirement.
- Fizzbuzz is the dump `display_name` for builtin `fizz`. AGENTS.md also lists `Fizz`. Do not invent a second Buzz name.
- `kind=exec` for Grok: paired seats, different display names (Grok.btc vs Grok).
