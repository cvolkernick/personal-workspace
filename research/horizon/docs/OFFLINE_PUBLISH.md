# Horizon offline publish (nest / GitHub)

**Issue:** #301 · **Path lock:** nest / GitHub offline publish while Pi is parked  
**User-facing name:** Horizon

While the live Horizon host is parked, **do not** freestyle writes to that host.
The approved write path is this nest worktree: Meridian (or a cloud agent)
stamps a new `world_state` / brief `version_id` via PR. Grok eng-gates the
publish PR. Orchestra and Horizon consumers read the published nest artifact.

This is a temporary SoT **only while Pi is parked**. Pi remains the long-term
source of truth. There is no public Vercel Horizon SoT.

---

## 1. Where SoT files live (Pi parked)

All paths are under `research/horizon/data/`.

| File | Role | In git? |
|------|------|---------|
| `world_state_latest.json` | Current world-state pointer | **Yes** — commit on the publish PR |
| `briefs/brief_latest.json` | Current brief (JSON) | **Yes** — commit on the publish PR |
| `briefs/brief_latest.md` | Current brief (markdown) | **Yes** — commit on the publish PR |
| `history/world_state_<version_id>.json` | Optional one snapshot of the published version | Ignored by `data/.gitignore`; force-add **at most one** if you want the snapshot in the PR |
| `briefs/brief_<version_id>.{json,md}` | Optional versioned brief copies | Ignored (`briefs/brief_20*`); force-add at most the matching pair |

`data/.gitignore` keeps the latest pointers trackable and ignores the rest of
`history/` plus dated brief runs. Do **not** un-ignore the whole history tree
or force-commit a pile of old runs.

Consumers (Orchestra / Horizon) read the latest pointers, not the live host,
while this path is active.

---

## 2. How to stamp a new `version_id`

`version_id` is the existing compact UTC ISO used by `make_version_id()` and
`ARCHITECTURE.md`:

```text
YYYYMMDDTHHMMSSZ
```

Example: `20260823T221500Z`.

### Preferred: restamp existing nest SoT (no new facts)

If a real `world_state_latest.json` is already in the tree, stamp a new
`version_id` onto **that** document. Nodes, facts, rates, and regime scores
are not invented or recomputed.

```bash
python3 research/horizon/run_horizon.py --publish-offline
```

Optional pin (must match the stamp format):

```bash
python3 research/horizon/run_horizon.py --publish-offline --version-id 20260823T221500Z
```

The helper wraps `store.save_world_state` / `store.save_brief` and existing
synthesis. It writes:

- `data/world_state_latest.json`
- `data/history/world_state_<version_id>.json` (local; gitignored unless force-added)
- `data/briefs/brief_latest.{json,md}` (brief `version_id` matches the stamp)

### Explicit fixtures only

`--from-fixtures` runs the **existing** offline pipeline against fixtures
already in `research/horizon/fixtures/`. Use this only when those fixtures
are the intended real input (empty tree / bootstrap). Do not use it to
overwrite a richer published SoT with a fixture replay.

```bash
python3 research/horizon/run_horizon.py --publish-offline --from-fixtures
```

---

## 3. Honest empty / held

If Meridian has **no real update**, do **not** invent a macro print, regime,
or rates just to move `version_id`.

- Leave the prior published version in place.
- The helper returns `"held": true` and writes nothing when
  `world_state_latest.json` is missing and `--from-fixtures` was not passed.
- A held / prior version is an honest publish. A fabricated world-state is not.

`--offline` without `--publish-offline` is the existing CI/fixture pipeline.
It is not a license to mint a fake brief for Chris or Orchestra.

---

## 4. PR workflow (Meridian → Grok eng-gate)

1. Branch from `master` (nest worktree).
2. Apply a real update **or** restamp the existing latest with
   `--publish-offline`. Do not invent payload.
3. Stage the SoT pointers:

   ```bash
   git add research/horizon/data/world_state_latest.json \
           research/horizon/data/briefs/brief_latest.json \
           research/horizon/data/briefs/brief_latest.md
   ```

   Optional — one history snapshot for the new `version_id`:

   ```bash
   git add -f research/horizon/data/history/world_state_<version_id>.json
   ```

4. Open a PR against `master` that cites #301 (or the publish request).
5. **Grok eng-gates** the publish PR. Do not merge around the gate.
6. After merge, Orchestra / Horizon read the nest latest pointers.

---

## 5. Private / secrets

Horizon stays private. Secrets stay off git.

- Do not commit API keys, tokens, `.env`, or host credentials.
- The helper refuses to write if a published artifact matches a small
  secret-like pattern scan.
- No public Vercel Horizon SoT.

---

## 6. Cutover when Pi returns (follow-up)

Document only here — this PR does **not** restore the live write.

When Pi unparks:

1. Live Horizon write resumes **on Pi**. Pi is again the long-term SoT.
2. Nest offline publish becomes **archive / read-cache only**. Stop opening
   nest PRs as the write path.
3. **No dual SoT.** Do not keep writing both nest and Pi as if they were
   peers. One writer: Pi.
4. Still no public Vercel SoT.

Restore of the live write surface is a follow-up on #301 (or a linked issue).
Until that lands, treat nest as the parked-Pi write path described above.
