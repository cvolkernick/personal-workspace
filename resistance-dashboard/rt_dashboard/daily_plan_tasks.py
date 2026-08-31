"""Daily plan quests synced to Google Tasks (Fitness list).

Sync identity:
  * Durable marker in notes: ``[fitdash-quest:YYYY-MM-DD]`` (titles stay human-only).
  * Kind key ``[fitdash-kind:group|slug]`` so title metrics (battery %, grams)
    upsert in place instead of appending a new incomplete leaf.
  * Meal-plan leaves also carry ``[fitdash-meal:YYYY-MM-DD]`` plus
    ``[fitdash-foods:<fp>]`` so same-day food-log regen can purge only those.
  * Local cache when the filesystem persists (Pi). Vercel is ephemeral — do not
    key rollover on cache alone.
  * Known group headers (Training / Cardio / Nutrition / Shopping /
    Sleep & recovery) and their children, so unmarked user-OAuth leftovers
    can still be swept.

  ~/.config/resistance-dashboard/daily_quest_cache.json
  { "day": { "list_id": "...", "ids": { "training|group": "taskId", "training|ex-foo": "..." } } }

Due date = civil day. Notes = optional motivation + the FitDash marker.
Chris jots, Turo, and Orchestra NOW/NEXT that are not FitDash quests are
never deleted. Meal-plan purge never deletes those either.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import gtasks_bridge as gtb
from .cardio_quest import (
    GROUP as CARDIO_GROUP,
    KIND_KEY as CARDIO_AZM_CACHE_KEY,
    SLUG as CARDIO_AZM_SLUG,
    cardio_spec,
)
from .nutrition_planner import food_logs_fingerprint, format_plan_portion
from .timeutil import local_today_iso

DEFAULT_LIST_TITLE = "Fitness"
QUEST_MARK_RE = re.compile(r"\[fitdash-quest:(\d{4}-\d{2}-\d{2})\]")
MEAL_MARK_RE = re.compile(r"\[fitdash-meal:(\d{4}-\d{2}-\d{2})\]")
FOODS_FP_RE = re.compile(r"\[fitdash-foods:([0-9a-f]{8,64})\]")
# Planner meal-leaf titles only (issue #321). Not "Cover remaining protein".
MEAL_TITLE_RE = re.compile(
    r"^(Next meal|Later meal|Evening|Optional snack|Earlier meal|Eat)"
    r"(?:\s·\s[^:]+)?:\s",
    re.I,
)
# Stable cache key / slug so gram-in-title updates upsert (issue #345).
PROTEIN_REMAINING_SLUG = "protein-remaining"
PROTEIN_REMAINING_CACHE_KEY = f"nutrition|{PROTEIN_REMAINING_SLUG}"
PROTEIN_REMAINING_TITLE_RE = re.compile(
    r"^cover(?: the)? remaining protein\b",
    re.I,
)
PROTEIN_REMAINING_SLUG_RE = re.compile(
    r"^(protein-remaining|protein-gap)$",
    re.I,
)
PROTEIN_REMAINING_LEGACY_SLUG_RE = re.compile(
    r"^action-nutrition-\d+$",
    re.I,
)
# Stable action slugs so title metrics (battery %, grams, clock) cannot
# mint a new incomplete leaf. Identity is kind + civil day (#357).
# Protect bedtime and Sleep battery low are two title families (#363).
PROTECT_BEDTIME_SLUG = "protect-bedtime"
PROTECT_BEDTIME_CACHE_KEY = f"sleep|{PROTECT_BEDTIME_SLUG}"
SLEEP_BATTERY_LOW_SLUG = "sleep-battery-low"
SLEEP_BATTERY_LOW_CACHE_KEY = f"sleep|{SLEEP_BATTERY_LOW_SLUG}"
TRAIN_SESSION_SLUG = "train-session"
TRAIN_SESSION_CACHE_KEY = f"training|{TRAIN_SESSION_SLUG}"
CALORIE_PACE_SLUG = "calorie-pace"
CALORIE_PACE_CACHE_KEY = f"nutrition|{CALORIE_PACE_SLUG}"
SHOP_TOP_SLUG = "shop-top"
SHOP_TOP_CACHE_KEY = f"shopping|{SHOP_TOP_SLUG}"
CARDIO_AZM_TITLE_RE = re.compile(
    r"^(Walk · Zone 2|Cardio)\s+[—-]\s+\d+\s*/\s*\d+\s*AZM\b",
    re.I,
)
KIND_MARK_RE = re.compile(r"\[fitdash-kind:([a-z0-9.|-]+)\]")
PROTECT_BEDTIME_TITLE_RE = re.compile(r"^Protect bedtime\b", re.I)
SLEEP_BATTERY_LOW_TITLE_RE = re.compile(r"^Sleep battery low\b", re.I)
SLEEP_QUEST_TITLE_RE = re.compile(
    r"^(Protect bedtime|Sleep battery low)\b",
    re.I,
)
PROTECT_BEDTIME_SLUG_RE = re.compile(
    r"^(protect-bedtime|sleep-bed|bedtime)$",
    re.I,
)
PROTECT_BEDTIME_LEGACY_SLUG_RE = re.compile(
    r"^action-sleep-\d+$",
    re.I,
)
SLEEP_BATTERY_LOW_SLUG_RE = re.compile(
    r"^(sleep-battery-low|battery-low)$",
    re.I,
)
TRAIN_SESSION_TITLE_RE = re.compile(
    r"^(Complete today's|Easy |Rest / recover today|Already trained today)\b",
    re.I,
)
CALORIE_PACE_TITLE_RE = re.compile(
    r"^Calorie pace is\b",
    re.I,
)
SHOP_LEAF_TITLE_RE = re.compile(
    r"^(Restock|Get|Add):",
    re.I,
)
LIFT_TITLE_RE = re.compile(
    r"^(?P<name>.+?)\s+\((?P<detail>[^)]*)\)\s*$"
)
GROUP_META = {
    "training": {"title": "Training", "order": 1, "emoji": "🏋️"},
    "cardio": {"title": "Cardio", "order": 2, "emoji": "🏃"},
    "nutrition": {"title": "Nutrition", "order": 3, "emoji": "🍽"},
    "shopping": {"title": "Shopping", "order": 4, "emoji": "🛒"},
    "sleep": {"title": "Sleep & recovery", "order": 5, "emoji": "😴"},
    "recovery": {"title": "Sleep & recovery", "order": 5, "emoji": "😴"},
    "other": {"title": "Other", "order": 9, "emoji": "✓"},
}


def _slug(text: str, limit: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "item")[:limit]


def _cache_path() -> Path:
    override = os.environ.get("RESISTANCE_DASHBOARD_CONFIG_DIR")
    base = Path(override).expanduser() if override else Path.home() / ".config" / "resistance-dashboard"
    return base / "daily_quest_cache.json"


def _load_cache() -> dict:
    path = _cache_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(data: dict) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        pass


def cache_key(group: str, slug: str) -> str:
    return f"{group}|{slug}"


def kind_marker(kind_key: str) -> str:
    key = str(kind_key or "").strip()
    return f"[fitdash-kind:{key}]" if key else ""


def kind_from_notes(notes: str) -> Optional[str]:
    match = KIND_MARK_RE.search(notes or "")
    return match.group(1) if match else None


def item_kind_key(item: PlannedItem) -> str:
    if is_protein_remaining_item(item):
        return PROTEIN_REMAINING_CACHE_KEY
    if is_protect_bedtime_item(item):
        return PROTECT_BEDTIME_CACHE_KEY
    if is_sleep_battery_low_item(item):
        return SLEEP_BATTERY_LOW_CACHE_KEY
    if is_cardio_azm_item(item):
        return CARDIO_AZM_CACHE_KEY
    return cache_key(item.group, item.slug)


def group_header_titles() -> set:
    return {
        str(m.get("title") or "").strip()
        for m in GROUP_META.values()
        if str(m.get("title") or "").strip()
    }


def quest_marker(day: str) -> str:
    return f"[fitdash-quest:{str(day or '')[:10]}]"


def quest_mark_day(notes: str) -> Optional[str]:
    match = QUEST_MARK_RE.search(notes or "")
    if not match:
        return None
    day = match.group(1)
    return day if _is_day_key(day) else None


def quest_notes(motivation: str, day: str, kind_key: str = "") -> str:
    """Human motivation (optional) plus the durable FitDash markers."""
    mark = quest_marker(day)
    extra = QUEST_MARK_RE.sub("", motivation or "")
    extra = MEAL_MARK_RE.sub("", extra)
    extra = FOODS_FP_RE.sub("", extra)
    extra = KIND_MARK_RE.sub("", extra).strip()
    bits = [mark]
    key = str(kind_key or "").strip()
    if key:
        bits.append(kind_marker(key))
    if extra:
        return extra + "\n\n" + "\n".join(bits)
    return "\n".join(bits)


def meal_marker(day: str) -> str:
    return f"[fitdash-meal:{str(day or '')[:10]}]"


def foods_fp_marker(fingerprint: str) -> str:
    fp = str(fingerprint or "").strip()
    return f"[fitdash-foods:{fp}]" if fp else ""


def meal_mark_day(notes: str) -> Optional[str]:
    match = MEAL_MARK_RE.search(notes or "")
    if not match:
        return None
    day = match.group(1)
    return day if _is_day_key(day) else None


def foods_fp_from_notes(notes: str) -> Optional[str]:
    match = FOODS_FP_RE.search(notes or "")
    return match.group(1) if match else None


def meal_quest_notes(
    motivation: str, day: str, foods_fp: str = "", kind_key: str = ""
) -> str:
    """Quest marker + meal-plan ownership + food-log fingerprint."""
    base = quest_notes(motivation, day, kind_key=kind_key)
    marks = [meal_marker(day)]
    fp_mark = foods_fp_marker(foods_fp)
    if fp_mark:
        marks.append(fp_mark)
    return f"{base}\n" + "\n".join(marks)


def is_meal_plan_cache_key(cache_key_s: str) -> bool:
    ck = str(cache_key_s or "")
    return ck.startswith("nutrition|meal-") or ck.startswith("nutrition|food-")


def is_meal_plan_item(item: PlannedItem) -> bool:
    if (item.meal_label or "").strip():
        return True
    slug = str(item.slug or "")
    return slug.startswith("meal-") or slug.startswith("food-")


def looks_like_meal_plan_title(title: str) -> bool:
    return bool(MEAL_TITLE_RE.match((title or "").strip()))


def looks_like_protein_remaining_title(title: str) -> bool:
    return bool(PROTEIN_REMAINING_TITLE_RE.match((title or "").strip()))


def looks_like_cardio_azm_title(title: str) -> bool:
    return bool(CARDIO_AZM_TITLE_RE.match((title or "").strip()))


def is_cardio_azm_cache_key(cache_key_s: str) -> bool:
    ck = str(cache_key_s or "")
    if ck == CARDIO_AZM_CACHE_KEY:
        return True
    if not ck.startswith("cardio|"):
        return False
    slug = ck.split("|", 1)[-1]
    return slug == CARDIO_AZM_SLUG or slug in ("cardio", "azm")


def is_cardio_azm_item(item: PlannedItem) -> bool:
    slug = str(item.slug or "")
    if slug == CARDIO_AZM_SLUG or slug in ("cardio", "azm"):
        return True
    if str(item.group or "") == CARDIO_GROUP:
        return True
    return looks_like_cardio_azm_title(item.title)


def cardio_azm_action_slug(act: dict) -> Optional[str]:
    """Stable slug so AZM progress in the title cannot fork a leaf."""
    aid = str((act or {}).get("id") or "").strip().lower()
    if aid in (CARDIO_AZM_SLUG, "cardio", "azm", CARDIO_AZM_CACHE_KEY):
        return CARDIO_AZM_SLUG
    kind = str((act or {}).get("kind") or "").strip().lower()
    if kind == CARDIO_GROUP:
        return CARDIO_AZM_SLUG
    if looks_like_cardio_azm_title(str((act or {}).get("text") or "")):
        return CARDIO_AZM_SLUG
    return None


def is_protein_remaining_cache_key(cache_key_s: str) -> bool:
    ck = str(cache_key_s or "")
    if ck == PROTEIN_REMAINING_CACHE_KEY:
        return True
    if not ck.startswith("nutrition|"):
        return False
    slug = ck.split("|", 1)[-1]
    return bool(
        PROTEIN_REMAINING_SLUG_RE.match(slug)
        or PROTEIN_REMAINING_LEGACY_SLUG_RE.match(slug)
    )


def is_protein_remaining_item(item: PlannedItem) -> bool:
    slug = str(item.slug or "")
    if slug == PROTEIN_REMAINING_SLUG or PROTEIN_REMAINING_SLUG_RE.match(slug):
        return True
    return looks_like_protein_remaining_title(item.title)


def protein_remaining_action_slug(act: dict) -> Optional[str]:
    """Stable slug for protein-remaining actions even when grams/title shift."""
    aid = str((act or {}).get("id") or "").strip().lower()
    if aid == PROTEIN_REMAINING_SLUG or PROTEIN_REMAINING_SLUG_RE.match(aid):
        return PROTEIN_REMAINING_SLUG
    if looks_like_protein_remaining_title(str((act or {}).get("text") or "")):
        return PROTEIN_REMAINING_SLUG
    return None


def looks_like_protect_bedtime_title(title: str) -> bool:
    return bool(PROTECT_BEDTIME_TITLE_RE.match((title or "").strip()))


def looks_like_sleep_battery_low_title(title: str) -> bool:
    return bool(SLEEP_BATTERY_LOW_TITLE_RE.match((title or "").strip()))


def looks_like_sleep_quest_title(title: str) -> bool:
    """Either sleep title family. Families stay separate for upsert (#363)."""
    return looks_like_protect_bedtime_title(title) or looks_like_sleep_battery_low_title(
        title
    )


def is_protect_bedtime_item(item: PlannedItem) -> bool:
    if looks_like_sleep_battery_low_title(item.title):
        return False
    slug = str(item.slug or "")
    if (
        slug == PROTECT_BEDTIME_SLUG
        or PROTECT_BEDTIME_SLUG_RE.match(slug)
        or PROTECT_BEDTIME_LEGACY_SLUG_RE.match(slug)
    ):
        return True
    return looks_like_protect_bedtime_title(item.title)


def is_sleep_battery_low_item(item: PlannedItem) -> bool:
    if looks_like_protect_bedtime_title(item.title):
        return False
    slug = str(item.slug or "")
    if slug == SLEEP_BATTERY_LOW_SLUG or SLEEP_BATTERY_LOW_SLUG_RE.match(slug):
        return True
    return looks_like_sleep_battery_low_title(item.title)


def is_protect_bedtime_cache_key(cache_key_s: str) -> bool:
    ck = str(cache_key_s or "")
    if ck == PROTECT_BEDTIME_CACHE_KEY:
        return True
    if not (ck.startswith("sleep|") or ck.startswith("recovery|")):
        return False
    slug = ck.split("|", 1)[-1]
    if slug == SLEEP_BATTERY_LOW_SLUG or SLEEP_BATTERY_LOW_SLUG_RE.match(slug):
        return False
    return bool(
        slug == PROTECT_BEDTIME_SLUG
        or PROTECT_BEDTIME_SLUG_RE.match(slug)
        or PROTECT_BEDTIME_LEGACY_SLUG_RE.match(slug)
    )


def is_sleep_battery_low_cache_key(cache_key_s: str) -> bool:
    ck = str(cache_key_s or "")
    if ck == SLEEP_BATTERY_LOW_CACHE_KEY:
        return True
    if not (ck.startswith("sleep|") or ck.startswith("recovery|")):
        return False
    slug = ck.split("|", 1)[-1]
    return bool(slug == SLEEP_BATTERY_LOW_SLUG or SLEEP_BATTERY_LOW_SLUG_RE.match(slug))


def sleep_quest_action_slug(act: dict) -> Optional[str]:
    """Stable slug per sleep title family. Action id must not fork a family.

    ``Protect bedtime`` and ``Sleep battery low`` stay two families even when
    the planner stamps the same or a fresh action id (#363).
    """
    text = str((act or {}).get("text") or "")
    if looks_like_sleep_battery_low_title(text):
        return SLEEP_BATTERY_LOW_SLUG
    if looks_like_protect_bedtime_title(text):
        return PROTECT_BEDTIME_SLUG
    aid = str((act or {}).get("id") or "").strip().lower()
    if SLEEP_BATTERY_LOW_SLUG_RE.match(aid):
        return SLEEP_BATTERY_LOW_SLUG
    if (
        aid == PROTECT_BEDTIME_SLUG
        or PROTECT_BEDTIME_SLUG_RE.match(aid)
        or PROTECT_BEDTIME_LEGACY_SLUG_RE.match(aid)
    ):
        return PROTECT_BEDTIME_SLUG
    return None


def looks_like_train_session_title(title: str) -> bool:
    return bool(TRAIN_SESSION_TITLE_RE.match((title or "").strip()))


def looks_like_calorie_pace_title(title: str) -> bool:
    return bool(CALORIE_PACE_TITLE_RE.match((title or "").strip()))


def looks_like_shop_leaf_title(title: str) -> bool:
    return bool(SHOP_LEAF_TITLE_RE.match((title or "").strip()))


def meal_food_name_from_title(title: str) -> str:
    """Planner meal-leaf food name. 'Later meal · 3:30 PM: Chicken · 170g' → Chicken.

    Split on ``: `` (label/food), not the first colon — clocks use ``12:00``.
    """
    text = (title or "").strip()
    if ": " not in text:
        return ""
    rest = text.rsplit(": ", 1)[-1].strip()
    return rest.split(" · ", 1)[0].strip()


def lift_name_from_title(title: str) -> str:
    """Exercise name from a lift leaf. Session / meal / sleep titles return ''."""
    text = (title or "").strip()
    if not text:
        return ""
    if looks_like_train_session_title(text):
        return ""
    if looks_like_sleep_quest_title(text):
        return ""
    if looks_like_protein_remaining_title(text):
        return ""
    if looks_like_cardio_azm_title(text):
        return ""
    if looks_like_meal_plan_title(text):
        return ""
    match = LIFT_TITLE_RE.match(text)
    if match:
        return (match.group("name") or "").strip()
    return ""


def stable_action_slug(act: dict, index: int) -> str:
    """Kind slug that ignores battery %, grams, clock, and action index."""
    protein = protein_remaining_action_slug(act)
    if protein:
        return protein
    cardio = cardio_azm_action_slug(act)
    if cardio:
        return cardio
    sleep = sleep_quest_action_slug(act)
    if sleep:
        return sleep
    aid = str((act or {}).get("id") or "").strip()
    if aid:
        return _slug(aid)
    kind = str((act or {}).get("kind") or "other").lower()
    text = str((act or {}).get("text") or "")
    if kind in ("sleep", "recovery") or looks_like_sleep_quest_title(text):
        return PROTECT_BEDTIME_SLUG
    if kind == "training" and looks_like_train_session_title(text):
        return TRAIN_SESSION_SLUG
    if looks_like_calorie_pace_title(text):
        return CALORIE_PACE_SLUG
    if kind == "shopping":
        return SHOP_TOP_SLUG
    return f"action-{kind}-{index}"


def _task_on_civil_day(task: dict, day: str) -> bool:
    """True when notes marker or due date belongs to this civil day."""
    want = str(day or "")[:10]
    marked = quest_mark_day((task or {}).get("notes") or "")
    if marked:
        return (not want) or marked == want
    due = _task_due_day(task)
    if due and _is_day_key(due):
        return (not want) or due == want
    return False


def _has_fitdash_kind_or_quest(task: dict) -> bool:
    notes = (task or {}).get("notes") or ""
    return bool(quest_mark_day(notes) or kind_from_notes(notes))


def _sleep_family_owned_task(task: dict, *, day: str, title_match) -> bool:
    if not isinstance(task, dict):
        return False
    if not title_match(task.get("title") or ""):
        return False
    if looks_like_meal_plan_title(task.get("title") or ""):
        return False
    if not _has_fitdash_kind_or_quest(task):
        return False
    return _task_on_civil_day(task, day)


def is_protect_bedtime_owned_task(task: dict, *, day: str = "") -> bool:
    """FitDash Protect bedtime leaf for this civil day. Not Sleep battery low."""
    return _sleep_family_owned_task(
        task, day=day, title_match=looks_like_protect_bedtime_title
    )


def is_sleep_battery_low_owned_task(task: dict, *, day: str = "") -> bool:
    """FitDash Sleep battery low leaf for this civil day. Not Protect bedtime."""
    return _sleep_family_owned_task(
        task, day=day, title_match=looks_like_sleep_battery_low_title
    )


def is_sleep_owned_task(task: dict, *, day: str = "") -> bool:
    """Either sleep title family. Families stay separate for collapse (#363)."""
    return is_protect_bedtime_owned_task(task, day=day) or (
        is_sleep_battery_low_owned_task(task, day=day)
    )


def is_train_session_owned_task(task: dict, *, day: str = "") -> bool:
    if not isinstance(task, dict):
        return False
    if not looks_like_train_session_title(task.get("title") or ""):
        return False
    if not _has_fitdash_kind_or_quest(task):
        return False
    return _task_on_civil_day(task, day)


def is_calorie_pace_owned_task(task: dict, *, day: str = "") -> bool:
    if not isinstance(task, dict):
        return False
    if not looks_like_calorie_pace_title(task.get("title") or ""):
        return False
    if not _has_fitdash_kind_or_quest(task):
        return False
    return _task_on_civil_day(task, day)


def collect_kind_tasks(
    tasks: Sequence[dict],
    *,
    day: str,
    match,
    incomplete_only: bool = False,
) -> List[dict]:
    out: List[dict] = []
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        if not match(task):
            continue
        if incomplete_only and not _is_incomplete(task):
            continue
        out.append(task)
    return out


def collect_sleep_quest_tasks(
    tasks: Sequence[dict],
    *,
    day: str,
    incomplete_only: bool = False,
) -> List[dict]:
    return collect_kind_tasks(
        tasks,
        day=day,
        match=lambda t: is_sleep_owned_task(t, day=day),
        incomplete_only=incomplete_only,
    )


def collect_protect_bedtime_tasks(
    tasks: Sequence[dict],
    *,
    day: str,
    incomplete_only: bool = False,
) -> List[dict]:
    return collect_kind_tasks(
        tasks,
        day=day,
        match=lambda t: is_protect_bedtime_owned_task(t, day=day),
        incomplete_only=incomplete_only,
    )


def collect_sleep_battery_low_tasks(
    tasks: Sequence[dict],
    *,
    day: str,
    incomplete_only: bool = False,
) -> List[dict]:
    return collect_kind_tasks(
        tasks,
        day=day,
        match=lambda t: is_sleep_battery_low_owned_task(t, day=day),
        incomplete_only=incomplete_only,
    )


def _shopping_name_from_title(title: str) -> str:
    text = (title or "").strip()
    match = re.match(
        r"^(?:Restock|Get|Add):?\s+(.+?)(?:\s+[—-]\s+.+)?$",
        text,
        re.I,
    )
    return (match.group(1) or "").strip() if match else ""


def task_matches_item(task: dict, item: PlannedItem, day: str) -> bool:
    """True when this Fitness task is the same kind+day as the planned leaf.

    Title-shape families (protein, cardio AZM, Protect bedtime, Sleep battery
    low) beat kind-mark equality so a fresh action id cannot fork a leaf (#363).
    Other kinds still match the kind marker first. Exact title is last-resort.
    Jots without a FitDash day hint never match.
    """
    if not isinstance(task, dict) or not isinstance(item, PlannedItem):
        return False
    if not _task_on_civil_day(task, day):
        return False
    if is_protein_remaining_item(item):
        return is_protein_remaining_owned_task(task, day=day)
    if is_cardio_azm_item(item):
        return is_cardio_azm_owned_task(task, day=day)
    if is_protect_bedtime_item(item):
        return is_protect_bedtime_owned_task(task, day=day)
    if is_sleep_battery_low_item(item):
        return is_sleep_battery_low_owned_task(task, day=day)
    marked_kind = kind_from_notes(task.get("notes") or "")
    want_kind = item_kind_key(item)
    if marked_kind:
        return marked_kind == want_kind
    title = task.get("title") or ""
    if item.slug == TRAIN_SESSION_SLUG or (
        item.group == "training" and looks_like_train_session_title(item.title)
    ):
        return is_train_session_owned_task(task, day=day)
    if item.slug == CALORIE_PACE_SLUG or looks_like_calorie_pace_title(item.title):
        return is_calorie_pace_owned_task(task, day=day)
    if is_meal_plan_item(item):
        if not is_meal_plan_owned_task(task, day=day):
            return False
        want = (item.item_name or meal_food_name_from_title(item.title)).strip().lower()
        got = meal_food_name_from_title(title).strip().lower()
        if want and got:
            return want == got
        return (title or "").strip() == (item.title or "").strip()
    if item.group == "training" and str(item.slug or "").startswith("ex-"):
        name = lift_name_from_title(title)
        return bool(name) and item.slug == f"ex-{_slug(name)}"
    if item.group == "shopping":
        want = ""
        if str(item.slug or "").startswith("buy-"):
            want = str(item.slug)[4:]
            want = re.sub(r"-\d+$", "", want)
        if not want:
            want = _slug(_shopping_name_from_title(item.title) or item.title)
        got = _slug(_shopping_name_from_title(title) or title)
        if want and got:
            return want == got or got.startswith(want) or want.startswith(got)
        return (title or "").strip() == (item.title or "").strip()
    return (title or "").strip() == (item.title or "").strip()


def is_protein_remaining_owned_task(task: dict, *, day: str = "") -> bool:
    """True for FitDash protein-remaining leaves on this civil day.

    Title shape plus ``[fitdash-quest:day]`` or due date. Never meal slots,
    lifts, shopping, sleep, or unmarked jots.
    """
    if not isinstance(task, dict):
        return False
    if not looks_like_protein_remaining_title(task.get("title") or ""):
        return False
    if looks_like_meal_plan_title(task.get("title") or ""):
        return False
    notes = task.get("notes") or ""
    marked = quest_mark_day(notes)
    want = str(day or "")[:10]
    if marked:
        return (not want) or marked == want
    due = _task_due_day(task)
    if due and _is_day_key(due):
        return (not want) or due == want
    return False


def collect_protein_remaining_tasks(
    tasks: Sequence[dict],
    *,
    day: str,
    incomplete_only: bool = False,
) -> List[dict]:
    out: List[dict] = []
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        if not is_protein_remaining_owned_task(task, day=day):
            continue
        if incomplete_only and not _is_incomplete(task):
            continue
        out.append(task)
    return out


def protein_remaining_payload(
    *,
    kept: Optional[str] = None,
    purged: Optional[Sequence[str]] = None,
    upserted: bool = False,
    created: bool = False,
) -> Dict[str, Any]:
    return {
        "kept": kept or None,
        "purged": list(purged or []),
        "upserted": bool(upserted),
        "created": bool(created),
    }


def _kind_keeper(
    tasks: Sequence[dict],
    *,
    match,
    cache_ids: Optional[Dict[str, str]] = None,
    cache_key_s: str = "",
    prefer_title: str = "",
) -> Optional[dict]:
    incompletes = [
        t
        for t in (tasks or [])
        if isinstance(t, dict) and match(t) and _is_incomplete(t)
    ]
    if not incompletes:
        return None
    stable_tid = str((cache_ids or {}).get(cache_key_s) or "")
    if stable_tid:
        for task in incompletes:
            if str(task.get("id") or "") == stable_tid:
                return task
    want = (prefer_title or "").strip()
    if want:
        exact = [
            t for t in incompletes if (t.get("title") or "").strip() == want
        ]
        if exact:
            return sorted(exact, key=lambda t: str(t.get("id") or ""))[0]
    marked = [
        t for t in incompletes if quest_mark_day(t.get("notes") or "")
    ]
    pool = marked or incompletes
    return sorted(pool, key=lambda t: str(t.get("id") or ""))[0]


def is_cardio_azm_owned_task(task: dict, *, day: str = "") -> bool:
    """FitDash cardio|azm leaf for this civil day. Not a PPL lift."""
    if not isinstance(task, dict):
        return False
    title = task.get("title") or ""
    kind = kind_from_notes(task.get("notes") or "")
    if kind == CARDIO_AZM_CACHE_KEY:
        return _task_on_civil_day(task, day)
    if not looks_like_cardio_azm_title(title):
        return False
    if looks_like_meal_plan_title(title) or looks_like_protein_remaining_title(title):
        return False
    if not _has_fitdash_kind_or_quest(task):
        return False
    return _task_on_civil_day(task, day)


def _family_remap_cache_key(item: PlannedItem):
    if is_protein_remaining_item(item):
        return is_protein_remaining_cache_key
    if is_cardio_azm_item(item):
        return is_cardio_azm_cache_key
    if is_protect_bedtime_item(item):
        return is_protect_bedtime_cache_key
    if is_sleep_battery_low_item(item):
        return is_sleep_battery_low_cache_key
    return None


def collapse_kind_tasks(
    *,
    list_id: str,
    day: str,
    match,
    cache_key_s: str,
    cache: Optional[dict] = None,
    save: bool = True,
    listed_tasks: Optional[Sequence[dict]] = None,
    prefer_title: str = "",
    remap_cache_key=None,
) -> Dict[str, Any]:
    """Keep ≤1 incomplete Fitness leaf for this kind+civil day.

    Extra incompletes are deleted. Completed copies stay history. Tasks
    that fail ``match`` (other families, jots) are never touched.
    """
    day = str(day or "")[:10]
    empty = protein_remaining_payload()
    if not list_id or not day or not _is_day_key(day) or not cache_key_s:
        return {**empty, "ok": False, "error": "missing list_id, day, or kind"}
    if cache is None:
        cache = _load_cache()
    day_cache = cache.get(day) if isinstance(cache.get(day), dict) else {}
    ids = dict(day_cache.get("ids") or {})
    tasks: List[dict] = list(listed_tasks or [])
    errors: List[str] = []
    if listed_tasks is None:
        try:
            listed = gtb.list_tasks(list_id, show_completed=True, show_hidden=True)
            if listed.get("ok"):
                tasks = [t for t in (listed.get("tasks") or []) if isinstance(t, dict)]
            else:
                return {
                    **empty,
                    "ok": True,
                    "error": str(listed.get("error") or "list_tasks failed")[:160],
                }
        except Exception as exc:  # noqa: BLE001
            return {**empty, "ok": True, "error": str(exc)[:160]}

    keeper = _kind_keeper(
        tasks,
        match=match,
        cache_ids=ids,
        cache_key_s=cache_key_s,
        prefer_title=prefer_title,
    )
    incompletes = [
        t
        for t in tasks
        if isinstance(t, dict) and match(t) and _is_incomplete(t)
    ]
    keep_id = str((keeper or {}).get("id") or "")
    extras = [
        t for t in incompletes if keep_id and str(t.get("id") or "") != keep_id
    ]
    purged: List[str] = []
    for task in extras:
        tid = str(task.get("id") or "")
        if not tid:
            continue
        try:
            result = gtb.delete_task(list_id, tid)
            if result.get("ok"):
                purged.append(tid)
            else:
                errors.append(f"{tid}: {result.get('error') or 'delete failed'}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{tid}: {exc}")

    if keep_id:
        ids[cache_key_s] = keep_id
    drop = set(purged)
    for ck in list(ids.keys()):
        if str(ids.get(ck) or "") in drop:
            ids.pop(ck, None)
        elif (
            ck != cache_key_s
            and str(ids.get(ck) or "") == keep_id
            and (
                (remap_cache_key and remap_cache_key(ck))
                or ck.split("|", 1)[-1].startswith("action-")
            )
        ):
            ids.pop(ck, None)
            ids[cache_key_s] = keep_id
    if isinstance(day_cache, dict) and (purged or keep_id):
        day_cache = dict(day_cache)
        day_cache["ids"] = ids
        cache[day] = day_cache
        if save:
            _save_cache(cache)

    return {
        **protein_remaining_payload(kept=keep_id or None, purged=purged),
        "ok": True,
        "errors": errors[:20],
        "error": errors[0] if errors else None,
    }


def collapse_protein_remaining_tasks(
    *,
    list_id: str,
    day: str,
    cache: Optional[dict] = None,
    save: bool = True,
    listed_tasks: Optional[Sequence[dict]] = None,
    prefer_title: str = "",
) -> Dict[str, Any]:
    """Keep ≤1 incomplete protein-remaining leaf for this civil day.

    Extra incompletes are deleted. Completed / checked copies stay history.
    Meal slots, lifts, shopping, sleep, and jots are never touched.
    """
    day = str(day or "")[:10]
    return collapse_kind_tasks(
        list_id=list_id,
        day=day,
        cache=cache,
        save=save,
        listed_tasks=listed_tasks,
        prefer_title=prefer_title,
        cache_key_s=PROTEIN_REMAINING_CACHE_KEY,
        match=lambda t: is_protein_remaining_owned_task(t, day=day),
        remap_cache_key=is_protein_remaining_cache_key,
    )


def is_meal_plan_owned_task(task: dict, *, day: str = "") -> bool:
    """True only for FitDash meal-plan leaves — never hand jots.

    Ownership: ``[fitdash-meal:day]`` in notes, or a ``[fitdash-quest:day]``
    leftover whose title matches the planner meal-leaf shape.
    """
    if not isinstance(task, dict):
        return False
    notes = task.get("notes") or ""
    marked = meal_mark_day(notes)
    want = str(day or "")[:10]
    if marked:
        return (not want) or marked == want
    qday = quest_mark_day(notes)
    if qday and ((not want) or qday == want) and looks_like_meal_plan_title(
        task.get("title") or ""
    ):
        return True
    return False


def collect_meal_plan_task_ids(
    tasks: Sequence[dict],
    *,
    day: str,
    cache_ids: Optional[Dict[str, str]] = None,
) -> set:
    """Ids FitDash wrote for this day's meal plan. Jots and lift quests omitted."""
    owned: set = set()
    for ck, tid in (cache_ids or {}).items():
        if is_meal_plan_cache_key(ck) and tid:
            owned.add(str(tid))
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        tid = str(task.get("id") or "")
        if tid and is_meal_plan_owned_task(task, day=day):
            owned.add(tid)
    return owned


def board_food_logs_fingerprint(today_board: dict, *, day: str = "") -> str:
    """Fingerprint from the today board the planner already built."""
    board = today_board or {}
    want = str(day or board.get("date") or "")[:10]
    nut = board.get("nutrition") or {}
    stamped = str(nut.get("food_logs_fp") or "").strip()
    if stamped:
        return stamped
    meal = board.get("meal") or {}
    logs = meal.get("food_logs_today") or []
    return food_logs_fingerprint(
        logs, consumed=nut.get("consumed"), day=want
    )


def meal_regen_payload(
    *,
    triggered: bool = False,
    reason: Optional[str] = None,
    purged: Optional[Sequence[str]] = None,
    created: Optional[Sequence[str]] = None,
    fingerprint: Optional[str] = None,
    prior_fingerprint: Optional[str] = None,
    silent: bool = True,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """Assay hook: before/after GT ids for one meal-plan purge/recreate.

    ``reason`` is ``food_logs`` when the foods fingerprint or food-identity
    set changed (or legacy leaves had no fp). Same-food title shifts
    upsert in place and do not set this payload.
    """
    return {
        "triggered": bool(triggered),
        "reason": reason,
        "purged": list(purged or []),
        "created": list(created or []),
        "fingerprint": fingerprint or None,
        "prior_fingerprint": prior_fingerprint or None,
        "silent": bool(silent),
        "error": error,
    }


def _is_incomplete(task: dict) -> bool:
    return str((task or {}).get("status") or "") != "completed"


def _quest_civil_day(task: dict) -> str:
    """Civil day this quest belongs to: marker first, then due."""
    marked = quest_mark_day((task or {}).get("notes") or "")
    if marked:
        return marked
    return _task_due_day(task)


def collect_fitdash_quest_ids(tasks: Sequence[dict]) -> set:
    """Ids FitDash wrote: marker, known group header, or child of those.

    Unmarked user-OAuth leftovers are still identifiable when they sit under
    Training / Cardio / Nutrition / Shopping / Sleep & recovery. Top-level
    jots and other lists are not included.
    """
    headers = group_header_titles()
    quest_ids: set = set()
    for task in tasks:
        if not isinstance(task, dict):
            continue
        tid = str(task.get("id") or "")
        if not tid:
            continue
        if quest_mark_day(task.get("notes") or ""):
            quest_ids.add(tid)
        if (task.get("title") or "").strip() in headers:
            quest_ids.add(tid)
    changed = True
    while changed:
        changed = False
        for task in tasks:
            if not isinstance(task, dict):
                continue
            tid = str(task.get("id") or "")
            parent = str(task.get("parent") or "")
            if tid and parent and parent in quest_ids and tid not in quest_ids:
                quest_ids.add(tid)
                changed = True
    return quest_ids


def _belonging_day(task: dict, by_id: Dict[str, dict]) -> str:
    day = _quest_civil_day(task)
    if day:
        return day
    parent = by_id.get(str((task or {}).get("parent") or ""))
    if parent:
        return _quest_civil_day(parent)
    return ""


@dataclass
class PlannedItem:
    group: str
    slug: str
    title: str
    notes_extra: str = ""
    meal_label: str = ""  # e.g. Next meal · 3:30 PM (UI / quest grouping)
    eat_at: str = ""  # ISO from meal bucket; empty → no Calendar event
    meal_slot: str = ""  # e.g. meal-0; one Calendar reminder per slot
    cal_label: str = ""  # bucket label without clock (Calendar title)
    item_name: str = ""
    portion_g: Optional[float] = None


@dataclass
class PlannedGroup:
    group: str
    title: str
    emoji: str = "✓"
    items: List[PlannedItem] = field(default_factory=list)


def plan_from_today_board(today: dict, *, day: Optional[str] = None) -> List[PlannedGroup]:
    """Build desired group → leaf items from coach.today board."""
    day = day or str((today or {}).get("date") or local_today_iso())
    groups: Dict[str, PlannedGroup] = {}

    def _g(kind: str) -> PlannedGroup:
        key = "sleep" if kind == "recovery" else kind
        if key not in groups:
            m = GROUP_META.get(key) or GROUP_META["other"]
            groups[key] = PlannedGroup(
                group=key, title=m["title"], emoji=m.get("emoji") or "✓", items=[]
            )
        return groups[key]

    # High-level actions (skip pure nutrition "eat through plan" if we expand meals)
    saw_family: set = set()
    for i, act in enumerate((today or {}).get("actions") or []):
        if not isinstance(act, dict):
            continue
        kind = str(act.get("kind") or "other").lower()
        text = str(act.get("text") or "").strip()
        if not text:
            continue
        # Prefer meal-bucket leaves over generic "eat through planned meals"
        if kind == "nutrition" and "planned meal" in text.lower():
            continue
        slug = stable_action_slug(act, i)
        if slug in (
            PROTEIN_REMAINING_SLUG,
            PROTECT_BEDTIME_SLUG,
            SLEEP_BATTERY_LOW_SLUG,
            TRAIN_SESSION_SLUG,
            CALORIE_PACE_SLUG,
            SHOP_TOP_SLUG,
            CARDIO_AZM_SLUG,
        ):
            if slug in saw_family:
                continue
            saw_family.add(slug)
        _g(kind).items.append(
            PlannedItem(
                group=("sleep" if kind == "recovery" else kind),
                slug=_slug(slug),
                title=text[:200],
                notes_extra=str(act.get("motivation") or "")[:400],
            )
        )

    # Training exercises. Already-trained days keep completed leaves as
    # history and must not seed the next PPL's ex-* cards.
    workout = (today or {}).get("workout") or {}
    if not workout.get("is_rest_day") and not workout.get("already_trained_today"):
        for ex in workout.get("exercises") or []:
            if not isinstance(ex, dict):
                continue
            name = str(ex.get("name") or "").strip()
            if not name:
                continue
            bits = []
            if ex.get("weight_lbs") is not None:
                bits.append(f"{ex.get('weight_lbs')} lb")
            if ex.get("sets") is not None and ex.get("reps") is not None:
                bits.append(f"{ex.get('sets')}×{ex.get('reps')}")
            detail = f"{name} ({' '.join(bits)})" if bits else name
            _g("training").items.append(
                PlannedItem(
                    group="training",
                    slug=f"ex-{_slug(name)}",
                    title=detail[:200],
                    notes_extra=str(ex.get("rationale") or "")[:400],
                )
            )

    # Nutrition: meal-plan buckets (Next meal / Later / Evening) — not flat day dump
    meal = (today or {}).get("meal") or {}
    meals = list(meal.get("meals") or [])
    if meals:
        for mi, bucket in enumerate(meals):
            if not isinstance(bucket, dict):
                continue
            bucket_label = str(bucket.get("label") or f"Meal {mi + 1}").strip()
            label = bucket_label
            clock = str(bucket.get("eat_at_label") or "").strip()
            if not clock and bucket.get("eat_at"):
                try:
                    raw_eat = str(bucket.get("eat_at") or "")
                    if raw_eat.endswith("Z"):
                        raw_eat = raw_eat[:-1] + "+00:00"
                    eat_dt = datetime.fromisoformat(raw_eat)
                    h24 = eat_dt.hour
                    h = h24 % 12 or 12
                    clock = f"{h}:{eat_dt.minute:02d} {'AM' if h24 < 12 else 'PM'}"
                except ValueError:
                    clock = ""
            if clock:
                label = f"{label} · {clock}"
            items = list(bucket.get("items") or [])
            if not items:
                continue
            for j, it in enumerate(items):
                if not isinstance(it, dict):
                    continue
                name = str(it.get("name") or "").strip()
                if not name:
                    continue
                portion = format_plan_portion(it)
                title = f"{label}: {name}"
                if portion:
                    title = f"{label}: {name} · {portion}"
                portion_g = None
                raw_pg = it.get("portion_g")
                if raw_pg is not None and str(raw_pg).strip() != "":
                    try:
                        pg = float(raw_pg)
                        if pg > 0:
                            portion_g = pg
                    except (TypeError, ValueError):
                        portion_g = None
                _g("nutrition").items.append(
                    PlannedItem(
                        group="nutrition",
                        slug=f"meal-{mi}-{_slug(name)}-{j}",
                        title=title[:200],
                        meal_label=label,
                        eat_at=str(bucket.get("eat_at") or "").strip(),
                        meal_slot=f"meal-{mi}",
                        cal_label=bucket_label,
                        item_name=name,
                        portion_g=portion_g,
                    )
                )
    else:
        # Fallback flat items if meals buckets empty
        for j, it in enumerate(meal.get("items") or []):
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "").strip()
            if not name:
                continue
            portion = format_plan_portion(it)
            title = f"Eat: {name}" + (f" · {portion}" if portion else "")
            _g("nutrition").items.append(
                PlannedItem(
                    group="nutrition",
                    slug=f"food-{_slug(name)}-{j}",
                    title=title[:200],
                )
            )

    for p in (today or {}).get("purchases") or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        if not name:
            continue
        g = _g("shopping")
        if any(name.lower() in it.title.lower() for it in g.items):
            continue
        act = "Restock" if p.get("action") == "restock" else "Get"
        g.items.append(
            PlannedItem(
                group="shopping",
                slug=f"buy-{_slug(name)}",
                title=f"{act}: {name}"[:200],
                notes_extra=str(p.get("reason") or "")[:400],
            )
        )

    spec = cardio_spec(today, as_of=day)
    cardio_g = groups.get(CARDIO_GROUP)
    cardio_items = [
        it for it in (cardio_g.items if cardio_g else []) if is_cardio_azm_item(it)
    ]
    if cardio_items:
        leaf = cardio_items[0]
        leaf.group = CARDIO_GROUP
        leaf.slug = CARDIO_AZM_SLUG
        leaf.title = spec["title"][:200]
        if not leaf.notes_extra:
            leaf.notes_extra = str(spec.get("motivation") or "")[:400]
    else:
        _g(CARDIO_GROUP).items.append(
            PlannedItem(
                group=CARDIO_GROUP,
                slug=CARDIO_AZM_SLUG,
                title=spec["title"][:200],
                notes_extra=str(spec.get("motivation") or "")[:400],
            )
        )

    ordered = sorted(
        [g for g in groups.values() if g.items],
        key=lambda g: (GROUP_META.get(g.group) or GROUP_META["other"])["order"],
    )
    return ordered


def _get_task_safe(list_id: str, task_id: str) -> Optional[dict]:
    try:
        r = gtb.get_task(list_id, task_id)
        if r.get("ok"):
            return r.get("task")
    except Exception:
        return None
    return None


def _task_due_day(task: dict) -> str:
    return str((task or {}).get("due") or "")[:10]


def _hydrate_ids_from_listed(
    ids: Dict[str, str],
    planned: List[PlannedGroup],
    listed: dict,
    day: str,
) -> Dict[str, str]:
    """Reuse existing Fitness tasks when the local cache is empty (Vercel).

    Leaves match by kind+day (title metrics may differ). Group headers
    still match exact title.
    """
    tasks = [t for t in (listed.get("tasks") or []) if isinstance(t, dict)]
    unused = list(tasks)
    used_ids = {str(v) for v in (ids or {}).values() if v}

    def take(title: str, parent_id: Optional[str] = None) -> Optional[dict]:
        want = (title or "").strip()
        for i, t in enumerate(unused):
            tid = str(t.get("id") or "")
            if tid and tid in used_ids:
                continue
            if (t.get("title") or "").strip() != want:
                continue
            due = _task_due_day(t)
            if due and due != day:
                continue
            if parent_id and str(t.get("parent") or "") not in ("", parent_id):
                continue
            unused.pop(i)
            return t
        return None

    def take_for_item(
        item: PlannedItem, parent_id: Optional[str] = None
    ) -> Optional[dict]:
        candidates: List[Tuple[int, int, dict]] = []
        for i, t in enumerate(unused):
            tid = str(t.get("id") or "")
            if tid and tid in used_ids:
                continue
            if not task_matches_item(t, item, day):
                continue
            due = _task_due_day(t)
            if due and due != day:
                continue
            if parent_id and str(t.get("parent") or "") not in ("", parent_id):
                continue
            incomplete = 0 if _is_incomplete(t) else 1
            candidates.append((incomplete, i, t))
        if not candidates:
            return None
        candidates.sort(key=lambda row: (row[0], row[1]))
        _inc, idx, found = candidates[0]
        unused.pop(idx)
        return found

    out = dict(ids)
    by_id = {str(t.get("id") or ""): t for t in tasks if t.get("id")}
    for ck, tid in list(out.items()):
        if not tid:
            continue
        task = by_id.get(str(tid))
        if not task:
            continue
        for g in planned:
            for it in g.items:
                stable = item_kind_key(it)
                if ck == stable:
                    continue
                if task_matches_item(task, it, day):
                    out.setdefault(stable, tid)
    for g in planned:
        parent_ck = cache_key(g.group, "group")
        parent_id = out.get(parent_ck)
        if not parent_id:
            found = take(g.title)
            if found and found.get("id"):
                parent_id = str(found["id"])
                out[parent_ck] = parent_id
                used_ids.add(parent_id)
        for it in g.items:
            ck = item_kind_key(it)
            if out.get(ck):
                continue
            found = take_for_item(it, parent_id=parent_id)
            if found and found.get("id"):
                out[ck] = str(found["id"])
                used_ids.add(str(found["id"]))
    return out


def _is_day_key(key: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", str(key or "")))


def _delete_order(ids: Dict[str, str]) -> List[Tuple[str, str]]:
    """Children first, group parents last (so leaves are gone before headers)."""
    items = [(str(ck), str(tid)) for ck, tid in (ids or {}).items() if ck and tid]

    def sort_key(item: Tuple[str, str]) -> Tuple[int, str]:
        ck, _tid = item
        is_parent = ck.endswith("|group")
        return (1 if is_parent else 0, ck)

    return sorted(items, key=sort_key)


def purge_stale_quest_tasks(
    *,
    list_id: str,
    today: str,
    cache: Optional[dict] = None,
    save: bool = True,
) -> Dict[str, Any]:
    """Delete yesterday's incomplete FitDash quests; leave everything else.

    User-OAuth writes on Vercel have no durable local cache. Identity is the
    notes marker, known group headers + children, then cache ids when present.
    Completed prior-day quests are left completed (never uncompleted).
    Non-FitDash tasks on this list (jots) and every other list are untouched.

    Returns stats: ``{days_purged, deleted, orphan_deleted, failed, errors}``.
    """
    today = str(today or "")[:10]
    if cache is None:
        cache = _load_cache()
    if not list_id or not today or not _is_day_key(today):
        return {
            "days_purged": [],
            "deleted": 0,
            "orphan_deleted": 0,
            "failed": 0,
            "errors": [],
            "ok": False,
            "error": "missing list_id or today",
        }

    stale_days = sorted(
        k
        for k in list(cache.keys())
        if _is_day_key(k) and k != today and isinstance(cache.get(k), dict)
    )
    deleted = 0
    failed = 0
    errors: List[str] = []
    days_purged: List[str] = []
    already: set = set()
    headers = group_header_titles()

    def _delete(target_list: str, tid: str, label: str) -> bool:
        nonlocal deleted, failed
        try:
            result = gtb.delete_task(target_list, tid)
            already.add(tid)
            if result.get("ok"):
                deleted += 1
                return True
            failed += 1
            errors.append(f"{label}: {result.get('error') or 'delete failed'}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            errors.append(f"{label}: {e}")
        return False

    for stale in stale_days:
        entry = cache.get(stale) or {}
        entry_list = str(entry.get("list_id") or list_id)
        ids = dict(entry.get("ids") or {})
        for ck, tid in _delete_order(ids):
            if tid in already:
                continue
            target_list = entry_list or list_id
            task = _get_task_safe(target_list, tid)
            if task and not _is_incomplete(task):
                already.add(tid)
                continue
            _delete(target_list, tid, f"{stale}/{ck}")
        cache.pop(stale, None)
        days_purged.append(stale)

    # Vercel / empty-cache safety net: identify FitDash quests on this list
    # and drop incomplete ones that belong to a prior civil day.
    orphan_deleted = 0
    try:
        listed = gtb.list_tasks(list_id, show_completed=True, show_hidden=True)
        if listed.get("ok"):
            tasks = [t for t in (listed.get("tasks") or []) if isinstance(t, dict)]
            quest_ids = collect_fitdash_quest_ids(tasks)
            by_id = {str(t.get("id")): t for t in tasks if t.get("id")}
            stale_tasks = []
            for task in tasks:
                tid = str(task.get("id") or "")
                if not tid or tid not in quest_ids or tid in already:
                    continue
                if not _is_incomplete(task):
                    continue
                day = _belonging_day(task, by_id)
                if not day or not _is_day_key(day) or day >= today:
                    continue
                stale_tasks.append(task)
            stale_tasks.sort(
                key=lambda t: (
                    1 if (t.get("title") or "").strip() in headers else 0,
                    str(t.get("id") or ""),
                )
            )
            for task in stale_tasks:
                tid = str(task.get("id") or "")
                if tid in already:
                    continue
                if _delete(list_id, tid, f"orphan/{tid}"):
                    orphan_deleted += 1
    except Exception as e:  # noqa: BLE001
        errors.append(f"orphan_sweep: {e}")

    if save and (days_purged or orphan_deleted):
        _save_cache(cache)

    return {
        "ok": True,
        "days_purged": days_purged,
        "deleted": deleted,
        "orphan_deleted": orphan_deleted,
        "failed": failed,
        "errors": errors[:20],
    }


def meal_food_identities_from_planned(planned: Sequence[PlannedGroup]) -> set:
    names = set()
    for group in planned or []:
        if group.group != "nutrition":
            continue
        for item in group.items:
            if not is_meal_plan_item(item):
                continue
            name = (item.item_name or meal_food_name_from_title(item.title)).strip().lower()
            if name:
                names.add(name)
    return names


def meal_food_identities_from_tasks(tasks: Sequence[dict], day: str) -> set:
    names = set()
    for task in tasks or []:
        if not isinstance(task, dict) or not is_meal_plan_owned_task(task, day=day):
            continue
        name = meal_food_name_from_title(task.get("title") or "").strip().lower()
        if name:
            names.add(name)
    return names


def purge_unplanned_training_leaves(
    *,
    list_id: str,
    day: str,
    planned: Sequence[PlannedGroup],
    listed_tasks: Optional[Sequence[dict]] = None,
    cache: Optional[dict] = None,
    save: bool = True,
) -> Dict[str, Any]:
    """Delete incomplete same-day training leaves that are not in today's plan.

    Completed copies stay history. Nutrition / shopping / sleep / jots are
    never touched. The Training group header is kept.
    """
    day = str(day or "")[:10]
    stats: Dict[str, Any] = {"deleted": [], "failed": 0, "errors": []}
    if not list_id or not day or not _is_day_key(day):
        stats["ok"] = False
        stats["error"] = "missing list_id or day"
        return stats
    keep: set = {"training|group"}
    for g in planned or []:
        if str(getattr(g, "group", "") or "") != "training":
            continue
        for it in g.items or []:
            keep.add(item_kind_key(it))
    tasks = [t for t in (listed_tasks or []) if isinstance(t, dict)]
    if cache is None:
        cache = _load_cache()
    day_cache = cache.get(day) if isinstance(cache.get(day), dict) else {}
    ids = dict(day_cache.get("ids") or {})
    deleted: List[str] = []
    for task in tasks:
        if not _is_incomplete(task):
            continue
        if not _has_fitdash_kind_or_quest(task):
            continue
        if not _task_on_civil_day(task, day):
            continue
        notes = task.get("notes") or ""
        kind = kind_from_notes(notes) or ""
        if not kind.startswith("training|"):
            continue
        if kind in keep:
            continue
        tid = str(task.get("id") or "")
        if not tid:
            continue
        try:
            result = gtb.delete_task(list_id, tid)
            if result.get("ok"):
                deleted.append(tid)
            else:
                stats["failed"] += 1
                stats["errors"].append(
                    f"{tid}: {result.get('error') or 'delete failed'}"
                )
        except Exception as exc:  # noqa: BLE001
            stats["failed"] += 1
            stats["errors"].append(f"{tid}: {exc}")
    drop = set(deleted)
    for ck in list(ids.keys()):
        if str(ids.get(ck) or "") in drop:
            ids.pop(ck, None)
    if deleted:
        cache[day] = {"list_id": list_id, "ids": ids}
        if save:
            _save_cache(cache)
    stats["deleted"] = deleted
    stats["ok"] = True
    return stats


def purge_wrong_rotation_lifts(
    *,
    list_id: str,
    day: str,
    pin: str,
    listed_tasks: Sequence[dict],
    cache: Optional[dict] = None,
    save: bool = True,
    catalog: Optional[dict] = None,
) -> Dict[str, Any]:
    """Delete incomplete same-day lift leaves that belong to another PPL slot.

    After a session is logged, dashboard paint used to generate the *next*
    rotation and seed those ``ex-*`` leaves. Completed copies stay. Same-type
    incomplete lifts (mid-session remainder) stay. Hand jots stay. Unknown
    catalog names stay — do not guess a letter.
    """
    from .workout_planner import session_types_for_lift_name

    day = str(day or "")[:10]
    pin_st = str(pin or "").lower()
    empty = {"ok": True, "purged": [], "failed": 0, "errors": []}
    if not list_id or not day or not _is_day_key(day) or pin_st not in (
        "push",
        "pull",
        "legs",
    ):
        return {**empty, "ok": False, "error": "missing list_id, day, or pin"}
    if cache is None:
        cache = _load_cache()
    day_cache = cache.get(day) if isinstance(cache.get(day), dict) else {}
    ids = dict(day_cache.get("ids") or {})
    purged: List[str] = []
    errors: List[str] = []
    for task in listed_tasks or []:
        if not isinstance(task, dict) or not _is_incomplete(task):
            continue
        if not _has_fitdash_kind_or_quest(task):
            continue
        if not _task_on_civil_day(task, day):
            continue
        title = task.get("title") or ""
        if looks_like_train_session_title(title):
            continue
        name = lift_name_from_title(title)
        if not name:
            continue
        types = session_types_for_lift_name(name, catalog)
        if not types or pin_st in types:
            continue
        tid = str(task.get("id") or "")
        if not tid:
            continue
        try:
            result = gtb.delete_task(list_id, tid)
            if result.get("ok"):
                purged.append(tid)
            else:
                errors.append(f"{tid}: {result.get('error') or 'delete failed'}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{tid}: {exc}")
    drop = set(purged)
    if drop:
        for ck in list(ids.keys()):
            if str(ids.get(ck) or "") in drop:
                ids.pop(ck, None)
        if isinstance(day_cache, dict):
            day_cache = dict(day_cache)
            day_cache["ids"] = ids
            cache[day] = day_cache
            if save:
                _save_cache(cache)
    return {
        "ok": True,
        "purged": purged,
        "failed": len(errors),
        "errors": errors,
    }


def _existing_meal_foods_fp(tasks: Sequence[dict], day: str) -> Optional[str]:
    fps = {
        foods_fp_from_notes(t.get("notes") or "")
        for t in tasks
        if isinstance(t, dict) and is_meal_plan_owned_task(t, day=day)
    }
    fps.discard(None)
    if len(fps) == 1:
        return next(iter(fps))  # type: ignore[return-value]
    return None


def purge_meal_plan_tasks(
    *,
    list_id: str,
    day: str,
    cache: Optional[dict] = None,
    save: bool = True,
    listed_tasks: Optional[Sequence[dict]] = None,
) -> Dict[str, Any]:
    """Delete same-day meal-plan-owned GT items. Silent if none exist.

    Only ``[fitdash-meal:]`` / planner meal-leaf leftovers. Never hand jots,
    training, shopping, sleep, or non-meal nutrition actions. Does not delete
    the Nutrition group header (ensure reuses it). Soft-fails on list errors.
    """
    day = str(day or "")[:10]
    empty = meal_regen_payload(silent=True)
    if not list_id or not day or not _is_day_key(day):
        return {
            "ok": False,
            "purged": [],
            "failed": 0,
            "errors": ["missing list_id or day"],
            "silent": True,
            "error": "missing list_id or day",
        }
    if cache is None:
        cache = _load_cache()
    day_cache = cache.get(day) if isinstance(cache.get(day), dict) else {}
    ids = dict(day_cache.get("ids") or {})
    tasks: List[dict] = list(listed_tasks or [])
    errors: List[str] = []
    if listed_tasks is None:
        try:
            listed = gtb.list_tasks(list_id, show_completed=True, show_hidden=True)
            if listed.get("ok"):
                tasks = [t for t in (listed.get("tasks") or []) if isinstance(t, dict)]
            else:
                errors.append(str(listed.get("error") or "list_tasks failed")[:160])
                return {
                    "ok": True,
                    "purged": [],
                    "failed": 0,
                    "errors": errors,
                    "silent": True,
                    "error": errors[0] if errors else None,
                }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": True,
                "purged": [],
                "failed": 0,
                "errors": [str(exc)[:160]],
                "silent": True,
                "error": str(exc)[:160],
            }

    owned = collect_meal_plan_task_ids(tasks, day=day, cache_ids=ids)
    if not owned:
        return {
            "ok": True,
            "purged": [],
            "failed": 0,
            "errors": [],
            "silent": True,
            "error": None,
        }

    purged: List[str] = []
    failed = 0
    for tid in sorted(owned):
        try:
            result = gtb.delete_task(list_id, tid)
            if result.get("ok"):
                purged.append(tid)
            else:
                failed += 1
                errors.append(f"{tid}: {result.get('error') or 'delete failed'}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            errors.append(f"{tid}: {exc}")

    if purged:
        drop = set(purged) | owned
        for ck in list(ids.keys()):
            if is_meal_plan_cache_key(ck) and str(ids.get(ck) or "") in drop:
                ids.pop(ck, None)
        if isinstance(day_cache, dict):
            day_cache = dict(day_cache)
            day_cache["ids"] = ids
            cache[day] = day_cache
            if save:
                _save_cache(cache)

    return {
        "ok": True,
        "purged": purged,
        "failed": failed,
        "errors": errors[:20],
        "silent": False,
        "error": None,
    }


def ensure_daily_tasks(
    today_board: dict,
    *,
    list_title: str = DEFAULT_LIST_TITLE,
    day: Optional[str] = None,
    create_missing: bool = True,
) -> dict:
    """Ensure / refresh quests. Titles stay human-only; notes carry markers.

    On each ensure for civil day D: remove incomplete FitDash quests that
    belong to any day other than D, then create/refresh today's groups and
    leaves. Completed prior-day quests are left completed.

    Every FitDash leaf upserts by kind + civil day (issue #357). Title
    metrics (battery %, wake time, grams, clock, lift load) may change in
    place. Incomplete extras of the same kind+day collapse to one.
    Completed copies stay history and are not resurrected.

    Meal-plan leaves still purge+recreate when the food-log fingerprint
    or the food-identity set changes (#321). Same foods with a clock /
    portion / slot-label shift upsert like every other family. Hand jots
    and non-FitDash Tasks are never touched.
    """
    day = day or str((today_board or {}).get("date") or local_today_iso())
    planned = plan_from_today_board(today_board or {}, day=day)
    cardio = cardio_spec(today_board or {}, as_of=day)
    cardio_hit = bool(cardio.get("hit"))
    foods_fp = board_food_logs_fingerprint(today_board or {}, day=day)
    meal_stats = meal_regen_payload(fingerprint=foods_fp, silent=True)
    protein_title = next(
        (
            it.title
            for g in planned
            if g.group == "nutrition"
            for it in g.items
            if is_protein_remaining_item(it)
        ),
        "",
    )
    protein_stats = protein_remaining_payload()

    cred = gtb.credentials_status()
    if not cred.get("ok"):
        return _local_payload(
            planned,
            day=day,
            error=cred.get("error") or "Google Tasks not configured",
            meal_regen=meal_stats,
        )

    try:
        list_id = gtb.resolve_list_id(list_title)
        if not list_id:
            return _local_payload(
                planned,
                day=day,
                error=f"Task list '{list_title}' not found",
                meal_regen=meal_stats,
            )

        cache = _load_cache()
        # Day rollover cleanup — must run before creating today's tasks
        purge_stats = purge_stale_quest_tasks(
            list_id=list_id, today=day, cache=cache, save=True
        )

        day_cache = cache.get(day) if isinstance(cache.get(day), dict) else {}
        if day_cache.get("list_id") != list_id:
            day_cache = {"list_id": list_id, "ids": {}}
        ids: Dict[str, str] = dict(day_cache.get("ids") or {})
        try:
            listed = gtb.list_tasks(
                list_id, show_completed=True, show_hidden=True
            )
        except Exception:
            listed = None
        listed_tasks = (
            [t for t in (listed.get("tasks") or []) if isinstance(t, dict)]
            if listed and listed.get("ok")
            else []
        )
        owned_meal = collect_meal_plan_task_ids(
            listed_tasks, day=day, cache_ids=ids
        )
        prior_fp = _existing_meal_foods_fp(listed_tasks, day)
        meal_stats["prior_fingerprint"] = prior_fp
        nut = (today_board or {}).get("nutrition") or {}
        try:
            log_n = int(nut.get("food_log_count") or 0)
        except (TypeError, ValueError):
            log_n = 0
        if not log_n:
            meal_logs = ((today_board or {}).get("meal") or {}).get(
                "food_logs_today"
            ) or []
            log_n = len(meal_logs)
        planned_foods = meal_food_identities_from_planned(planned)
        existing_foods = meal_food_identities_from_tasks(listed_tasks, day)
        # Recreate only when the meal *foods* changed (fp or identity set).
        # Clock / portion / slot-label shifts upsert in place (#357).
        foods_diverged = bool(planned_foods or existing_foods) and (
            planned_foods != existing_foods
        )
        fp_changed = prior_fp is not None and prior_fp != foods_fp
        legacy_stale = prior_fp is None and log_n > 0 and foods_diverged
        if owned_meal and (fp_changed or foods_diverged):
            meal_purge = purge_meal_plan_tasks(
                list_id=list_id,
                day=day,
                cache=cache,
                save=True,
                listed_tasks=listed_tasks,
            )
            purged_ids = list(meal_purge.get("purged") or [])
            for tid in purged_ids:
                for ck, cached in list(ids.items()):
                    if cached == tid:
                        ids.pop(ck, None)
            day_cache = cache.get(day) if isinstance(cache.get(day), dict) else day_cache
            if isinstance(day_cache, dict) and day_cache.get("ids"):
                ids = dict(day_cache.get("ids") or ids)
            listed_tasks = [
                t
                for t in listed_tasks
                if str(t.get("id") or "") not in set(purged_ids)
            ]
            listed = {"ok": True, "tasks": listed_tasks}
            reason = (
                "food_logs"
                if (fp_changed or legacy_stale)
                else "refresh_resync"
            )
            meal_stats = meal_regen_payload(
                triggered=True,
                reason=reason,
                purged=purged_ids,
                fingerprint=foods_fp,
                prior_fingerprint=prior_fp,
                silent=not purged_ids,
                error=meal_purge.get("error"),
            )

        # Collapse same-day incomplete extras per kind before seed (#357).
        collapsed: set = set()
        for g in planned:
            for it in g.items:
                ck = item_kind_key(it)
                if ck in collapsed:
                    continue
                collapsed.add(ck)
                prefer = protein_title if is_protein_remaining_item(it) else it.title
                kind_collapse = collapse_kind_tasks(
                    list_id=list_id,
                    day=day,
                    cache=cache,
                    save=True,
                    listed_tasks=listed_tasks,
                    prefer_title=prefer,
                    cache_key_s=ck,
                    match=lambda t, item=it: task_matches_item(t, item, day),
                    remap_cache_key=_family_remap_cache_key(it),
                )
                kind_purged = list(kind_collapse.get("purged") or [])
                if is_protein_remaining_item(it) and (
                    kind_collapse.get("kept") or kind_purged
                ):
                    protein_stats = protein_remaining_payload(
                        kept=kind_collapse.get("kept"),
                        purged=kind_purged,
                    )
                if kind_purged:
                    listed_tasks = [
                        t
                        for t in listed_tasks
                        if str(t.get("id") or "") not in set(kind_purged)
                    ]
                    listed = {"ok": True, "tasks": listed_tasks}
        workout = (today_board or {}).get("workout") or {}
        pin = str(workout.get("session_type") or "").lower()
        if workout.get("already_trained_today") and pin in ("push", "pull", "legs"):
            # Planned set has no remaining ex-* after a log. Unplanned purge
            # would also drop leftover same-letter lifts (mid-session remainder).
            wrong = purge_wrong_rotation_lifts(
                list_id=list_id,
                day=day,
                pin=pin,
                listed_tasks=listed_tasks,
                cache=cache,
                save=True,
            )
            unplanned_ids = set(wrong.get("purged") or [])
        else:
            unplanned = purge_unplanned_training_leaves(
                list_id=list_id,
                day=day,
                planned=planned,
                listed_tasks=listed_tasks,
                cache=cache,
                save=True,
            )
            unplanned_ids = set(unplanned.get("deleted") or [])
        if unplanned_ids:
            listed_tasks = [
                t
                for t in listed_tasks
                if str(t.get("id") or "") not in unplanned_ids
            ]
            listed = {"ok": True, "tasks": listed_tasks}
            day_cache = cache.get(day) if isinstance(cache.get(day), dict) else day_cache
            if isinstance(day_cache, dict) and day_cache.get("ids"):
                ids = dict(day_cache.get("ids") or ids)
        day_cache = cache.get(day) if isinstance(cache.get(day), dict) else day_cache
        if isinstance(day_cache, dict) and day_cache.get("ids"):
            ids = dict(day_cache.get("ids") or ids)

        if listed and listed.get("ok"):
            ids = _hydrate_ids_from_listed(ids, planned, listed, day)

        groups_out: List[dict] = []
        created_meal_ids: List[str] = []

        for g in planned:
            parent_ck = cache_key(g.group, "group")
            parent_id = ids.get(parent_ck)
            parent_task = _get_task_safe(list_id, parent_id) if parent_id else None
            if not parent_task and create_missing:
                created = gtb.create_task(
                    list_id,
                    g.title,  # no date stamp in the title
                    notes=quest_notes("", day, kind_key=parent_ck),
                    due=day,
                )
                if created.get("ok") and created.get("task"):
                    parent_task = created["task"]
                    parent_id = str(parent_task.get("id") or "")
                    if parent_id:
                        ids[parent_ck] = parent_id
            elif not parent_task:
                parent_id = None

            items_out: List[dict] = []
            for it in g.items:
                protein_item = is_protein_remaining_item(it)
                ck = item_kind_key(it)
                tid = ids.get(ck)
                task = _get_task_safe(list_id, tid) if tid else None
                existing_kind = collect_kind_tasks(
                    listed_tasks,
                    day=day,
                    match=lambda t, item=it: task_matches_item(t, item, day),
                )
                if not task and existing_kind:
                    incompletes = [
                        t for t in existing_kind if _is_incomplete(t)
                    ]
                    pick = incompletes[0] if incompletes else existing_kind[0]
                    if pick and pick.get("id"):
                        task = pick
                        tid = str(pick["id"])
                        ids[ck] = tid
                if (
                    task
                    and create_missing
                    and _is_incomplete(task)
                    and (task.get("title") or "").strip() != it.title.strip()
                ):
                    notes_now = task.get("notes") or ""
                    kind_mark = kind_marker(ck)
                    new_notes = None
                    if kind_mark and kind_mark not in notes_now:
                        new_notes = (
                            (notes_now.rstrip() + "\n" + kind_mark).strip()
                            if notes_now
                            else kind_mark
                        )
                    updated = gtb.update_task(
                        list_id,
                        str(task.get("id") or tid),
                        title=it.title,
                        notes=new_notes,
                    )
                    if updated.get("ok") and updated.get("task"):
                        task = updated["task"]
                        listed_tasks = [
                            updated["task"]
                            if str(t.get("id") or "") == str(task.get("id") or "")
                            else t
                            for t in listed_tasks
                        ]
                    if protein_item:
                        protein_stats["upserted"] = True
                        protein_stats["kept"] = str(task.get("id") or tid)
                if not task and create_missing and not existing_kind:
                    if is_meal_plan_item(it):
                        notes = meal_quest_notes(
                            it.notes_extra or "", day, foods_fp, kind_key=ck
                        )
                    else:
                        notes = quest_notes(
                            it.notes_extra or "", day, kind_key=ck
                        )
                    created = gtb.create_task(
                        list_id,
                        it.title,
                        notes=notes,
                        due=day,
                        parent=str(parent_id) if parent_id else None,
                    )
                    if created.get("ok") and created.get("task"):
                        task = created["task"]
                        tid = str(task.get("id") or "")
                        if tid:
                            ids[ck] = tid
                            listed_tasks.append(task)
                            if is_meal_plan_item(it):
                                created_meal_ids.append(tid)
                            if protein_item:
                                protein_stats["created"] = True
                                protein_stats["kept"] = tid
                if (
                    is_cardio_azm_item(it)
                    and create_missing
                    and task
                    and cardio_hit
                    and _is_incomplete(task)
                ):
                    tid_done = str(task.get("id") or tid)
                    done = gtb.complete_task(
                        list_id, tid_done, completed=True
                    )
                    if done.get("ok") and isinstance(done.get("task"), dict):
                        task = done["task"]
                    else:
                        task = dict(task)
                        task["status"] = "completed"
                if not task:
                    items_out.append(
                        {
                            "slug": it.slug,
                            "title": it.title,
                            "completed": False,
                            "task_id": tid,
                            "list_id": list_id if tid else None,
                            "group": g.group,
                            "meal_label": it.meal_label or None,
                        }
                    )
                    continue
                completed = str(task.get("status") or "") == "completed"
                items_out.append(
                    {
                        "slug": it.slug,
                        "title": task.get("title") or it.title,
                        "completed": completed,
                        "task_id": task.get("id"),
                        "list_id": list_id,
                        "group": g.group,
                        "meal_label": it.meal_label or None,
                    }
                )

            all_done = bool(items_out) and all(x["completed"] for x in items_out)
            parent_completed = (
                parent_task is not None
                and str(parent_task.get("status") or "") == "completed"
            )
            if parent_id and create_missing:
                if all_done and not parent_completed:
                    gtb.complete_task(list_id, str(parent_id), completed=True)
                    parent_completed = True
                elif not all_done and parent_completed:
                    gtb.complete_task(list_id, str(parent_id), completed=False)
                    parent_completed = False

            done_n = sum(1 for x in items_out if x["completed"])
            # Nest open items under meal_label for UI
            items_out_open = [x for x in items_out if not x["completed"]]
            groups_out.append(
                {
                    "group": g.group,
                    "title": g.title,
                    "emoji": g.emoji,
                    "task_id": parent_id,
                    "list_id": list_id,
                    "completed": parent_completed or (bool(items_out) and done_n == len(items_out)),
                    "done": done_n,
                    "total": len(items_out),
                    "items": items_out,
                    "open_items": items_out_open,
                }
            )

        day_cache = {"list_id": list_id, "ids": ids}
        cache[day] = day_cache
        # prune old days (keep last 14)
        if len(cache) > 16:
            keys = sorted(k for k in cache.keys() if re.match(r"\d{4}-\d{2}-\d{2}", k))
            for old in keys[:-14]:
                cache.pop(old, None)
        _save_cache(cache)

        total = sum(g["total"] for g in groups_out)
        done = sum(g["done"] for g in groups_out)
        if created_meal_ids:
            meal_stats["created"] = list(created_meal_ids)
            if meal_stats.get("triggered"):
                meal_stats["silent"] = False
            elif owned_meal:
                # Hydrate-miss create while meal-owned GT already existed —
                # never leave triggered=false / empty created while ids rotate.
                meal_stats["triggered"] = True
                meal_stats["reason"] = meal_stats.get("reason") or "refresh_resync"
                meal_stats["silent"] = False
        completed_by_ck: Dict[str, bool] = {}
        for grp in groups_out:
            kind = str(grp.get("group") or "")
            for row in grp.get("items") or []:
                if not isinstance(row, dict):
                    continue
                slug = str(row.get("slug") or "")
                if slug:
                    completed_by_ck[cache_key(kind, slug)] = bool(row.get("completed"))
        calendar = _sync_meal_calendar(planned, ids, completed_by_ck, day)
        return {
            "ok": True,
            "source": "google_tasks",
            "list_title": list_title,
            "list_id": list_id,
            "day": day,
            "groups": groups_out,
            "summary": {"done": done, "total": total},
            "purge": purge_stats,
            "meal_regen": meal_stats,
            "protein_remaining": protein_stats,
            "calendar": calendar,
            "error": None,
        }
    except Exception as e:
        return _local_payload(
            planned,
            day=day,
            error=str(e),
            meal_regen=meal_stats,
            protein_remaining=protein_stats,
        )


def plan_preview(today_board: dict, *, day: Optional[str] = None) -> dict:
    """Fast local structure for UI skeleton before GT ensure finishes."""
    day = day or str((today_board or {}).get("date") or local_today_iso())
    planned = plan_from_today_board(today_board or {}, day=day)
    return _local_payload(planned, day=day, error=None, source="plan_preview")


def _local_payload(
    planned: List[PlannedGroup],
    *,
    day: str,
    error: Optional[str] = None,
    source: str = "local_preview",
    meal_regen: Optional[dict] = None,
    protein_remaining: Optional[dict] = None,
) -> dict:
    groups_out = []
    for g in planned:
        items = [
            {
                "slug": it.slug,
                "title": it.title,
                "completed": False,
                "task_id": None,
                "list_id": None,
                "group": g.group,
                "meal_label": it.meal_label or None,
                "local": True,
            }
            for it in g.items
        ]
        groups_out.append(
            {
                "group": g.group,
                "title": g.title,
                "emoji": g.emoji,
                "task_id": None,
                "list_id": None,
                "completed": False,
                "done": 0,
                "total": len(items),
                "items": items,
                "open_items": items,
            }
        )
    total = sum(g["total"] for g in groups_out)
    return {
        "ok": error is None,
        "source": source,
        "list_title": DEFAULT_LIST_TITLE,
        "list_id": None,
        "day": day,
        "groups": groups_out,
        "summary": {"done": 0, "total": total},
        "error": error,
        "meal_regen": meal_regen
        or meal_regen_payload(
            fingerprint=board_food_logs_fingerprint({"date": day}, day=day),
            silent=True,
        ),
        "protein_remaining": protein_remaining or protein_remaining_payload(),
    }


def _meal_slots_from_plan(
    planned: List[PlannedGroup],
    ids: Dict[str, str],
    completed_by_ck: Dict[str, bool],
    day: str,
) -> list:
    """One reminder per meal bucket that has eat_at. No invented times."""
    from .meal_calendar import MealSlotReminder, calendar_event_title

    buckets: Dict[str, dict] = {}
    order: List[str] = []
    for g in planned:
        if g.group != "nutrition":
            continue
        for it in g.items:
            eat = str(it.eat_at or "").strip()
            slot = str(it.meal_slot or "").strip()
            if not eat or not slot:
                continue
            if slot not in buckets:
                buckets[slot] = {
                    "slot": slot,
                    "title": calendar_event_title(
                        it.cal_label, it.item_name, it.portion_g
                    ),
                    "eat_at": eat,
                    "task_ids": [],
                    "completed": [],
                }
                order.append(slot)
            ck = cache_key(g.group, it.slug)
            tid = str(ids.get(ck) or "").strip()
            if tid:
                buckets[slot]["task_ids"].append(tid)
            buckets[slot]["completed"].append(bool(completed_by_ck.get(ck)))
    slots = []
    for i, key in enumerate(order):
        raw = buckets[key]
        next_eat = ""
        if i + 1 < len(order):
            next_eat = str(buckets[order[i + 1]].get("eat_at") or "")
        done_flags = raw["completed"]
        slots.append(
            MealSlotReminder(
                day=day,
                slot=raw["slot"],
                title=str(raw["title"] or "")[:200],
                eat_at=str(raw["eat_at"] or ""),
                task_ids=list(raw["task_ids"]),
                all_completed=bool(done_flags) and all(done_flags),
                next_eat_at=next_eat,
            )
        )
    return slots


def _sync_meal_calendar(
    planned: List[PlannedGroup],
    ids: Dict[str, str],
    completed_by_ck: Dict[str, bool],
    day: str,
) -> dict:
    """Best-effort Calendar upsert beside GT publish. Never fails the checklist."""
    try:
        from .meal_calendar import sync_meal_reminders

        slots = _meal_slots_from_plan(planned, ids, completed_by_ck, day)
        return sync_meal_reminders(slots, day=day)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "skipped": True,
            "error": str(exc),
            "error_code": "calendar_error",
            "upserted": 0,
            "deleted": 0,
        }


def complete_leaf(
    list_id: str,
    task_id: str,
    *,
    completed: bool = True,
    parent_id: Optional[str] = None,
    sibling_all_done: Optional[bool] = None,
) -> dict:
    if not list_id or not task_id:
        return {"ok": False, "error": "missing list_id or task_id"}
    try:
        result = gtb.complete_task(list_id, task_id, completed=completed)
        if not result.get("ok"):
            return result
        if parent_id and sibling_all_done is not None:
            gtb.complete_task(list_id, parent_id, completed=bool(sibling_all_done))
        calendar = None
        if completed:
            task = result.get("task") or {}
            notes = str(task.get("notes") or "")
            if not notes:
                fetched = _get_task_safe(list_id, task_id)
                notes = str((fetched or {}).get("notes") or "")
            try:
                from .meal_calendar import cancel_reminder_for_task

                calendar = cancel_reminder_for_task(
                    task_id, day=quest_mark_day(notes), notes=notes
                )
            except Exception as exc:  # noqa: BLE001
                calendar = {
                    "ok": False,
                    "skipped": True,
                    "error": str(exc),
                    "deleted": 0,
                }
        return {
            "ok": True,
            "task": result.get("task"),
            "parent_id": parent_id,
            "calendar": calendar,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
