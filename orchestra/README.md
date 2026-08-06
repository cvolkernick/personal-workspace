# Orchestra — top-level command center

Unifies **strategy**, **workflow/projects**, **finance/treasury**, **fitness/health**, **time-allocation**, and **IoT/home** into one orchestration interface.

Surfaces:

- **Recommended next actions** (primary) — automated merge of hygiene, high/medium synergies, and priorities
- Multi-domain status (file-based; optional live port probe)
- Cross-domain **synergies** (high preferred; medium fallback when none are high)
- Supporting streams: attention, raw priorities, day bridge
- **Source freshness** ages for treasury snapshot and ops backlog
- Deep-links / launch commands for every subordinate dashboard

## Launch (recommended)

From the monorepo root:

```bash
python3 launch.py
# or
python3 orchestra/server.py --port 8790
```

Double-click **`open-command-center.command`** (opens Orchestra).

- UI: http://127.0.0.1:8790/
- Full payload: http://127.0.0.1:8790/api/orchestra
- Fan-in strip (host + regime + implications): http://127.0.0.1:8790/api/fan-in
- Recommended actions: http://127.0.0.1:8790/api/recommendations
- Attention + freshness: http://127.0.0.1:8790/api/attention
- Health: http://127.0.0.1:8790/api/health

Add `?probe=1` to probe child server ports for live badges.

See `REVIEW.md` for architecture review and prioritized roadmap.

## Subordinate dashboards

| Domain | Port | Launch |
|--------|------|--------|
| Finance / financial-command | 8000 | `python3 financial-command/server.py` |
| Workflow / projects-dashboard | 8765 | `python3 projects-dashboard/server.py` |
| Time / holistic | 8770 | `python3 holistic/server.py` |
| IoT / home lights | 8780 | `python3 iot/server.py` |
| Fitness / resistance-dashboard | 8787 | `python3 resistance-dashboard/server.py` |
| **Orchestra (this)** | **8790** | `python3 orchestra/server.py` |

## Tests

```bash
python3 -m unittest discover -s orchestra/tests -v
```

Pure collectors/synergies/priorities run against fixtures without child servers.
