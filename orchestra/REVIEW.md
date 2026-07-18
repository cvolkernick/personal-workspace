# Orchestra Dashboard — Comprehensive Review

**Date:** 2026-07-18  
**Scope:** `personal-workspace/orchestra/` (server, collectors, synergies, priorities, payload, UI, tests)  
**Method:** Full source read, real `build_orchestra_payload` against the live workspace, existing unit suite.

---

## 1. Purpose and overview

Orchestra is the **top-level command center** for personal-workspace. It does not replace subordinate dashboards (financial-command, projects-dashboard, holistic, iot, resistance-dashboard). It **aggregates on-disk domain sources**, detects **cross-domain overlaps/synergies**, synthesizes a **coordinated action plan**, and deep-links / documents launch commands for every subordinate UI.

**Operator value:** Answer “what’s going on across strategy, work, money, health, time, and home — and what should I do next?” without opening six apps.

**Real workspace sample (2026-07-18):** 6/6 domains available; 17 synergies; 12 priorities; 2 initiatives; 5 open today items; 4 day-bridge candidates.

---

## 2. Architecture

```
on-disk sources (strategy/, ops/, treasury/, fitness/, holistic/, iot/)
        │
        ▼
 collect_all_domains()     collectors.py  (pure I/O → domain snapshots)
        │
        ├─► detect_synergies()           synergies.py  (pure)
        ├─► synthesize_priorities()      priorities.py (pure)
        └─► bridge candidates            payload.py
        │
        ▼
 build_orchestra_payload()  payload.py
        │
        ▼
 ThreadingHTTPServer        server.py  →  index.html (fetch /api/orchestra)
```

| Layer | Module | Role |
|-------|--------|------|
| Registry | `domains.py` | Domain specs (ports, URLs, launch cmds, sources) + theme keywords |
| Collectors | `collectors.py` | File/JSON parsers per domain; optional TCP port probe |
| Synergies | `synergies.py` | Theme co-occurrence, initiative bridges, backlog cross-links, IoT/day-plan ties |
| Priorities | `priorities.py` | Ranked action plan from today / initiatives / backlog / finance / fitness / IoT / high synergies |
| Payload | `payload.py` | Single assembly entry; counts, links, day-bridge, meta ports |
| HTTP | `server.py` | Thin handlers: health, full payload, domains, synergies, priorities; static UI |
| UI | `index.html` | Summary stats, domain cards, synergies list, action plan, day bridge, launch list |
| Launch | `launch.py`, `open-command-center.command` | Monorepo entry → port **8790** |

**Design strengths:** Pure collectors + pure synthesis are easy to test with temp fixtures; server stays thin; no child servers required for a useful view; optional `?probe=1` for live badges.

---

## 3. Surfaces

### 3.1 HTTP APIs

| Path | Behavior |
|------|----------|
| `GET /api/health` | `{ok, service: orchestra, workspace}` |
| `GET /api/orchestra` (+ aliases `/api/status`, `/api/payload`) | Full payload; `?probe=1` probes ports |
| `GET /api/domains` | Domains + links subset |
| `GET /api/synergies` | Synergies list |
| `GET /api/priorities` (alias `/api/action-plan`) | Priorities + action_plan |
| `GET /` | `index.html` |

### 3.2 Payload primary fields

`ok`, `service`, `name`, `purpose`, `generated_at`, `workspace`, `domains`, `domain_ids`, `links`, `synergies`, `priorities`, `action_plan`, `bridge`, `counts`, `meta`.

### 3.3 Domains collected

| id | Sources (representative) | Subordinate port |
|----|--------------------------|------------------|
| strategy | `strategy/bets.md`, `today.md`, `initiatives/*.md` | files only |
| workflow | `ops/backlog/items.json`, session index, git dirty | 8765 |
| finance | treasury / FCC snapshot JSON | 8000 |
| fitness | health-metrics, workouts | 8787 |
| holistic | `holistic/data/tasks.json` | 8770 |
| iot | bulbs, groups, schedule | 8780 |

### 3.4 UI sections

1. Sticky header + Refresh / Probe ports  
2. Summary stats (Strategy bets, domain availability, synergies, priorities, initiatives, today, day bridge)  
3. Domain cards (strategy intentionally moved to summary strip only)  
4. Synergies list (kind + strength pills, evidence tags)  
5. Coordinated action plan (ranked)  
6. Day bridge (unlinked backlog → time allocator)  
7. Launch subordinates (URL + `python3 …/server.py`)

---

## 4. Current strengths

1. **Clear orchestration role** — coordinates rather than re-implements domain UIs.  
2. **Offline-first** — useful with only disk state; probe is optional.  
3. **Real multi-domain synthesis** — today.md, initiatives, backlog, treasury actions, fitness, holistic, IoT all feed one plan.  
4. **Day bridge** — explicit macro backlog → day plan without merging UIs.  
5. **Testable core** — unittest fixtures drive shipped collectors/synergies/priorities/payload.  
6. **Monorepo launch path** — `launch.py` / double-click command documented in root README.

---

## 5. Gaps and feedback

| Area | Observation | Impact |
|------|-------------|--------|
| **Operator attention** | UI shows raw counts and long lists; no ranked “needs attention now” digest (missing domain, stress, stale snapshot, empty today, bridge backlog). | High — scan cost |
| **Data freshness** | Finance `as_of` and backlog `updated_at` exist in sources but age is not surfaced; operators cannot tell if treasury is hours vs days old. | High — trust |
| **Synergy volume** | Live workspace produced ~17 synergies; no default “high only” filter. | Medium — noise |
| **Stale UI** | Single fetch; no auto-refresh or relative “generated X ago”. | Medium |
| **Priority domain tagging** | Today-item keyword tags cover fitness/finance/workflow; IoT/home keywords missing. | Low |
| **No attention severity in API** | Downstream tools / agents cannot query “what’s red” without re-deriving heuristics. | Medium for agents |
| **Read-only only** | Correct for scope; no write-back (start child, mark today done). | Non-goal for now |
| **Strategy card hidden** | Intentional layout choice; strategy lives only in stats strip. | Low |

---

## 6. Prioritized recommendations

### Implement in this goal (high impact / low–medium effort)

| # | Recommendation | Why | Approach |
|---|----------------|-----|----------|
| **R1** | **Attention digest** on payload + UI | Operators need a short ranked list of what is wrong/urgent | Pure `synthesize_attention`; section in `index.html` |
| **R2** | **Source freshness** ages for finance/backlog (and summary) | Trust stale treasury before acting | Parse timestamps → hours age; flags + pills |
| **R3** | **UI polish for R1/R2** + synergy strength filter | Make digests usable without redesign | Attention strip; stale badges; High/All synergies toggle |

### Next steps (after this goal)

| # | Recommendation | Effort | Notes |
|---|----------------|--------|-------|
| N1 | Auto-refresh every N minutes + “generated relative” clock | Low | UI only |
| N2 | Cap / cluster theme-overlap synergies; link priority → synergy | Medium | synergies.py |
| N3 | Agent-oriented `GET /api/attention` thin route | Low | once attention field exists |
| N4 | Snapshot age thresholds configurable via query or config file | Low | |
| N5 | Optional one-click copy of top-3 priorities for chat/session notes | Low | UI |
| N6 | Research domain collector only if research/ gains structured status | Medium | out of scope unless trivial |

### Explicit non-goals (per plan)

- Rewriting subordinate dashboards  
- New heavy domains / frameworks  
- Auth, remote host, multi-tenant  
- Full visual design-system overhaul  
- Live mutation of child systems  

---

## 7. Enhancement choices for implementation

From R1–R3, ship:

1. **`attention.py` + payload field `attention`** — ranked operator alerts (severity, title, domains, kind, detail).  
2. **`freshness` on payload** — per-source ages and `stale` flags; finance collector exposes age when `as_of` present.  
3. **UI:** Attention section, stale/fresh meta on domain cards and footer, synergies All/High filter.

Each is pure-logic testable against fixtures and operator-visible in API + UI.

---

## 8. Launch and verification path

```bash
python3 launch.py
# or
python3 orchestra/server.py --port 8790 --no-browser
python3 -m unittest discover -s orchestra/tests -v
curl -s http://127.0.0.1:8790/api/health
curl -s http://127.0.0.1:8790/api/orchestra | python3 -m json.tool | head
```

Default port: **8790**. Subordinates: 8000, 8765, 8770, 8780, 8787.
