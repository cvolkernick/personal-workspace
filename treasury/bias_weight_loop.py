#!/usr/bin/env python3
"""Autonomous bias-weighting loop (#768).

Small residual-mix moves auto-apply. Pin changes, sleeve-target changes, and
oversized batches stage for the Chairman. Pins in consider_share.json stay
hard rails — the loop never invents pins. 60/40 sleeve targets never move
without the Chairman. Not an order ticket and not fund-manager autopilot.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONSIDER_SHARE_PATH = ROOT / "investment" / "consider_share.json"
STAGED_PATH = ROOT / "investment" / "bias_weight_staged.json"
JOURNAL_PATH = ROOT / "investment" / "fund_manager_journal.md"
JSONL_PATH = ROOT / "treasury" / "snapshots" / "bias_weight_loop.jsonl"
FUND_POLICY_PATH = ROOT / "investment" / "fund_manager.json"

# Chairman lock: issue #768 proposed defaults, approved 2026-09-15 (spec the middle path).
LOCKED_GUARDRAILS = {
    "locked_by": "Chairman",
    "locked_as_of": "2026-09-15",
    "source": "https://github.com/cvolkernick/personal-workspace/issues/768",
    "single_symbol_max_auto": 2.0,
    "total_abs_max_auto": 5.0,
    "never_auto_sleeve_targets": True,
    "never_invent_pins": True,
    "thin_signal_skips": True,
}

SLEEVE_KEYS = {
    "BTC_DIGITAL_CREDIT_PCT",
    "STOCKS_GROWTH_PCT",
    "BAND_PCT",
    "BTC_DIGITAL_CREDIT",
    "STOCKS_GROWTH",
}

_SYMBOL_LINE = re.compile(
    r"^\s*[-*]\s+\*\*([A-Z]{1,6})\*\*(?:\s|\(|—|-|$)",
)
_RELEVANCE = re.compile(
    r"Allocation relevance:\s+\*\*(up|down|neutral)\*\*",
    re.I,
)
_SECTION = re.compile(r"^##\s+(.*)$")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sym(value: Any) -> str:
    return str(value or "").strip().upper()


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def pins_from(share: Dict[str, Any]) -> Dict[str, float]:
    raw = share.get("pins") if isinstance(share.get("pins"), dict) else {}
    out: Dict[str, float] = {}
    for key, val in raw.items():
        sym = _sym(key)
        pct = _f(val)
        if sym and pct > 0:
            out[sym] = pct
    return out


def load_guardrails(share: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Chairman-locked numbers. File may echo them; code lock wins on conflict."""
    out = dict(LOCKED_GUARDRAILS)
    block = (share or {}).get("guardrails") if isinstance(share, dict) else None
    if isinstance(block, dict):
        # Echo-only: do not let a local file loosen the Chairman lock.
        for key in ("single_symbol_max_auto", "total_abs_max_auto"):
            if key in block:
                try:
                    file_val = float(block[key])
                except (TypeError, ValueError):
                    continue
                out[key] = min(float(out[key]), file_val)
    return out


def parse_digest_proposals(text: str, *, source: str = "") -> List[Dict[str, Any]]:
    """Map digest Allocation-relevance lines to residual deltas.

    up → +1, down → −1. Neutral / quiet / private-watchlist → skip (thin signal).
    Pins are tagged so the engine stages them; this parser never emits sleeve moves.
    """
    proposals: List[Dict[str, Any]] = []
    skip_section = False
    current: Optional[Dict[str, Any]] = None

    def _flush() -> None:
        nonlocal current
        if current and current.get("material"):
            proposals.append(current)
        current = None

    for line in (text or "").splitlines():
        heading = _SECTION.match(line)
        if heading:
            _flush()
            title = heading.group(1).strip().lower()
            skip_section = title.startswith("private")
            continue
        if skip_section:
            continue
        sym_m = _SYMBOL_LINE.match(line)
        if sym_m:
            _flush()
            current = {
                "symbol": _sym(sym_m.group(1)),
                "delta": 0.0,
                "kind": "residual",
                "rationale": "",
                "source": source,
                "material": False,
                "citation": "",
            }
            continue
        if current is None:
            continue
        rel = _RELEVANCE.search(line)
        if rel:
            direction = rel.group(1).lower()
            rest = line.split("—", 1)[-1].strip() if "—" in line else line
            current["rationale"] = rest
            if direction == "up":
                current["delta"] = 1.0
                current["material"] = True
            elif direction == "down":
                current["delta"] = -1.0
                current["material"] = True
            else:
                current["material"] = False
        if "http://" in line or "https://" in line:
            url = re.search(r"https?://\S+", line)
            if url:
                current["citation"] = url.group(0).rstrip(").,]")
    _flush()
    # One net delta per symbol; mixed up+down becomes stage later via critic.
    by_sym: Dict[str, Dict[str, Any]] = {}
    mixed: set[str] = set()
    for prop in proposals:
        sym = prop["symbol"]
        if sym in by_sym and (by_sym[sym]["delta"] > 0) != (prop["delta"] > 0):
            mixed.add(sym)
            by_sym[sym]["delta"] = by_sym[sym]["delta"] + prop["delta"]
            by_sym[sym]["rationale"] += " | " + str(prop.get("rationale") or "")
            by_sym[sym]["mixed"] = True
        elif sym in by_sym:
            by_sym[sym]["delta"] = by_sym[sym]["delta"] + prop["delta"]
            if prop.get("citation") and not by_sym[sym].get("citation"):
                by_sym[sym]["citation"] = prop["citation"]
        else:
            by_sym[sym] = dict(prop)
    return [by_sym[k] for k in sorted(by_sym)]


def _tag_kind(prop: Dict[str, Any], pins: Dict[str, float]) -> str:
    kind = str(prop.get("kind") or "residual").lower()
    if kind == "sleeve" or _sym(prop.get("symbol")) in SLEEVE_KEYS:
        return "sleeve"
    if kind == "pin" or _sym(prop.get("symbol")) in pins:
        return "pin"
    return "residual"


def classify_proposals(
    proposals: List[Dict[str, Any]],
    *,
    share: Dict[str, Any],
    guardrails: Optional[Dict[str, Any]] = None,
    override: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Split proposals into apply / stage / skip. Never auto-touches sleeves or pins."""
    g = load_guardrails(share) if guardrails is None else dict(guardrails)
    pins = pins_from(share)
    single_max = float(g.get("single_symbol_max_auto") or 2)
    total_max = float(g.get("total_abs_max_auto") or 5)
    override_sym = _sym((override or {}).get("symbol")) if override else ""

    apply: List[Dict[str, Any]] = []
    stage: List[Dict[str, Any]] = []
    skip: List[Dict[str, Any]] = []

    for raw in proposals:
        prop = dict(raw)
        prop["symbol"] = _sym(prop.get("symbol"))
        prop["delta"] = _f(prop.get("delta"))
        prop["kind"] = _tag_kind(prop, pins)
        is_override = bool(override_sym and prop["symbol"] == override_sym)

        if prop["kind"] == "sleeve":
            prop["stage_reason"] = "sleeve_targets_always_chairman"
            stage.append(prop)
            continue
        if prop["kind"] == "pin" and not is_override:
            prop["stage_reason"] = "pin_hard_rail"
            stage.append(prop)
            continue
        if not prop.get("material") and not is_override:
            prop["skip_reason"] = "thin_signal"
            skip.append(prop)
            continue
        if abs(prop["delta"]) < 1e-9 and not is_override:
            prop["skip_reason"] = "zero_delta"
            skip.append(prop)
            continue
        if abs(prop["delta"]) > single_max and not is_override:
            prop["stage_reason"] = "single_symbol_over_max"
            stage.append(prop)
            continue
        apply.append(prop)

    apply_abs = sum(abs(_f(p.get("delta"))) for p in apply)
    if apply_abs > total_max:
        for prop in apply:
            if override_sym and prop["symbol"] == override_sym:
                continue
            prop["stage_reason"] = "total_abs_over_max"
            stage.append(prop)
        apply = [p for p in apply if not p.get("stage_reason")]
    return {"apply": apply, "stage": stage, "skip": skip}


def committee_debate(
    classified: Dict[str, List[Dict[str, Any]]],
    *,
    override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Existing fund-manager roles: scout / thesis / risk / critic. Dissent stays."""
    apply = classified.get("apply") or []
    stage = classified.get("stage") or []
    skip = classified.get("skip") or []
    override = override or {}

    def _fmt(rows: List[Dict[str, Any]]) -> str:
        if not rows:
            return "none"
        return ", ".join(
            f"{r.get('symbol')} {float(r.get('delta') or 0):+g}" for r in rows
        )

    scout_note = (
        f"Proposals in: apply {_fmt(apply)}; stage {_fmt(stage)}; skip {_fmt(skip)}."
    )
    thesis_vote = "apply" if apply else ("quiet" if not stage else "stage")
    thesis_note = (
        "Material residual deltas within guardrails should auto-apply with citations."
        if apply
        else "No in-guardrail residual tilt today."
    )
    risk_vote = "stage" if stage else "ok"
    risk_note = (
        "Stage pin/sleeve/oversize; never auto-move 60/40; never invent pins."
        if stage
        else "Guardrails hold for the apply set."
    )
    critic_dissent = []
    for row in stage:
        critic_dissent.append(
            f"{row.get('symbol')} staged ({row.get('stage_reason')})"
        )
    for row in skip:
        critic_dissent.append(f"{row.get('symbol')} skip ({row.get('skip_reason')})")
    mixed = [r for r in apply + stage if r.get("mixed")]
    for row in mixed:
        critic_dissent.append(f"{row.get('symbol')} mixed up/down in one sweep")
    critic_vote = "dissent" if critic_dissent else "ok"
    critic_note = (
        "; ".join(critic_dissent)
        if critic_dissent
        else "No dissent — citations present, pins/sleeves untouched."
    )
    if override:
        thesis_note += (
            f" Chairman override on {override.get('symbol')} applies immediately."
        )
        critic_note += " Override is logged as Chairman, not a loop decision."

    final = "quiet"
    if apply and stage:
        final = "apply_and_stage"
    elif apply:
        final = "apply"
    elif stage:
        final = "stage"

    return {
        "roles": {
            "scout": {"vote": "observe", "note": scout_note},
            "thesis": {"vote": thesis_vote, "note": thesis_note},
            "risk": {"vote": risk_vote, "note": risk_note},
            "critic": {"vote": critic_vote, "note": critic_note, "dissent": critic_dissent},
        },
        "final_call": final,
        "disagreement": bool(critic_dissent) or (bool(apply) and bool(stage)),
    }


def _write_share_adjustments(
    share: Dict[str, Any],
    apply_rows: List[Dict[str, Any]],
    *,
    path: Path,
) -> Dict[str, float]:
    adj = dict(share.get("loop_adjustments") or {})
    pins = pins_from(share)
    for row in apply_rows:
        sym = _sym(row.get("symbol"))
        if not sym or sym in pins:
            continue
        adj[sym] = round(_f(adj.get(sym)) + _f(row.get("delta")), 2)
        if abs(adj[sym]) < 1e-9:
            adj.pop(sym, None)
    share["loop_adjustments"] = adj
    share.setdefault("guardrails", dict(LOCKED_GUARDRAILS))
    save_json(path, share)
    return {str(k): float(v) for k, v in adj.items()}


def _write_staged(
    existing: Dict[str, Any],
    stage_rows: List[Dict[str, Any]],
    *,
    path: Path,
    as_of: str,
) -> List[Dict[str, Any]]:
    pending = list(existing.get("pending") or [])
    for row in stage_rows:
        pending.append(
            {
                "as_of": as_of,
                "symbol": _sym(row.get("symbol")),
                "delta": _f(row.get("delta")),
                "kind": row.get("kind") or "residual",
                "reason": row.get("stage_reason") or "guardrail",
                "rationale": row.get("rationale") or "",
                "citation": row.get("citation") or "",
                "source": row.get("source") or "",
            }
        )
    out = {
        "schema": "fcc_bias_weight_staged_v0",
        "as_of": as_of,
        "notes": "Chairman queue. Not applied. Not a second consider-share SoT.",
        "pending": pending,
    }
    save_json(path, out)
    return pending


def _minutes_markdown(entry: Dict[str, Any]) -> str:
    debate = entry.get("committee") or {}
    roles = debate.get("roles") or {}
    lines = [
        f"\n## {entry.get('as_of', _now())[:19]} — bias_weight_loop\n",
        f"**Summary:** {entry.get('summary')}\n",
        "\n### Committee minutes\n",
    ]
    for role in ("scout", "thesis", "risk", "critic"):
        v = roles.get(role) or {}
        lines.append(
            f"- **{role}:** {v.get('vote', '—')} — {v.get('note', '')}\n"
        )
    lines.append(f"**Final call:** {debate.get('final_call')}\n")
    applied = entry.get("applied") or []
    staged = entry.get("staged") or []
    if applied:
        lines.append("**Applied:** " + ", ".join(
            f"{r.get('symbol')} {float(r.get('delta') or 0):+g}" for r in applied
        ) + "\n")
    if staged:
        lines.append("**Staged:** " + ", ".join(
            f"{r.get('symbol')} {float(r.get('delta') or 0):+g} ({r.get('stage_reason')})"
            for r in staged
        ) + "\n")
    rat = entry.get("rationale") or {}
    if rat.get("why_now"):
        lines.append(f"**Why now:** {rat['why_now']}\n")
    if rat.get("rejected_alternatives"):
        lines.append(f"**Rejected alternatives:** {rat['rejected_alternatives']}\n")
    if entry.get("override"):
        ov = entry["override"]
        lines.append(
            f"**Override:** Chairman {ov.get('symbol')} {float(ov.get('delta') or 0):+g}"
            f" — {ov.get('why') or ov.get('instruction') or ''}\n"
        )
    else:
        lines.append("**Override:** none\n")
    lines.append("\n")
    return "".join(lines)


def _append_journal(entry: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text("# Fund manager journal\n\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(_minutes_markdown(entry))


def _append_jsonl(entry: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def run_assessment(
    *,
    proposals: Optional[List[Dict[str, Any]]] = None,
    digest_text: Optional[str] = None,
    digest_source: str = "",
    share: Optional[Dict[str, Any]] = None,
    share_path: Optional[Path] = None,
    staged_path: Optional[Path] = None,
    journal_path: Optional[Path] = None,
    jsonl_path: Optional[Path] = None,
    write: bool = False,
    override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    share_path = share_path or CONSIDER_SHARE_PATH
    staged_path = staged_path or STAGED_PATH
    journal_path = journal_path or JOURNAL_PATH
    jsonl_path = jsonl_path or JSONL_PATH
    share = dict(share) if isinstance(share, dict) else load_json(share_path)
    if digest_text and not proposals:
        proposals = parse_digest_proposals(digest_text, source=digest_source)
    proposals = list(proposals or [])

    if override:
        ov = dict(override)
        ov["symbol"] = _sym(ov.get("symbol"))
        ov["delta"] = _f(ov.get("delta"), 1.0)
        ov["kind"] = ov.get("kind") or "residual"
        ov["material"] = True
        ov["rationale"] = ov.get("why") or ov.get("instruction") or "Chairman override"
        ov["source"] = ov.get("source") or "chairman_override"
        # Override replaces any same-symbol loop proposal.
        proposals = [p for p in proposals if _sym(p.get("symbol")) != ov["symbol"]]
        proposals.append(ov)

    classified = classify_proposals(
        proposals, share=share, override=override
    )
    debate = committee_debate(classified, override=override)
    as_of = _now()
    applied: List[Dict[str, Any]] = []
    staged_rows: List[Dict[str, Any]] = []
    new_adj: Dict[str, float] = dict(share.get("loop_adjustments") or {})
    sleeve_before = None
    if FUND_POLICY_PATH.is_file():
        pol = load_json(FUND_POLICY_PATH)
        sleeve_before = (pol.get("targets") or {}) if isinstance(pol, dict) else {}

    if write:
        pin_overrides = [
            r
            for r in classified["apply"]
            if _tag_kind(r, pins_from(share)) == "pin"
        ]
        residual_apply = [
            r
            for r in classified["apply"]
            if _tag_kind(r, pins_from(share)) != "pin"
            and _tag_kind(r, pins_from(share)) != "sleeve"
        ]
        if residual_apply:
            new_adj = _write_share_adjustments(
                share, residual_apply, path=share_path
            )
        if pin_overrides:
            pins = pins_from(share)
            for row in pin_overrides:
                sym = _sym(row.get("symbol"))
                if sym not in pins:
                    continue
                pins[sym] = round(pins[sym] + _f(row.get("delta")), 2)
            share["pins"] = pins
            save_json(share_path, share)
        applied = classified["apply"]
        if classified["stage"]:
            existing = load_json(staged_path)
            _write_staged(
                existing, classified["stage"], path=staged_path, as_of=as_of
            )
            staged_rows = classified["stage"]
    else:
        applied = classified["apply"]
        staged_rows = classified["stage"]

    if not proposals:
        summary = "No change — quiet sweep (no material developments)."
        brief = "Bias loop: quiet, no change."
    elif override:
        summary = (
            f"Chairman override {override.get('symbol')} "
            f"{_f(override.get('delta')):+g} applied immediately."
        )
        brief = summary
    elif applied and staged_rows:
        summary = (
            f"Applied {len(applied)} residual move(s); "
            f"staged {len(staged_rows)} for Chairman."
        )
        brief = summary
    elif applied:
        bits = ", ".join(
            f"{r['symbol']} {float(r['delta']):+g}" for r in applied
        )
        summary = f"Applied residual tilt: {bits}."
        brief = summary
    elif staged_rows:
        summary = f"Staged {len(staged_rows)} move(s) for Chairman; nothing applied."
        brief = summary
    else:
        summary = "No change — thin signal skipped."
        brief = "Bias loop: no change."

    entry = {
        "as_of": as_of,
        "kind": "override" if override else "bias_weight_loop",
        "summary": summary,
        "brief": brief,
        "applied": applied,
        "staged": staged_rows,
        "skipped": classified.get("skip") or [],
        "loop_adjustments": new_adj,
        "committee": debate,
        "override": (
            {
                "symbol": _sym(override.get("symbol")),
                "delta": _f(override.get("delta")),
                "why": override.get("why") or override.get("instruction"),
            }
            if override
            else None
        ),
        "rationale": {
            "why_now": (
                override.get("why")
                if override
                else "Daily developments sweep → committee → apply-or-stage."
            ),
            "rejected_alternatives": (
                "Did not invent pins; did not move 60/40 sleeve targets; "
                "did not treat consider-share as an order."
            ),
        },
        "sleeve_targets_untouched": True,
        "sleeve_targets_before": sleeve_before,
    }
    if write:
        _append_journal(entry, journal_path)
        _append_jsonl(entry, jsonl_path)
    return entry


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Bias-weighting loop (#768)")
    p.add_argument("--digest", type=Path, help="Path to investment/digests/YYYY-MM-DD.md")
    p.add_argument("--proposals-json", type=Path, help="JSON list of {symbol,delta,...}")
    p.add_argument("--override", help="Chairman override symbol")
    p.add_argument("--delta", type=float, default=1.0, help="Override delta (points)")
    p.add_argument("--why", default="", help="Override rationale")
    p.add_argument("--apply", action="store_true", help="Write SoT + journal (default dry-run)")
    p.add_argument("--share", type=Path, default=CONSIDER_SHARE_PATH)
    args = p.parse_args(argv)

    digest_text = None
    source = ""
    proposals = None
    if args.digest:
        digest_text = args.digest.read_text(encoding="utf-8")
        source = str(args.digest)
    if args.proposals_json:
        raw = json.loads(args.proposals_json.read_text(encoding="utf-8"))
        proposals = list(raw) if isinstance(raw, list) else []

    override = None
    if args.override:
        override = {
            "symbol": args.override,
            "delta": args.delta,
            "why": args.why or "Chairman override",
            "kind": "residual",
        }

    entry = run_assessment(
        proposals=proposals,
        digest_text=digest_text,
        digest_source=source,
        share_path=args.share,
        write=bool(args.apply),
        override=override,
    )
    print(entry.get("brief") or entry.get("summary"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
