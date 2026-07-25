"""Parse coach commands from Ask Grok chat (local actions).

Understands flexible, human-readable phrasing so users do not need exact
key=value syntax. Pure questions still go to the model; only clear write
intent is treated as an action.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence


# --- intent helpers ---------------------------------------------------------

_QUESTION_PREFIX = re.compile(
    r"^(?:what|why|how|when|where|who|which|should|could|would|is|are|do|does|"
    r"can i|can you tell|tell me|explain|analyze|review|compare|thoughts on|"
    r"what do you think|what about|opinions? on)\b",
    re.I,
)

# Strong apply / write verbs (must appear for free-form target changes)
_WRITE_INTENT = re.compile(
    r"\b(?:"
    r"set|update|change|adjust|modify|apply|save|write|use|put|"
    r"make(?:\s+my)?|bump|raise|lower|increase|decrease|reduce|"
    r"switch(?:\s+to)?|move(?:\s+to)?|go\s+with|lock\s+in|"
    r"please\s+set|please\s+update|please\s+change|please\s+apply|"
    r"can you set|can you update|can you change|can you apply|"
    r"go ahead|do it|yes apply|yes do|apply that|apply those|"
    r"make those changes|make the changes|use those|use these|"
    r"update my targets|set my targets|change my targets|"
    r"set targets|update targets|change targets"
    r")\b",
    re.I,
)

_APPLY_FROM_CONTEXT = re.compile(
    r"^\s*(?:please\s+)?(?:"
    r"apply(?:\s+(?:that|those|it|them))?(?:\s+(?:the|your))?(?:\s+"
    r"(?:recommendations?|changes?|targets?|numbers?|macros?|suggestions?))?|"
    r"do\s+it|"
    r"yes(?:\s+please)?|"
    r"go\s+ahead(?:\s+and\s+(?:apply|do\s+it|update))?|"
    r"make\s+(?:those|the)\s+changes|"
    r"use\s+(?:those|these)(?:\s+(?:numbers?|targets?|macros?|recommendations?))?|"
    r"(?:update|set)\s+(?:my\s+)?targets\s+accordingly|"
    r"sounds good(?:[,.]?\s*(?:apply|do\s+it|update))?"
    r")\s*[.!?]?\s*$",
    re.I,
)


def _is_likely_question_only(low: str) -> bool:
    """True when the user is asking for advice, not commanding a write."""
    if _APPLY_FROM_CONTEXT.match(low):
        return False
    if _WRITE_INTENT.search(low) and _extract_target_vals(low):
        # "should I set protein to 220?" is still a question
        if low.rstrip().endswith("?") and _QUESTION_PREFIX.match(low):
            return True
        if re.match(
            r"^(?:should|would|could|what if|is it (?:ok|good|better)|"
            r"do you (?:think|recommend)|thoughts)\b",
            low,
        ):
            return True
        return False
    if low.rstrip().endswith("?"):
        return True
    if _QUESTION_PREFIX.match(low):
        return True
    return False


# --- target value extraction ------------------------------------------------

def _num(s: str) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _extract_target_vals(text: str) -> Dict[str, float]:
    """Pull calorie/macro targets from free-form text.

    Accepts many shapes, e.g.:
      cal=2100 protein=200
      2100 kcal, 200g protein, 180 carbs, 55 fat
      protein to 220 / protein: 220 / set protein 220
      P220 C150 F55 / 220p 150c 55f
    """
    low = (text or "").lower()
    vals: Dict[str, float] = {}

    # key=value or key: value
    for m in re.finditer(
        r"\b(cal(?:ories)?|cals|kcal|protein(?:_g)?|carbs?(?:_g)?|"
        r"carb(?:ohydrates?)?|fat(?:_g)?|p|c|f)\s*[=:]\s*(\d+(?:\.\d+)?)",
        low,
    ):
        _assign_target_key(vals, m.group(1), m.group(2))

    # "protein to/at/of 220" / "set protein 220" / "protein 220g"
    for m in re.finditer(
        r"\b(?:set\s+|update\s+|change\s+|adjust\s+|make\s+)?"
        r"(calories?|cals|kcal|protein|carbs?|carbohydrates?|fat)"
        r"(?:\s+target)?(?:\s+(?:to|at|of|=|:))?\s*"
        r"(\d+(?:\.\d+)?)\s*(?:g|grams?|kcal|cal(?:ories)?)?\b",
        low,
    ):
        _assign_target_key(vals, m.group(1), m.group(2))

    # "220g protein" / "2100 calories" / "55g fat"
    # Negative lookbehind: skip values already bound via key=val (e.g. cal=2100 protein=…)
    for m in re.finditer(
        r"(?<![=:])\b(\d+(?:\.\d+)?)\s*(?:g|grams?)?\s*"
        r"(calories?|cals|kcal|protein|carbs?|carbohydrates?|fat)\b(?!\s*[=:])",
        low,
    ):
        _assign_target_key(vals, m.group(2), m.group(1))

    # Compact: P220 C150 F55 or 220p 150c 55f
    for m in re.finditer(r"\bp\s*(\d+(?:\.\d+)?)\b", low):
        vals.setdefault("protein_g", float(m.group(1)))
    for m in re.finditer(r"\bc\s*(\d+(?:\.\d+)?)\b", low):
        # Avoid matching "cal" partially — require word boundary after single c
        # already have \b after number via pattern end; "c150" ok, "cal" no match
        vals.setdefault("carbs_g", float(m.group(1)))
    for m in re.finditer(r"\bf\s*(\d+(?:\.\d+)?)\b", low):
        vals.setdefault("fat_g", float(m.group(1)))
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*p\b", low):
        vals.setdefault("protein_g", float(m.group(1)))
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*c\b", low):
        vals.setdefault("carbs_g", float(m.group(1)))
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*f\b", low):
        vals.setdefault("fat_g", float(m.group(1)))

    # "2100 cal" without word calories (kcal handled above)
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(?:kcal|cals?)\b", low):
        vals.setdefault("calories", float(m.group(1)))

    return vals


def _assign_target_key(vals: Dict[str, float], key: str, raw_num: str) -> None:
    n = _num(raw_num)
    if n is None:
        return
    k = key.lower().strip()
    if k in ("cal", "cals", "calorie", "calories", "kcal"):
        vals["calories"] = n
    elif k in ("p", "protein", "protein_g"):
        vals["protein_g"] = n
    elif k in ("c", "carb", "carbs", "carbohydrate", "carbohydrates", "carbs_g"):
        vals["carbs_g"] = n
    elif k in ("f", "fat", "fat_g"):
        vals["fat_g"] = n


def _targets_from_history(history: Optional[Sequence[dict]]) -> Dict[str, float]:
    """Best-effort: pull recommended macros from the latest assistant message."""
    if not history:
        return {}
    # Walk newest first
    for turn in reversed(list(history)):
        if not isinstance(turn, dict):
            continue
        if str(turn.get("role") or "") not in ("assistant", "grok", "model"):
            continue
        content = str(turn.get("content") or "")
        if not content.strip():
            continue
        vals = _extract_target_vals(content)
        # Prefer messages that look like recommendations (at least 2 macros or cal+macro)
        if len(vals) >= 2:
            return vals
        if vals:
            # Keep scanning for a richer message
            richer = vals
            for turn2 in reversed(list(history)):
                if str(turn2.get("role") or "") not in ("assistant", "grok", "model"):
                    continue
                v2 = _extract_target_vals(str(turn2.get("content") or ""))
                if len(v2) > len(richer):
                    richer = v2
            return richer
    return {}


# --- public API -------------------------------------------------------------

def try_parse_coach_action(
    question: str,
    history: Optional[Sequence[dict]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Return an action dict if the user message is a write command.

    Flexible examples:
      - set targets cal=2100 protein=200 carbs=180 fat=55
      - set protein to 220 and carbs to 150
      - update my calories to 2000
      - change macros to 220p 150c 55f
      - apply those recommendations  (uses last Grok reply for numbers)
      - mark chicken out of stock / set eggs in stock
      - refresh meal plan / regenerate my meal plan
    """
    q = (question or "").strip()
    if not q:
        return None
    low = q.lower().strip()

    # --- stock --------------------------------------------------------------
    m = re.match(
        r"^(?:set\s+stock|stock)\s+(.+?)\s+(on|off|true|false|in|out)\s*$",
        low,
    )
    if m:
        ident = m.group(1).strip()
        flag = m.group(2)
        in_stock = flag in ("on", "true", "in")
        return {"action": "set_stock", "id_or_name": ident, "in_stock": in_stock}

    m = re.match(
        r"^(?:mark|set|make)\s+(.+?)\s+(?:as\s+)?(out of stock|in stock|out|in)\s*$",
        low,
    )
    if m:
        ident = m.group(1).strip()
        in_stock = m.group(2) in ("in stock", "in")
        return {"action": "set_stock", "id_or_name": ident, "in_stock": in_stock}

    m = re.match(
        r"^(.+?)\s+(?:is|as)\s+(out of stock|in stock)\s*$",
        low,
    )
    if m and _WRITE_INTENT.search(low) is None:
        # "chicken is out of stock" — treat as stock command only if short
        ident = m.group(1).strip()
        if len(ident) <= 40 and not _QUESTION_PREFIX.match(low):
            in_stock = m.group(2) == "in stock"
            return {"action": "set_stock", "id_or_name": ident, "in_stock": in_stock}

    # --- meal / workout plan refresh ----------------------------------------
    if re.match(
        r"^(?:please\s+)?(?:refresh|regenerate|rebuild|update|generate|make)\s+"
        r"(?:(?:a|my|the)\s+)?meal\s+plan\s*$",
        low,
    ) or low in ("refresh meal plan", "generate meal plan", "update meal plan"):
        return {"action": "refresh_meal_plan"}

    m = re.match(
        r"^(?:please\s+)?(?:refresh|regenerate|rebuild|update|generate)\s+"
        r"(?:(?:a|my|the)\s+)?workout\s+plan(?:\s+(push|pull|legs))?\s*$",
        low,
    )
    if m:
        return {"action": "refresh_workout_plan", "session_type": m.group(1)}

    # --- targets: apply last recommendation ---------------------------------
    if _APPLY_FROM_CONTEXT.match(low):
        vals = _targets_from_history(history)
        if vals:
            return {
                "action": "set_targets",
                "targets": vals,
                "from_context": True,
            }
        # No numbers found — let the model explain what it would change
        return None

    # --- targets: free-form with numbers ------------------------------------
    # Skip pure advisory questions.
    if not _is_likely_question_only(low):
        vals = _extract_target_vals(q)
        if vals and (
            _WRITE_INTENT.search(low)
            or low.startswith("set targets")
            or low.startswith("update targets")
            or low.startswith("change targets")
            # Short imperative like "protein 220 carbs 150 fat 55"
            or (
                len(low) < 120
                and len(vals) >= 2
                and re.match(
                    r"^(?:targets?|macros?|please|set|update|change|make)\b",
                    low,
                )
            )
        ):
            return {"action": "set_targets", "targets": vals}

        # Explicit "set targets …" even if only one field
        if (
            re.match(
                r"^(?:please\s+)?(?:set|update|change|adjust|save)\s+"
                r"(?:my\s+)?(?:daily\s+)?(?:targets?|macros?|calories?|protein|carbs?|fat)\b",
                low,
            )
            and vals
        ):
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
        bits = []
        if t.get("calories") is not None:
            bits.append(f"{t.get('calories')} kcal")
        if t.get("protein_g") is not None:
            bits.append(f"P{t.get('protein_g')}")
        if t.get("carbs_g") is not None:
            bits.append(f"C{t.get('carbs_g')}")
        if t.get("fat_g") is not None:
            bits.append(f"F{t.get('fat_g')}")
        detail = " · ".join(bits) if bits else str(t)
        return f"**Targets updated:** {detail}."
    if action == "refresh_meal_plan":
        msg = (result.get("plan") or {}).get("message") or "Meal plan refreshed."
        return f"**Meal plan refreshed.** {msg}"
    if action == "refresh_workout_plan":
        plan = result.get("plan") or {}
        return (
            f"**Workout plan refreshed.** "
            f"{plan.get('message') or plan.get('session_type') or 'OK'}"
        )
    return f"**Done.** ({action})"
