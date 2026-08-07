# Meet Recordings → B2 (#59)

**Nest runbook:** `~/.buzz/GUIDES/MEET_RECORDINGS_B2_INGEST.md`  
**Plan:** nest `PLANS/MEET_RECORDINGS_B2_INGEST.md`  
**Code:** `b2-ux/b2_kb/meet_recordings.py`

## Path (documented AC)

```
Google Drive folder "Meet Recordings"
  id: 1Xg-gpTN0Hc0TGqEchcNsFRCU0v8HUxBd
  → agent: list folder (Drive MCP)
  → python3 -m b2_kb.meet_recordings plan --manifest files.json
  → agent: read each to_fetch Doc
  → python3 -m b2_kb.meet_recordings ingest --manifest items.json --format channel
  → if channel summary non-empty: post to #b2-drop (bbc5c4ae-2986-4aa9-9842-9fc62a72a575)
```

Default **auto-promote** → `~/B2/inbox/captures/`. Opt-out: `--no-promote`.

## Standing order (no Chris kick)

| | |
|--|--|
| Cadence | Daily |
| Runner | **Grok** — scheduled task or manual `@Grok scan meet recordings` |
| Quiet | Empty channel output when nothing new / empty transcript |
| **Never** | File eng Ready / GitHub issue for missing recording or empty transcript |

Cadence may add a one-line check on daily status (“Meet scan last 24h?”) — process only; not an eng card.

## Failure modes (no-spam)

| Case | Behavior |
|------|----------|
| No new/changed Docs | `notify=false`, no #b2-drop post |
| Empty transcript body | skip `empty_transcript`, quiet |
| Video/audio only | plan `skip` mime, no eng ticket |
| Hard error | `notify=true` with error line only |

## Verify

```bash
cd b2-ux
python3 -m unittest tests.test_meet_recordings -v
python3 -m b2_kb.meet_recordings status
```
