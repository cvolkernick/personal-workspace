#!/usr/bin/env node
/**
 * #946: trailing 7-day means for calories in and calories out.
 * Missing logs are excluded, not counted as 0. Fewer than 4 logged
 * days in the window suppresses the point. Prior days before the
 * visible labels count when the map already has them.
 */
"use strict";

const roll = require("../static/calorie-rolling-avg.js");

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

function almostEqual(a, b, msg) {
  assert(a != null && b != null && Math.abs(a - b) < 1e-9, msg + " got " + a + " expected " + b);
}

assert(roll.WINDOW_DAYS === 7, "window is 7 calendar days");
assert(roll.MIN_LOGGED === 4, "fewer than 4 logged days suppresses the point");

assert(roll.addDays("2026-03-09", -1) === "2026-03-08", "DST spring boundary stays one civil day");
assert(roll.addDays("2026-11-02", -1) === "2026-11-01", "DST fall boundary stays one civil day");
assert(roll.addDays("2026-01-01", -1) === "2025-12-31", "year boundary");

const logged = roll.kcalByDate([
  { date: "2026-09-01", calories: 1000 },
  { date: "2026-09-01", calories: 1100 },
  { date: "2026-09-02", calories: 0 },
  { date: "2026-09-03", calories: null },
  { date: "2026-09-04", calories: "nope" },
  { date: "2026-09-05T15:00:00Z", calories: 1400 },
]);
assert(logged["2026-09-01"] === 1100, "duplicate date keeps the later finite value");
assert(logged["2026-09-02"] === 0, "a logged zero is a food log");
assert(!Object.prototype.hasOwnProperty.call(logged, "2026-09-03"), "null calories is not a log");
assert(!Object.prototype.hasOwnProperty.call(logged, "2026-09-04"), "non-finite calories is not a log");
assert(logged["2026-09-05"] === 1400, "date key is the civil day");

const axis = roll.valuesOnLabels(logged, ["2026-09-01", "2026-09-02", "2026-09-03"]);
assert(axis[0] === 1100 && axis[1] === 0 && axis[2] === null, "raw axis keeps 0 and leaves gaps null");

// Six logged days at 2100 and one missing day. Missing must not become 0
// (that would pull the mean to 1800).
const dense = {};
const denseLabels = [];
for (let i = 0; i < 7; i++) {
  const day = roll.addDays("2026-09-14", -6 + i);
  denseLabels.push(day);
  if (day !== "2026-09-11") dense[day] = 2100;
}
const denseMeans = roll.trailingMeans(dense, ["2026-09-14"]);
almostEqual(denseMeans[0], 2100, "missing day is excluded from the mean, not counted as 0");

// Exactly four logged days.
const four = {
  "2026-09-08": 1000,
  "2026-09-10": 2000,
  "2026-09-12": 3000,
  "2026-09-14": 4000,
};
almostEqual(
  roll.trailingMeans(four, ["2026-09-14"])[0],
  2500,
  "four logged days average those days only"
);

// Three logged days in the window, even if they are large, is suppressed.
// Older finite days outside the 7-day calendar window must not be pulled in.
const sparse = {
  "2026-09-01": 9000,
  "2026-09-02": 9000,
  "2026-09-03": 9000,
  "2026-09-12": 5000,
  "2026-09-13": 5000,
  "2026-09-14": 5000,
};
assert(
  roll.trailingMeans(sparse, ["2026-09-14"])[0] === null,
  "fewer than 4 logged days in the calendar window suppresses the point"
);

// A logged zero counts toward the minimum and toward the mean.
const withZero = {
  "2026-09-11": 0,
  "2026-09-12": 100,
  "2026-09-13": 100,
  "2026-09-14": 100,
};
almostEqual(roll.trailingMeans(withZero, ["2026-09-14"])[0], 75, "logged zero counts as a day");

// First visible day uses the six days before the axis when those days exist.
const prior = {};
for (let i = 0; i < 7; i++) prior[roll.addDays("2026-09-08", -6 + i)] = 100;
const first = roll.trailingMeans(prior, ["2026-09-08"]);
almostEqual(first[0], 100, "first visible day uses prior days outside the labels");

const noPrior = { "2026-09-08": 100, "2026-09-09": 100, "2026-09-10": 100 };
assert(
  roll.trailingMeans(noPrior, ["2026-09-08", "2026-09-09", "2026-09-10"])[0] === null,
  "without prior days the first point has one log and is suppressed"
);
assert(
  roll.trailingMeans(noPrior, ["2026-09-08", "2026-09-09", "2026-09-10"])[2] === null,
  "three logged days at the start of the axis are still under the minimum"
);

// Year boundary is inside the window, not clipped to January.
const year = {};
for (let i = 0; i < 7; i++) year[roll.addDays("2026-01-03", -6 + i)] = 1800;
almostEqual(
  roll.trailingMeans(year, ["2026-01-03"])[0],
  1800,
  "window crosses into the previous year"
);

// Intake holes do not zero the calories-out mean.
const intakeHoles = { "2026-09-13": 2000, "2026-09-14": 2200 };
const burnedFull = {};
for (let i = 0; i < 7; i++) burnedFull[roll.addDays("2026-09-14", -6 + i)] = 2400;
assert(roll.trailingMeans(intakeHoles, ["2026-09-14"])[0] === null, "intake window under 4 is suppressed");
almostEqual(roll.trailingMeans(burnedFull, ["2026-09-14"])[0], 2400, "calories out uses its own logs");

const tip = roll.tooltipLines({
  avgIn: 2000,
  avgOut: 2500.4,
  rawIn: 1800,
  rawOut: null,
});
assert(tip.length === 5, "tooltip has five lines");
assert(tip[0] === "7-day avg in: 2,000 kcal", "tooltip 7-day avg in " + tip[0]);
assert(tip[1] === "7-day avg out: 2,500 kcal", "tooltip 7-day avg out " + tip[1]);
assert(tip[2] === "daily in: 1,800 kcal", "tooltip daily in " + tip[2]);
assert(tip[3] === "daily out: —", "tooltip daily out missing " + tip[3]);
assert(
  tip[4] === "7-day avg out − in: +500 kcal (deficit)",
  "tooltip deficit is out minus in " + tip[4]
);

const surplus = roll.tooltipLines({ avgIn: 2500, avgOut: 2000, rawIn: 2600, rawOut: 1900 });
assert(
  surplus[4] === "7-day avg out − in: -500 kcal (surplus)",
  "tooltip surplus is out minus in " + surplus[4]
);
assert(surplus[2] === "daily in: 2,600 kcal", "tooltip raw in");
assert(surplus[3] === "daily out: 1,900 kcal", "tooltip raw out");

const even = roll.tooltipLines({ avgIn: 2000, avgOut: 2000, rawIn: 0, rawOut: 2000 });
assert(even[4] === "7-day avg out − in: 0 kcal (even)", "even balance " + even[4]);
assert(even[2] === "daily in: 0 kcal", "raw zero stays visible in the tooltip");

const empty = roll.tooltipLines({});
assert(empty[0] === "7-day avg in: —", "missing avg in");
assert(empty[4] === "7-day avg out − in: —", "missing balance");

console.log("ok calorie-rolling-avg");
