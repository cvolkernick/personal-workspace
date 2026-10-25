"""Biomarker dashboard config — the ONE place to tune pins, thresholds, caveats.

Owners: Frankenfit (training/recovery), Nourish (cardiometabolic,
micronutrients), Restore (sleep/stress). Every number here is a
rule-of-thumb default, not a clinical rule. Flags say "review", never
diagnose. A marker is shown only if a stored panel actually reports it.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

UI_FOOTNOTE = "Thresholds are rule-of-thumb defaults, not clinical rules."

# --- Canonical marker ids ---------------------------------------------------
# canonical id -> (display name, aliases). Aliases are matched after
# lowercasing and collapsing punctuation to spaces, so "LDL-C",
# "ldl_mg_dl" and "LDL Cholesterol" all land on ``ldl``.
MARKERS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "hs_crp": ("hs-CRP", ("hs crp", "hscrp", "high sensitivity c reactive protein",
                          "hs crp high sensitivity c reactive protein", "crp")),
    "ferritin": ("Ferritin", ()),
    "iron": ("Iron", ("serum iron", "iron total")),
    "total_testosterone": ("Total testosterone", ("testosterone", "testosterone total")),
    "free_testosterone": ("Free testosterone", ("free t", "testosterone free")),
    "hemoglobin": ("Hemoglobin", ("hgb", "haemoglobin")),
    "hematocrit": ("Hematocrit", ("hct", "haematocrit")),
    "tsh": ("TSH", ("thyroid stimulating hormone",)),
    "vitamin_d": ("Vitamin D 25-OH", ("vitamin d", "25 oh vitamin d", "vitamin d 25 oh",
                                      "25 hydroxy vitamin d", "vit d")),
    "cortisol": ("Cortisol (AM)", ("am cortisol", "cortisol am", "morning cortisol")),
    "shbg": ("SHBG", ("sex hormone binding globulin",)),
    "estradiol": ("Estradiol", ("estrogen", "e2", "estradiol e2")),
    "alt": ("ALT", ("alanine aminotransferase", "sgpt", "alt sgpt")),
    "ast": ("AST", ("aspartate aminotransferase", "sgot", "ast sgot")),
    "creatinine": ("Creatinine", ()),
    "egfr": ("eGFR", ("estimated gfr", "egfr non afr am")),
    "ck": ("CK", ("creatine kinase", "cpk", "creatine kinase total")),
    "apob": ("ApoB", ("apolipoprotein b", "apo b")),
    "ldl": ("LDL-C", ("ldl", "ldl c", "ldl cholesterol", "ldl cholesterol calc")),
    "triglycerides": ("Triglycerides", ("tg", "trigs")),
    "hdl": ("HDL-C", ("hdl", "hdl c", "hdl cholesterol")),
    "glucose": ("Fasting glucose", ("glucose", "glucose fasting", "fasting glucose")),
    "hba1c": ("HbA1c", ("a1c", "hemoglobin a1c", "hgb a1c")),
    "vitamin_b12": ("Vitamin B12", ("b12", "cobalamin")),
}

# --- Pinned groups (final merge 2026-10-24: Frankenfit + Nourish + Restore) --
# Order inside a group is display order. CK is intentionally NOT pinned
# (Rythm doesn't test it); if a panel ever reports CK it lands in "Other".
PINNED_GROUPS: List[Tuple[str, str, List[str]]] = [
    ("training", "Training & recovery", [
        "hs_crp", "ferritin", "iron", "total_testosterone", "free_testosterone",
        "hemoglobin", "hematocrit", "tsh", "vitamin_d", "cortisol", "shbg",
        "estradiol", "alt", "ast", "creatinine", "egfr",
    ]),
    ("cardiometabolic", "Cardiometabolic", [
        "apob", "ldl", "triglycerides", "hdl", "glucose", "hba1c",
    ]),
    ("micronutrients", "Micronutrients", ["vitamin_b12"]),
]
OTHER_GROUP = ("other", "Everything else")

# --- Big-move thresholds vs the previous panel ------------------------------
# pct: relative change (|Δ|/prev ≥ pct%). abs: absolute change in the
# marker's unit. ratio: flag if new/prev ≥ ratio (rises only).
# ignore_pct: no default % rule for this marker. Any rule that triggers
# flags. Crossing a printed range boundary also flags.
DEFAULT_MOVE = {"pct": 20.0}
MOVE_RULES: Dict[str, dict] = {
    "apob": {"pct": 10.0},
    "ldl": {"pct": 15.0},
    "hdl": {"pct": 15.0},
    "triglycerides": {"pct": 30.0},
    "glucose": {"abs": 10.0},
    "hba1c": {"abs": 0.3},
    "ferritin": {"pct": 25.0},
    "vitamin_d": {"pct": 25.0, "abs": 10.0},
    "vitamin_b12": {"pct": 25.0},
    "creatinine": {"pct": 15.0},
    "egfr": {"abs": 10.0},
    "hs_crp": {"ratio": 2.0, "ignore_pct": True},
    "ck": {"ratio": 2.0, "ignore_pct": True},
    "total_testosterone": {"pct": 25.0},
    "free_testosterone": {"pct": 25.0},
    "cortisol": {"pct": 25.0},
    "hemoglobin": {"pct": 10.0},
    "hematocrit": {"pct": 10.0},
    "tsh": {"pct": 50.0},
}
RANGE_CROSSING_FLAGS = True

# Absolute lines flagged even inside the lab range ("review", not diagnosis).
# First matching rule per marker wins, so list the stricter one first.
LEVEL_RULES: Dict[str, List[dict]] = {
    "ferritin": [{"op": "<", "value": 30.0, "msg": "below 30 ng/mL"}],
    "vitamin_d": [{"op": "<", "value": 30.0, "msg": "below 30 ng/mL"}],
    "hs_crp": [
        {"op": ">", "value": 10.0,
         "msg": "above 10 mg/L — likely acute (illness/hard session), retest"},
        {"op": ">", "value": 3.0, "msg": "above 3 mg/L"},
    ],
}
# Lines whose crossing (either direction) always flags.
CROSSING_LINES: Dict[str, List[float]] = {"hba1c": [5.7]}

# Falling vs previous panel flags even in range (cut-trend watch).
CUT_TREND_MARKERS = ("total_testosterone", "free_testosterone", "ferritin")
CUT_TREND_MIN_DROP_PCT = 5.0  # ignore assay noise below this

# --- Training context (read-only from FitDash logged sessions) --------------
TRAINING_RECENT_HOURS = 72          # any logged lift within this window
HEAVY_LEGS_RECENT_DAYS = 5          # heavy legs / deadlift within this window
HEAVY_LEGS_PATTERN = r"deadlift|\brdl\b|squat|leg press|romanian"
TRAINING_CAVEAT_MARKERS = ("hs_crp", "creatinine", "alt", "ast", "ck")
SHOW_DAYS_SINCE_LIFT_ON = ("ck",)   # shown next to these only when present
MORNING_CUTOFF_HOUR = 10            # draws at/after this hour are non-morning
RECENT_RESULT_DAYS = 7              # Today card: result this fresh
MIN_FAST_HOURS = 8.0

# --- Context caveats (annotations, NOT flags) ------------------------------
CAVEATS: Dict[str, dict] = {
    "creatine": {
        "markers": ("creatinine", "egfr"),
        "text": "Creatine not paused — raises creatinine / lowers eGFR. Pause ~1 week before draws to compare.",
    },
    "alcohol": {
        "markers": ("triglycerides", "glucose"),
        "text": "Alcohol in the 48h before the draw skews triglycerides/glucose.",
    },
    "not_fasted": {
        "markers": ("triglycerides", "glucose"),
        "text": "Not fasted (or under 8h) — triglycerides/glucose read high.",
    },
    "training": {
        "markers": TRAINING_CAVEAT_MARKERS,
        "text": "Recent training, may read high.",
        "extra": {"creatinine": " Creatine use also raises creatinine."},
    },
    "biotin": {
        "markers": ("tsh", "total_testosterone", "free_testosterone", "estradiol",
                    "cortisol", "shbg"),
        "text": "Biotin not paused — can distort TSH and hormone immunoassays.",
    },
    "non_morning": {
        "markers": ("total_testosterone", "free_testosterone", "cortisol"),
        "text": "Non-morning draw — only compare morning draws.",
    },
    "illness": {
        "markers": ("hs_crp", "ferritin"),
        "text": "Recent illness — inflammation markers may read high.",
    },
}

# Per-draw context fields for the entry form. "auto" = filled from FitDash
# (sessions / weights / nutrition phase), read-only.
CONTEXT_FIELDS: List[dict] = [
    {"key": "draw_time", "label": "Draw time (HH:MM)", "type": "time"},
    {"key": "fasted", "label": "Fasted", "type": "yn"},
    {"key": "fast_hours", "label": "Fasting hours", "type": "number"},
    {"key": "creatine", "label": "Creatine", "type": "choice", "choices": ["", "no", "yes", "paused"]},
    {"key": "biotin", "label": "Biotin", "type": "choice", "choices": ["", "no", "yes", "paused"]},
    {"key": "alcohol_48h", "label": "Alcohol in prior 48h", "type": "yn"},
    {"key": "recent_illness", "label": "Recent illness", "type": "yn"},
    {"key": "days_since_hard_session", "label": "Days since last hard session", "type": "auto"},
    {"key": "days_since_heavy_legs", "label": "Days since heavy legs / deadlift", "type": "auto"},
    {"key": "phase", "label": "Phase", "type": "auto"},
    {"key": "bodyweight_lbs", "label": "Bodyweight (lb)", "type": "auto"},
]
CONTEXT_KEYS = tuple(f["key"] for f in CONTEXT_FIELDS)

MAX_IMPORT_ROWS = 400
