#!/usr/bin/env node
/**
 * Body-fat series on the Trends weight chart.
 * Raw points: every valid logged measurement, original date and value.
 * Overlay: trailing 7-calendar-day arithmetic mean. Missing days are
 * excluded, not counted as 0. An empty window is null, not 0.
 * This is not the mean of the last seven measurements.
 */
"use strict";

const bf = require("../static/body-fat-series.js");

let passed = 0;
function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
  passed += 1;
}

function almostEqual(a, b, msg) {
  assert(
    a != null && b != null && Math.abs(a - b) < 1e-9,
    msg + " got " + a + " expected " + b
  );
}

assert(bf.WINDOW_DAYS === 7, "window is 7 calendar days");
assert(bf.addDays("2026-03-09", -1) === "2026-03-08", "DST spring boundary stays one civil day");
assert(bf.addDays("2026-11-02", -1) === "2026-11-01", "DST fall boundary stays one civil day");
assert(bf.addDays("2026-01-01", -1) === "2025-12-31", "year boundary");

const samples = [
  { date: "2026-11-06", percentage: 18.1, recorded_at: "2026-11-06T11:00:00Z" },
  { date: "2026-11-06", percentage: 18.4, recorded_at: "2026-11-06T15:02:00Z" },
  { date: "2026-11-05", percentage: 18.6, recorded_at: "2026-11-05T12:00:00Z" },
  { date: "2026-11-04", percentage: 140, recorded_at: "2026-11-04T12:00:00Z" },
  { date: "2026-11-03", percentage: -1 },
];

const aligned = bf.alignToLabels(
  ["2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07"],
  samples
);
assert(JSON.stringify(aligned) === JSON.stringify([null, 18.6, 18.4, null]), "align " + JSON.stringify(aligned));

const latest = bf.latest(samples);
assert(latest && latest.date === "2026-11-06" && latest.percentage === 18.4, "latest " + JSON.stringify(latest));

const sameStamp = bf.lastByDate([
  { date: "2026-11-06", percentage: 19, recorded_at: "2026-11-06T11:00:00Z" },
  { date: "2026-11-06", percentage: 19.5, recorded_at: "2026-11-06T11:00:00Z" },
]);
assert(sameStamp["2026-11-06"].percentage === 19.5, "same timestamp last write wins");

assert(bf.alignToLabels(["2026-11-06"], [])[0] === null, "empty stays null");
assert(bf.latest([]) === null, "no latest");

// Every logged measurement stays a point. Same-day readings are not collapsed.
const raw = bf.measurements(samples);
assert(raw.length === 3, "invalid percentages dropped, both same-day readings kept " + raw.length);
assert(raw[0].date === "2026-11-05" && raw[0].percentage === 18.6, "first raw point");
assert(raw[1].percentage === 18.1 && raw[2].percentage === 18.4, "same-day points keep original values");
assert(raw[1].date === "2026-11-06" && raw[2].date === "2026-11-06", "same-day points keep original date");

// Irregular spacing: not the mean of the last seven measurements, and missing
// days inside the window are not zeros.
const irregular = [
  { date: "2026-10-01", percentage: 10 },
  { date: "2026-10-10", percentage: 10 },
  { date: "2026-10-20", percentage: 10 },
  { date: "2026-11-01", percentage: 10 },
  { date: "2026-11-02", percentage: 20 },
  { date: "2026-11-03", percentage: 30 },
  { date: "2026-11-04", percentage: 40 },
  { date: "2026-11-08", percentage: 50 },
];
const irregularAvg = bf.trailingAverage(irregular, ["2026-11-08"]);
almostEqual(irregularAvg[0], 35, "irregular window is the mean of readings in [D-6, D]");
assert(irregularAvg[0] !== 0, "irregular window is not filled with zeros");

// Last-seven-measurements mean would include 2026-10-10 and would not be 35.
const lastSeven = [10, 10, 10, 20, 30, 40, 50];
const lastSevenMean = lastSeven.reduce((s, n) => s + n, 0) / lastSeven.length;
assert(Math.abs(irregularAvg[0] - lastSevenMean) > 1, "not the mean of the last seven measurements");

// Missing days between two readings contribute nothing.
const gapped = bf.trailingAverage(
  [
    { date: "2026-11-02", percentage: 10 },
    { date: "2026-11-08", percentage: 30 },
  ],
  ["2026-11-08"]
);
almostEqual(gapped[0], 20, "missing days are skipped, not averaged in as 0");

// Window boundaries are inclusive on both ends. D-7 and D+1 stay out.
const bounded = bf.trailingAverage(
  [
    { date: "2026-11-01", percentage: 100 },
    { date: "2026-11-02", percentage: 10 },
    { date: "2026-11-08", percentage: 20 },
    { date: "2026-11-09", percentage: 80 },
  ],
  ["2026-11-08"]
);
almostEqual(bounded[0], 15, "inclusive [D-6, D]; D-7 and D+1 excluded");

// DST fall-back: 2026-11-01 is the US transition. D = 2026-11-02 still reaches 2026-10-27.
const dst = bf.trailingAverage(
  [
    { date: "2026-10-26", percentage: 40 },
    { date: "2026-10-27", percentage: 14 },
    { date: "2026-11-02", percentage: 16 },
  ],
  ["2026-11-02"]
);
almostEqual(dst[0], 15, "DST boundary keeps a 7-civil-day window");

// Year boundary: D = 2026-01-03 includes 2025-12-28 and excludes 2025-12-27.
const year = bf.trailingAverage(
  [
    { date: "2025-12-27", percentage: 40 },
    { date: "2025-12-28", percentage: 12 },
    { date: "2026-01-03", percentage: 18 },
  ],
  ["2026-01-03"]
);
almostEqual(year[0], 15, "year boundary includes D-6 and excludes D-7");

// Single-point window is that measurement. Empty window is null, not 0.
almostEqual(
  bf.trailingAverage([{ date: "2026-11-08", percentage: 17.5 }], ["2026-11-08"])[0],
  17.5,
  "single-point window"
);
assert(
  bf.trailingAverage([], ["2026-11-08"])[0] === null,
  "empty sample list omits the overlay"
);
assert(
  bf.trailingAverage([{ date: "2026-11-01", percentage: 10 }], ["2026-11-08"])[0] === null,
  "window with no valid measurement is null"
);
const gapLabels = bf.trailingAverage(
  [{ date: "2026-11-01", percentage: 10 }],
  ["2026-11-01", "2026-11-10"]
);
assert(gapLabels[0] === 10 && gapLabels[1] === null, "later empty window stays null " + gapLabels.join(","));

// A logged 0% is a real measurement. It is not the same as a missing day.
almostEqual(
  bf.trailingAverage([{ date: "2026-11-08", percentage: 0 }], ["2026-11-08"])[0],
  0,
  "logged zero is a measurement"
);
almostEqual(
  bf.trailingAverage(
    [
      { date: "2026-11-05", percentage: 0 },
      { date: "2026-11-08", percentage: 20 },
    ],
    ["2026-11-08"]
  )[0],
  10,
  "logged zero counts in the mean"
);

// Invalid readings in the window are dropped. They do not zero the mean.
almostEqual(
  bf.trailingAverage(
    [
      { date: "2026-11-06", percentage: 20 },
      { date: "2026-11-07", percentage: 140 },
      { date: "2026-11-08", percentage: -1 },
      { date: "2026-11-08", percentage: "nope" },
    ],
    ["2026-11-08"]
  )[0],
  20,
  "invalid readings are not measurements"
);
assert(
  bf.trailingAverage(
    [
      { date: "2026-11-08", percentage: 140 },
      { date: "2026-11-08", percentage: null },
    ],
    ["2026-11-08"]
  )[0] === null,
  "only-invalid window is empty"
);

// Two readings on one day both count. The mean is not the later timestamp.
almostEqual(
  bf.trailingAverage(
    [
      { date: "2026-11-08", percentage: 18, recorded_at: "2026-11-08T08:00:00Z" },
      { date: "2026-11-08", percentage: 22, recorded_at: "2026-11-08T20:00:00Z" },
    ],
    ["2026-11-08"]
  )[0],
  20,
  "same-day readings are both in the mean"
);

// A measurement before the first label still counts when it falls in [D-6, D].
almostEqual(
  bf.trailingAverage(
    [
      { date: "2026-11-03", percentage: 16 },
      { date: "2026-11-08", percentage: 20 },
    ],
    ["2026-11-08"]
  )[0],
  18,
  "lead-in day before the label counts"
);

// body_fat_pct is accepted, same as percentage.
almostEqual(
  bf.trailingAverage([{ date: "2026-11-08", body_fat_pct: 19.25 }], ["2026-11-08"])[0],
  19.25,
  "body_fat_pct field"
);

// Axis keeps every weigh-in date and every valid measurement date.
const axis = bf.axisDates(
  ["2026-11-01", "2026-11-08"],
  [
    { date: "2026-11-04", percentage: 18 },
    { date: "2026-11-08", percentage: 19, recorded_at: "2026-11-08T08:00:00Z" },
    { date: "2026-11-08", percentage: 21, recorded_at: "2026-11-08T20:00:00Z" },
    { date: "2026-11-09", percentage: 140 },
    { date: "2026-11-03", percentage: -5 },
  ]
);
assert(
  JSON.stringify(axis) === JSON.stringify(["2026-11-01", "2026-11-04", "2026-11-08"]),
  "axis dates " + JSON.stringify(axis)
);

// Order of rows does not change the mean. The window is by date, not by array order.
almostEqual(
  bf.trailingAverage(
    [
      { date: "2026-11-08", percentage: 30 },
      { date: "2026-11-02", percentage: 10 },
    ],
    ["2026-11-08"]
  )[0],
  20,
  "unsorted measurements use civil dates"
);

// Sparse clusters, a gap longer than the window, several readings on one
// day, and the inclusive [D-6, D] edge. Missing days are not filled with 0.
const sparse = [
  { date: "2026-01-01", percentage: 21 },
  { date: "2026-02-01", percentage: 25 },
  { date: "2026-03-01", percentage: 23 },
];
const sparseAvg = bf.trailingAverage(sparse, [
  "2026-01-01",
  "2026-01-07",
  "2026-01-08",
  "2026-02-01",
]);
almostEqual(sparseAvg[0], 21, "sparse: logged day is that reading");
almostEqual(sparseAvg[1], 21, "sparse: day D-6 still includes the reading");
assert(sparseAvg[2] === null, "gap > 7 days omits the overlay");
assert(sparseAvg[2] !== 0, "omitted overlay is not a zero fill");
almostEqual(sparseAvg[3], 25, "next cluster does not pull in the older reading");
const sparseAll = (21 + 25 + 23) / 3;
assert(Math.abs(sparseAvg[3] - sparseAll) > 1, "sparse window is not a last-measurements mean");

const sameDayRows = [
  { date: "2026-04-01", percentage: 10, recorded_at: "2026-04-01T08:00:00Z" },
  { date: "2026-04-01", percentage: 14, recorded_at: "2026-04-01T12:00:00Z" },
  { date: "2026-04-01", percentage: 18, recorded_at: "2026-04-01T18:00:00Z" },
  { date: "2026-04-03", percentage: 22 },
];
const sameDayRaw = bf.measurements(sameDayRows);
assert(sameDayRaw.length === 4, "raw points keep every same-day measurement");
assert(
  sameDayRaw.map((p) => p.percentage).join(",") === "10,14,18,22",
  "raw points keep the original percentages " + sameDayRaw.map((p) => p.percentage).join(",")
);
assert(
  sameDayRaw.filter((p) => p.date === "2026-04-01").length === 3,
  "three readings stay on their own day"
);
almostEqual(
  bf.trailingAverage(sameDayRows, ["2026-04-03"])[0],
  16,
  "same-day readings each count once in the window"
);

// D = 2026-05-10. D-6 = 2026-05-04 is inside. D-7 = 2026-05-03 is outside.
almostEqual(
  bf.trailingAverage(
    [
      { date: "2026-05-03", percentage: 30 },
      { date: "2026-05-04", percentage: 10 },
      { date: "2026-05-10", percentage: 20 },
    ],
    ["2026-05-10"]
  )[0],
  15,
  "window boundary includes D-6 and excludes D-7"
);

console.log("ok body-fat-series");
console.log(passed + " assertions");
