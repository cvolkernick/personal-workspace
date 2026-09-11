#!/usr/bin/env python3
"""Deterministic GetHelpFrom.ai v1 scorer. No LLM. Canonical files only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def load_json(name: str) -> dict[str, Any]:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def why_lines(scenario_by_id: dict[str, Any], taps: dict[str, str], solution_id: str) -> list[str]:
    lines: list[str] = []
    for sid, resp in taps.items():
        if resp not in ("thats_me", "sort_of"):
            continue
        sc = scenario_by_id.get(sid)
        if not sc or sc.get("solution") != solution_id:
            continue
        verb = "that's you" if resp == "thats_me" else "sort of you"
        lines.append(f"We suggested this because {verb}: “{sc['text']}”")
        if len(lines) >= 2:
            break
    if not lines:
        lines.append("We suggested this from your other taps in this area.")
    return lines[:2]


def score_session(
    taps: dict[str, str],
    *,
    hours: str = "3-5",
    urgency: str = "this_quarter",
    trust: str = "3",
    tools: list[str] | None = None,
    scoring: dict[str, Any] | None = None,
    scenarios: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    scoring = scoring or load_json("scoring.json")
    scenarios = scenarios or load_json("scenarios.json")["scenarios"]
    scenario_by_id = {s["id"]: s for s in scenarios}
    tap_scores: dict[str, int] = scoring["tap_scores"]
    tools = tools or []

    raw: dict[str, float] = {}
    answered = 0
    shown = 0
    for sc in scenarios:
        sid = sc["id"]
        if sid not in taps:
            continue
        shown += 1
        resp = taps[sid]
        if resp != "skipped":
            answered += 1
        sol = sc["solution"]
        raw[sol] = raw.get(sol, 0.0) + float(tap_scores.get(resp, 0))

    hours_m = float(scoring["hours_multiplier"].get(hours, 1.0))
    urg_m = float(scoring["urgency_multiplier"].get(urgency, 1.0))
    trust_m = float(scoring["trust_feasibility"].get(str(trust), 0.75))
    conf = (answered / shown) if shown else 0.0

    ranked: list[dict[str, Any]] = []
    for sol, tap_sum in raw.items():
        if tap_sum < scoring["shortlist"]["min_value_taps"]:
            continue
        meta = scoring["solutions"].get(sol, {})
        tool_hit = 1.0
        wanted = meta.get("tools") or []
        if wanted and tools:
            tool_hit = 1.1 if any(t in tools for t in wanted) else 0.85
        value = max(0.0, tap_sum) * hours_m * urg_m
        feasibility = trust_m * tool_hit
        w = scoring["weights"]
        total = w["value"] * value + w["feasibility"] * feasibility * 10 + w["confidence"] * conf * 10
        ranked.append(
            {
                "solution_id": sol,
                "label": meta.get("label", sol),
                "value": round(value, 3),
                "feasibility": round(feasibility, 3),
                "confidence": round(conf, 3),
                "score": round(total, 3),
                "why": why_lines(scenario_by_id, taps, sol),
            }
        )
    ranked.sort(key=lambda r: r["score"], reverse=True)
    n = int(scoring["shortlist"]["default_n"])
    return {
        "scoring_version": scoring["version"],
        "shortlist": ranked[:n],
        "expanded": ranked[: int(scoring["shortlist"]["expand_n"])],
        "all": ranked,
    }


if __name__ == "__main__":
    raise SystemExit("import score_session from tests or a REPL")
