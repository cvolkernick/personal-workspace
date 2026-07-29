"""Nested Action Plan template: orchestrator macro + per-domain micro plans.

Pure filesystem helpers — no HTTP. Shared skeleton matches the Focus Coach
structure (Generated, Linked Bets, Weight/Priority, Freshness, Single Next
Action, Supporting Micro-Actions, Hygiene gate, Up-Channel Signal).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from .domains import DOMAIN_SPECS
except ImportError:  # unittest path insert
    from domains import DOMAIN_SPECS

TEMPLATE_REL = "strategy/action-plan-template.md"
MACRO_PLAN_REL = "strategy/action-plan.md"
DOMAIN_PLANS_DIR_REL = "strategy/action-plans"

# Required markers that every skeleton / plan instance must contain
REQUIRED_MARKERS: tuple[str, ...] = (
    "Action Plan",
    "**Generated**:",
    "**Linked Bets**:",
    "**Weight / Priority**:",
    "**Freshness Check**:",
    "### 1. Single Next Action",
    "**What**:",
    "**Why**:",
    "**Timebox**:",
    "**Exit Criteria**:",
    "### 2–5. Supporting Micro-Actions",
    "### Hygiene / Stale Data Gate",
    "### Up-Channel Signal",
    "**Status for Orchestrator**:",
    "**Suggested Domain Re-weight**:",
)

# Built-in fallback if the on-disk template file is absent (still testable pure)
_BUILTIN_SKELETON = """# {title} Action Plan

**Generated**: {generated}  
**Linked Bets**: _list thematic bets this plan advances_  
**Weight / Priority**: _dynamic score or rank_  
**Freshness Check**: _stale sources? refresh before acting?_

### 1. Single Next Action
- **What**: _one concrete, one-sitting task_
- **Why**: _Ikigai + intent link_
- **Timebox**: _minutes or Pomodoro count_
- **Exit Criteria**: _how you know it's done_

### 2–5. Supporting Micro-Actions (if needed)
- _Item 2_
- _Item 3_
- _Item 4_
- _Item 5_

### Hygiene / Stale Data Gate (if any)
- _refresh or batch task before proceeding — or "none"_

### Up-Channel Signal
- **Status for Orchestrator**: _done | blocked | needs re-weight_
- **Suggested Domain Re-weight**: _increase | maintain | decrease_ + reason

---
_Edit this file. Orchestrator lists macro + domain plans; **Run Domain Template** creates a domain plan from the shared skeleton when missing._
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def domain_plan_rel(domain_id: str) -> str:
    did = (domain_id or "").strip().lower()
    return f"{DOMAIN_PLANS_DIR_REL}/{did}.md"


def action_plan_domain_ids() -> list[str]:
    """Domains that host a micro action plan (all registered except strategy files)."""
    out = []
    for spec in DOMAIN_SPECS:
        did = str(spec.get("id") or "")
        if not did or did == "strategy":
            continue
        out.append(did)
    return out


def skeleton_text(
    workspace: Optional[Path] = None,
    *,
    title: str = "[Domain / Orchestrator]",
    generated: Optional[str] = None,
) -> str:
    """Return skeleton markdown, preferring strategy/action-plan-template.md."""
    gen = generated or _now_iso()
    title_s = (title or "[Domain / Orchestrator]").strip()
    ws = Path(workspace) if workspace else None
    if ws is not None:
        path = ws / TEMPLATE_REL
        if path.is_file():
            raw = path.read_text(encoding="utf-8")
            return (
                raw.replace("{{TITLE}}", title_s)
                .replace("{{GENERATED}}", gen)
                .replace("{title}", title_s)
                .replace("{generated}", gen)
            )
    return _BUILTIN_SKELETON.format(title=title_s, generated=gen)


def ensure_template_file(workspace: Path) -> dict[str, Any]:
    """Ensure the shared template file exists under strategy/."""
    ws = Path(workspace).resolve()
    path = ws / TEMPLATE_REL
    created = False
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            skeleton_text(None, title="{{TITLE}}", generated="{{GENERATED}}"),
            encoding="utf-8",
        )
        created = True
    return {
        "ok": True,
        "layer": "template",
        "rel_path": TEMPLATE_REL,
        "path": str(path),
        "exists": True,
        "created": created,
    }


def _ensure_plan_file(
    workspace: Path,
    *,
    rel_path: str,
    title: str,
    layer: str,
    domain_id: Optional[str] = None,
) -> dict[str, Any]:
    ws = Path(workspace).resolve()
    ensure_template_file(ws)
    path = ws / rel_path
    created = False
    if path.is_file():
        # leave intact
        pass
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            skeleton_text(ws, title=title, generated=_now_iso()),
            encoding="utf-8",
        )
        created = True
    body = ""
    try:
        body = path.read_text(encoding="utf-8")
    except OSError:
        body = ""
    return {
        "ok": True,
        "layer": layer,
        "id": domain_id or "orchestrator",
        "label": title,
        "rel_path": rel_path,
        "path": str(path),
        "exists": path.is_file(),
        "created": created,
        "size": len(body.encode("utf-8")) if body else 0,
        "view_url": (
            f"/api/action-plans/view?domain={domain_id}"
            if domain_id
            else "/api/action-plans/view?layer=macro"
        ),
    }


def ensure_macro_action_plan(workspace: Path) -> dict[str, Any]:
    """Create strategy/action-plan.md from skeleton if missing; never overwrite."""
    return _ensure_plan_file(
        workspace,
        rel_path=MACRO_PLAN_REL,
        title="Orchestrator (Macro)",
        layer="macro",
        domain_id=None,
    )


def ensure_domain_action_plan(workspace: Path, domain_id: str) -> dict[str, Any]:
    """Create strategy/action-plans/<domain>.md from skeleton if missing."""
    did = (domain_id or "").strip().lower()
    if not did:
        return {"ok": False, "error": "domain_id is required"}
    if did == "strategy":
        return {
            "ok": False,
            "error": "strategy uses the macro plan (strategy/action-plan.md), not a domain plan",
        }
    label = did
    for spec in DOMAIN_SPECS:
        if spec.get("id") == did:
            label = str(spec.get("label") or did)
            break
    if did not in action_plan_domain_ids() and not any(
        s.get("id") == did for s in DOMAIN_SPECS
    ):
        # Still allow ensure for known-looking ids (future domains) with id as label
        pass
    return _ensure_plan_file(
        workspace,
        rel_path=domain_plan_rel(did),
        title=label,
        layer="domain",
        domain_id=did,
    )


def plan_status(workspace: Path, rel_path: str, *, layer: str, plan_id: str, label: str) -> dict[str, Any]:
    ws = Path(workspace).resolve()
    path = ws / rel_path
    exists = path.is_file()
    size = 0
    if exists:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
    return {
        "id": plan_id,
        "label": label,
        "layer": layer,
        "rel_path": rel_path,
        "path": str(path),
        "exists": exists,
        "size": size,
        "view_url": (
            f"/api/action-plans/view?domain={plan_id}"
            if layer == "domain"
            else "/api/action-plans/view?layer=macro"
        ),
        "ensure_hint": "POST /api/action-plans/ensure",
    }


def collect_action_plans(workspace: Path) -> dict[str, Any]:
    """Payload slice: template + macro + per-domain plan status (no auto-create)."""
    ws = Path(workspace).resolve()
    template_path = ws / TEMPLATE_REL
    macro = plan_status(
        ws,
        MACRO_PLAN_REL,
        layer="macro",
        plan_id="orchestrator",
        label="Orchestrator (Macro)",
    )
    domains: list[dict[str, Any]] = []
    for spec in DOMAIN_SPECS:
        did = str(spec.get("id") or "")
        if not did or did == "strategy":
            continue
        domains.append(
            plan_status(
                ws,
                domain_plan_rel(did),
                layer="domain",
                plan_id=did,
                label=str(spec.get("label") or did),
            )
        )
    return {
        "template": {
            "rel_path": TEMPLATE_REL,
            "path": str(template_path),
            "exists": template_path.is_file(),
        },
        "macro": macro,
        "domains": domains,
        "section_title": "Today's Focus → Domain Action Plan",
        "run_control_label": "Run Domain Template",
    }


def read_plan_body(workspace: Path, *, layer: str = "macro", domain_id: Optional[str] = None) -> dict[str, Any]:
    """Read plan markdown; does not create."""
    ws = Path(workspace).resolve()
    if layer == "domain" or domain_id:
        did = (domain_id or "").strip().lower()
        if not did:
            return {"ok": False, "error": "domain_id required for domain layer"}
        rel = domain_plan_rel(did)
        plan_id = did
        label = did
        for spec in DOMAIN_SPECS:
            if spec.get("id") == did:
                label = str(spec.get("label") or did)
                break
    else:
        rel = MACRO_PLAN_REL
        plan_id = "orchestrator"
        label = "Orchestrator (Macro)"
    path = ws / rel
    if not path.is_file():
        return {
            "ok": False,
            "error": f"plan not found: {rel}",
            "rel_path": rel,
            "path": str(path),
            "exists": False,
            "id": plan_id,
            "label": label,
        }
    try:
        body = path.read_text(encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": str(e), "rel_path": rel, "path": str(path)}
    return {
        "ok": True,
        "id": plan_id,
        "label": label,
        "layer": "domain" if domain_id or layer == "domain" else "macro",
        "rel_path": rel,
        "path": str(path),
        "exists": True,
        "body": body,
    }


def skeleton_has_required_markers(text: str) -> list[str]:
    """Return list of missing required markers (empty = complete)."""
    missing = []
    for m in REQUIRED_MARKERS:
        if m not in text:
            missing.append(m)
    return missing
