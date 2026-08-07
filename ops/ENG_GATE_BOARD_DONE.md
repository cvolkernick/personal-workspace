# Eng-gate: board Status → Done after merge (issue #58)

**Problem:** GitHub issue `state` and Buzz Board **Status** are different fields.
Closing/merging an issue does **not** move the Project column (ceremony lock 2026-08-06).
Closed cards can rot on **Pending Review** and starve free-agent inventory.

**Pilot:** eng-gate (Grok merges in-scope PRs). Plan: nest `PLANS/GROK_ENGINEERING_GATE_PILOT.md`.

---

## Single owner path (always valid)

| Role | When | Command |
|------|------|---------|
| **Grok** (merger) | Immediately after approve+merge + issue closed | `scripts/buzz-board set-status N Done` |
| **Cadence** | On notice if merger missed | same |
| **Forge** | Platform residual / script dry-run | same + `scripts/eng_gate_post_merge.py` |

```bash
# From monorepo root (canonical)
./scripts/buzz-board set-status 58 Done

# Nest alias (same behavior if present)
~/.buzz/scripts/buzz-board set-status 58 Done
```

Status aliases: `done` → `Done`, `pending` → `Pending Review`, `wip` → `In Progress`.

**Auth:** `GITHUB_TOKEN` / `GH_TOKEN` with scopes **`repo` + `project`**.

---

## Preferred automation (post-merge script)

```bash
# After merge — PR body must contain Fixes/Closes #N
python3 scripts/eng_gate_post_merge.py --pr 47

# Explicit issue
python3 scripts/eng_gate_post_merge.py --issue 58

# Dry-run
python3 scripts/eng_gate_post_merge.py --issue 58 --dry-run
```

### Deployable residual (Pi not green yet)

Merged ≠ Done when path-scoped deploy/health is still outstanding:

```bash
python3 scripts/eng_gate_post_merge.py --issue 58 \
  --residual "await Pi health mapped units (workspace-sync + on_merge)"
# → board stays / moves to In Progress with owner evidence required
```

When Pi health is green:

```bash
./scripts/buzz-board set-status 58 Done
# or
python3 scripts/eng_gate_post_merge.py --issue 58
```

Do **not** mark Done from Mac localhost alone. Policy: nest `GUIDES/PI_PROD_MAC_DEV.md`.

---

## Eng-gate review packet — required checklist item

Every merge packet (Grok) must include:

```markdown
### Review #N / PR #M
- Issue AC: …
- Tests run: …
- Rails: secrets? path map? CI?
- **Board Status → Done:** [ ] done via `scripts/buzz-board set-status N Done`
  **or** [ ] residual In Progress (Pi evidence: …)
  **or** [ ] `python3 scripts/eng_gate_post_merge.py --pr M` (JSON ok)
- Decision: MERGE | CHANGES REQUESTED
```

**Zero-miss rule (pilot):** for 1 week after this lands, every in-scope merge either sets Done the same turn or leaves an explicit residual note. Cadence flags any closed+Pending Review item older than **24h**.

---

## Sweep (hygiene)

```bash
# Report closed issues stuck on Pending Review / In Progress
python3 scripts/eng_gate_post_merge.py --sweep

# Auto-Done only for closed + Pending Review (skips In Progress residuals)
python3 scripts/eng_gate_post_merge.py --sweep --apply
```

SLA (#58 AC): **no closed issue on Pending Review for >24h** in `personal-workspace`.

Cadence daily status / eng-gate 15m sweep should run `--sweep` when touching board hygiene.

---

## Lifecycle (eng-gate pilot)

```
In Progress  → implement
Pending Review → PR up (Grok reviews)
MERGE + issue closed
   ├─ no deploy residual → Done   (merger / post_merge script)
   └─ Pi residual        → In Progress until health → Done
```

Never leave **closed** issues on **Pending Review**.

---

## Optional GitHub Action

Template: `ops/github-workflows/eng-gate-board-done.yml`  
Copy to `.github/workflows/` when the pushing token has **`workflow`** scope.
Requires a PAT secret with `repo` + `project` (default `GITHUB_TOKEN` in Actions often lacks Projects write).

Until installed: **script + checklist** are the production path.

---

## Related

| Doc | Role |
|-----|------|
| nest `GUIDES/CADENCE_SCRUM_CEREMONIES.md` § board Status lock | Process SoT |
| nest `GUIDES/BUZZ_BOARD_ACCESS.md` | Board CLI access |
| nest `PLANS/GROK_ENGINEERING_GATE_PILOT.md` | Eng-gate RACI |
| `ops/SDLC_MERGE_DEPLOY.md` | Path-scoped Pi deploy |
| `projects-dashboard/buzz_board_cli.py` | Alternate JSON CLI (`set-status --item-id`) |

## Verification

```bash
./scripts/buzz-board whoami
python3 -m unittest discover -s scripts/tests -v
python3 scripts/eng_gate_post_merge.py --sweep
```
