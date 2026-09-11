---
title: "Agent context persistence"
tags: [ops, context, backup]
status: active
created: 2026-09-11
---

# Context persistence (#580)

```bash
python3 ops/context/persist.py backup              # dry-run
python3 ops/context/persist.py backup --apply      # commit + push private remote
python3 ops/context/persist.py restore --to DIR
python3 ops/context/persist.py drift               # silent if clean
python3 ops/context/persist.py contradict --paths MEMORY.md memory
```

E6 is **standalone** (not folded into #575 `/learn`). `/learn` reads Grok traces;
this reads MEMORY.md / `memory/`. Timer runs contradict **report-only**.
`--apply` reconciles in place (last occurrence wins) and appends
`CONTRADICTION_LOG.md`.
