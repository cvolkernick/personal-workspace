"""Parse simple coach commands from Ask Grok chat (local actions)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


def try_parse_coach_action(question: str) -> Optional[Dict[str, Any]]:
    """
    Return an action dict if the user message is a structured command.

    Supported:
      - set stock <id|name> on|off
      - mark <id|name> out of stock / in stock
      - set targets cal=2100 protein=200 carbs=180 fat=55
      - refresh meal plan
      - refresh workout plan [push|pull|legs]
    """
    q = (question or "").strip()
    if not q:
        return None
    low = q.lower().strip()

    m = re.match(
        r"^(?:set\s+stock|stock)\s+(.+?)\s+(on|off|true|false|in|out)\s*$",
        low,
    )
    if m:
        ident = m.group(1).strip()
        flag = m.group(2)
        in_stock = flag in ("on", "true", "in")
        return {"action": "set_stock", "id_or_name": ident, "in_stock": in_stock}

    m = re.match(r"^mark\s+(.+?)\s+(out of stock|in stock|out|in)\s*$", low)
    if m:
        ident = m.group(1).strip()
        in_stock = m.group(2) in ("in stock", "in")
        return {"action": "set_stock", "id_or_name": ident, "in_stock": in_stock}

    if low in ("refresh meal plan", "generate meal plan", "update meal plan"):
        return {"action": "refresh_meal_plan"}

    m = re.match(r"^refresh workout plan(?:\s+(push|pull|legs))?\s*$", low)
    if m:
        return {"action": "refresh_workout_plan", "session_type": m.group(1)}

    if low.startswith("set targets"):
        rest = q[len("set targets") :].strip()
        # cal=2100 protein=200 carbs=180 fat=55
        vals = {}
        for part in re.split(r"[\s,;]+", rest):
            if "=" not in part:
                continue
            k, _, v = part.partition("=")
            k = k.strip().lower()
            try:
                num = float(v.strip())
            except ValueError:
                continue
            if k in ("cal", "cals", "calories", "kcal"):
                vals["calories"] = num
            elif k in ("p", "protein", "protein_g"):
                vals["protein_g"] = num
            elif k in ("c", "carbs", "carb", "carbs_g"):
                vals["carbs_g"] = num
            elif k in ("f", "fat", "fat_g"):
                vals["fat_g"] = num
        if vals:
            return {"action": "set_targets", "targets": vals}

    return None


def format_action_reply(result: Dict[str, Any]) -> str:
    if not result.get("ok"):
        return f"**Action failed:** {result.get('error') or 'unknown error'}"
    action = result.get("action")
    if action == "set_stock":
        state = "in stock" if result.get("in_stock") else "out of stock"
        return f"**Done.** Marked **{result.get('name') or result.get('id')}** as {state}."
    if action == "set_targets":
        t = result.get("targets") or {}
        return (
            "**Targets updated:** "
            f"{t.get('calories')} kcal · P{t.get('protein_g')} · C{t.get('carbs_g')} · F{t.get('fat_g')}."
        )
    if action == "refresh_meal_plan":
        msg = (result.get("plan") or {}).get("message") or "Meal plan refreshed."
        return f"**Meal plan refreshed.** {msg}"
    if action == "refresh_workout_plan":
        plan = result.get("plan") or {}
        return f"**Workout plan refreshed.** {plan.get('message') or plan.get('session_type') or 'OK'}"
    return f"**Done.** ({action})"
