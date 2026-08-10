"""Unitary day_plan composer for Orchestrator.

Pure functions — no I/O, no HTTP, no child servers. Consumes domain snapshots
(+ optional frozen constraint packet fields on signals) and produces the frozen
`day_plan` shape for P1 (see PLANS/ORCHESTRATOR_UNITARY_DAILY_PLANNER.md).

Domains remain write SoT. Composer is read-only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

try:
    from .attention import hours_since, parse_timestamp
except ImportError:  # unittest path insert
    from attention import hours_since, parse_timestamp

SCHEMA_VERSION = 1
MAX_NEXT3 = 3

# Domain freshness (product freeze 2026-08-10)
FINANCE_SOFT_HOURS = 6.0
FINANCE_HARD_HOURS = 48.0
FITNESS_BODY_HOURS = 24.0
WORKFLOW_FRESH_HOURS = 4.0

# Finance action whitelist for day plan / Next 3 (Nakatoshi freeze)
FINANCE_ACTION_WHITELIST = frozenset(
    {
        "fill_manual",
        "ltv_check",
        "card_float",
        "card_paydown",
        "vault_pull",
        "loan_buffer",
        "bridge_powder",
        "dca_pause",
        "refresh",
    }
)

# Allowed under red_mode (no *new free/external* discretionary dollars)
FINANCE_RED_MODE_ALLOW = frozenset(
    {
        "fill_manual",
        "ltv_check",
        "card_float",
        "card_paydown",
        "vault_pull",
        "loan_buffer",
        "bridge_powder",
        "dca_pause",
        "refresh",
    }
)

# Free-dollar risk kinds explicitly excluded when red_mode or free_cash unknown
FINANCE_FREE_DOLLAR_RISK = frozenset(
    {
        "dca",
        "dca_buy",
        "spot_buy",
        "deploy_free_cash",
        "external_risk",
        "buy_btc",
        "open_risk",
    }
)

DEFAULT_DEEP_LINKS = {
    "holistic": "http://127.0.0.1:8770/",
    "workflow": "http://127.0.0.1:8765/",
    "fitness": "http://127.0.0.1:8787/",
    "finance": "http://127.0.0.1:8000/financial-command/",
}


def _utc_now(now: Optional[datetime] = None) -> datetime:
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        return ref.replace(tzinfo=timezone.utc)
    return ref.astimezone(timezone.utc)


def _domain_by_id(domains: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(d.get("id")): d for d in domains if d.get("id")}


def finance_freshness_tier(
    as_of: Any,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Dual-tier finance freshness: fresh ≤6h, soft_stale ≤48h, else unknown."""
    ref = _utc_now(now)
    age = hours_since(as_of, now=ref)
    if as_of is None or age is None:
        return {
            "freshness": "unknown",
            "stale": True,
            "unknown": True,
            "age_hours": None,
            "fresh_for_hours": FINANCE_SOFT_HOURS,
            "max_age_hard_hours": FINANCE_HARD_HOURS,
        }
    if age > FINANCE_HARD_HOURS:
        tier = "unknown"
        unknown = True
        stale = True
    elif age > FINANCE_SOFT_HOURS:
        tier = "soft_stale"
        unknown = False
        stale = True
    else:
        tier = "fresh"
        unknown = False
        stale = False
    return {
        "freshness": tier,
        "stale": stale,
        "unknown": unknown,
        "age_hours": round(age, 2),
        "fresh_for_hours": FINANCE_SOFT_HOURS,
        "max_age_hard_hours": FINANCE_HARD_HOURS,
    }


def compute_red_mode(
    stress_overall: Any,
    dca: Optional[dict[str, Any]] = None,
    *,
    known: bool,
) -> tuple[Optional[bool], list[str]]:
    """Red-mode when known and (overall red OR DCA pause for margin heat).

    Returns (red_mode, reasons). red_mode is None when not known.
    """
    if not known:
        return None, []
    reasons: list[str] = []
    overall = str(stress_overall or "").strip().lower()
    if overall == "red":
        reasons.append("stress_overall_red")
    dca = dca if isinstance(dca, dict) else {}
    throttle = str(dca.get("throttle") or "").strip().lower()
    reason = str(dca.get("reason") or "").lower()
    margin_use = dca.get("margin_use")
    # Margin heat only — not BP-floor / size pauses
    if throttle in ("pause", "paused") and (
        "margin" in reason or margin_use is not None and "margin" in reason
    ):
        reasons.append("margin_heat")
    elif throttle in ("pause", "paused") and "margin heat" in reason:
        reasons.append("margin_heat")
    # Explicit margin_use flag from eval without BP-floor wording
    if throttle in ("pause", "paused") and margin_use is not None:
        # Only count as margin heat when reason mentions margin or use exceeds policy
        if "margin" in reason or "margin_use" in reason:
            if "margin_heat" not in reasons:
                reasons.append("margin_heat")
    return (True if reasons else False), reasons


def free_cash_gate_value(
    *,
    red_mode: Optional[bool],
    freshness: str,
) -> str:
    if freshness == "unknown" or red_mode is None:
        return "unknown"
    if red_mode is True:
        return "block_new_risk"
    return "allow"


def _envelope(
    domain: str,
    *,
    as_of: Any = None,
    fresh_for_hours: float = 24.0,
    stale: bool = False,
    confidence: float = 0.0,
    summary: str = "",
    constraints: Optional[list[dict[str, Any]]] = None,
    suggested_actions: Optional[list[dict[str, Any]]] = None,
    deep_link: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "domain": domain,
        "as_of": as_of,
        "fresh_for_hours": fresh_for_hours,
        "stale": bool(stale),
        "confidence": float(confidence),
        "summary": summary or "",
        "constraints": list(constraints or []),
        "suggested_actions": list(suggested_actions or []),
        "deep_link": deep_link or DEFAULT_DEEP_LINKS.get(domain),
    }
    if extra:
        out.update(extra)
    return out


def _map_block_kind(block: dict[str, Any]) -> str:
    role = str(block.get("role") or "").lower()
    kind = str(block.get("kind") or "").lower()
    bid = str(block.get("id") or "").lower()
    title = str(block.get("title") or "").lower()
    if role == "reserve" or bid == "sleep" or "sleep" in title:
        return "sleep" if "sleep" in bid or "sleep" in title else "reserve"
    if role == "fixed" or "duchess" in bid or "walk" in title and "duchess" in title:
        return "fixed"
    if role in ("capacity", "work", "adhoc") or kind in (
        "capacity",
        "work",
        "adhoc",
        "fixed",
        "sleep",
        "reserve",
    ):
        if kind in ("sleep", "fixed", "capacity", "work", "reserve", "adhoc"):
            return kind
        return role if role in ("capacity", "work", "adhoc", "fixed", "reserve") else "work"
    if kind in ("rolling_avg", "daily_duration"):
        return "reserve" if role == "reserve" else "fixed"
    return "work"


def build_holistic_source(
    holistic: dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Envelope + day blocks from holistic domain snapshot."""
    del now  # reserved for future age calc
    sig = holistic.get("signals") if isinstance(holistic.get("signals"), dict) else {}
    deep = holistic.get("url") or DEFAULT_DEEP_LINKS["holistic"]
    available = bool(holistic.get("available"))

    plan_blocks = sig.get("plan_blocks") or []
    blocks_out: list[dict[str, Any]] = []
    if isinstance(plan_blocks, list):
        for b in plan_blocks:
            if not isinstance(b, dict):
                # legacy string id
                title = str(b)
                blocks_out.append(
                    {
                        "id": title,
                        "title": title,
                        "kind": "work",
                        "start": None,
                        "end": None,
                        "minutes": 0,
                        "source": "holistic",
                    }
                )
                continue
            bid = str(b.get("id") or b.get("title") or "block")
            blocks_out.append(
                {
                    "id": bid,
                    "title": str(b.get("title") or bid),
                    "kind": _map_block_kind(b),
                    "start": b.get("start"),
                    "end": b.get("end"),
                    "minutes": int(b.get("minutes") or 0),
                    "source": "holistic",
                }
            )

    free_minutes = sig.get("free_minutes")
    if free_minutes is None:
        free_minutes = sig.get("unallocated_active_minutes")

    # Ensure sleep + Duchess on spine when targets exist even if plan empty
    targets = list(sig.get("target_objects") or []) + list(sig.get("targets") or [])
    target_ids = set()
    target_titles_l: list[str] = []
    for t in targets:
        if isinstance(t, dict) and t.get("id"):
            target_ids.add(str(t["id"]).lower())
            if t.get("title"):
                target_titles_l.append(str(t["title"]).lower())
        elif isinstance(t, str):
            target_ids.add(t.lower())
            target_titles_l.append(t.lower())
    existing_ids = {str(b.get("id")).lower() for b in blocks_out}
    has_sleep_target = "sleep" in target_ids or any("sleep" in t for t in target_titles_l)
    has_duchess_target = any("duchess" in tid for tid in target_ids) or any(
        "duchess" in t for t in target_titles_l
    )
    if has_sleep_target and "sleep" not in existing_ids:
        blocks_out.insert(
            0,
            {
                "id": "sleep",
                "title": "Sleep",
                "kind": "sleep",
                "start": None,
                "end": None,
                "minutes": int(sig.get("sleep_reserve_minutes") or 0),
                "source": "holistic",
            },
        )
    if has_duchess_target and not any(
        "duchess" in str(b.get("id") or "").lower() for b in blocks_out
    ):
        blocks_out.append(
            {
                "id": "duchess-walk",
                "title": "Walk Duchess",
                "kind": "fixed",
                "start": None,
                "end": None,
                "minutes": 45,
                "source": "holistic",
            }
        )

    as_of = sig.get("as_of") or sig.get("plan_as_of")
    stale = not available or not blocks_out
    conf = 0.8 if available and blocks_out else 0.2 if available else 0.0
    summary_bits = []
    if free_minutes is not None:
        summary_bits.append(f"{free_minutes} free min")
    if blocks_out:
        summary_bits.append(f"{len(blocks_out)} block(s)")
    summary = "; ".join(summary_bits) or (
        holistic.get("summary") or ("holistic missing" if not available else "no plan blocks")
    )

    constraints: list[dict[str, Any]] = []
    if free_minutes is not None:
        constraints.append(
            {
                "id": "free_minutes",
                "kind": "capacity",
                "severity": "info",
                "title": "Free minutes",
                "detail": f"{free_minutes} unallocated active minutes",
                "blocks_minutes": free_minutes,
                "until": None,
            }
        )

    env = _envelope(
        "holistic",
        as_of=as_of,
        fresh_for_hours=24.0,
        stale=stale,
        confidence=conf,
        summary=summary,
        constraints=constraints,
        suggested_actions=[],
        deep_link=deep,
        extra={
            "plan_blocks": blocks_out,
            "free_minutes": free_minutes,
            "sleep_reserve_minutes": sig.get("sleep_reserve_minutes"),
            "targets": list(sig.get("targets") or [])[:12],
        },
    )
    return env, blocks_out


def build_workflow_source(
    workflow: dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Envelope, gates, suggested work actions from workflow/board packet signals."""
    ref = _utc_now(now)
    sig = workflow.get("signals") if isinstance(workflow.get("signals"), dict) else {}
    board = sig.get("board") if isinstance(sig.get("board"), dict) else {}
    deep = (
        board.get("deep_link")
        or workflow.get("url")
        or DEFAULT_DEEP_LINKS["workflow"]
    )

    # Prefer frozen board packet fields; fall back to explicit day packet
    day = sig.get("day") if isinstance(sig.get("day"), dict) else {}
    src = {**day, **board} if board or day else {}

    as_of = src.get("as_of") or board.get("as_of") or day.get("as_of")
    fresh_for = float(src.get("fresh_for_hours") or WORKFLOW_FRESH_HOURS)
    age = hours_since(as_of, now=ref) if as_of else None
    fetch_ok = src.get("fetch_ok")
    if fetch_ok is None:
        # present packet without fetch_ok → assume ok if as_of present
        fetch_ok = as_of is not None and src.get("ready_count") is not None

    stale = bool(src.get("stale"))
    if not fetch_ok:
        stale = True
    if age is not None and age > fresh_for:
        stale = True
    if not src or src.get("ready_count") is None:
        # no board packet — honest unknown (not pretty zeros)
        stale = True
        fetch_ok = False

    conf = 0.0 if stale else float(src.get("confidence") if src.get("confidence") is not None else 0.85)

    def _int_or_none(key: str) -> Optional[int]:
        if not fetch_ok or stale and src.get(key) is None:
            return None if not fetch_ok else src.get(key)
        v = src.get(key)
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    if not fetch_ok or (stale and src.get("ready_count") is None):
        ready_count = None
        pending_review_count = None
        free_agent_count = None
        in_progress: list = []
        ready_top: list = []
        blocked: list = []
        wip_overload = None
        pipeline_pressure = None
        summary = "Board unknown — refresh Workflow / Board"
        conf = 0.0
    else:
        ready_count = _int_or_none("ready_count")
        if ready_count is None and "ready_count" in src:
            try:
                ready_count = int(src["ready_count"])
            except (TypeError, ValueError):
                ready_count = None
        pending_review_count = src.get("pending_review_count")
        free_agent_count = src.get("free_agent_count")
        in_progress = list(src.get("in_progress") or [])
        ready_top = list(src.get("ready_top") or [])[:3]
        blocked = list(src.get("blocked") or [])
        wip_overload = bool(src.get("wip_overload")) if src.get("wip_overload") is not None else False
        pipeline_pressure = src.get("pipeline_pressure") or "ok"
        summary = src.get("summary") or (
            f"Ready {ready_count} · IP {len(in_progress)} · PR {pending_review_count} · free {free_agent_count}"
        )

    gates: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    suggested: list[dict[str, Any]] = []

    if not fetch_ok or (stale and ready_count is None):
        gates.append(
            {
                "id": "workflow_board",
                "domain": "workflow",
                "severity": "unknown",
                "title": "Board unknown",
                "detail": "Buzz Board fetch failed or no day packet — not inventing Ready/IP zeros",
                "as_of": as_of,
                "stale": True,
                "freshness": "unknown",
                "deep_link": deep,
            }
        )
        constraints.append(
            {
                "id": "workflow_stale",
                "kind": "gate",
                "severity": "unknown",
                "title": "Board unknown",
                "detail": "stale or fetch fail",
                "blocks_minutes": None,
                "until": None,
            }
        )
    else:
        if stale:
            gates.append(
                {
                    "id": "workflow_freshness",
                    "domain": "workflow",
                    "severity": "warn",
                    "title": "Board stale",
                    "detail": f"age_hours={age}; fresh_for={fresh_for}h",
                    "as_of": as_of,
                    "stale": True,
                    "freshness": "unknown" if age is not None and age > fresh_for else "soft_stale",
                    "deep_link": deep,
                }
            )
        if wip_overload:
            gates.append(
                {
                    "id": "wip_overload",
                    "domain": "workflow",
                    "severity": "block",
                    "title": "WIP overload",
                    "detail": "Agent WIP cap = 1 In Progress; primary_owner on >1 IP card",
                    "as_of": as_of,
                    "stale": stale,
                    "freshness": "fresh" if not stale else "unknown",
                    "deep_link": deep,
                }
            )
            constraints.append(
                {
                    "id": "wip_overload",
                    "kind": "gate",
                    "severity": "block",
                    "title": "WIP overload",
                    "detail": "cap=1 In Progress",
                    "blocks_minutes": None,
                    "until": None,
                }
            )
        if blocked:
            gates.append(
                {
                    "id": "workflow_blocked",
                    "domain": "workflow",
                    "severity": "warn",
                    "title": f"{len(blocked)} blocked",
                    "detail": "; ".join(
                        f"#{b.get('number')}: {b.get('reason') or b.get('title')}"
                        for b in blocked[:3]
                        if isinstance(b, dict)
                    ),
                    "as_of": as_of,
                    "stale": stale,
                    "freshness": "fresh" if not stale else "unknown",
                    "deep_link": deep,
                }
            )
        # Suggested work actions
        if in_progress:
            top = in_progress[0] if isinstance(in_progress[0], dict) else {}
            suggested.append(
                {
                    "id": f"wf-ip-{top.get('number') or 1}",
                    "title": f"Continue / unblock #{top.get('number')}: {top.get('title') or 'In Progress'}",
                    "domain": "workflow",
                    "why": f"owner={top.get('primary_owner') or '?'}",
                    "severity": "warn" if blocked else "info",
                    "deep_link": deep,
                    "kind": "in_progress",
                }
            )
        if (pending_review_count or 0) > 0:
            suggested.append(
                {
                    "id": "wf-eng-gate",
                    "title": f"Eng-gate: {pending_review_count} in Pending Review",
                    "domain": "workflow",
                    "why": "PR queue does not busy agents",
                    "severity": "info",
                    "deep_link": deep,
                    "kind": "pending_review",
                }
            )
        if (ready_count or 0) > 0 and (free_agent_count or 0) >= 1 and not wip_overload:
            top_r = ready_top[0] if ready_top and isinstance(ready_top[0], dict) else {}
            num = top_r.get("number")
            title = top_r.get("title") or "Ready card"
            suggested.append(
                {
                    "id": f"wf-ready-{num or 'x'}",
                    "title": f"Pull candidate #{num}: {title}" if num else f"Pull Ready: {title}",
                    "domain": "workflow",
                    "why": "Ready supply + free agent",
                    "severity": "info",
                    "deep_link": deep,
                    "kind": "ready",
                }
            )
        if pipeline_pressure == "dry":
            suggested.append(
                {
                    "id": "wf-dry",
                    "title": "Promote Parked→Ready (ceremony)",
                    "domain": "workflow",
                    "why": "pipeline dry — Ready=0 with free agents",
                    "severity": "info",
                    "deep_link": deep,
                    "kind": "pipeline",
                }
            )

    env = _envelope(
        "workflow",
        as_of=as_of,
        fresh_for_hours=fresh_for,
        stale=stale,
        confidence=conf,
        summary=summary,
        constraints=constraints,
        suggested_actions=suggested,
        deep_link=deep,
        extra={
            "ready_count": ready_count,
            "ready_top": ready_top if fetch_ok else [],
            "in_progress": in_progress if fetch_ok else [],
            "pending_review_count": pending_review_count,
            "blocked": blocked if fetch_ok else [],
            "wip_overload": wip_overload,
            "free_agent_count": free_agent_count,
            "pipeline_pressure": pipeline_pressure,
            "age_hours": round(age, 2) if age is not None else None,
            "fetch_ok": bool(fetch_ok),
        },
    )
    return env, gates, suggested


def build_fitness_source(
    fitness: dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Envelope, body gates, suggested fit actions."""
    ref = _utc_now(now)
    sig = fitness.get("signals") if isinstance(fitness.get("signals"), dict) else {}
    day = sig.get("day") if isinstance(sig.get("day"), dict) else {}
    deep = day.get("deep_link") or fitness.get("url") or DEFAULT_DEEP_LINKS["fitness"]

    as_of = day.get("as_of") or sig.get("as_of")
    age = hours_since(as_of, now=ref) if as_of else None
    body_stale = as_of is None or age is None or age > FITNESS_BODY_HOURS
    # Also stale if no day packet at all
    has_day = bool(day) and (
        day.get("train_recommendation") is not None
        or day.get("session_due") is not None
        or day.get("recovery_score") is not None
        or day.get("protein_gap_band") is not None
    )

    if not has_day:
        body_stale = True

    train_rec = day.get("train_recommendation")
    recovery_score = day.get("recovery_score")
    recovery_label = day.get("recovery_label")
    session_due = day.get("session_due")
    session_type = day.get("session_type")
    protein_band = day.get("protein_gap_band")
    protein_remaining = day.get("protein_remaining_g")
    protein_target = day.get("protein_target_g")
    sleep_h = day.get("sleep_last_night_h")
    sleep_ok = day.get("sleep_ok")
    sleep_battery = day.get("sleep_battery")

    # Protein same civil day
    protein_as_of = day.get("protein_as_of") or as_of
    protein_stale = False
    if protein_as_of:
        pdt = parse_timestamp(protein_as_of)
        if pdt is None:
            protein_stale = True
        else:
            # same civil day in UTC for P1 simplicity
            if pdt.astimezone(timezone.utc).date() != ref.date():
                protein_stale = True
                protein_band = "unknown"
    elif protein_band is not None and protein_band != "unknown":
        # no as_of → unknown band
        protein_stale = True
        protein_band = "unknown"

    if body_stale:
        conf = 0.0
        train_rec = train_rec  # keep last known for honesty if present but mark stale
    else:
        conf = float(day.get("confidence") if day.get("confidence") is not None else 0.8)

    gates: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    suggested: list[dict[str, Any]] = []

    rest_block = False
    if not has_day or (body_stale and train_rec is None and recovery_score is None):
        gates.append(
            {
                "id": "body_unknown",
                "domain": "fitness",
                "severity": "unknown",
                "title": "Body unknown",
                "detail": "No Fit day packet or stale >24h — not inventing Ready",
                "as_of": as_of,
                "stale": True,
                "freshness": "unknown",
                "deep_link": deep,
            }
        )
    else:
        if body_stale:
            gates.append(
                {
                    "id": "body_freshness",
                    "domain": "fitness",
                    "severity": "warn",
                    "title": "Body stale",
                    "detail": f"age_hours={age}; max {FITNESS_BODY_HOURS}h",
                    "as_of": as_of,
                    "stale": True,
                    "freshness": "unknown",
                    "deep_link": deep,
                }
            )
        score_num = None
        try:
            if recovery_score is not None:
                score_num = float(recovery_score)
        except (TypeError, ValueError):
            score_num = None
        if (train_rec and str(train_rec).lower() == "rest") or (
            score_num is not None and score_num < 40
        ):
            rest_block = True
            gates.append(
                {
                    "id": "body_rest",
                    "domain": "fitness",
                    "severity": "block",
                    "title": "Rest / low recovery",
                    "detail": f"train_recommendation={train_rec}; recovery_score={recovery_score}",
                    "as_of": as_of,
                    "stale": body_stale,
                    "freshness": "unknown" if body_stale else "fresh",
                    "deep_link": deep,
                }
            )
            constraints.append(
                {
                    "id": "body_rest",
                    "kind": "gate",
                    "severity": "block",
                    "title": "Rest gate",
                    "detail": "blocks training-shaped Next actions",
                    "blocks_minutes": None,
                    "until": None,
                }
            )
        elif recovery_label == "Caution" or (
            score_num is not None and 40 <= score_num < 55
        ):
            gates.append(
                {
                    "id": "body_caution",
                    "domain": "fitness",
                    "severity": "warn",
                    "title": "Body caution",
                    "detail": f"label={recovery_label}; score={recovery_score}",
                    "as_of": as_of,
                    "stale": body_stale,
                    "freshness": "unknown" if body_stale else "fresh",
                    "deep_link": deep,
                }
            )

        # Protein
        if protein_band == "gap" and not protein_stale:
            gates.append(
                {
                    "id": "protein_gap",
                    "domain": "fitness",
                    "severity": "warn",
                    "title": "Protein gap",
                    "detail": f"remaining_g={protein_remaining}; target={protein_target}",
                    "as_of": protein_as_of,
                    "stale": False,
                    "freshness": "fresh",
                    "deep_link": deep,
                }
            )
            suggested.append(
                {
                    "id": "fit-protein",
                    "title": "Close protein gap",
                    "domain": "fitness",
                    "why": f"band=gap; remaining≈{protein_remaining}g",
                    "severity": "warn",
                    "deep_link": deep,
                    "kind": "protein",
                }
            )
        elif protein_band == "watch" and not protein_stale:
            suggested.append(
                {
                    "id": "fit-protein-watch",
                    "title": "Watch protein remaining",
                    "domain": "fitness",
                    "why": f"band=watch; remaining≈{protein_remaining}g",
                    "severity": "info",
                    "deep_link": deep,
                    "kind": "protein",
                }
            )

        # Session due — only if not rest-blocked
        if session_due and not rest_block and not body_stale:
            if train_rec in (None, "train", "easy") or str(train_rec).lower() in (
                "train",
                "easy",
            ):
                suggested.append(
                    {
                        "id": "fit-session",
                        "title": f"Train session ({session_type or 'planned'})",
                        "domain": "fitness",
                        "why": f"session_due; rec={train_rec or 'train'}",
                        "severity": "info",
                        "deep_link": deep,
                        "kind": "train",
                    }
                )

    summary = day.get("summary") or fitness.get("summary") or "fitness"
    if rest_block:
        summary = f"REST · {summary}"
    elif protein_band == "gap":
        summary = f"protein gap · {summary}"

    env = _envelope(
        "fitness",
        as_of=as_of,
        fresh_for_hours=FITNESS_BODY_HOURS,
        stale=body_stale or not has_day,
        confidence=conf,
        summary=str(summary),
        constraints=constraints,
        suggested_actions=suggested,
        deep_link=deep,
        extra={
            "session_due": session_due,
            "session_type": session_type,
            "train_recommendation": train_rec,
            "recovery_label": recovery_label,
            "recovery_score": recovery_score,
            "protein_gap_band": protein_band if not protein_stale else "unknown",
            "protein_remaining_g": protein_remaining,
            "protein_target_g": protein_target,
            "sleep_last_night_h": sleep_h,
            "sleep_ok": sleep_ok,
            "sleep_battery": sleep_battery,
            "rest_blocks_train": rest_block,
            "age_hours": round(age, 2) if age is not None else None,
        },
    )
    return env, gates, suggested


def build_finance_source(
    finance: dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Envelope, capital gates, whitelist suggested actions."""
    ref = _utc_now(now)
    sig = finance.get("signals") if isinstance(finance.get("signals"), dict) else {}
    deep = finance.get("url") or DEFAULT_DEEP_LINKS["finance"]
    as_of = sig.get("as_of")
    tier = finance_freshness_tier(as_of, now=ref)
    freshness = tier["freshness"]
    known = freshness in ("fresh", "soft_stale")

    stress_overall = sig.get("stress_overall") or sig.get("stress")
    if isinstance(stress_overall, dict):
        stress_overall = stress_overall.get("overall")
    if not known:
        stress_for_mode = None
        stress_display = "unknown"
    else:
        stress_for_mode = stress_overall
        stress_display = str(stress_overall or "unknown").lower()

    dca = sig.get("dca") if isinstance(sig.get("dca"), dict) else {}
    red_mode, red_reasons = compute_red_mode(
        stress_for_mode if known else None,
        dca,
        known=known,
    )
    # If collector already computed red_mode, prefer when known
    if known and sig.get("red_mode") is not None:
        red_mode = bool(sig.get("red_mode"))
        red_reasons = list(sig.get("red_mode_reasons") or red_reasons)

    fcg = sig.get("free_cash_gate") or free_cash_gate_value(
        red_mode=red_mode, freshness=freshness
    )
    if not known:
        fcg = "unknown"
        red_mode = None

    conf = 0.0
    if freshness == "fresh":
        conf = 0.9
    elif freshness == "soft_stale":
        conf = 0.45
    else:
        conf = 0.0

    gates: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    suggested: list[dict[str, Any]] = []

    # Freshness gate
    if freshness == "unknown":
        gates.append(
            {
                "id": "capital_freshness",
                "domain": "finance",
                "severity": "warn",
                "title": "FCC unknown — refresh",
                "detail": f"age_hours={tier.get('age_hours')}; hard>{FINANCE_HARD_HOURS}h or missing as_of",
                "as_of": as_of,
                "stale": True,
                "freshness": "unknown",
                "deep_link": deep,
            }
        )
        constraints.append(
            {
                "id": "capital_freshness",
                "kind": "gate",
                "severity": "warn",
                "title": "FCC unknown",
                "detail": "hard stale or missing as_of",
                "blocks_minutes": None,
                "until": None,
            }
        )
        suggested.append(
            {
                "id": "fin-refresh",
                "title": "Refresh FCC treasury snapshot",
                "domain": "finance",
                "why": "hard-unknown capital — no free-dollar risk recs",
                "severity": "warn",
                "deep_link": deep,
                "kind": "refresh",
            }
        )
    elif freshness == "soft_stale":
        gates.append(
            {
                "id": "capital_freshness",
                "domain": "finance",
                "severity": "warn",
                "title": "FCC soft stale",
                "detail": f"age_hours={tier.get('age_hours')}; soft>{FINANCE_SOFT_HOURS}h",
                "as_of": as_of,
                "stale": True,
                "freshness": "soft_stale",
                "deep_link": deep,
            }
        )

    # Red-mode / free cash
    if red_mode is True:
        gates.append(
            {
                "id": "capital_red_mode",
                "domain": "finance",
                "severity": "block",
                "title": "Capital red-mode",
                "detail": ", ".join(red_reasons) or "red_mode",
                "as_of": as_of,
                "stale": tier["stale"],
                "freshness": freshness,
                "deep_link": deep,
            }
        )
        gates.append(
            {
                "id": "free_cash",
                "domain": "finance",
                "severity": "block",
                "title": "Free-cash gate: block new risk",
                "detail": "block_new_risk — no new free/external discretionary dollars",
                "as_of": as_of,
                "stale": tier["stale"],
                "freshness": freshness,
                "deep_link": deep,
            }
        )
        constraints.append(
            {
                "id": "capital_red_mode",
                "kind": "gate",
                "severity": "block",
                "title": "Capital red-mode",
                "detail": ", ".join(red_reasons) or "red",
                "blocks_minutes": None,
                "until": None,
            }
        )
    elif fcg == "unknown" and freshness != "unknown":
        gates.append(
            {
                "id": "free_cash",
                "domain": "finance",
                "severity": "warn",
                "title": "Free-cash unknown",
                "detail": "cannot certify allow",
                "as_of": as_of,
                "stale": True,
                "freshness": freshness,
                "deep_link": deep,
            }
        )

    # Whitelist actions from signals
    raw_actions = list(sig.get("day_actions") or sig.get("actions") or [])
    # Also parse action_titles with kinds if day_actions empty
    if not raw_actions and sig.get("action_titles"):
        for t in sig.get("action_titles") or []:
            if isinstance(t, str):
                raw_actions.append({"title": t, "kind": None})

    for a in raw_actions:
        if not isinstance(a, dict):
            continue
        kind = str(a.get("kind") or "").strip().lower() or None
        title = a.get("title") or kind or "treasury action"
        if kind and kind not in FINANCE_ACTION_WHITELIST:
            continue
        if kind and kind in FINANCE_FREE_DOLLAR_RISK:
            if red_mode is True or fcg in ("block_new_risk", "unknown"):
                continue
        if red_mode is True or fcg in ("block_new_risk", "unknown"):
            if kind and kind not in FINANCE_RED_MODE_ALLOW:
                continue
            if kind is None:
                # untitled free-form — allow only if looks like LTV/card/refresh
                tl = str(title).lower()
                if not any(
                    x in tl
                    for x in (
                        "ltv",
                        "card",
                        "float",
                        "paydown",
                        "vault",
                        "buffer",
                        "refresh",
                        "fill missing",
                        "morpho",
                    )
                ):
                    continue
        if freshness == "unknown" and kind != "refresh":
            # only refresh competes when hard unknown
            continue
        suggested.append(
            {
                "id": f"fin-{kind or 'act'}-{len(suggested)}",
                "title": str(title)[:160],
                "domain": "finance",
                "why": str(a.get("detail") or a.get("why") or kind or "treasury whitelist"),
                "severity": "block" if red_mode else ("warn" if tier["stale"] else "info"),
                "deep_link": deep,
                "kind": kind,
            }
        )
        if len(suggested) >= 3:
            break

    if freshness == "unknown" and not any(s.get("kind") == "refresh" for s in suggested):
        suggested.insert(
            0,
            {
                "id": "fin-refresh",
                "title": "Refresh FCC treasury snapshot",
                "domain": "finance",
                "why": "hard-unknown capital",
                "severity": "warn",
                "deep_link": deep,
                "kind": "refresh",
            },
        )

    summary_bits = [f"freshness={freshness}"]
    if stress_display:
        summary_bits.append(f"stress={stress_display}")
    if red_mode is True:
        summary_bits.append("RED-MODE")
    summary = sig.get("day_summary") or "; ".join(summary_bits)

    env = _envelope(
        "finance",
        as_of=as_of,
        fresh_for_hours=FINANCE_SOFT_HOURS,
        stale=bool(tier["stale"]),
        confidence=conf,
        summary=str(summary),
        constraints=constraints,
        suggested_actions=suggested[:3],
        deep_link=deep,
        extra={
            "max_age_hard_hours": FINANCE_HARD_HOURS,
            "freshness": freshness,
            "unknown": bool(tier.get("unknown")),
            "age_hours": tier.get("age_hours"),
            "stress_overall": stress_display if known else "unknown",
            "stress": sig.get("stress_parts") or sig.get("stress_detail"),
            "red_mode": red_mode,
            "red_mode_reasons": red_reasons,
            "free_cash_gate": fcg,
            "rh_bp_deployable": sig.get("rh_bp_deployable"),
            "working_usdc": sig.get("working_usdc"),
            "floors_shortfall_usd": sig.get("floors_shortfall_usd"),
            "dca": dca or None,
            "ltv_known": sig.get("ltv_known"),
        },
    )
    return env, gates, suggested[:3]


def _rank_key(item: dict[str, Any]) -> tuple[int, int]:
    sev = str(item.get("severity") or "info")
    sev_score = {"block": 0, "warn": 1, "info": 2, "unknown": 1}.get(sev, 3)
    # Prefer non-refresh work/body when not blocked
    kind = str(item.get("kind") or "")
    kind_bump = 0 if kind in ("in_progress", "train", "protein", "ltv_check", "fill_manual") else 1
    return (sev_score, kind_bump)


def _is_train_action(item: dict[str, Any]) -> bool:
    kind = str(item.get("kind") or "").lower()
    title = str(item.get("title") or "").lower()
    if kind in ("train", "session", "workout"):
        return True
    return any(x in title for x in ("train session", "ppl", "workout session", "hit the full"))


def _is_free_dollar_risk(item: dict[str, Any]) -> bool:
    kind = str(item.get("kind") or "").lower()
    if kind in FINANCE_FREE_DOLLAR_RISK:
        return True
    if kind in FINANCE_RED_MODE_ALLOW:
        return False
    title = str(item.get("title") or "").lower()
    return any(
        x in title
        for x in ("deploy free", "spot buy", "dca buy", "buy btc", "open risk")
    )


def compose_day_plan(
    domains: list[dict[str, Any]],
    *,
    recommendations: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Compose frozen day_plan from domain snapshots.

    Pure function. Optional recommendations items may contribute candidates
    after domain suggested_actions (not a permanent second ranker).
    """
    ref = _utc_now(now)
    by_id = _domain_by_id(domains)

    hol_src, blocks = build_holistic_source(by_id.get("holistic") or {}, now=ref)
    wf_src, wf_gates, wf_sugg = build_workflow_source(by_id.get("workflow") or {}, now=ref)
    fit_src, fit_gates, fit_sugg = build_fitness_source(by_id.get("fitness") or {}, now=ref)
    fin_src, fin_gates, fin_sugg = build_finance_source(by_id.get("finance") or {}, now=ref)

    gates = list(fin_gates) + list(fit_gates) + list(wf_gates)

    rest_blocks_train = bool(fit_src.get("rest_blocks_train"))
    red_mode = fin_src.get("red_mode")
    fcg = fin_src.get("free_cash_gate")
    wip_overload = wf_src.get("wip_overload") is True

    candidates: list[dict[str, Any]] = []
    for pool in (wf_sugg, fit_sugg, fin_sugg):
        candidates.extend(pool)

    # Optional light merge from recommendations (same shape filter)
    if recommendations and isinstance(recommendations, dict):
        for item in recommendations.get("items") or []:
            if not isinstance(item, dict):
                continue
            domains_list = item.get("domains") or []
            dom = None
            for d in domains_list:
                if d in ("fitness", "finance", "workflow", "holistic"):
                    dom = d
                    break
            if not dom:
                continue
            candidates.append(
                {
                    "id": str(item.get("id") or item.get("title") or "rec"),
                    "title": str(item.get("action") or item.get("title") or "")[:160],
                    "domain": dom,
                    "why": str(item.get("why") or item.get("rationale") or "")[:200],
                    "severity": "info",
                    "deep_link": DEFAULT_DEEP_LINKS.get(dom),
                    "kind": item.get("kind"),
                }
            )

    filtered: list[dict[str, Any]] = []
    for c in candidates:
        if not c.get("title"):
            continue
        if rest_blocks_train and _is_train_action(c):
            continue
        if (red_mode is True or fcg in ("block_new_risk", "unknown")) and _is_free_dollar_risk(
            c
        ):
            continue
        if wip_overload and c.get("kind") == "ready":
            # do not pull more Ready while overloaded
            continue
        filtered.append(c)

    # Dedup by title
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for c in sorted(filtered, key=_rank_key):
        key = str(c.get("title") or "").strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(
            {
                "id": str(c.get("id") or key)[:80],
                "title": str(c.get("title")),
                "domain": c.get("domain"),
                "why": c.get("why") or "",
                "severity": c.get("severity") or "info",
                "deep_link": c.get("deep_link") or DEFAULT_DEEP_LINKS.get(str(c.get("domain"))),
                "kind": c.get("kind"),
            }
        )
        if len(deduped) >= MAX_NEXT3:
            break

    # Summary line
    gate_titles = [g.get("title") for g in gates if g.get("severity") in ("block", "unknown")]
    summary_parts = []
    if gate_titles:
        summary_parts.append("gates: " + ", ".join(str(t) for t in gate_titles[:3]))
    if deduped:
        summary_parts.append("next: " + deduped[0]["title"][:60])
    elif blocks:
        summary_parts.append(f"{len(blocks)} day blocks")
    summary = " · ".join(summary_parts) if summary_parts else "day_plan empty — check domain freshness"

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": ref.isoformat(),
        "summary": summary,
        "next3": deduped,
        "blocks": blocks,
        "gates": gates,
        "sources": {
            "holistic": hol_src,
            "workflow": wf_src,
            "fitness": fit_src,
            "finance": fin_src,
        },
    }
