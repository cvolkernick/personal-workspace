# Horizon — Global Macro & Geopolitical Intelligence System

**Codename:** Horizon (alternate: MacroState)  
**Scope (this iteration):** Core world-state model + daily synthesis pipeline + personal-strategy linkage.  
**Location:** `research/horizon/` (Finance domain / `work/treasury` branch)

---

## 1. Overall architecture

Horizon is a modular, offline-capable intelligence pipeline that:

1. **Ingests** signals from source adapters (public feeds and fixtures).
2. **Updates** a structured multi-domain **world-state** graph/hierarchy.
3. **Links** world-state items to personal strategy priorities (Orchestrator thesis).
4. **Synthesizes** a daily executive brief, world-state summary, strategy implications, and ranked watchlist.
5. **Versions** world-state and briefs so successive runs are auditable.

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│ Source adapters │────▶│ World-state model│────▶│ Versioned store │
│ (RSS / fixture) │     │ update / query   │     │ data/history/   │
└─────────────────┘     └────────┬─────────┘     └────────▲────────┘
                                 │                        │
                    ┌────────────▼────────────┐           │
                    │ Strategy loader + link  │           │
                    │ (strategy/, investment/)│           │
                    └────────────┬────────────┘           │
                                 │                        │
                    ┌────────────▼────────────┐           │
                    │ Daily synthesis         │───────────┘
                    │ brief / implications /  │  data/briefs/
                    │ watchlist               │
                    └─────────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │ CLI: run_horizon.py     │
                    │ --offline | live RSS    │
                    └─────────────────────────┘
```

**Layering rules**

| Layer | Responsibility | Depends on |
|-------|----------------|------------|
| `domains` | Canonical domain IDs and labels | — |
| `world_state` | Pure in-memory model (update, query, history append) | domains |
| `store` | Load/save versioned JSON | world_state |
| `sources.*` | Event production (no world-state mutation) | domains |
| `strategy_link` | Load thesis + map events → priorities | filesystem strategy paths |
| `synthesis` | Compose brief sections from state + linkages | world_state, strategy_link |
| `pipeline` | Orchestrate I/O + transforms | all above |
| `run_horizon` | CLI entry | pipeline |

Pure transforms are unit-tested without network. I/O is isolated behind adapters and the store.

---

## 2. Data sources

### Design

Source adapters implement a narrow interface:

```text
fetch(context) -> list[SourceEvent]
```

Each `SourceEvent` carries: id, domain, title, facts, optional interpretation, confidence, impact score, tags, source URL/name, timestamp.

### This iteration

| Adapter | Mode | What it provides |
|---------|------|------------------|
| `FixtureSource` | Offline (default for CI) | Curated multi-domain events in `fixtures/sample_events.json` |
| `RssSource` | Live (optional) | High-credibility public RSS (e.g. Fed press, EIA, Reuters world) with graceful fallback |

### Preferred future sources (modular add-ons)

- Official: central banks, energy agencies (EIA/IEA), defense/stat releases
- Markets: rates, FX, commodities (read-only; no trading)
- Think-tank / academic digests
- Selective X/social only as weak signals with low default confidence
- OSINT indicators (optional later)

**Curation principle:** Prefer primary/high-credibility sources; tag provenance; keep confidence explicit; treat social as low-confidence unless corroborated.

---

## 3. State representation

### Domains (required set)

`geopolitics`, `macroeconomics`, `energy`, `technology_ai`, `military`, `demographics`, `supply_chains`, `capital_flows`, `climate_resources`, `narrative_information`

### World-state document (JSON)

```json
{
  "schema_version": 1,
  "version_id": "20260726T150000Z",
  "updated_at": "2026-07-26T15:00:00+00:00",
  "domains": {
    "geopolitics": {
      "label": "Geopolitics",
      "nodes": [ /* WorldNode */ ],
      "summary": "optional short domain rollup"
    }
  },
  "edges": [
    {
      "from_id": "node-a",
      "to_id": "node-b",
      "relation": "affects|depends_on|amplifies|diverges",
      "note": "optional causal note"
    }
  ],
  "meta": {
    "source_modes": ["fixture"],
    "event_count": 12,
    "run_id": "..."
  }
}
```

### WorldNode fields

| Field | Purpose |
|-------|---------|
| `id` | Stable id within a run / merge key across updates |
| `domain` | One of the required domains |
| `title` | Short headline |
| `facts` | List of factual claims (no framing) |
| `interpretation` | Optional analysis, separated from facts |
| `confidence` | 0.0–1.0 judgment confidence |
| `impact` | `low` \| `medium` \| `high` \| `critical` |
| `priority_score` | Numeric rank input (impact × confidence × recency) |
| `tags` | Freeform keywords for linkage |
| `related_domains` | Cross-domain hints |
| `sources` | Provenance list |
| `updated_at` | ISO timestamp |

Versioned history: each pipeline run writes `data/history/world_state_<version_id>.json` and updates `data/world_state_latest.json`.

---

## 4. Update loops

### Daily core update (primary cadence)

1. Load previous world-state (if any).
2. Fetch events from adapters (`--offline` → fixtures only; else try live RSS + merge fixtures as baseline structure).
3. Apply events: upsert nodes by id, refresh timestamps, recompute priority scores.
4. Optionally add/refresh cross-domain edges from tag/domain co-occurrence.
5. Persist versioned snapshot + latest pointer.
6. Load personal strategy; recompute linkages.
7. Run synthesis → write brief under `data/briefs/brief_<version_id>.{json,md}` + `brief_latest.*`.

### Event-driven refresh (future)

Same pipeline, triggered by high-impact source events or manual `run_horizon.py`. Not a separate real-time bus in this iteration.

### Strategy-change refresh

When `strategy/bets.md`, `intent.json`, `today.md`, or investment context changes, re-run synthesis/linkage **without** requiring world-state rewrite: `run_horizon.py --link-only` (or full run) reloads strategy and rewrites implications/watchlist.

### Auditability

- Immutable history files per `version_id`
- Briefs include `version_id` and strategy source paths used
- Diff successive world-state files for change review

---

## 5. Personal-strategy mapping

### Sources of truth (read-only)

| Path | Role |
|------|------|
| `strategy/bets.md` | Multi-year thematic bets (Energy, Bitcoin, AI, Autonomy, Robotics) |
| `strategy/intent.json` | Near-term Orchestrator intent |
| `strategy/today.md` | Micro plan / focus |
| `investment/positions.md` | Open positions / thematic holdings |
| `treasury/snapshots/*` (optional) | Liquidity context if present |

### Linkage model

1. **Extract priorities** from strategy files (thematic keywords, accomplishing statement, position symbols/themes).
2. **Score** each world node against priorities via keyword/tag/domain affinity (deterministic, no hidden model required).
3. **Emit linkages**: `{ node_id, priority, affinity, rationale }`.
4. **Implications section** groups high-affinity nodes under each priority with fact vs interpretation preserved.

Changing strategy inputs and re-running updates linkages and the implications section; the world-state core is not rewritten unless ingestion also runs.

### Separation principle

- World-state = objective external landscape.
- Strategy package = personal thesis.
- Linkage + synthesis = composition layer only.

---

## 6. Outputs

| Artifact | Content |
|----------|---------|
| Executive brief | Top developments, ranked, concise |
| Current World State | Per-domain structured summary |
| Implications for My Strategy | Mapped to loaded priorities |
| Watchlist / radar | Ranked variables/events + rationale + confidence |

Each judgment site separates **facts** from **interpretation** and records **confidence**.

---

## 7. Entry point

```bash
# Offline / CI (fixtures)
python3 research/horizon/run_horizon.py --offline

# Prefer live RSS when available (falls back on failure)
python3 research/horizon/run_horizon.py

# Recompute linkages only against latest world-state
python3 research/horizon/run_horizon.py --link-only --offline
```

---

## 8. Extension points

- New domains: add to `DOMAINS` constant; fixtures and adapters can emit them.
- New sources: implement `SourceAdapter.fetch`.
- Smarter linkage: swap keyword scorer for embeddings later without changing artifact schema.
- Dashboards / agents: consume `data/world_state_latest.json` and `data/briefs/brief_latest.json`.
