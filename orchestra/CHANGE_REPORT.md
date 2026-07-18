# Orchestra change report — attention + freshness enhancements

**Date:** 2026-07-18  
**Review:** [`REVIEW.md`](./REVIEW.md) (recommendations R1–R3)

---

## What changed

### 1. Operator attention digest (R1)

| Piece | Change |
|-------|--------|
| **New** `orchestra/attention.py` | Pure functions: `synthesize_attention`, `compute_freshness`, `parse_timestamp`, `hours_since` |
| **`payload.py`** | Payload fields `attention` (ranked list) and `freshness` (source ages); counts `attention`, `stale_sources`, `synergies_high` |
| **`server.py`** | `GET /api/attention` returns attention + freshness subset |
| **`index.html`** | New **Needs attention** section; summary stats for Attention and Stale |

**How it works for operators:** On each `/api/orchestra` load, Orchestra ranks cues such as:

- Missing / partial domains  
- Stale treasury or backlog sources (see freshness)  
- Elevated finance stress  
- Empty or overloaded today plan  
- Day-bridge backlog waiting for allocation  
- High-strength synergies to coordinate  
- Top priority as an info cue  
- Offline subordinate servers (when probed)

UI shows severity pills (critical/high/medium/low/info) and domain tags. Agents can hit `/api/attention` without the full payload.

### 2. Source freshness (R2)

| Piece | Change |
|-------|--------|
| **`compute_freshness`** | Ages finance `signals.as_of` and backlog `updated_at`; flags `stale` when age &gt; threshold (default **48h**) |
| **`payload.py`** | Annotates domain snapshots with `stale` / `age_hours` and `signals.freshness` |
| **`index.html`** | Domain cards show **stale** (red) or age (green) pills; footer notes stale source count |

**How it works:** Before acting on treasury or backlog, operators see whether data is hours or days old. Threshold is `stale_hours` on `build_orchestra_payload` (default 48) and appears in `meta.stale_hours` / freshness summary.

### 3. UI polish for R1/R2 + synergy filter (R3)

| Piece | Change |
|-------|--------|
| Synergies **All / High only** toggle | Reduces noise when many theme overlaps appear |
| Relative “generated … ago” in footer | Faster trust that the view is current |
| Attention strip above domains | Scan path: attention → domains → synergies → plan |

### 4. Small priority tagging polish

Today-plan items mentioning home/IoT keywords now tag the **iot** domain in `priorities.py` (same pattern as fitness/finance/workflow).

### 5. Docs / package

- `REVIEW.md` — full architecture review + prioritized recommendations  
- `README.md` — attention API + freshness surfaces  
- `__init__.py` — docstring updated  

### 6. Tests

`orchestra/tests/test_orchestra.py` — new `FreshnessAndAttentionTests` driving **shipped** `parse_timestamp`, `hours_since`, `compute_freshness`, `synthesize_attention`, and `build_orchestra_payload` on temp fixtures (including intentionally stale finance snapshot). Existing collector/synergy/priority tests still pass.

```text
python3 -m unittest discover -s orchestra/tests -v
# 7 tests, OK
```

---

## Why (mapped to review)

| Review item | Shipped? |
|-------------|----------|
| R1 Attention digest | Yes — API + UI |
| R2 Source freshness | Yes — finance + backlog ages, domain annotations |
| R3 UI for attention/freshness + synergy filter | Yes |
| N1–N6 later roadmap | Documented only in REVIEW.md |

These address **operator scan cost** and **data trust** without rewriting subordinate dashboards or adding frameworks.

---

## How to use

```bash
python3 launch.py
# or
python3 orchestra/server.py --port 8790 --no-browser
```

1. Open http://127.0.0.1:8790/  
2. Read **Needs attention** first  
3. Check domain **stale** badges before finance actions  
4. Use **High only** on synergies when the list is long  
5. Refresh / Probe ports as before  

API:

- `GET /api/orchestra` — full payload including `attention`, `freshness`  
- `GET /api/attention` — digest only  
- `GET /api/health` — unchanged  

---

## Verification performed

| Check | Result |
|-------|--------|
| Unit suite (×1 full, also logged under goal scratch) | 7 passed |
| Server launch + probe ×2 | `ok: true`, service `orchestra`; payload has domains/synergies/priorities/links/attention/freshness; UI HTML contains Orchestrator + Needs attention |
| Port | 8791 used when 8790 busy (env); behavior identical |
