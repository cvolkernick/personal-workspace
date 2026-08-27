#!/usr/bin/env node
/**
 * #392: Trends AZM is 90 civil days of daily total_minutes, a 7d rolling
 * average of present points, and an OLS trendline on those daily points.
 * Missing / empty → honest "—". Never invent from burned kcal / steps.
 */
"use strict";

const path = require("path");
const azm = require("../static/trends-azm.js");

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

function almostEqual(a, b, msg) {
  assert(Math.abs(a - b) < 1e-9, msg + " got " + a + " expected " + b);
}

assert(azm.SPAN_DAYS === 90, "window is 90 civil days");
assert(azm.ROLL_DAYS === 7, "rolling average is 7 days");

const now = new Date(2026, 7, 27, 12, 0, 0);
const labels = azm.windowLabels(90, now);
assert(labels.length === 90, "90 civil labels");
assert(labels[labels.length - 1] === "2026-08-27", "window ends today");
assert(labels[0] === "2026-05-30", "window starts 89 days earlier");

const assayLike = [
  { date: "2026-05-20", total_minutes: 413 },
  { date: "2026-08-11", total_minutes: 10 },
  { date: "2026-08-16", total_minutes: 22 },
  { date: "2026-08-17", total_minutes: 18 },
  { date: "2026-08-18", total_minutes: 24 },
  { date: "2026-08-19", total_minutes: 12 },
  { date: "2026-08-20", total_minutes: 30 },
  { date: "2026-08-21", total_minutes: 16 },
];

const present = azm.azmSeries(assayLike, now);
assert(present.spanDays === 90, "series reports 90d span");
assert(present.pointDays === 7, "only days inside the 90d window count");
assert(present.daily.length === 90, "daily series is one slot per civil day");
assert(present.daily[0] === null, "2026-05-30 missing stays null, not 0");
assert(present.labels.indexOf("2026-08-11") !== -1, "Aug 11 is in the 90d window");
assert(present.daily[present.labels.indexOf("2026-08-11")] === 10, "in-window Aug 11 is 10");
assert(present.daily[present.labels.indexOf("2026-08-22")] === null, "2026-08-22 missing stays null, not 0");
assert(present.daily[present.labels.indexOf("2026-08-27")] === null, "2026-08-27 missing stays null, not 0");
assert(present.daily.indexOf(413) === -1, "out-of-window May 20 is not invented into the series");

const idx21 = present.labels.indexOf("2026-08-21");
almostEqual(
  present.rolling7[idx21],
  (22 + 18 + 24 + 12 + 30 + 16) / 6,
  "7d rolling avg on Aug 21 averages present points in the last 7 civil days (gap days omitted, not 0)"
);
assert(present.lastRolling7 != null, "latest rolling avg is present");
assert(azm.formatRolling(present.lastRolling7) !== "—", "present formats the rolling avg");

const rollWindow = azm.rollingAverage([10, null, 20, 30], 7);
almostEqual(rollWindow[0], 10, "first present point is its own rolling avg");
almostEqual(rollWindow[1], 10, "gap day still carries trailing avg of present neighbors");
almostEqual(rollWindow[3], (10 + 20 + 30) / 3, "rolling avg never treats null as 0");

const trend = azm.linearTrend([10, 20, 30]);
almostEqual(trend[0], 10, "2-or-more present points get an OLS line");
almostEqual(trend[2], 30, "OLS through 10,20,30 is the line itself");
const gappyTrend = azm.linearTrend([10, null, 30]);
assert(gappyTrend[0] != null && gappyTrend[2] != null, "trend fits present points only");
almostEqual(gappyTrend[1], 20, "trend is evaluated on gap slots, not set to 0");
assert(
  azm.linearTrend([10, null, null]).every(function (v) {
    return v == null;
  }),
  "one present point is not a trendline"
);

assert(azm.formatRolling(null) === "—", "empty formats as em dash");
assert(azm.formatRolling(0) === "0", "zero minutes formats as 0, not em dash");

const empty = azm.azmSeries([], now);
assert(empty.lastRolling7 === null, "empty list is null, not 0");
assert(empty.pointDays === 0, "empty has no point days");
assert(azm.formatRolling(empty.lastRolling7) === "—", "empty formats as em dash");
assert(
  empty.trend.every(function (v) {
    return v == null;
  }),
  "empty series has no invented trend"
);

const missing = azm.azmSeries(null, now);
assert(missing.lastRolling7 === null, "null points stay honest empty");
assert(azm.formatRolling(missing.lastRolling7) === "—", "null formats as em dash");

const outsideOnly = azm.azmSeries(
  [
    { date: "2026-05-20", total_minutes: 413 },
    { date: "2026-05-28", total_minutes: 88 },
  ],
  now
);
assert(outsideOnly.lastRolling7 === null, "points outside 90d do not invent a rolling avg");
assert(azm.formatRolling(outsideOnly.lastRolling7) === "—", "out-of-window is em dash");

const zeroDay = azm.azmSeries([{ date: "2026-08-27", total_minutes: 0 }], now);
assert(zeroDay.lastRolling7 === 0, "a real 0-minute day is a present point");
assert(zeroDay.pointDays === 1, "zero-minute day counts as a point day");

const noTotal = azm.azmSeries(
  [
    { date: "2026-08-27", fat_burn_minutes: 12, cardio_minutes: 8 },
    { date: "2026-08-26", steps: 8000, calories: 2400 },
  ],
  now
);
assert(noTotal.lastRolling7 === null, "does not invent total from zones / steps / kcal");

assert(azm.azmPoints({}).length === 0, "missing health key is []");
assert(azm.azmPoints({ health: {} }).length === 0, "missing AZM key is []");
assert(
  azm.azmPoints({
    health: { calories_burned: [{ date: "2026-08-27", calories: 2400 }] },
  }).length === 0,
  "does not read burned kcal as AZM"
);
assert(
  azm.azmPoints({ health: { active_zone_minutes: assayLike } }).length === 8,
  "reads health.active_zone_minutes"
);
assert(
  azm.azmPoints({ today: { active_zone_minutes: assayLike } }).length === 8,
  "can reuse agent Today slice of the same field"
);

assert(
  azm.sparklineSvg(present.daily, present.labels, present.rolling7, present.trend).indexOf("<svg") === 0,
  "present days get a sparkline"
);
assert(azm.sparklineSvg(empty.daily, empty.labels, empty.rolling7, empty.trend) === "", "no sparkline when no points");
assert(
  azm.sparklineSvg(present.daily, present.labels, present.rolling7, present.trend).indexOf("413") === -1,
  "sparkline does not invent out-of-window values"
);

const spark = azm.sparklineSvg(present.daily, present.labels, present.rolling7, present.trend);
assert(spark.indexOf('data-y-min="0"') !== -1, "Y domain starts at 0");
assert(spark.indexOf('data-y-max="30"') !== -1, "Y top is this window's max present minutes (30)");
assert((spark.match(/class="azm-y"/g) || []).length === 2, "Y ticks: 0 and max only");
assert(spark.indexOf('class="azm-y"') !== -1 && spark.indexOf(">0</text>") !== -1, "Y floor label is 0");
assert(spark.indexOf(">30</text>") !== -1, "Y top label is 30 minutes, not a min-max stretch");
assert(spark.indexOf("azm-baseline") !== -1, "faint 0-baseline");
assert(spark.indexOf("azm-daily") !== -1, "daily series is drawn");
assert(spark.indexOf("azm-roll") !== -1, "7d rolling avg is drawn");
assert(spark.indexOf("azm-trend") !== -1, "trendline is drawn");
assert(spark.indexOf("stroke-dasharray") !== -1, "trendline is dashed");
assert(spark.indexOf("#3d9cf0") !== -1, "rolling avg stays #3d9cf0");
assert(spark.indexOf("#f07178") !== -1, "trendline stays house #f07178");
assert((spark.match(/<circle /g) || []).length === 0, "90d spark has no per-day dots");
const yTexts = [...spark.matchAll(/class="azm-y"[^>]*>([^<]+)/g)].map(function (m) {
  return m[1];
});
assert(yTexts.indexOf("0") !== -1 && yTexts.indexOf("30") !== -1, "Y ticks are 0 and 30");
assert(
  yTexts.every(function (t) {
    return t.toUpperCase().indexOf("AZM") === -1;
  }),
  "Y ticks are minutes only, no AZM word"
);

const ticks = azm.monthTickIndexes(present.labels);
assert(ticks.length >= 3, "90d window has several month ticks");
assert(azm.monthLabel(present.labels[0]) === "May", "window starts in May");
assert(spark.indexOf(">May</text>") === -1, "skip May 30 when it would sit on Jun 1");
assert(spark.indexOf(">Jun</text>") !== -1, "Jun tick");
assert(spark.indexOf(">Jul</text>") !== -1, "Jul tick");
assert(spark.indexOf(">Aug</text>") !== -1, "Aug tick");
assert((spark.match(/class="azm-x"/g) || []).length === ticks.length, "X labels are month ticks, not SMTWTFS");
assert(ticks[0] > 0, "first kept tick is Jun 1, not the May 30 start");
assert(spark.indexOf(">S</text>") === -1, "no weekday-letter X ticks on the 90d spark");
assert(spark.indexOf("chart.js") === -1, "still an SVG spark, not Chart.js");
assert(spark.indexOf("last 90 days") !== -1, "aria names the 90d window");

const els = {};
function makeEl(id) {
  const el = { id: id, textContent: "", innerHTML: "" };
  els[id] = el;
  return el;
}
global.document = {
  getElementById: function (id) {
    return els[id] || null;
  },
};
makeEl("azm-trend-card");
makeEl("azm-week-value");
makeEl("azm-week-sub");
makeEl("azm-trend-note");
makeEl("azm-sparkline");

const painted = azm.paintAzmCard({ health: { active_zone_minutes: assayLike } }, now);
assert(painted && painted.lastRolling7 != null, "paint uses 7d rolling avg");
assert(els["azm-week-value"].textContent !== "—", "card shows rolling avg");
assert(els["azm-week-value"].textContent !== "132", "card is not a 7d trailing sum");
assert(els["azm-week-sub"].textContent.indexOf("90d") !== -1, "sub names the 90d window");
assert(els["azm-week-sub"].textContent.indexOf("7 days") !== -1, "sub labels present days");
assert(els["azm-trend-note"].textContent.indexOf("7d rolling avg") !== -1, "note documents 7d rolling avg");
assert(els["azm-trend-note"].textContent.indexOf("trendline on daily points") !== -1, "note names the daily-point fit");
assert(els["azm-trend-note"].textContent.indexOf("7d trailing sum") === -1, "note dropped the 7d trailing sum");
assert(els["azm-sparkline"].innerHTML.indexOf("<svg") !== -1, "sparkline paints from the same points");
assert(els["azm-sparkline"].innerHTML.indexOf("azm-roll") !== -1, "painted spark includes rolling avg");
assert(els["azm-sparkline"].innerHTML.indexOf("azm-trend") !== -1, "painted spark includes trendline");
assert(els["azm-sparkline"].innerHTML.indexOf("chicken") === -1, "does not invent food");
assert(els["azm-week-value"].textContent.indexOf("2400") === -1, "does not show burned kcal");

const emptyPaint = azm.paintAzmCard({ health: { active_zone_minutes: [] } }, now);
assert(emptyPaint.lastRolling7 === null, "empty paint rolling is null");
assert(els["azm-week-value"].textContent === "—", "empty card is em dash");
assert(els["azm-sparkline"].innerHTML === "", "empty card has no invented sparkline");
assert(
  els["azm-trend-note"].textContent.indexOf("No Active Zone Minutes") !== -1,
  "empty note is honest"
);
assert(els["azm-trend-note"].textContent.indexOf("90 days") !== -1, "empty note names 90 days");

const burnedOnly = azm.paintAzmCard(
  {
    health: {
      calories_burned: [{ date: "2026-08-27", calories: 2400 }],
      nutrition: [{ date: "2026-08-27", calories: 2000 }],
    },
  },
  now
);
assert(burnedOnly.lastRolling7 === null, "burned-only payload does not invent AZM");
assert(els["azm-week-value"].textContent === "—", "burned-only stays em dash");

console.log("ok trends-azm");
