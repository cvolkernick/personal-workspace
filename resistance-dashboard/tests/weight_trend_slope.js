#!/usr/bin/env node
/**
 * Body-weight footer: OLS x is calendar days, not array index.
 * The series is clipped to the last 90 civil days before the chart and the note.
 * A window with fewer than 2 points has no trend.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "static", "app.js"),
  "utf8"
);

function extractFn(name) {
  const start = SRC.indexOf(`function ${name}`);
  if (start < 0) throw new Error("missing function " + name);
  const brace = SRC.indexOf("{", start);
  let depth = 0;
  for (let i = brace; i < SRC.length; i++) {
    const ch = SRC[i];
    if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) return SRC.slice(start, i + 1);
    }
  }
  throw new Error("unclosed function " + name);
}

function loadFn(name) {
  return new Function(`${extractFn(name)}; return ${name};`)();
}

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

const trendSlopePerDay = loadFn("trendSlopePerDay");
const trendSlopePerIndex = loadFn("trendSlopePerIndex");
const clipToRecentCalendarDays = loadFn("clipToRecentCalendarDays");
const weightGoalGapLabel = loadFn("weightGoalGapLabel");

// Weigh-ins every 4 civil days, losing 0.8 lb/week.
// Index × 7 reports 3.2 lb/week (4× the calendar rate).
const perDay = 0.8 / 7;
const dates = [
  "2026-01-01",
  "2026-01-05",
  "2026-01-09",
  "2026-01-13",
  "2026-01-17",
];
const sparse = dates.map((date, i) => ({
  date,
  value: 200 - 4 * i * perDay,
}));
const calendar = trendSlopePerDay(sparse);
const index = trendSlopePerIndex(sparse.map((p) => p.value));
assert(calendar != null && index != null, "both slopes exist");
const calendarWeek = calendar * 7;
const indexWeek = index * 7;
assert(
  Math.abs(calendarWeek - -0.8) < 1e-9,
  "calendar slope is -0.8 lb/week, got " + calendarWeek
);
assert(
  Math.abs(indexWeek - -3.2) < 1e-9,
  "index slope × 7 is -3.2 lb/week, got " + indexWeek
);
assert(
  Math.abs(indexWeek / calendarWeek - 4) < 1e-9,
  "index week-rate is 4× the calendar rate, got " + indexWeek / calendarWeek
);

assert(trendSlopePerDay([{ date: "2026-01-01", value: 180 }]) === null, "<2 points");
assert(
  trendSlopePerDay([
    { date: "2026-01-01", value: 180 },
    { date: "2026-01-01", value: 181 },
  ]) === null,
  "same civil day is not a daily rate"
);
assert(
  trendSlopePerDay([
    { date: "nope", value: 180 },
    { date: "2026-01-01", value: 181 },
  ]) === null,
  "undated points do not invent an index slope"
);

// 2026-11-05 minus 89 days is 2026-08-08. That day stays; the day before does not.
const asOf = "2026-11-05";
const history = [
  { date: "2026-05-01", weight_lbs: 250 },
  { date: "2026-08-07", weight_lbs: 210 },
  { date: "2026-08-08", weight_lbs: 200 },
  { date: "2026-10-01", weight_lbs: 190 },
  { date: "2026-11-05", weight_lbs: 180 },
  { date: "2026-11-06", weight_lbs: 170 },
];
const clipped = clipToRecentCalendarDays(history, 90, asOf);
assert(
  clipped.map((p) => p.date).join(",") === "2026-08-08,2026-10-01,2026-11-05",
  "90d clip keeps the window only, got " + clipped.map((p) => p.date).join(",")
);

const oneInside = clipToRecentCalendarDays(
  [
    { date: "2026-01-01", weight_lbs: 250 },
    { date: "2026-11-05", weight_lbs: 180 },
  ],
  90,
  asOf
);
assert(oneInside.length === 1, "old history must not pad a short window");
assert(
  trendSlopePerDay(oneInside.map((w) => ({ date: w.date, value: w.weight_lbs }))) ===
    null,
  "fewer than 2 points in the 90d window has no trend"
);

// Ancient drop must not set the 90d rate. Inside the window the scale is flat.
const mixed = clipToRecentCalendarDays(
  [
    { date: "2026-01-01", value: 250 },
    { date: "2026-08-08", value: 180 },
    { date: "2026-11-05", value: 180 },
  ],
  90,
  asOf
);
const mixedSlope = trendSlopePerDay(mixed);
assert(mixedSlope === 0, "clipped series is flat, got " + mixedSlope);

assert(
  weightGoalGapLabel(200.6, 180) === "+20.6 lb above goal",
  "positive gap is last minus goal, above goal"
);
assert(
  weightGoalGapLabel(174.4, 180) === "5.6 lb below goal",
  "negative gap reads below goal, got " + weightGoalGapLabel(174.4, 180)
);
assert(weightGoalGapLabel(180, 180) === "0.0 lb above goal", "zero gap");

const weightBlock = SRC.slice(
  SRC.indexOf("const weightSorted"),
  SRC.indexOf("Prefer server-expanded calendar series")
);
assert(weightBlock.includes("clipToRecentCalendarDays(weightSorted, 90)"), "weight clips to 90d");
assert(
  weightBlock.indexOf("clipToRecentCalendarDays") < weightBlock.indexOf("downsamplePoints"),
  "clip runs before downsample"
);
assert(weightBlock.includes("const wSlope = trendSlopePerDay("), "footer uses calendar slope");
assert(!weightBlock.includes("trendSlopePerIndex"), "weight does not use the index slope");
assert(!weightBlock.includes("lb vs last"), "old goal wording is gone");
assert(weightBlock.includes("weightGoalGapLabel(lastW, weightGoal)"), "goal gap uses the label");
assert(SRC.includes("lb above goal"), "note can say above goal");
assert(!SRC.includes("lb vs last"), "vs last is not left anywhere");

const strength = SRC.slice(
  SRC.indexOf("function renderStrength"),
  SRC.indexOf("function renderHistory")
);
assert(
  strength.includes("const loadSlope = trendSlopePerDay("),
  "sparse session load uses calendar days"
);
assert(strength.includes("date: p && p.date"), "session slope reads each point's date");
assert(!strength.includes("trendSlopePerIndex"), "session load is not an index slope");

// Sessions 7 days apart, +5 lb each time → +5 lb/week, not +35.
const sessions = [0, 1, 2, 3].map((i) => ({
  date: `2026-03-${String(1 + i * 7).padStart(2, "0")}`,
  value: 100 + i * 5,
}));
const sessionWeek = trendSlopePerDay(sessions) * 7;
assert(Math.abs(sessionWeek - 5) < 1e-9, "session calendar rate is +5 lb/week, got " + sessionWeek);
const sessionIndexWeek = trendSlopePerIndex(sessions.map((p) => p.value)) * 7;
assert(Math.abs(sessionIndexWeek - 35) < 1e-9, "index rate would claim +35 lb/week");

console.log("weight_trend_slope: ok");
