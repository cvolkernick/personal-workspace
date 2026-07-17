"""Project backlog for personal-workspace — ideas to start later + goal launch.

Stored under ops/backlog/ (git-tracked JSON). Initiating an item writes a goal
seed (spec skeleton + MVP scope) and a ready-to-paste /goal objective so a
Grok Build planning session can flesh out the plan and build an MVP.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
BACKLOG_DIR = WORKSPACE_ROOT / "ops" / "backlog"
ITEMS_PATH = BACKLOG_DIR / "items.json"
SEEDS_DIR = BACKLOG_DIR / "seeds"

STATUSES = (
    "idea",  # captured, not ready
    "ready",  # ready to start planning
    "planning",  # goal/planning session in flight
    "active",  # building / iterating
    "done",
    "parked",
)
PRIORITIES = ("low", "medium", "high", "critical")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(title: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (title or "item").strip().lower()).strip("-")
    return (s[:48] or "item")


def _ensure_store() -> None:
    BACKLOG_DIR.mkdir(parents=True, exist_ok=True)
    SEEDS_DIR.mkdir(parents=True, exist_ok=True)
    if not ITEMS_PATH.is_file():
        payload = {
            "version": 1,
            "updated_at": _now(),
            "items": [],
        }
        ITEMS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        readme = BACKLOG_DIR / "README.md"
        if not readme.is_file():
            readme.write_text(
                "# Project backlog\n\n"
                "Ideas and projects to start later. Managed via the Workflow Management "
                "dashboard or `projects-dashboard/backlog.py`.\n\n"
                "- `items.json` — source of truth\n"
                "- `seeds/` — goal planning seeds when you **Initiate** an item\n\n"
                "Initiate → writes a seed plan + `/goal` objective → open Grok in "
                "personal-workspace and paste the objective (or run the launch script).\n",
                encoding="utf-8",
            )


def load_backlog() -> dict[str, Any]:
    _ensure_store()
    try:
        data = json.loads(ITEMS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {"version": 1, "updated_at": _now(), "items": []}
    if "items" not in data or not isinstance(data["items"], list):
        data["items"] = []
    return data


def save_backlog(data: dict[str, Any]) -> None:
    _ensure_store()
    data["updated_at"] = _now()
    data["version"] = data.get("version") or 1
    ITEMS_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def list_items(
    *,
    include_done: bool = False,
    status: Optional[str] = None,
    ranked: bool = True,
) -> list[dict[str, Any]]:
    all_items = list(load_backlog().get("items") or [])
    if ranked:
        try:
            from backlog_groom import rank_items  # noqa: WPS433

            ranked_all = rank_items(all_items)
            if status:
                return [i for i in ranked_all if i.get("status") == status]
            if not include_done:
                return [i for i in ranked_all if i.get("status") not in ("done",)]
            return ranked_all
        except Exception:
            pass
    items = all_items
    if status:
        items = [i for i in items if i.get("status") == status]
    elif not include_done:
        items = [i for i in items if i.get("status") not in ("done",)]
    pri = {p: i for i, p in enumerate(PRIORITIES)}
    items.sort(
        key=lambda x: (
            pri.get(x.get("priority") or "medium", 1),
            x.get("updated_at") or "",
        ),
        reverse=True,
    )
    return items


def get_item(item_id: str) -> Optional[dict[str, Any]]:
    for it in load_backlog().get("items") or []:
        if it.get("id") == item_id:
            return it
    return None


def add_item(
    title: str,
    *,
    description: str = "",
    priority: str = "medium",
    status: str = "idea",
    tags: Optional[list[str]] = None,
    mvp_scope: str = "",
    notes: str = "",
    area: str = "",
) -> dict[str, Any]:
    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "title required"}
    if priority not in PRIORITIES:
        priority = "medium"
    if status not in STATUSES:
        status = "idea"
    data = load_backlog()
    item = {
        "id": str(uuid.uuid4()),
        "slug": _slug(title),
        "title": title,
        "description": (description or "").strip(),
        "priority": priority,
        "status": status,
        "tags": tags or [],
        "area": (area or "").strip(),  # monorepo area hint e.g. treasury
        "mvp_scope": (mvp_scope or "").strip(),
        "notes": (notes or "").strip(),
        "created_at": _now(),
        "updated_at": _now(),
        "seed_path": None,
        "goal_objective": None,
        "initiated_at": None,
        "launch_script": None,
    }
    data["items"].append(item)
    save_backlog(data)
    return {"ok": True, "item": item}


def update_item(item_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    data = load_backlog()
    found = None
    for it in data["items"]:
        if it.get("id") == item_id:
            found = it
            break
    if not found:
        return {"ok": False, "error": "not found"}
    allowed = {
        "title",
        "description",
        "priority",
        "status",
        "tags",
        "area",
        "mvp_scope",
        "notes",
    }
    for k, v in patch.items():
        if k in allowed:
            if k == "priority" and v not in PRIORITIES:
                continue
            if k == "status" and v not in STATUSES:
                continue
            if k == "title" and not str(v).strip():
                continue
            found[k] = v
    if "title" in patch and patch.get("title"):
        found["slug"] = _slug(str(patch["title"]))
    found["updated_at"] = _now()
    save_backlog(data)
    return {"ok": True, "item": found}


def delete_item(item_id: str) -> dict[str, Any]:
    data = load_backlog()
    before = len(data["items"])
    data["items"] = [i for i in data["items"] if i.get("id") != item_id]
    if len(data["items"]) == before:
        return {"ok": False, "error": "not found"}
    save_backlog(data)
    return {"ok": True, "deleted": item_id}


def build_goal_objective(item: dict[str, Any]) -> str:
    """Text suitable for `/goal <objective>` — planning + MVP build."""
    title = item.get("title") or "Untitled project"
    desc = item.get("description") or ""
    mvp = item.get("mvp_scope") or (
        "A minimal working slice we can use and iterate on, with tests or "
        "verification for the happy path, committed in personal-workspace."
    )
    area = item.get("area") or ""
    area_line = f" Prefer living under personal-workspace/{area}/." if area else (
        " Place durable work under personal-workspace on an appropriate work/<area> branch."
    )
    return (
        f"Backlog project: {title}\n\n"
        f"Context:\n{desc}\n\n"
        f"Do this in two phases without asking me to re-specify basics:\n"
        f"1) Planning — write a short design/spec (problem, users, success criteria, "
        f"non-goals, MVP scope, file layout, risks) into the seed plan under "
        f"ops/backlog/seeds/ and refine it as needed.\n"
        f"2) Build — implement the MVP: {mvp}\n"
        f"{area_line}\n"
        f"Use git_workflow (work branch + sync/protect) so changes are committed and pushed. "
        f"When MVP is usable, mark progress and leave clear next iteration steps."
    )


def build_seed_markdown(item: dict[str, Any], objective: str) -> str:
    return f"""# Goal seed: {item.get("title")}

- **Backlog id:** `{item.get("id")}`
- **Priority:** {item.get("priority")}
- **Status:** planning
- **Area:** {item.get("area") or "(tbd)"}
- **Created:** {item.get("created_at")}
- **Initiated:** {_now()}

## Problem / intent

{item.get("description") or "_Fill in during planning._"}

## MVP scope

{item.get("mvp_scope") or "_Define the smallest shippable slice._"}

## Success criteria (draft)

- [ ] Spec written (this file refined)
- [ ] MVP implemented and runnable
- [ ] Basic verification (test or manual checklist) passes
- [ ] Changes committed on `work/<area>` and pushed

## Non-goals

- Full multi-user polish
- Premature optimization

## Notes

{item.get("notes") or ""}

## Grok `/goal` objective

```
{objective}
```

## How to start

From personal-workspace:

```bash
# Option A — launch helper (opens instruction + copies objective path)
bash ops/backlog/seeds/{item.get("slug")}-{item.get("id", "")[:8]}.launch.sh

# Option B — in an existing Grok session:
# /goal <paste objective above>
```

After planning, implement MVP and iterate. Update backlog status via the dashboard.
"""


def initiate_item(
    item_id: str,
    *,
    try_spawn_grok: bool = False,
) -> dict[str, Any]:
    """Mark item planning, write seed + launch script, return /goal text.

    try_spawn_grok: best-effort open a new Grok session (may fail headless).
    """
    data = load_backlog()
    item = None
    for it in data["items"]:
        if it.get("id") == item_id:
            item = it
            break
    if not item:
        return {"ok": False, "error": "not found"}

    objective = build_goal_objective(item)
    slug = item.get("slug") or _slug(item.get("title") or "item")
    short = (item.get("id") or "")[:8]
    seed_name = f"{slug}-{short}.md"
    seed_path = SEEDS_DIR / seed_name
    seed_path.write_text(build_seed_markdown(item, objective), encoding="utf-8")

    launch_name = f"{slug}-{short}.launch.sh"
    launch_path = SEEDS_DIR / launch_name
    obj_file = SEEDS_DIR / f"{slug}-{short}.goal.txt"
    obj_file.write_text(objective + "\n", encoding="utf-8")

    rel_seed = str(seed_path.relative_to(WORKSPACE_ROOT))
    rel_obj = str(obj_file.relative_to(WORKSPACE_ROOT))
    launch_path.write_text(
        f"""#!/bin/bash
# Launch a Grok Build planning session for backlog item: {item.get("title")}
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
OBJ="{rel_obj}"
SEED="{rel_seed}"
echo "=== Backlog goal: {item.get("title")} ==="
echo "Seed: $SEED"
echo "Objective file: $OBJ"
echo ""
echo "Paste into Grok (or run /goal with this text):"
echo "----------------------------------------------"
cat "$OBJ"
echo "----------------------------------------------"
echo ""
# Copy to clipboard on macOS when available
if command -v pbcopy >/dev/null 2>&1; then
  cat "$OBJ" | pbcopy
  echo "(Objective copied to clipboard)"
fi
echo ""
echo "Starting Grok in personal-workspace…"
echo "After it opens: /goal and paste (or Cmd+V)."
if command -v grok >/dev/null 2>&1; then
  exec grok
elif [ -x "$HOME/.grok/bin/grok" ]; then
  exec "$HOME/.grok/bin/grok"
else
  echo "grok CLI not found on PATH. Open Grok manually in: $ROOT"
  exit 0
fi
""",
        encoding="utf-8",
    )
    launch_path.chmod(launch_path.stat().st_mode | 0o111)

    item["status"] = "planning"
    item["goal_objective"] = objective
    item["seed_path"] = rel_seed
    item["launch_script"] = str(launch_path.relative_to(WORKSPACE_ROOT))
    item["objective_path"] = rel_obj
    item["initiated_at"] = _now()
    item["updated_at"] = _now()
    save_backlog(data)

    spawn: dict[str, Any] = {"attempted": False}
    if try_spawn_grok:
        spawn["attempted"] = True
        try:
            # Open Terminal-less: just start grok in background is interactive —
            # prefer `open` on macOS with a note. We run launch script in Terminal.
            if Path("/usr/bin/open").is_file():
                subprocess.Popen(
                    [
                        "open",
                        "-a",
                        "Terminal",
                        str(launch_path),
                    ],
                    cwd=str(WORKSPACE_ROOT),
                )
                spawn["ok"] = True
                spawn["method"] = "open -a Terminal launch.sh"
            else:
                spawn["ok"] = False
                spawn["error"] = "no macOS open; run launch script manually"
        except OSError as e:
            spawn["ok"] = False
            spawn["error"] = str(e)

    return {
        "ok": True,
        "item": item,
        "goal_objective": objective,
        "seed_path": rel_seed,
        "launch_script": item["launch_script"],
        "objective_path": rel_obj,
        "slash_command": f"/goal {objective[:200]}…" if len(objective) > 200 else f"/goal {objective}",
        "instructions": (
            f"1. Run: bash {item['launch_script']}\n"
            "2. Or open Grok in personal-workspace and paste /goal objective from the seed.\n"
            "3. Grok should refine the seed spec then build the MVP."
        ),
        "spawn": spawn,
    }


def import_initiatives() -> dict[str, Any]:
    """One-shot: pull title/status from initiatives/*.md into backlog if empty-ish."""
    init_dir = WORKSPACE_ROOT / "initiatives"
    if not init_dir.is_dir():
        return {"ok": False, "error": "no initiatives dir"}
    existing_titles = {i.get("title") for i in load_backlog().get("items") or []}
    added = []
    for md in sorted(init_dir.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        title = md.stem.replace("-", " ").title()
        status = "idea"
        desc = ""
        next_action = ""
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                fm, body = parts[1], parts[2]
                for line in fm.splitlines():
                    if line.startswith("title:"):
                        title = line.split(":", 1)[1].strip().strip('"').strip("'")
                    if line.startswith("status:"):
                        st = line.split(":", 1)[1].strip()
                        status = "active" if st == "active" else "idea"
                    if line.startswith("next_action:"):
                        next_action = line.split(":", 1)[1].strip().strip('"')
                desc = body.strip()[:2000]
        if title in existing_titles:
            continue
        r = add_item(
            title,
            description=desc or f"Imported from initiatives/{md.name}",
            status="ready" if status == "active" else "idea",
            notes=next_action,
            tags=["imported", "initiative"],
        )
        if r.get("ok"):
            added.append(r["item"]["id"])
            existing_titles.add(title)
    return {"ok": True, "added": added, "count": len(added)}


def backlog_payload(*, include_done: bool = False) -> dict[str, Any]:
    items = list_items(include_done=include_done, ranked=True)
    try:
        from backlog_groom import enrich_backlog_payload  # noqa: WPS433

        extra = enrich_backlog_payload(list(load_backlog().get("items") or []))
    except Exception:
        extra = {}
    meta = load_backlog().get("groom_meta") or {}
    return {
        "ok": True,
        "path": str(ITEMS_PATH.relative_to(WORKSPACE_ROOT)),
        "count": len(items),
        "items": items,
        "ranked": extra.get("ranked") or items,
        "by_schedule": extra.get("by_schedule") or [],
        "by_priority": extra.get("by_priority") or [],
        "top": extra.get("top") or items[:3],
        "last_groomed_at": load_backlog().get("last_groomed_at"),
        "groom_meta": meta,
        "statuses": list(STATUSES),
        "priorities": list(PRIORITIES),
        "how_to_initiate": (
            "POST /api/backlog/initiate {id} writes a goal seed and launch script; "
            "run the script or paste the objective into Grok with /goal."
        ),
        "how_to_groom": (
            "POST /api/backlog/groom re-scores, press-ranks, schedules (now/this week/…), "
            "and applies safe priority/status hygiene."
        ),
    }


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        print(json.dumps(backlog_payload(include_done="--all" in sys.argv), indent=2))
    elif cmd == "add" and len(sys.argv) > 2:
        print(json.dumps(add_item(" ".join(sys.argv[2:])), indent=2))
    elif cmd == "initiate" and len(sys.argv) > 2:
        print(json.dumps(initiate_item(sys.argv[2], try_spawn_grok="--spawn" in sys.argv), indent=2))
    elif cmd == "import":
        print(json.dumps(import_initiatives(), indent=2))
    elif cmd == "groom":
        from backlog_groom import groom_backlog  # noqa: WPS433

        apply = "--dry-run" not in sys.argv
        print(json.dumps(groom_backlog(apply=apply), indent=2))
    else:
        print(
            "Usage: backlog.py [list [--all]|add <title>|initiate <id> [--spawn]|import|groom [--dry-run]]",
            file=sys.stderr,
        )
        raise SystemExit(2)
