#!/usr/bin/env node
/**
 * Evaluate FitDash macro calorie-share math from static/app.js and print
 * a snapshot of the actual output. Does not reimplement the formulas.
 *
 * Reads static/app.js only. Targets below are the caller's inputs
 * (170 g / 195 g / 60 g → 34% / 39% / 27%). They are not read from
 * targets.json and this script never writes them.
 *
 * Regenerate the checked-in snapshot with:
 *   UPDATE_SNAPSHOTS=1 python3 -m unittest tests.test_macro_share_snapshots
 */
"use strict";

const fs = require("fs");
const path = require("path");

const APP = path.join(__dirname, "..", "static", "app.js");
const src = fs.readFileSync(APP, "utf8");

function extractFunction(name) {
  const marker = "function " + name + "(";
  const start = src.indexOf(marker);
  if (start < 0) throw new Error("missing function " + name);
  const open = src.indexOf("{", start);
  let depth = 0;
  for (let j = open; j < src.length; j++) {
    const ch = src[j];
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return src.slice(start, j + 1);
    }
  }
  throw new Error("unclosed function " + name);
}

function sliceBetween(startMarker, endMarker) {
  const i = src.indexOf(startMarker);
  if (i < 0) throw new Error("missing start marker: " + startMarker);
  const j = src.indexOf(endMarker, i + startMarker.length);
  if (j < 0) throw new Error("missing end marker: " + endMarker);
  return src.slice(i, j);
}

const splitsSrc = sliceBetween(
  "const splits = macroDays.map((n) => {",
  "const base = chartDefaults();"
);
const latestSrc = sliceBetween(
  "// Last civil day on a 90d axis is often empty (today not logged yet) —",
  "const lastRoll = (() => {"
);
const lastRollSrc = sliceBetween(
  "const lastRoll = (() => {",
  "// Period averages over the same days shown on the chart"
);
const sumSrc = sliceBetween("let sumPg = 0;", "if (!last || last.p == null)");
const pctSrc = sliceBetween(
  "const totK = sumPg * 4 + sumCg * 4 + sumFg * 9;",
  "const firstD = macroDays[0]"
);

const factory = new Function(
  [
    extractFunction("rollingAverage"),
    extractFunction("macroRollingShare"),
    extractFunction("macroTrailingLoggedCount"),
    extractFunction("macroGram"),
    extractFunction("macroCalPct"),
    extractFunction("macroTargetBandClass"),
    extractFunction("targetPct"),
    "function chartMath(macroDays) {",
    splitsSrc,
    latestSrc,
    lastRollSrc,
    sumSrc,
    pctSrc,
    "return { splits, pPct, cPct, fPct, pRoll, cRoll, fRoll, rollWin, pRoll30, cRoll30, fRoll30, roll30Win, roll30Min, last, lastIdx, lastDate, lastRoll, roll30, roll30Logged, sumPg, sumCg, sumFg, nDays, totK, pctP, pctC, pctF, avgPg, avgCg, avgFg };",
    "}",
    "return { macroCalPct, macroTargetBandClass, targetPct, rollingAverage, macroRollingShare, macroTrailingLoggedCount, chartMath };",
  ].join("\n")
);

const api = factory();
const { macroCalPct, macroTargetBandClass, targetPct, chartMath } = api;

// Live applied targets from the nutrition owner. Not the git seed.
const TARGETS = { protein_g: 170, carbs_g: 195, fat_g: 60 };
const TGT_PCT = macroCalPct(TARGETS.protein_g, TARGETS.carbs_g, TARGETS.fat_g);

function bands(pct) {
  if (!pct) return { p: "", c: "", f: "" };
  return {
    p: macroTargetBandClass(pct.p, TGT_PCT.p),
    c: macroTargetBandClass(pct.c, TGT_PCT.c),
    f: macroTargetBandClass(pct.f, TGT_PCT.f),
  };
}

function gramHit(grams) {
  return {
    p: targetPct(grams.p, TARGETS.protein_g),
    c: targetPct(grams.c, TARGETS.carbs_g),
    f: targetPct(grams.f, TARGETS.fat_g),
  };
}

function dayRecord(input) {
  const macroDays = [
    {
      date: input.date || null,
      protein_g: input.protein_g,
      carbs_g: input.carbs_g,
      fat_g: input.fat_g,
    },
  ];
  const chart = chartMath(macroDays);
  const split = chart.splits[0];
  const tile = macroCalPct(input.protein_g, input.carbs_g, input.fat_g);
  return {
    input: {
      protein_g: input.protein_g === undefined ? null : input.protein_g,
      carbs_g: input.carbs_g === undefined ? null : input.carbs_g,
      fat_g: input.fat_g === undefined ? null : input.fat_g,
    },
    tile_pct: tile,
    chart_pct: { p: split.p, c: split.c, f: split.f },
    chart_grams: split.grams,
    band_on_tile_pct: bands(tile),
    band_on_chart_pct: bands({ p: split.p, c: split.c, f: split.f }),
    gram_hit_pct: gramHit(split.grams),
    displayed_grams: {
      p: Math.round(split.grams.p),
      c: Math.round(split.grams.c),
      f: Math.round(split.grams.f),
    },
    roll_window_days: chart.rollWin,
    roll_pct: chart.lastRoll,
  };
}

function seriesRecord(rows) {
  const macroDays = rows.map((row) => ({
    date: row.date,
    protein_g: row.protein_g,
    carbs_g: row.carbs_g,
    fat_g: row.fat_g,
  }));
  const chart = chartMath(macroDays);
  const days = macroDays.map((row, i) => {
    const split = chart.splits[i];
    const pct = { p: split.p, c: split.c, f: split.f };
    return {
      date: row.date,
      input: {
        protein_g: row.protein_g,
        carbs_g: row.carbs_g,
        fat_g: row.fat_g,
      },
      pct,
      grams: split.grams,
      band: bands(pct),
      roll: {
        p: chart.pRoll[i],
        c: chart.cRoll[i],
        f: chart.fRoll[i],
      },
      roll30: {
        p: chart.pRoll30[i],
        c: chart.cRoll30[i],
        f: chart.fRoll30[i],
      },
    };
  });
  const periodPct = { p: chart.pctP, c: chart.pctC, f: chart.pctF };
  const avgG = { p: chart.avgPg, c: chart.avgCg, f: chart.avgFg };
  const end30 = {
    p: chart.pRoll30.at(-1),
    c: chart.cRoll30.at(-1),
    f: chart.fRoll30.at(-1),
  };
  const rolling30 = {
    window_days: chart.roll30Win,
    min_logged_days: chart.roll30Min,
    logged_days: chart.roll30Logged,
    enough: end30.p != null && end30.c != null && end30.f != null,
    pct: end30.p == null ? null : end30,
    band: end30.p == null ? { p: "", c: "", f: "" } : bands(end30),
    chip: chart.roll30,
  };
  return {
    roll_window_days: chart.rollWin,
    days,
    latest: chart.last
      ? {
          date: chart.lastDate,
          pct: { p: chart.last.p, c: chart.last.c, f: chart.last.f },
          grams: chart.last.grams,
          band: bands(chart.last),
          gram_hit_pct: gramHit(chart.last.grams),
        }
      : null,
    rolling_7d: chart.lastRoll
      ? { pct: chart.lastRoll, band: bands(chart.lastRoll) }
      : null,
    period: {
      n_days: chart.nDays,
      sum_g: { p: chart.sumPg, c: chart.sumCg, f: chart.sumFg },
      avg_g: avgG,
      displayed_avg_g: {
        p: Math.round(avgG.p),
        c: Math.round(avgG.c),
        f: Math.round(avgG.f),
      },
      pct: periodPct,
      band: bands(periodPct),
      gram_hit_pct: gramHit(avgG),
      tot_kcal: chart.totK,
    },
    rolling_30d: rolling30,
  };
}

const spanMatch = src.match(/const macroSpanDays = (\d+);/);
if (!spanMatch) throw new Error("macroSpanDays not found");

const ON = { protein_g: 170, carbs_g: 195, fat_g: 60 };
const GAP = { protein_g: null, carbs_g: null, fat_g: null };
const ZERO = { protein_g: 0, carbs_g: 0, fat_g: 0 };
const REALITY = { protein_g: 121.5, carbs_g: 184.5, fat_g: 64 };
const HALF = { protein_g: 85, carbs_g: 97.5, fat_g: 30 };
const PROTEIN_ONLY = { protein_g: 170, carbs_g: null, fat_g: null };

function iso(day) {
  return "2026-09-" + String(day).padStart(2, "0");
}

function dated(n, row) {
  const days = [];
  for (let i = 1; i <= n; i++) {
    days.push(Object.assign({ date: iso(i) }, row));
  }
  return days;
}

// Consecutive civil days. Nulls sit in the middle of the window so a
// zero-fill would move the mean; skipping them must not.
function loggedWithMiddleGaps(loggedRow, loggedCount, nullCount) {
  const head = Math.ceil(loggedCount / 2);
  const tail = loggedCount - head;
  const rows = []
    .concat(dated(head, loggedRow))
    .concat(dated(nullCount, GAP))
    .concat(dated(tail, loggedRow));
  return rows.map((row, i) => Object.assign({}, row, { date: iso(i + 1) }));
}

function judge30(rows) {
  const chart = chartMath(rows);
  const pct =
    chart.roll30 && chart.roll30.p != null
      ? { p: chart.roll30.p, c: chart.roll30.c, f: chart.roll30.f }
      : null;
  return {
    window_days: chart.roll30Win,
    min_logged_days: chart.roll30Min,
    logged_days: chart.roll30Logged,
    enough: pct != null,
    pct,
    band: pct ? bands(pct) : { p: "", c: "", f: "" },
    civil_days: rows.length,
  };
}

const thirty = [];
for (let day = 1; day <= 30; day++) {
  let row = Object.assign({ date: iso(day) }, ON);
  if (day === 5 || day === 6 || day === 12) row = Object.assign({ date: iso(day) }, GAP);
  else if (day === 8) row = Object.assign({ date: iso(day) }, ZERO);
  else if (day === 10) row = Object.assign({ date: iso(day) }, PROTEIN_ONLY);
  else if (day === 29) row = Object.assign({ date: iso(day) }, HALF);
  else if (day === 30) row = Object.assign({ date: iso(day) }, REALITY);
  thirty.push(row);
}

const directBand = {
  protein_target_34: {
    "29": macroTargetBandClass(29, TGT_PCT.p),
    "39": macroTargetBandClass(39, TGT_PCT.p),
    "28.9": macroTargetBandClass(28.9, TGT_PCT.p),
    "39.1": macroTargetBandClass(39.1, TGT_PCT.p),
    "34": macroTargetBandClass(34, TGT_PCT.p),
  },
  carbs_target_39: {
    "34": macroTargetBandClass(34, TGT_PCT.c),
    "44": macroTargetBandClass(44, TGT_PCT.c),
    "33.9": macroTargetBandClass(33.9, TGT_PCT.c),
    "44.1": macroTargetBandClass(44.1, TGT_PCT.c),
  },
  fat_target_27: {
    "22": macroTargetBandClass(22, TGT_PCT.f),
    "32": macroTargetBandClass(32, TGT_PCT.f),
    "21.9": macroTargetBandClass(21.9, TGT_PCT.f),
    "32.1": macroTargetBandClass(32.1, TGT_PCT.f),
  },
  null_and_non_positive_target: {
    null_value: macroTargetBandClass(null, 34),
    null_target: macroTargetBandClass(34, null),
    zero_target: macroTargetBandClass(34, 0),
  },
};

const payload = {
  meta: {
    source: "resistance-dashboard/static/app.js",
    evaluated: [
      "macroGram",
      "macroCalPct",
      "macroTargetBandClass",
      "targetPct",
      "rollingAverage",
      "macroRollingShare",
      "macroTrailingLoggedCount",
      "chart day split",
      "7-day rolling percents",
      "30-day health-goal average",
      "period gram pool",
    ],
    chart_roll_30_days: 30,
    chart_roll_30_min_logged: 21,
    kcal_per_gram: { protein: 4, carbs: 4, fat: 9 },
    band_percentage_points: 5,
    band_inclusive: true,
    chart_roll_window_days: chartMath([Object.assign({ date: "2026-09-01" }, ON)]).rollWin,
    chart_span_days: Number(spanMatch[1]),
    targets_are_inputs: true,
    targets_json_not_read: true,
  },
  targets: {
    protein_g: TARGETS.protein_g,
    carbs_g: TARGETS.carbs_g,
    fat_g: TARGETS.fat_g,
    tile_pct: TGT_PCT,
    chart_pct: dayRecord(ON).chart_pct,
  },
  cases: {
    on_target_day: dayRecord(ON),
    recent_reality_protein_low_fat_high: dayRecord(REALITY),
    band_edges_from_grams: {
      protein_29: dayRecord({ protein_g: 134.5, carbs_g: 195, fat_g: 60 }),
      protein_28_9: dayRecord({ protein_g: 133.9, carbs_g: 195, fat_g: 60 }),
      protein_39: dayRecord({ protein_g: 210.6, carbs_g: 195, fat_g: 60 }),
      protein_39_1: dayRecord({ protein_g: 211.5, carbs_g: 195, fat_g: 60 }),
      fat_22: dayRecord({ protein_g: 170, carbs_g: 195, fat_g: 45.7 }),
      fat_21_9: dayRecord({ protein_g: 170, carbs_g: 195, fat_g: 45.4 }),
      fat_32: dayRecord({ protein_g: 170, carbs_g: 195, fat_g: 76.2 }),
      fat_32_1: dayRecord({ protein_g: 170, carbs_g: 195, fat_g: 76.6 }),
      carbs_34: dayRecord({ protein_g: 170, carbs_g: 156.8, fat_g: 60 }),
      carbs_33_9: dayRecord({ protein_g: 170, carbs_g: 156.1, fat_g: 60 }),
      carbs_44: dayRecord({ protein_g: 170, carbs_g: 239.2, fat_g: 60 }),
      carbs_44_1: dayRecord({ protein_g: 170, carbs_g: 240.2, fat_g: 60 }),
    },
    band_edges_direct: directBand,
    zero_food_day: dayRecord(ZERO),
    missing_fields: {
      all_null: dayRecord(GAP),
      protein_only: dayRecord(PROTEIN_ONLY),
      fat_missing: dayRecord({ protein_g: 170, carbs_g: 195, fat_g: null }),
      non_numeric_protein: dayRecord({ protein_g: "nope", carbs_g: 10, fat_g: 10 }),
    },
    thirty_day_series_with_gaps: seriesRecord(thirty),
    grams_vs_pct_disagreement: {
      half_portions_same_pct: dayRecord(HALF),
      protein_grams_on_target_share_low: dayRecord({
        protein_g: 170,
        carbs_g: 195,
        fat_g: 120,
      }),
    },
    formula_disagreement: dayRecord({ protein_g: 100.5, carbs_g: 5, fat_g: 42 }),
    rounding_residue: dayRecord({ protein_g: 1, carbs_g: 1, fat_g: 1 }),
    rolling_30d_judge: {
      logged_20: judge30(dated(20, ON)),
      logged_21: judge30(dated(21, ON)),
      twenty_one_logged_among_nulls: judge30(loggedWithMiddleGaps(ON, 21, 9)),
      twenty_logged_among_nulls: judge30(loggedWithMiddleGaps(ON, 20, 10)),
      band_edges: {
        protein_29: judge30(dated(21, { protein_g: 134.5, carbs_g: 195, fat_g: 60 })),
        protein_28_9: judge30(dated(21, { protein_g: 133.9, carbs_g: 195, fat_g: 60 })),
        protein_39: judge30(dated(21, { protein_g: 210.6, carbs_g: 195, fat_g: 60 })),
        protein_39_1: judge30(dated(21, { protein_g: 211.5, carbs_g: 195, fat_g: 60 })),
        fat_22: judge30(dated(21, { protein_g: 170, carbs_g: 195, fat_g: 45.7 })),
        fat_21_9: judge30(dated(21, { protein_g: 170, carbs_g: 195, fat_g: 45.4 })),
        fat_32: judge30(dated(21, { protein_g: 170, carbs_g: 195, fat_g: 76.2 })),
        fat_32_1: judge30(dated(21, { protein_g: 170, carbs_g: 195, fat_g: 76.6 })),
        carbs_34: judge30(dated(21, { protein_g: 170, carbs_g: 156.8, fat_g: 60 })),
        carbs_33_9: judge30(dated(21, { protein_g: 170, carbs_g: 156.1, fat_g: 60 })),
        carbs_44: judge30(dated(21, { protein_g: 170, carbs_g: 239.2, fat_g: 60 })),
        carbs_44_1: judge30(dated(21, { protein_g: 170, carbs_g: 240.2, fat_g: 60 })),
      },
    },
  },
};

process.stdout.write(JSON.stringify(payload, null, 2) + "\n");
